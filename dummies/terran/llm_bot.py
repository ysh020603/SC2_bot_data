"""LLM 驱动的人族 Bot.

架构总览（详见 README/llm 文档 与 用户需求文档）::

    ┌──────────────────────────────────────────────────────────────────┐
    │ create_plan()  →  BuildOrder([                                   │
    │     empty.depots,              # 自动补给                         │
    │     MorphOrbitals(),           # 自动指挥中心升级                  │
    │     ActLLMOngoingTasks(...)    # ★ 动态运营层 (LLM 驱动)          │
    │     TerranBaseTactics(...),    # 静态战术层 (微操/采矿/防御...)    │
    │ ])                                                              │
    └──────────────────────────────────────────────────────────────────┘

* **静态战术层** ``TerranBaseTactics`` 来自 ``SKILL/terran/marine_rush/base_tactics.py``，
  纯粹负责"怎么打、怎么采矿"；不被 LLM 阻塞，每帧自动执行。
* **动态运营层** ``ActLLMOngoingTasks`` 持有对 ``LLMBot.active_tasks`` 的引用。这里的
  ``active_tasks`` 是按生成顺序保存的动作历史；每帧迭代历史动作，按需 ``lazy``
  实例化对应的 Sharpy ``Act``、调用 ``start``、再 ``execute()``，让 Sharpy 自然地
  分配矿/气/SCV。动作不会再根据完成条件出队。
* **LLM 触发**：``pre_step_execute`` 中按时间轮询（每 ``LLM_POLL_INTERVAL_SECONDS``
  游戏秒）同步调用 LLM。

  触发后 **同步** 调用 ``call_openai``，即 **LLM 推理期间 SC2 环境被阻塞**——
  python-sc2 在 ``real_time=False`` 下，只要 ``on_step`` 协程不让出，游戏帧就
  不会推进；这正是我们想要的"等 LLM 决定后再走下一步"语义，避免决策与
  状态错位（例如基于 5 秒前观测做出现在已不合理的决策）。

  对应地，本实现 **不需要** ``_llm_in_flight`` 之类的并发互斥——同步调用
  天然不会重入。代价是 ``real_time=True`` 下游戏不会暂停，bot 会在 LLM 调用
  期间错过若干步；这种情况建议线下/录制模式使用本 Bot。

* **双阶段 LLM Pipeline**：
    - Stage 1（reasoning profile）：根据观测直接给出 ``新增任务：…`` 或 ``none``；
      厂商「思考」开关由当前选用的 LLM 配置文件里对应 profile 的 ``is_reasoning`` 决定
      （默认 ``API_config/llm_settings.json``；可用 ``LLMBot.LLM_SETTINGS_FILE`` 或
      构造函数 / ladder 第二参数改为 ``llm_settings2.json`` 等）。
    - Stage 2（translation, ``is_reasoning=False``）：把 Stage 1 描述映射到严格 JSON
      ``{"action": <key>, "to_count": <int>}``。

  Stage 2 输出反序列化后 append 到动作历史，下一帧由动态运营层接管。每次 LLM
  响应会与响应时刻的动作历史一起写入 ``games/*.json``。
"""

from __future__ import annotations

import json
import logging
import re
import time as _wall_time
from typing import Any, Dict, List, Optional

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId

from sharpy.interfaces import IZoneManager
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, Step
from sharpy.plans.acts import ActBase
from sharpy.plans.acts.terran import MorphOrbitals
from sharpy.plans.require import UnitReady

from API_Tools.llm_caller import call_openai, load_llm_settings
from SKILL.terran.Action import (
    get_action,
    get_action_space,
)
from SKILL.terran.two_base_tanks.base_tactics import TwoBaseTanksTactics

logger = logging.getLogger("LLMBot")

# 从 Stage 1 自然语言里抽取"新增任务"行的正则。允许中英文/全角半角冒号 + 任意空白前缀。
_NEW_TASK_RE = re.compile(
    r"(?:新增任务|new\s*task)\s*[:：]\s*(.+)",
    re.IGNORECASE,
)

# 只把最近的动作历史塞进 LLM prompt，避免上下文越来越长；执行层仍保留全量 active_tasks。
LLM_ACTION_HISTORY_PROMPT_LIMIT = 15


