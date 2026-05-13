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
from typing import Any, Dict, List, Optional, Tuple

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
from SKILL.terran.battle_cruisers.base_tactics import BattleCruisersTactics

logger = logging.getLogger("LLMBot")

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

    LLM_POLL_INTERVAL_SECONDS: float = 12.0
    """两次 LLM 轮询之间的最小游戏时间间隔。"""

    LLM_SETTINGS_FILE: Optional[str] = None
    """选用哪份 LLM 配置 JSON。

    * ``None``：使用默认 ``API_config/llm_settings.json``。
    * 仅文件名（无 ``/``）：解析为 ``API_config/<文件名>``，例如 ``llm_settings2.json``。
    * 含路径：相对仓库根，例如 ``API_config/custom.json``。

    ladder / ``__init__`` 第二参数 ``llm_settings_file`` 非空时会覆盖本类属性。
    """

    zone_manager: IZoneManager

    def __init__(
        self,
        build_name: str = "default",
        llm_settings_file: str = "",
        record_dir: str = "",
    ):
        super().__init__("LLM Bot")
        self.build_name = build_name
        self.record_dir = record_dir.strip()
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
        self.current_natural_tasks: List[str] = []

        # 触发节流：初始化为 -interval 让游戏一开局即可触发首次 LLM 调用。
        self._last_llm_time: float = -self.LLM_POLL_INTERVAL_SECONDS

        # action_space 是一份 description 字典，缓存以避免每次重新构造。
        self._action_space_cache: Optional[Dict[str, str]] = None

        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_start(self):
        await super().on_start()
        self.zone_manager = self.knowledge.get_required_manager(IZoneManager)
        self._action_space_cache = get_action_space()
        self.llm_observation_recorder.interval_seconds = self.LLM_POLL_INTERVAL_SECONDS
        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

        # 首轮 LLM 会在 t=0 附近触发；补给链由 BuildOrder 的 empty.depots 自动管理。

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
        """同步执行 Stage1→Stage2 双阶段 LLM 调用，并全量刷新当前任务队列。"""
        game_time = self.time
        pipeline_start = _wall_time.monotonic()
        obs_text: str = ""
        obs_snapshot: Optional[Dict[str, Any]] = None
        previous_natural_tasks = list(self.current_natural_tasks)
        stage1_text: str = ""
        stage1_tasks: List[str] = []
        stage2_translations: List[Dict[str, Any]] = []
        parsed_tasks: List[Dict[str, Any]] = []
        error_text: Optional[str] = None

        self._llm_infer_emit(
            f">>> START (trigger={trigger_reason}, game_time={game_time:.1f}s, "
            f"active_task_count={len(self.active_tasks)})",
            include_active_tasks=True,
        )

        try:
            obs_text, obs_snapshot = self._capture_observation_bundle()
            self._llm_infer_emit(
                f"observation_at_decision_time (game_time={game_time:.1f}s):\n{obs_text}"
            )

            # ===================== Stage 1: Planning ======================
            _s1_prof = "stage1_reasoning"
            _s1_reasoning = (
                load_llm_settings(settings_path=self._llm_settings_path)
                .get("profiles") or {}
            ).get(_s1_prof, {}).get("is_reasoning", False)
            self._llm_infer_emit(
                f"    calling Stage1 (profile={_s1_prof}, is_reasoning={_s1_reasoning})..."
            )
            s1_start = _wall_time.monotonic()
            stage1_text = self._call_stage1(obs_text, previous_natural_tasks)
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

            parsed_stage1_tasks = self._parse_stage1_tasks_json(stage1_text)
            if parsed_stage1_tasks is None:
                error_text = "invalid_stage1_json"
                self._llm_infer_emit("    Stage1 output failed JSON validation; keeping current active_tasks.")
                return
            stage1_tasks = parsed_stage1_tasks
            self._llm_infer_emit(
                f"    Stage1 parsed {len(stage1_tasks)} natural-language tasks."
            )

            # ===================== Stage 2: Translation ===================
            for index, natural_task in enumerate(stage1_tasks, start=1):
                self._llm_infer_emit(
                    f"    calling Stage2 #{index}/{len(stage1_tasks)} for task={natural_task!r}..."
                )
                translation_record: Dict[str, Any] = {
                    "raw": natural_task,
                    "response": "",
                    "parsed": None,
                }
                s2_start = _wall_time.monotonic()
                try:
                    stage2_text = self._call_stage2(natural_task, obs_text)
                    translation_record["response"] = stage2_text
                    s2_elapsed = _wall_time.monotonic() - s2_start
                    if not stage2_text:
                        translation_record["error"] = "empty_response"
                        self._llm_infer_emit(
                            f"    Stage2 #{index} returned EMPTY ({s2_elapsed:.2f}s); dropping."
                        )
                        continue

                    parsed_task = self._parse_stage2_json(stage2_text)
                    if parsed_task is None:
                        translation_record["error"] = "invalid_json_or_action"
                        self._llm_infer_emit(
                            f"    Stage2 #{index} failed validation; dropping."
                        )
                        logger.warning(
                            "[LLMBot] Stage2 output is not valid action JSON: %r",
                            stage2_text,
                        )
                        continue

                    translation_record["parsed"] = self._slim_action(parsed_task)
                    parsed_tasks.append(parsed_task)
                    self._llm_infer_emit(
                        f"    Stage2 #{index} accepted in {s2_elapsed:.2f}s: "
                        f"{translation_record['parsed']}"
                    )
                except Exception as exc:
                    translation_record["error"] = repr(exc)
                    self._llm_infer_emit(
                        f"    Stage2 #{index} EXCEPTION; dropping task: {exc!r}"
                    )
                    logger.warning("[LLMBot] Stage2 translation failed: %s", exc)
                finally:
                    stage2_translations.append(translation_record)

            self._replace_active_tasks(parsed_tasks)
            self.current_natural_tasks = list(stage1_tasks)
            self._llm_infer_emit(
                f"    REFRESHED active_tasks with {len(parsed_tasks)} parsed actions "
                f"from {len(stage1_tasks)} Stage1 tasks."
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
                    "game_time": round(game_time, 2),
                    "trigger_reason": trigger_reason,
                    "wall_elapsed_seconds": round(total_elapsed, 3),
                    "observation_at_this_moment": obs_text,
                    "observation_structured": obs_snapshot,
                    "stage1_input_previous_tasks": previous_natural_tasks,
                    "stage1_raw_response": stage1_text,
                    "stage1_output_new_tasks": stage1_tasks,
                    "stage2_translations": stage2_translations,
                    "active_tasks_after_refresh": self._serialise_active_tasks(),
                    "error": error_text,
                }
            )
            self._llm_infer_emit(
                f"<<< END (total {total_elapsed:.2f}s wall, "
                f"active_task_count={len(self.active_tasks)})"
            )

    # --- 观测注入 ------------------------------------------------------

    def _capture_observation_bundle(self) -> Tuple[str, Optional[Dict[str, Any]]]:
        """从 ``LLMObservationRecorder`` 即时构造 (英文文本观测, 结构化快照)。

        Recorder 自身是周期性记录（默认 20 秒一次），但其 ``_build_snapshot`` 与
        ``_generate_english_text_obs`` 都是无副作用的纯函数，我们这里直接复用，
        避免另写一套数据提取逻辑。双返回值保证 LLM 与日志共用同一次快照。
        """
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return "(LLMObservationRecorder unavailable)", None
        try:
            snapshot = recorder._build_snapshot()
            return recorder._generate_english_text_obs(snapshot), snapshot
        except Exception as exc:
            logger.warning("[LLMBot] failed to build observation: %s", exc)
            return "(observation unavailable)", None

    def _capture_observation_text(self) -> str:
        """仅文本观测；内部与 :meth:`_capture_observation_bundle` 共用构建逻辑。"""
        text, _ = self._capture_observation_bundle()
        return text

    def _replace_active_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        """原地全量刷新当前执行目标，保持执行器持有的 list 引用不变。"""
        self.active_tasks.clear()
        for idx, task in enumerate(tasks, start=1):
            self.active_tasks.append(
                {
                    "sequence": idx,
                    "action": task.get("action"),
                    "to_count": task.get("to_count"),
                }
            )

    @staticmethod
    def _slim_action(task: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if task is None:
            return None
        slim = {"action": task.get("action"), "to_count": task.get("to_count")}
        if "sequence" in task:
            slim["sequence"] = task.get("sequence")
        return slim

    def _serialise_active_tasks(self) -> List[Dict[str, Any]]:
        """返回可写入 log/json 的当前执行任务，不包含运行时 Act 对象。"""
        tasks: List[Dict[str, Any]] = []
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
            tasks.append(item)
        return tasks

    def _active_tasks_snapshot(self) -> str:
        """当前执行任务的 JSON，便于控制台与文件日志同一格式。"""
        tasks = self._serialise_active_tasks()
        if not tasks:
            return "[]"
        return json.dumps(tasks, ensure_ascii=False)

    def _record_llm_interaction(self, record: Dict[str, Any]) -> None:
        """把每次决策的完整交互对象挂到 observation recorder。"""
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

    def _llm_infer_emit(self, message: str, *, include_active_tasks: bool = False) -> None:
        """LLM 流水线进度：写入 logger（进入 games/*.log）。"""
        line = f"[LLMBot][LLM-INFER] {message}"
        if include_active_tasks:
            line += f" | active_tasks={self._active_tasks_snapshot()}"
        try:
            self.knowledge.print(line, stats=False)
        except Exception:
            logger.info("%s", line)

    # --- Stage 1: reasoning -------------------------------------------

    def _call_stage1(self, obs_text: str, previous_tasks: List[str]) -> str:
        """构造全局规划 Prompt，并要求 Stage 1 返回 reasoning + tasks JSON。"""
        previous_tasks_json = json.dumps(previous_tasks, ensure_ascii=False, indent=2)
        system_msg = """You are a senior StarCraft II strategist controlling a Terran bot.
This is a macro Planning Task. You are the global plan manager for the next 20 seconds.

Execution model:
* Your output list will be translated and executed concurrently by the lower layer.
* A blocked task does NOT block later tasks.
* The order of the list dictates absolute resource priority: earlier tasks claim minerals, gas, and workers first. Therefore, you MUST place urgent, important, and short-term tasks at the very front of the list. Less urgent, long-term goals must be placed at the back.
* Tasks that act as tech-tree bottlenecks (e.g., the opening Supply Depot or the first Barracks) MUST be prioritized at the absolute front. To guarantee their immediate execution, you can even issue them as a single isolated task for that cycle.
* The lower layer is declarative: if you ask for "Train Marines to 20", it will keep training until the absolute target count is reached.

Your job each cycle:
* Compare the current observation with the previous natural-language task list.
* Remove tasks that are already complete or no longer appropriate.
* Update tasks whose target count should increase or decrease.
* Add new tasks needed for the current stage.
* Describe each task clearly: Each natural-language task MUST contain only ONE single plan or action. Do not combine multiple actions in one sentence. While there is no limit on the total number of tasks, you must strictly maintain the priority order. Key bottleneck actions can still be issued as a single isolated task for that cycle to guarantee focus.

Your overall strategy is a Battlecruiser-focused macro build supported by Marines and Siege Tanks, with a strict emphasis on rapid Starport tech progression and balancing the developmental sequence between your ground forces and heavy air fleet:

* Opening & Tech: Start with a standard opening: a Supply Depot at 13 supply, followed immediately by a Barracks and your first gas Refinery. Expand to a second base early. Once the Barracks is ready, morph your Command Centers to Orbital Commands, take your second gas Refinery, and immediately start a Factory, followed directly by a Starport as soon as the Factory finishes.
* Early Defense & Detection Setup: Build a defensive Bunker at your natural entrance to survive early pressure. Once the Starport finishes, heavily prioritize building up to 2 Ravens to ensure detection against stealth threats (like Dark Templars or Banshees) and provide utility. Add a Factory Tech Lab and a second Barracks.
* Simultaneous Air Tech & Core Push: As soon as the first Starport finishes, immediately construct a Fusion Core and attach a Tech Lab to the Starport. Once the Fusion Core is ready, prioritize the non-stop production of Battlecruisers. Take your fourth gas Refinery as soon as your first Battlecruiser is in production or already exists.
* Production Shift & Upgrades: After your first Battlecruiser is out, scale up your infantry infrastructure to 3 Barracks, equipping them with a mix of a Tech Lab (to research Combat Shield) and a Reactor. Add a second Starport with a Tech Lab to double your Battlecruiser production. When you find yourself floating excess minerals (over 600), scale up to 5 Barracks.
* Expansion & Tactics: Expand to a third base once your mid-game production is secured. Tactically, use your Battlecruisers to execute Tactical Jumps into the back of the enemy's mineral lines for early harassment. Launch a massive, decisive zone attack when your overall army value reaches the 50-80 threshold.
* End Goal: Continually train units non-stop until you reach a late-game cap of 20 Battlecruisers, 10 Siege Tanks, and 50 Marines, supported by a strong economy of up to 46 SCVs.

Output format:
1. First write one concise reasoning paragraph outside JSON.
2. Then output one JSON object with this exact schema:
{"tasks":["natural-language task 1","natural-language task 2"]}

Do not output markdown, comments, or action keys. The JSON object itself must contain only the tasks field."""

        user_msg = f"""[Current Observation]
{obs_text}

[Previous Natural-Language Tasks]
{previous_tasks_json}"""
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        return call_openai(
            messages,
            profile="stage1_reasoning",
            settings_path=self._llm_settings_path,
        )

    @staticmethod
    def _parse_stage1_tasks_json(text: str) -> Optional[List[str]]:
        """解析 Stage 1 的 ``{"tasks": [...]}`` 输出；格式错误返回 ``None``。"""
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

        try:
            data = json.loads(cleaned)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if not match:
                logger.warning("[LLMBot] Stage1 output is not valid JSON: %r", text)
                return None
            try:
                data = json.loads(match.group(0))
            except Exception:
                logger.warning("[LLMBot] Stage1 output is not valid JSON: %r", text)
                return None

        if not isinstance(data, dict):
            return None
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list):
            return None
        return [task.strip() for task in raw_tasks if isinstance(task, str) and task.strip()]

    # --- Stage 2: translation -----------------------------------------

    def _call_stage2(self, task_description: str, obs_text: str) -> str:
        """构造 Stage 2 Prompt，强制模型只输出严格 JSON。

        关键点：

        * ``is_reasoning=False`` 让 :mod:`API_Tools.llm_caller` 对应厂商关闭思考字段。
        * profile=``stage2_translation`` 同时附带 ``response_format=json_object``
          （在当前选用的 LLM settings JSON 中配置），让支持 JSON Mode 的厂商把输出钳进 JSON。
        * Prompt 中明确列出合法 action key + 描述，并附上当前 obs 作为上下文。
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
  * Translate only the single task provided by the planner.

[Legal Action Space]
{action_space_text}"""

        user_msg = f"""[Task Description]
{task_description}

[Current Observation]
{obs_text}"""
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
        # base_tactics = TwoBaseTanksTactics()
        base_tactics = BattleCruisersTactics()

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