# ======================================================================
# 动态运营层执行器
# ======================================================================


class ActLLMOngoingTasks(ActBase):
    """每帧扫一遍动作历史，把每个目标态翻译成具体 Sharpy 调用。

    每个动作的状态机：

        待初始化（无 ``_act``） ──► 已初始化未启动（有 ``_act``，``_started=False``）
            ──► 启动完成后每帧继续 ``execute()``

    设计与硬编码 ``BuildOrder`` 的差异：

    * 动作历史是 **运行时可变** 的：LLM 可在游戏中途随时 ``append``。
    * 每个动作独立执行，不串联阻塞——一个 ``train_marine`` 动作卡住不会
      影响 ``build_supply_depot`` 动作。
    * 动作不会因为外部完成条件被删除；Sharpy Act 自己决定当前帧是否需要继续下指令。
    """

    def __init__(
        self,
        active_tasks_ref: List[Dict[str, Any]],
    ):
        super().__init__()
        # 这里保存的是对外部 list 的引用，故 LLM 主线程 append 会被本 Act 看到。
        self.active_tasks = active_tasks_ref

    async def execute(self) -> bool:
        for task in self.active_tasks:
            if task.get("_disabled", False):
                continue

            action_key = task.get("action")
            try:
                to_count = int(task.get("to_count", 1))
            except (TypeError, ValueError):
                # 历史动作不删除；标记为 disabled，避免后续每帧重复刷 warning。
                logger.warning("Invalid to_count in action history item %s; disabling.", task)
                task["_disabled"] = True
                task["_error"] = "invalid_to_count"
                continue

            if not action_key:
                task["_disabled"] = True
                task["_error"] = "missing_action"
                continue

            # lazy 实例化对应 Sharpy Act。
            act: Optional[ActBase] = task.get("_act")
            if act is None:
                try:
                    act = get_action(action_key, to_count)
                except Exception as exc:
                    logger.warning(
                        "Failed to instantiate act for action=%s to_count=%s: %s",
                        action_key,
                        to_count,
                        exc,
                    )
                    task["_disabled"] = True
                    task["_error"] = f"instantiate_failed: {exc}"
                    continue
                task["_act"] = act
                task["_started"] = False

            # lazy 启动（一次性）。
            if not task.get("_started", False):
                try:
                    await self.start_component(act, self.knowledge)
                except Exception as exc:
                    logger.warning(
                        "Failed to start act for action=%s: %s",
                        action_key,
                        exc,
                    )
                    task["_disabled"] = True
                    task["_error"] = f"start_failed: {exc}"
                    continue
                task["_started"] = True

            # 每帧调用 execute()，让 Sharpy 自己处理资源/builders/排队等。
            try:
                await act.execute()
            except Exception as exc:
                logger.warning(
                    "Act execute failed for action=%s: %s", action_key, exc
                )
                # 单次 execute 失败不立即剔除：可能只是临时缺资源/缺 builders。

        # 始终返回 True 让 BuildOrder 继续走后续 act（如静态战术层）。
        return True


# ======================================================================
# LLMBot 主类
# ======================================================================


class LLMBot(KnowledgeBot):
    """LLM 驱动的人族 Bot.

    主要状态：

    * ``self.active_tasks``：按生成顺序保存的动作历史；``ActLLMOngoingTasks`` 与 LLM
      流水线共享。
    * ``self._last_llm_time``：上一次"触发 LLM 调用"的游戏时间戳。

    注意：LLM 推理是 **同步阻塞** 的——``pre_step_execute`` 在调用 ``call_openai``
    时不让出协程控制权，python-sc2 在非实时模式下不会推进游戏帧，从而实现
    "等 LLM 决定后再走下一步"的强一致性语义。
    """

    LLM_POLL_INTERVAL_SECONDS: float = 5.0
    """两次 LLM 轮询之间的最小游戏时间间隔。"""

    LLM_SETTINGS_FILE: Optional[str] = None
    """选用哪份 LLM 配置 JSON。

    * ``None``：使用默认 ``API_config/llm_settings.json``。
    * 仅文件名（无 ``/``）：解析为 ``API_config/<文件名>``，例如 ``llm_settings2.json``。
    * 含路径：相对仓库根，例如 ``API_config/custom.json``。

    ladder / ``__init__`` 第二参数 ``llm_settings_file`` 非空时会覆盖本类属性。
    """

    zone_manager: IZoneManager

    def __init__(self, build_name: str = "default", llm_settings_file: str = ""):
        super().__init__("LLM Bot")
        self.build_name = build_name
        explicit = (llm_settings_file or "").strip()
        if explicit:
            self._llm_settings_path: Optional[str] = explicit
        elif (type(self).LLM_SETTINGS_FILE or "").strip():
            self._llm_settings_path = str(type(self).LLM_SETTINGS_FILE).strip()
        else:
            self._llm_settings_path = None

        # active_tasks 中每个 dict 形态；名称保留以兼容执行器，但语义是动作历史：
        #   {
        #       "action": str,              # SKILL/terran/Action.py 的合法 key
        #       "to_count": int,            # 目标数量
        #       "_act": ActBase | None,     # 运行时延迟实例化
        #       "_started": bool,           # 是否已 start_component
        #   }
        self.active_tasks: List[Dict[str, Any]] = []

        # 触发节流：初始化为 -interval 让游戏一开局即可触发首次 LLM 调用。
        self._last_llm_time: float = -self.LLM_POLL_INTERVAL_SECONDS

        # action_space 是一份 description 字典，缓存以避免每次重新构造。
        self._action_space_cache: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_start(self):
        await super().on_start()
        self.zone_manager = self.knowledge.get_required_manager(IZoneManager)
        self._action_space_cache = get_action_space()

        # 给一个最小 bootstrap，保证 LLM 还没返回的"冷启动"窗口里 Bot 不会原地不动：
        #   先把工人补满到 16；再确保第一个补给站不缺。LLM 介入后会接管。
        if not self.active_tasks:
            # self.active_tasks.append({"action": "train_scv", "to_count": 16})
            self._append_action_to_history({"action": "build_supply_depot", "to_count": 1})

    async def pre_step_execute(self):
        """每帧 Tick 入口，由 ``CustomFuncManager`` 触发。

        触发条件命中后 **同步** 调用 LLM 流水线（不让出协程控制权），从而在
        非实时模式下让 SC2 游戏帧暂停等待 LLM 推理完成。这保证下达的指令是
        基于最新观测的，避免决策与状态错位。
        """
        trigger_reason: Optional[str] = None

        # 频率触发：达到了轮询窗口。
        if self.time - self._last_llm_time >= self.LLM_POLL_INTERVAL_SECONDS:
            trigger_reason = "poll"

        if trigger_reason is None:
            return

        # 记录触发时间用于下一次节流；放在调用 **之前** 是为了即便调用本身耗时
        # 长（例如 20 秒），下一次轮询窗口也按"开始时刻"算，不会立刻再触发。
        self._last_llm_time = self.time

        # 同步执行；此期间事件循环被卡住，SC2 帧也随之挂起，符合"阻塞 SC2 环境"的诉求。
        self._run_llm_pipeline_blocking(trigger_reason=trigger_reason)

    # ------------------------------------------------------------------
    # LLM 双阶段流水线
    # ------------------------------------------------------------------

    def _run_llm_pipeline_blocking(self, *, trigger_reason: str = "unknown") -> None:
        """同步执行 Stage1→Stage2 双阶段 LLM 调用，**阻塞** SC2 环境。

        本方法刻意写成同步函数：``call_openai`` 是 OpenAI SDK 的同步入口，整
        个调用期间事件循环被占住，python-sc2 在非实时模式下也不会推进游戏帧。
        这是 "等 LLM 决定后再走下一步" 的语义体现。

        失败兜底：任何阶段异常都吞掉并降级为 "本轮不新增任务"，绝不能因为
        LLM 接口抖动而炸掉游戏循环。

        :param trigger_reason: 触发来源，便于在控制台与 ``games/*.log`` 中分辨本次推理。
        """
        game_time = self.time
        pipeline_start = _wall_time.monotonic()
        obs_text: str = ""
        action_history_summary: str = ""
        stage1_text: str = ""
        stage2_text: str = ""
        new_task_desc: Optional[str] = None
        parsed_task: Optional[Dict[str, Any]] = None
        appended_task: Optional[Dict[str, Any]] = None
        error_text: Optional[str] = None

        self._llm_infer_emit(
            f">>> START (trigger={trigger_reason}, game_time={game_time:.1f}s, "
            f"action_history_count={len(self.active_tasks)})",
            include_action_history=True,
        )

        try:
            obs_text = self._capture_observation_text()
            action_history_summary = self._summarise_action_history()

            # ===================== Stage 1: Reasoning =====================
            _s1_prof = "stage1_reasoning"
            _s1_reasoning = (
                load_llm_settings(settings_path=self._llm_settings_path)
                .get("profiles") or {}
            ).get(_s1_prof, {}).get("is_reasoning", False)
            self._llm_infer_emit(
                f"    calling Stage1 (profile={_s1_prof}, is_reasoning={_s1_reasoning})..."
            )
            s1_start = _wall_time.monotonic()
            stage1_text = self._call_stage1(obs_text, action_history_summary)
            s1_elapsed = _wall_time.monotonic() - s1_start
            if not stage1_text:
                self._llm_infer_emit(
                    f"    Stage1 returned EMPTY ({s1_elapsed:.2f}s)."
                )
                logger.warning("[LLMBot] Stage1 returned empty content.")
                return
            self._llm_infer_emit(
                f"    Stage1 done in {s1_elapsed:.2f}s, {len(stage1_text)} chars."
            )
            self._llm_infer_emit(f"    Stage1 output: {stage1_text.strip()!r}")

            new_task_desc = self._extract_new_task_from_stage1(stage1_text)
            if new_task_desc is None:
                # 包含 `none` 或没有提取到新任务 —— 提前终止，不进入第二阶段。
                self._llm_infer_emit("    Stage1 verdict: `none` (no new task).")
                return
            self._llm_infer_emit(
                f"    Stage1 extracted task desc: {new_task_desc!r}"
            )

            # ===================== Stage 2: Translation ===================
            self._llm_infer_emit(
                "    calling Stage2 (translation, is_reasoning=False)..."
            )
            s2_start = _wall_time.monotonic()
            stage2_text = self._call_stage2(new_task_desc, obs_text, action_history_summary)
            s2_elapsed = _wall_time.monotonic() - s2_start
            if not stage2_text:
                self._llm_infer_emit(
                    f"    Stage2 returned EMPTY ({s2_elapsed:.2f}s)."
                )
                logger.warning("[LLMBot] Stage2 returned empty content.")
                return
            self._llm_infer_emit(
                f"    Stage2 done in {s2_elapsed:.2f}s, raw={stage2_text.strip()!r}"
            )

            parsed_task = self._parse_stage2_json(stage2_text)
            if parsed_task is None:
                self._llm_infer_emit(
                    "    Stage2 output failed validation; no task appended."
                )
                logger.warning(
                    "[LLMBot] Stage2 output is not valid action JSON: %r",
                    stage2_text,
                )
                return

            # 合法非法都已过滤。直接入队。
            appended_task = self._append_action_to_history(parsed_task)
            self._llm_infer_emit(
                "    APPENDED action: "
                f"action={appended_task.get('action')} to_count={appended_task.get('to_count')}"
            )
        except Exception as exc:
            # 任何异常都吞掉——LLM 失败绝不能炸掉游戏循环。
            error_text = repr(exc)
            self._llm_infer_emit(f"    EXCEPTION: {exc!r}")
            logger.warning("[LLMBot] LLM pipeline failed: %s", exc)
        finally:
            total_elapsed = _wall_time.monotonic() - pipeline_start
            self._record_llm_interaction(
                {
                    "game_time_seconds": round(game_time, 2),
                    "game_time_formatted": getattr(self, "time_formatted", ""),
                    "trigger_reason": trigger_reason,
                    "wall_elapsed_seconds": round(total_elapsed, 3),
                    "stage1_output": stage1_text,
                    "extracted_task_description": new_task_desc,
                    "stage2_output": stage2_text,
                    "parsed_action": self._slim_action(parsed_task) if parsed_task else None,
                    "appended_action": self._slim_action(appended_task) if appended_task else None,
                    "error": error_text,
                    "action_history_at_response": self._serialise_action_history(),
                }
            )
            self._llm_infer_emit(
                f"<<< END (total {total_elapsed:.2f}s wall, "
                f"action_history_count={len(self.active_tasks)})"
            )

    # --- 观测注入 ------------------------------------------------------

    def _capture_observation_text(self) -> str:
        """从 ``LLMObservationRecorder`` 即时构造一份英文文本观测。

        Recorder 自身是周期性记录（默认 20 秒一次），但其 ``_build_snapshot`` 与
        ``_generate_english_text_obs`` 都是无副作用的纯函数，我们这里直接复用，
        避免另写一套数据提取逻辑。
        """
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return "(LLMObservationRecorder unavailable)"
        try:
            snapshot = recorder._build_snapshot()
            return recorder._generate_english_text_obs(snapshot)
        except Exception as exc:
            logger.warning("[LLMBot] failed to build observation: %s", exc)
            return "(observation unavailable)"

    def _append_action_to_history(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """把 LLM 动作按生成顺序放进历史，并返回实际入队对象。"""
        item = {
            "sequence": len(self.active_tasks) + 1,
            "action": task.get("action"),
            "to_count": task.get("to_count"),
        }
        self.active_tasks.append(item)
        return item

    @staticmethod
    def _slim_action(task: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if task is None:
            return None
        slim = {"action": task.get("action"), "to_count": task.get("to_count")}
        if "sequence" in task:
            slim["sequence"] = task.get("sequence")
        return slim

    def _serialise_action_history(self) -> List[Dict[str, Any]]:
        """返回可写入 prompt/log/json 的动作历史，不包含运行时 Act 对象。"""
        history: List[Dict[str, Any]] = []
        for idx, task in enumerate(self.active_tasks, start=1):
            item: Dict[str, Any] = {
                "sequence": task.get("sequence", idx),
                "action": task.get("action"),
                "to_count": task.get("to_count"),
            }
            if task.get("_disabled", False):
                item["disabled"] = True
                if task.get("_error"):
                    item["error"] = task.get("_error")
            history.append(item)
        return history

    def _serialise_recent_action_history(
        self, limit: int = LLM_ACTION_HISTORY_PROMPT_LIMIT
    ) -> List[Dict[str, Any]]:
        """返回给 LLM prompt 使用的最近动作历史；执行层仍使用完整 active_tasks。"""
        history = self._serialise_action_history()
        if limit <= 0:
            return history
        return history[-limit:]

    def _summarise_action_history(
        self, limit: int = LLM_ACTION_HISTORY_PROMPT_LIMIT
    ) -> str:
        """把最近动作历史按生成顺序渲染成 LLM 易消化的多行文本摘要。"""
        history = self._serialise_recent_action_history(limit)
        if not history:
            return "(empty)"
        lines: List[str] = []
        omitted_count = len(self.active_tasks) - len(history)
        if omitted_count > 0:
            lines.append(f"(older {omitted_count} issued actions omitted)")
        for item in history:
            lines.append(
                f"- #{item.get('sequence')}: action={item.get('action')!r}, "
                f"to_count={item.get('to_count')}"
            )
        return "\n".join(lines)

    def _action_history_snapshot(self) -> str:
        """当前动作历史的 JSON，便于控制台与文件日志同一格式。"""
        history = self._serialise_action_history()
        if not history:
            return "[]"
        return json.dumps(history, ensure_ascii=False)

    def _record_llm_interaction(self, record: Dict[str, Any]) -> None:
        """把每次 LLM 响应与响应时刻动作历史挂到 observation recorder。"""
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return
        append_func = getattr(recorder, "record_llm_interaction", None)
        if append_func is None:
            return
        try:
            append_func(record)
        except Exception as exc:
            logger.warning("[LLMBot] failed to record LLM interaction: %s", exc)

    def _llm_infer_emit(self, message: str, *, include_action_history: bool = False) -> None:
        """LLM 流水线进度：写入 logger（进入 games/*.log）。"""
        line = f"[LLMBot][LLM-INFER] {message}"
        if include_action_history:
            line += f" | action_history={self._action_history_snapshot()}"
        try:
            self.knowledge.print(line, stats=False)
        except Exception:
            logger.info("%s", line)

    # --- Stage 1: reasoning -------------------------------------------

    def _call_stage1(self, obs_text: str, action_history_summary: str) -> str:
        """构造 Prompt 并调用 reasoning profile。

        Prompt 设计要点：

        * 强制模型只输出 ``新增任务：xxx`` 或 ``none``，方便正则抽取并缩短响应。
        * 任务描述用 **自然语言** 而非动作 key —— Stage 2 才负责映射，这样减少
          Stage 1 对动作空间细节的依赖，让模型更专注于战略推理。
        """
#         system_msg = """You are a senior StarCraft II strategist controlling a Terran bot.
# Given the current game observation and the recent ordered history of build-order actions already issued, decide whether to issue ONE additional action.

# Your overall strategy is a Marine and Siege Tank macro build:

# Build a Supply Depot at 13 supply.
# When the first Supply Depot is almost ready, build a Barracks, followed by your first gas Refinery at 16 supply.
# Immediately expand to a second base and build a second Supply Depot.
# Once the Barracks is finished, morph your Command Centers into Orbital Commands and build a Factory.
# Attach a Tech Lab to the Factory as soon as it finishes to start producing Siege Tanks.
# Train a couple of initial Marines for defense, then grab your second gas Refinery.
# Expand to a third and fourth base when it is safe to do so, constantly building Supply Depots to avoid supply blocks.
# Research the Shield Wall (Combat Shield) upgrade to strengthen your infantry.
# Scale up your production heavily by building up to 3 Factories (all with Tech Labs) and 5 Barracks (with a mix of Reactors and a Tech Lab).
# Continually train units non-stop from these structures until you reach a cap of 20 Siege Tanks and 100 Marines, supported by a strong economy of up to 44 SCVs.
# Execute this strategy dynamically based on the current state.

# Respond in natural language with your concise reasoning.
# Your reply MUST end with EXACTLY one of the following final lines:
#   New Task: <a concise natural-language description of one new task>
#   none

# Output `none` if the situation is stable, the action history already covers what is needed, or any new task would be premature.
# Examples of valid final lines:
#   New Task: Train more marines, target 20
#   New Task: Build a second barracks
#   none"""

#         system_msg = """You are a senior StarCraft II strategist controlling a Terran bot.
# Given the current game observation and the recent ordered history of build-order actions already issued, decide whether to issue ONE additional action.

# Your overall strategy is a Marine and Siege Tank macro build:

# Opening: Build a Barracks, followed by your first gas Refinery. Immediately expand to a second base.
# Tech & Early Factory: Once the Barracks is finished, morph your Command Centers into Orbital Commands and build a Factory.
# Tank Priority (Rule Update): As soon as the Factory is finished, attach a Tech Lab. Prioritize Siege Tank production above all else. Aggressively scale up to 3 Factories (all with Tech Labs) as early as possible to accelerate Tank development.
# Marine Buffer (Rule Update): Synchronously train Marines to maintain a solid defensive buffer. Grab your second gas Refinery.
# Expansion: Expand to a third and fourth base when it is safe to do so.
# Mid-Game Production: Research the Combat Shield upgrade to strengthen your infantry. Scale your infantry production by building up to 5 Barracks (with a mix of Reactors and a Tech Lab).
# Production Shift (Rule Update): Once you have accumulated a sufficient number of Marines to act as a safe buffer, shift your primary focus and resources entirely toward mass-producing Siege Tanks.
# End Goal: Continually train units non-stop until you reach a cap of 20 Siege Tanks and 100 Marines, supported by a strong economy of up to 44 SCVs.
# Execution: Execute this strategy dynamically based on the current state. (Note: All supply depot construction is auto-managed and can be ignored).

# Respond in natural language with your concise reasoning.
# Your reply MUST end with EXACTLY one of the following final lines:
#   New Task: <a concise natural-language description of one new task>
#   none

# Output `none` if the situation is stable, the action history already covers what is needed, or any new task would be premature.
# Examples of valid final lines:
#   New Task: Train more marines, target 20
#   New Task: Build a second barracks
#   none"""

        system_msg = """You are a senior StarCraft II strategist controlling a Terran bot. This is a planning task: your objective is to set strategic goals and actions for the near future.
Given the current game observation and the recent ordered history of build-order actions already issued, decide whether to issue ONE additional action.

Your overall strategy is a Marine and Siege Tank macro build, with a strict emphasis on the developmental sequence between Barracks/Factories and Marines/Tanks:

*   **Opening & Tech:** Build a Barracks, followed by your first gas Refinery. Immediately expand to a second base. Once the Barracks finishes, morph Command Centers to Orbital Commands and start your first Factory.
*   **Early Marine Buffer & Setup:** Train a couple of initial Marines for defense. Grab your second gas Refinery. Begin scaling your infantry infrastructure up to 5 Barracks (utilizing a mix of 1 Tech Lab to research Combat Shield, and Reactors for the rest). Rapidly pump out a solid number of Marines early on to serve as a defensive buffer.
*   **Simultaneous Factory & Tank Push:** While building the initial Marine buffer, aggressively push Factory development. As soon as the first Factory finishes, attach a Tech Lab and start Siege Tanks. Prioritize scaling up to 3 Factories (all with Tech Labs) as early as possible.
*   **Production Shift:** Once you have accumulated enough Marines to secure your defense, shift your primary focus and resources toward mass-producing Siege Tanks, while maintaining steady Marine reinforcement.
*   **Expansion:** Expand to a third and fourth base dynamically when it is safe to do so.
*   **End Goal:** Continually train units non-stop until you reach a cap of 20 Siege Tanks and 100 Marines, supported by a strong economy of up to 44 SCVs.
*   **Execution:** Execute this strategy dynamically based on the current state. (Note: All supply depot construction and supply management are fully automated and MUST be ignored).

Respond in natural language with your concise reasoning.
Your reply MUST end with EXACTLY one of the following final lines:
  New Task: <a concise natural-language description of one new task>
  none

Output `none` if the situation is stable, the action history already covers what is needed, or any new task would be premature.
Examples of valid final lines:
  New Task: Train more marines, target 20
  New Task: Build a second factory
  none"""

        user_msg = f"""[Current Observation]
{obs_text}

[Action History]
{action_history_summary}"""
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        # 不传 is_reasoning：沿用当前 settings 文件里 stage1_reasoning 的 is_reasoning。
        return call_openai(
            messages,
            profile="stage1_reasoning",
            settings_path=self._llm_settings_path,
        )

    @staticmethod
    def _extract_new_task_from_stage1(text: str) -> Optional[str]:
        """从 Stage 1 输出中抽取"新增任务"自然语言描述。

        约定：模型按 prompt 指令输出，最后一行非空内容应为 ``新增任务：…`` 或 ``none``。
        为了对模型不完全守规则做兜底：
        * 倒序扫描行；
        * 行首是 ``none``（或单独一行 ``none``）就视为无新任务；
        * 否则匹配 ``新增任务[:：]`` 取冒号后文本；
        * 全部失败也回退到 ``None``（视作 ``none``，避免误生成）。
        """
        if not text:
            return None
        cleaned = text.strip()
        if not cleaned:
            return None
        # 整体清洗后等于 `none`（区分大小写无关）：
        if cleaned.lower() == "none":
            return None

        # 倒序扫描每一行，找到首条匹配项。
        for raw_line in reversed(cleaned.splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            if line.lower() == "none":
                return None
            match = _NEW_TASK_RE.search(line)
            if match:
                desc = match.group(1).strip()
                # 兜底：模型偶尔会把句号/中文标点带进来，原样返回交给 Stage 2 解决即可。
                return desc or None

        # 没匹配到任何"新增任务/none"——保守起见视为无新任务，避免乱来。
        return None

    # --- Stage 2: translation -----------------------------------------

    def _call_stage2(
        self, task_description: str, obs_text: str, action_history_summary: str
    ) -> str:
        """构造 Stage 2 Prompt，强制模型只输出严格 JSON。

        关键点：

        * ``is_reasoning=False`` 让 :mod:`API_Tools.llm_caller` 对应厂商关闭思考字段。
        * profile=``stage2_translation`` 同时附带 ``response_format=json_object``
          （在当前选用的 LLM settings JSON 中配置），让支持 JSON Mode 的厂商把输出钳进 JSON。
        * Prompt 中明确列出合法 action key + 描述，并附上当前 obs / action history 作为上下文。
        """
        action_space_lines = [
            f'  - "{key}": {desc}'
            for key, desc in (self._action_space_cache or {}).items()
        ]
        action_space_text = "\n".join(action_space_lines) or "  (empty)"

        system_msg = f"""You translate ONE Terran build-order task description into a single JSON object that strictly matches the action space below.
Output ONLY the JSON object, no prose, no markdown fences.

Schema:
  {{"action": "<action_key>", "to_count": <positive integer>}}

Constraints:
  * <action_key> MUST be one of the legal keys listed below.
  * <to_count> is the ABSOLUTE target count on the field (including under-construction).
  * Use [Action History] to avoid contradicting or pointlessly duplicating actions already issued.

[Legal Action Space]
{action_space_text}"""

        user_msg = f"""[Task Description]
{task_description}

[Current Observation]
{obs_text}

[Action History]
{action_history_summary}"""
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        return call_openai(
            messages,
            profile="stage2_translation",
            is_reasoning=False,
            settings_path=self._llm_settings_path,
        )

    @staticmethod
    def _parse_stage2_json(text: str) -> Optional[Dict[str, Any]]:
        """从 Stage 2 输出里解析 ``{"action": ..., "to_count": ...}``。

        对模型可能的"小毛病"做兜底：
        * 外层带 ``json`` 代码块包裹；
        * 多余的前后说明文字；
        * to_count 类型为字符串。
        """
        if not text:
            return None
        cleaned = text.strip()

        # 去掉 ```json ... ``` / ``` ... ``` 包裹。
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

        # 直接 json.loads；失败则尝试在字符串里抓第一个 {...}。
        parsed: Optional[Dict[str, Any]] = None
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                parsed = data
        except Exception:
            parsed = None

        if parsed is None:
            brace_match = re.search(r"\{[\s\S]*?\}", cleaned)
            if not brace_match:
                return None
            try:
                data = json.loads(brace_match.group(0))
            except Exception:
                return None
            if not isinstance(data, dict):
                return None
            parsed = data

        action = parsed.get("action")
        to_count_raw = parsed.get("to_count")
        if not isinstance(action, str) or not action:
            return None
        try:
            to_count = int(to_count_raw)
        except (TypeError, ValueError):
            return None
        if to_count <= 0:
            return None

        # 校验 action 是否在合法动作空间内（兜底，防止 Stage 2 编造 key）。
        legal_keys = set(get_action_space().keys())
        if action not in legal_keys:
            logger.warning(
                "[LLMBot] Stage2 produced illegal action %r; dropping.", action
            )
            return None

        return {"action": action, "to_count": to_count}

    # ------------------------------------------------------------------
    # Sharpy plan 入口
    # ------------------------------------------------------------------

    async def create_plan(self) -> BuildOrder:
        """组装 BuildOrder：补给 + Orbital + LLM 动态运营 + 静态战术层。"""
        # 静态战术层：纯战术行为 (微操、采矿、防御、扫描等)
        # 这里 num_marines 只影响 TerranBaseTactics 内 DodgeRampAttack 的攻击阈值；
        # LLM 自己也可以再下 `train_marine` 任务把数量推得更高。
        # base_tactics = TerranBaseTactics(num_marines=20)
        base_tactics = TwoBaseTanksTactics()

        # 动态运营层：LLM 驱动的并行任务执行器
        llm_executor = ActLLMOngoingTasks(
            active_tasks_ref=self.active_tasks,
        )

        empty = BuildOrder([])

        return BuildOrder(
            # 补给链：经典自适应链，避免 supply block；不归 LLM 管。
            empty.depots,
            # 一旦造出兵营即升级为 Orbital，开 Mule。
            Step(
                None,
                MorphOrbitals(),
                skip_until=UnitReady(UnitTypeId.BARRACKS, 1),
            ),
            # ★ 动态运营层：LLM 决定造什么、造多少。
            llm_executor,
            # 静态战术层：决定怎么打、怎么采矿；与 LLM 完全解耦。
            base_tactics,
        )


# ----------------------------------------------------------------------
# 备用 LLM 配置入口
# ----------------------------------------------------------------------


class MyLLMBot(LLMBot):
    """使用 ``API_config/llm_settings2.json`` 的 LLM Bot（与 ``LLMBot`` 逻辑相同，仅静态配置不同）。"""

    LLM_SETTINGS_FILE = "llm_settings2.json"


# ----------------------------------------------------------------------
# Ladder 兼容入口（与 dummies/terran/test_bot.py 风格一致）
# ----------------------------------------------------------------------


class LadderBot(LLMBot):
    @property
    def my_race(self):
        return Race.Terran
