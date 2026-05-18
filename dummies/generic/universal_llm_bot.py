"""Universal LLM Bot — 跨种族三层 Agent 架构 Bot.

架构总览::

    ┌─────────────────────────────────────────────────────────────────────┐
    │ Top Agent  (全局指挥官)                                              │
    │   t=0 → 选择 SKILL/{race}/ 策略                                    │
    │   每 60s → 评估阶段 (early/mid/late) + 焦点指导                     │
    ├─────────────────────────────────────────────────────────────────────┤
    │ Mid Agent  (运营执行官)                                              │
    │   每 N 秒 → 结合 obs + Top 上下文 → 自然语言宏观任务列表              │
    ├─────────────────────────────────────────────────────────────────────┤
    │ Down Agent (微操执行官)                                              │
    │   逐条翻译 → {"action": key, "to_count": int}                      │
    ├─────────────────────────────────────────────────────────────────────┤
    │ create_plan() → BuildOrder([                                        │
    │     empty.depots,                                                   │
    │     MorphOrbitals(),                                                │
    │     ActLLMOngoingTasks(...),     # 动态运营层 (Mid/Down Agent 驱动)  │
    │     DynamicBaseTactics(...),     # 动态加载的静态战术层               │
    │ ])                                                                  │
    └─────────────────────────────────────────────────────────────────────┘

关键变化（相较于旧 ``dummies/terran/llm_bot.py``）：

* Prompt 全部从 ``SC2_Agent/`` 三个模块导入，Bot 本身不再包含长文本 Prompt。
* 种族通过 ``race_name`` 参数传入，动作空间 / 策略 / 战术均按种族动态加载。
* 新增 Top Agent 层：t=0 策略选择 + 60 秒轮询阶段评估。
* 模型通过 ``model_key`` 从 ``config.json`` 池动态获取。
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import time as _wall_time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId

from sharpy.interfaces import IZoneManager
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, Step
from sharpy.plans.acts import ActBase
from sharpy.plans.acts.terran import MorphOrbitals
from sharpy.plans.require import UnitReady
from sharpy.plans.sequential_list import SequentialList

from API_Tools.llm_caller import call_openai
from SC2_Agent.top_agent import (
    CUSTOM_STRATEGY_NAME,
    build_initial_strategy_messages,
    build_phase_assessment_messages,
    build_view_followup_user_message,
    parse_initial_action,
    parse_phase_assessment,
    parse_top_agent_0_md,
)
from SC2_Agent.mid_agent import (
    build_planning_messages,
    parse_planning_response,
)
from SC2_Agent.down_agent import (
    build_translation_messages,
    parse_translation_response,
)

logger = logging.getLogger("UniversalLLMBot")

_RACE_MAP = {
    "terran": Race.Terran,
    "zerg": Race.Zerg,
    "protoss": Race.Protoss,
    "random": Race.Random,
}


# ======================================================================
# 动态运营层执行器（与原版一致）
# ======================================================================


class ActLLMOngoingTasks(ActBase):
    """每帧扫一遍动作历史，把每个目标态翻译成具体 Sharpy 调用。"""

    def __init__(self, active_tasks_ref: List[Dict[str, Any]]):
        super().__init__()
        self.active_tasks = active_tasks_ref

    async def execute(self) -> bool:
        for task in self.active_tasks:
            if task.get("_disabled", False):
                continue

            action_key = task.get("action")
            try:
                to_count = int(task.get("to_count", 1))
            except (TypeError, ValueError):
                logger.warning("Invalid to_count in action history item %s; disabling.", task)
                task["_disabled"] = True
                task["_error"] = "invalid_to_count"
                continue

            if not action_key:
                task["_disabled"] = True
                task["_error"] = "missing_action"
                continue

            act: Optional[ActBase] = task.get("_act")
            if act is None:
                get_action_fn = task.get("_get_action_fn")
                if get_action_fn is None:
                    task["_disabled"] = True
                    task["_error"] = "no_action_resolver"
                    continue
                try:
                    act = get_action_fn(action_key, to_count)
                except Exception as exc:
                    logger.warning(
                        "Failed to instantiate act for action=%s to_count=%s: %s",
                        action_key, to_count, exc,
                    )
                    task["_disabled"] = True
                    task["_error"] = f"instantiate_failed: {exc}"
                    continue
                task["_act"] = act
                task["_started"] = False

            if not task.get("_started", False):
                try:
                    await self.start_component(act, self.knowledge)
                except Exception as exc:
                    logger.warning("Failed to start act for action=%s: %s", action_key, exc)
                    task["_disabled"] = True
                    task["_error"] = f"start_failed: {exc}"
                    continue
                task["_started"] = True

            try:
                await act.execute()
            except Exception as exc:
                logger.warning("Act execute failed for action=%s: %s", action_key, exc)

        return True


# ======================================================================
# 默认空战术（当无策略可加载时的兜底）
# ======================================================================


class EmptyTactics(SequentialList):
    """种族无关的最小兜底战术列表——当 SKILL 目录无对应策略时使用。

    只包含跨种族通用的 tactics primitive；任何种族专属逻辑（如 Terran 的 Repair
    / ContinueBuilding / CallMule）都不能放在这里，否则会在 Zerg/Protoss 上 crash。
    """

    def __init__(self):
        from sharpy.plans.tactics import (
            PlanZoneDefense, PlanZoneAttack, PlanFinishEnemy, DistributeWorkers,
        )
        super().__init__([
            PlanZoneDefense(),
            DistributeWorkers(),
            PlanZoneAttack(40),
            PlanFinishEnemy(),
        ])


# ======================================================================
# UniversalLLMBot 主类
# ======================================================================


class UniversalLLMBot(KnowledgeBot):
    """跨种族、三层 Agent 架构的通用 LLM Bot。"""

    TOP_AGENT_POLL_INTERVAL: float = 60.0
    MID_AGENT_POLL_INTERVAL: float = 12.0
    #: t=0 多轮交互式策略选择的最大轮次，防止 LLM 死循环 VIEW。
    TOP_AGENT_INITIAL_MAX_TURNS: int = 5

    zone_manager: IZoneManager

    def __init__(
        self,
        race_name: str = "terran",
        instruct: str = "",
        top_model_key: str = "",
        mid_model_key: str = "",
        down_model_key: str = "",
        record_dir: str = "",
        use_top_60_prompt: bool = False,
        use_mid_prompt: bool = False,
    ):
        super().__init__("Universal LLM Bot")
        self.race_name = race_name.strip().lower()
        self.instruct = instruct.strip()
        self.top_model_key = top_model_key.strip()
        self.mid_model_key = mid_model_key.strip()
        self.down_model_key = down_model_key.strip()
        self.record_dir = record_dir.strip()
        self.use_top_60_prompt: bool = bool(use_top_60_prompt)
        self.use_mid_prompt: bool = bool(use_mid_prompt)

        # --- Top Agent 状态 ---
        self.selected_strategy: Optional[str] = None
        self.strategy_description: str = ""
        self.current_phase: str = ""
        self.current_focus: str = ""
        self._top_agent_initialized: bool = False
        self._last_top_agent_time: float = -self.TOP_AGENT_POLL_INTERVAL

        # --- Mid/Down Agent 状态 ---
        self.active_tasks: List[Dict[str, Any]] = []
        self.current_natural_tasks: List[str] = []
        self._last_mid_agent_time: float = -self.MID_AGENT_POLL_INTERVAL

        # --- 种族动态模块 ---
        self._get_action_fn: Optional[Callable] = None
        self._get_action_space_fn: Optional[Callable] = None
        self._action_space_cache: Optional[Dict[str, str]] = None
        # 形如 ``{name: {"summary": str, "detail": str}}``。
        self._available_strategies: Dict[str, Dict[str, str]] = {}

        # --- 阶段性指导文件（t=60 与 mid agent） ---
        self._top60_guidance_text: str = ""
        self._mid_guidance_text: str = ""

        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

    # ------------------------------------------------------------------
    # 种族动态加载
    # ------------------------------------------------------------------

    def _load_race_action_module(self) -> None:
        """动态加载 ``SKILL.{race}.Action`` 模块。"""
        module_path = f"SKILL.{self.race_name}.Action"
        try:
            mod = importlib.import_module(module_path)
            self._get_action_fn = getattr(mod, "get_action", None)
            self._get_action_space_fn = getattr(mod, "get_action_space", None)
        except ImportError:
            logger.warning("Action module %s not found; action space will be empty.", module_path)
            self._get_action_fn = None
            self._get_action_space_fn = None

        self._action_space_cache = (
            self._get_action_space_fn() if self._get_action_space_fn else {}
        )

    # ------------------------------------------------------------------
    # SKILL 目录工具
    # ------------------------------------------------------------------

    @property
    def _skill_race_dir(self) -> str:
        """``SKILL/{race}/`` 绝对路径。"""
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            os.pardir, os.pardir,
            "SKILL", self.race_name,
        )
        return os.path.normpath(path)

    def _discover_strategies(self) -> Dict[str, Dict[str, str]]:
        """遍历 ``SKILL/{race}/`` 目录，发现所有 ``Top_agent_0.md`` 策略并解析。

        :return: ``{策略名: {"summary": str, "detail": str}}``。
                 不包含 ``generic/`` 目录（它仅为兜底指导文件载体）。
        """
        skill_dir = self._skill_race_dir
        strategies: Dict[str, Dict[str, str]] = {}
        if not os.path.isdir(skill_dir):
            logger.info("SKILL directory %s not found.", skill_dir)
            return strategies

        for entry in sorted(os.listdir(skill_dir)):
            if entry == "generic":
                continue
            entry_path = os.path.join(skill_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            top0_path = os.path.join(entry_path, "Top_agent_0.md")
            if not os.path.isfile(top0_path):
                continue
            try:
                with open(top0_path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as exc:
                logger.warning("Failed to read %s: %s", top0_path, exc)
                continue
            parsed = parse_top_agent_0_md(raw)
            strategies[entry] = {
                "summary": parsed["summary"] or "(no summary)",
                "detail": parsed["detail"] or raw.strip(),
            }
        return strategies

    def _read_md(self, path: str) -> str:
        """安全读取 markdown 文件，不存在/空内容/异常均返回空串。"""
        if not path or not os.path.isfile(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return ""

    def _resolve_phase_guidance(self) -> None:
        """根据 ``selected_strategy`` 路由读取 ``Top_agent_60.md`` / ``mid_agent.md``。

        路由规则：
        * 若策略是已有策略（且对应文件存在/非空）：读取 ``SKILL/{race}/{strategy}/``。
        * 否则（``Custom_Generated`` 或文件缺失/空）：读取 ``SKILL/{race}/generic/``。
        """
        skill_dir = self._skill_race_dir
        generic_dir = os.path.join(skill_dir, "generic")

        # Top_agent_60.md
        self._top60_guidance_text = ""
        # mid_agent.md
        self._mid_guidance_text = ""

        candidate_dirs: List[str] = []
        if (
            self.selected_strategy
            and self.selected_strategy != CUSTOM_STRATEGY_NAME
        ):
            candidate_dirs.append(os.path.join(skill_dir, self.selected_strategy))
        candidate_dirs.append(generic_dir)

        for d in candidate_dirs:
            if not self._top60_guidance_text:
                self._top60_guidance_text = self._read_md(os.path.join(d, "Top_agent_60.md"))
            if not self._mid_guidance_text:
                self._mid_guidance_text = self._read_md(os.path.join(d, "mid_agent.md"))
            if self._top60_guidance_text and self._mid_guidance_text:
                break

        self._llm_infer_emit(
            "    Loaded phase guidance: top60=%d chars, mid=%d chars (use_top60=%s, use_mid=%s)",
            len(self._top60_guidance_text),
            len(self._mid_guidance_text),
            self.use_top_60_prompt,
            self.use_mid_prompt,
        )

    # ------------------------------------------------------------------
    # 动态战术加载（importlib）
    # ------------------------------------------------------------------

    def _load_dynamic_tactics(self) -> SequentialList:
        """根据 ``selected_strategy`` 动态 import 对应的 base_tactics 模块。

        约定：``SKILL.{race}.{strategy}.base_tactics`` 模块中，
        第一个继承 ``SequentialList`` 的类即为战术执行器。
        ``Custom_Generated`` 策略没有对应文件夹，直接使用兜底 EmptyTactics。
        """
        if not self.selected_strategy:
            logger.info("No strategy selected; using EmptyTactics.")
            return EmptyTactics()

        if self.selected_strategy == CUSTOM_STRATEGY_NAME:
            logger.info("Custom-generated strategy has no base_tactics; using EmptyTactics.")
            return EmptyTactics()

        module_path = f"SKILL.{self.race_name}.{self.selected_strategy}.base_tactics"
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            logger.warning(
                "Cannot import tactics module %s; falling back to EmptyTactics.",
                module_path,
            )
            return EmptyTactics()

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, SequentialList)
                and attr is not SequentialList
            ):
                try:
                    return attr()
                except TypeError:
                    try:
                        return attr(20)
                    except Exception as exc:
                        logger.warning("Failed to instantiate %s: %s", attr_name, exc)

        logger.warning("No SequentialList subclass found in %s.", module_path)
        return EmptyTactics()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_start(self):
        # ★ 关键：种族模块加载 + Top Agent t=0 策略选择必须在 super().on_start() 之前完成。
        # 因为 super().on_start() → knowledge.start() → ActManager.post_start() → create_plan()，
        # 而 create_plan() 需要 self.selected_strategy 才能动态加载对应的 base_tactics。
        self._load_race_action_module()
        self._available_strategies = self._discover_strategies()
        self._run_top_agent_initial_blocking()
        # 选定策略后，根据策略路由加载阶段性指导文件（Top_agent_60.md / mid_agent.md）。
        self._resolve_phase_guidance()
        self._top_agent_initialized = True

        await super().on_start()

        self.zone_manager = self.knowledge.get_required_manager(IZoneManager)
        self.llm_observation_recorder.interval_seconds = self.MID_AGENT_POLL_INTERVAL
        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

    async def pre_step_execute(self):
        """每帧 Tick 入口：依次检查 Top Agent 和 Mid Agent 的触发条件。"""
        # --- Top Agent: 60 秒轮询（t=0 已在 on_start 中执行）---
        if self.time - self._last_top_agent_time >= self.TOP_AGENT_POLL_INTERVAL:
            self._last_top_agent_time = self.time
            self._run_top_agent_poll_blocking()

        # --- Mid Agent: N 秒轮询 ---
        if self.time - self._last_mid_agent_time >= self.MID_AGENT_POLL_INTERVAL:
            self._last_mid_agent_time = self.time
            self._run_mid_agent_pipeline_blocking(trigger_reason="poll")

    # ------------------------------------------------------------------
    # Top Agent
    # ------------------------------------------------------------------

    def _run_top_agent_initial_blocking(self) -> None:
        """t=0 多轮交互式策略选择（SELECT / VIEW / GENERATE，同步阻塞）。

        除日志外，会把完整流程作为一条 ``trigger_reason="top_agent_initial_t0"`` 的
        记录追加进 ``llm_observation_recorder.llm_interactions``，便于事后回放。
        """
        pipeline_start = _wall_time.monotonic()
        self._llm_infer_emit(">>> TOP AGENT: t=0 interactive strategy selection START")

        # 即便没有任何预定义策略，依旧允许 LLM GENERATE 一套自定义策略。
        if not self._available_strategies:
            self._llm_infer_emit(
                "    No predefined strategies in SKILL/%s/; LLM will be asked to GENERATE.",
                self.race_name,
            )

        summaries: Dict[str, str] = {
            name: info.get("summary", "") for name, info in self._available_strategies.items()
        }
        self._llm_infer_emit(
            f"    Top Agent t=0 inputs: race={self.race_name!r}, "
            f"instruct={self.instruct!r}, "
            f"available_strategies={list(summaries.keys())}"
        )

        messages = build_initial_strategy_messages(
            race=self.race_name,
            instruct=self.instruct,
            strategy_summaries=summaries,
        )

        valid_names = list(self._available_strategies.keys())
        viewed: List[str] = []
        max_turns = self.TOP_AGENT_INITIAL_MAX_TURNS

        turn_records: List[Dict[str, Any]] = []
        final_action: str = "UNKNOWN"
        final_error: Optional[str] = None

        for turn in range(1, max_turns + 1):
            turn_log: Dict[str, Any] = {
                "turn": turn,
                "messages_sent": [dict(m) for m in messages],
                "raw_response": "",
                "parsed_action": None,
                "wall_elapsed_seconds": None,
                "note": "",
            }
            self._llm_infer_emit(
                f"    >>> TOP AGENT t=0 turn {turn}/{max_turns} REQUEST "
                f"({len(messages)} messages)"
            )

            t_start = _wall_time.monotonic()
            try:
                raw = self._call_llm(messages, agent="top")
            except Exception as exc:
                turn_log["wall_elapsed_seconds"] = round(_wall_time.monotonic() - t_start, 3)
                turn_log["note"] = f"llm_call_exception: {exc!r}"
                turn_records.append(turn_log)
                logger.warning("[TopAgent] LLM call failed (turn %d): %s", turn, exc)
                self._llm_infer_emit(f"    Top Agent t=0 turn {turn} EXCEPTION: {exc!r}")
                final_error = repr(exc)
                final_action = "EXCEPTION"
                break
            turn_log["wall_elapsed_seconds"] = round(_wall_time.monotonic() - t_start, 3)
            turn_log["raw_response"] = raw

            if not raw:
                turn_log["note"] = "empty_response"
                turn_records.append(turn_log)
                self._llm_infer_emit(f"    Top Agent t=0 turn {turn} returned EMPTY.")
                final_action = "EMPTY"
                break

            self._llm_infer_emit(
                f"    <<< TOP AGENT t=0 turn {turn} RESPONSE "
                f"({turn_log['wall_elapsed_seconds']:.2f}s, {len(raw)} chars): "
                f"{raw.strip()!r}"
            )
            action = parse_initial_action(raw)
            turn_log["parsed_action"] = action
            if action is None:
                turn_log["note"] = "invalid_action_json"
                turn_records.append(turn_log)
                self._llm_infer_emit(
                    f"    Top Agent turn {turn} produced invalid action JSON; aborting loop."
                )
                final_action = "INVALID_JSON"
                break

            self._llm_infer_emit(f"    Top Agent turn {turn} parsed action: {action}")
            # 把 LLM 这一轮的原始输出作为 assistant 消息追加进历史。
            messages.append({"role": "assistant", "content": raw})

            if action["action"] == "SELECT":
                resolved = self._resolve_strategy_name(action["strategy"], valid_names)
                if resolved is None:
                    turn_log["note"] = f"select_unknown:{action['strategy']!r}"
                    turn_records.append(turn_log)
                    self._llm_infer_emit(
                        f"    SELECT target {action['strategy']!r} not in available list; "
                        f"re-prompting LLM."
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your SELECT target '{action['strategy']}' is not in the available list. "
                            "Please choose strictly from the listed strategy names, or VIEW more "
                            "detail, or GENERATE a custom strategy."
                        ),
                    })
                    continue
                self.selected_strategy = resolved
                self.strategy_description = self._available_strategies[resolved]["detail"]
                turn_log["note"] = f"selected:{resolved}"
                turn_records.append(turn_log)
                self._llm_infer_emit(
                    f"    Top Agent SELECTED existing strategy: '{resolved}' (turn {turn})"
                )
                final_action = "SELECT"
                break

            if action["action"] == "VIEW":
                requested = action["strategies"]
                view_details: Dict[str, str] = {}
                unknown: List[str] = []
                for name in requested:
                    resolved = self._resolve_strategy_name(name, valid_names)
                    if resolved is None:
                        unknown.append(name)
                        continue
                    view_details[resolved] = self._available_strategies[resolved]["detail"]
                    if resolved not in viewed:
                        viewed.append(resolved)

                self._llm_infer_emit(
                    f"    Top Agent VIEW requested {requested!r}; "
                    f"resolved={list(view_details.keys())}, unknown={unknown}"
                )
                followup = build_view_followup_user_message(view_details)
                if unknown:
                    followup["content"] = (
                        f"WARNING: these requested names were not found and ignored: {unknown}.\n\n"
                        + followup["content"]
                    )
                messages.append(followup)
                turn_log["note"] = (
                    f"viewed:{list(view_details.keys())}; unknown:{unknown}"
                )
                turn_records.append(turn_log)
                continue

            if action["action"] == "GENERATE":
                generated_text = action["strategy"].strip()
                if not generated_text:
                    turn_log["note"] = "generate_empty"
                    turn_records.append(turn_log)
                    self._llm_infer_emit("    GENERATE returned empty strategy text; aborting.")
                    final_action = "GENERATE_EMPTY"
                    break
                self.selected_strategy = CUSTOM_STRATEGY_NAME
                self.strategy_description = generated_text
                turn_log["note"] = f"generated:{len(generated_text)}chars"
                turn_records.append(turn_log)
                self._llm_infer_emit(
                    f"    Top Agent GENERATED a custom strategy ({len(generated_text)} chars, "
                    f"turn {turn}); marked as '{CUSTOM_STRATEGY_NAME}'."
                )
                final_action = "GENERATE"
                break

            # 未知 action（理论上 parse_initial_action 已过滤）
            turn_log["note"] = f"unknown_action:{action['action']!r}"
            turn_records.append(turn_log)
            self._llm_infer_emit(f"    Unknown action {action['action']!r}; aborting loop.")
            final_action = "UNKNOWN_ACTION"
            break
        else:
            # for-else: 5 轮全部跑完仍未 break -> 视为超过最大轮次
            final_action = "MAX_TURNS_EXCEEDED"
            self._llm_infer_emit(
                f"    Top Agent t=0 reached max turns ({max_turns}) without decision."
            )

        # —— 未成功决策：fallback ——
        fallback_used: Optional[str] = None
        if not self.selected_strategy:
            fallback = valid_names[0] if valid_names else None
            if fallback is not None:
                self.selected_strategy = fallback
                self.strategy_description = self._available_strategies[fallback]["detail"]
                fallback_used = fallback
                self._llm_infer_emit(
                    f"    Top Agent loop exhausted; fallback to first strategy '{fallback}'."
                )
            else:
                self._llm_infer_emit(
                    "    Top Agent loop exhausted with no available strategies; "
                    "will run with EmptyTactics."
                )

        total_elapsed = _wall_time.monotonic() - pipeline_start
        self._llm_infer_emit(
            f"<<< TOP AGENT: t=0 END "
            f"(final_action={final_action}, "
            f"selected_strategy={self.selected_strategy!r}, "
            f"turns={len(turn_records)}, "
            f"wall={total_elapsed:.2f}s)"
        )

        # —— 追加 JSON 记录 ——
        self._record_llm_interaction({
            "game_time": 0.0,
            "trigger_reason": "top_agent_initial_t0",
            "wall_elapsed_seconds": round(total_elapsed, 3),
            "top_agent_initial": {
                "race": self.race_name,
                "instruct": self.instruct,
                "available_strategies": valid_names,
                "strategy_summaries": summaries,
                "max_turns": max_turns,
                "turns": turn_records,
                "viewed_strategies": viewed,
                "final_action": final_action,
                "fallback_used": fallback_used,
                "selected_strategy": self.selected_strategy,
                "strategy_description": self.strategy_description,
                "error": final_error,
            },
        })

    @staticmethod
    def _resolve_strategy_name(name: str, valid_names: List[str]) -> Optional[str]:
        """大小写不敏感地把 LLM 给出的策略名映射到合法名（找不到返回 None）。"""
        if not name:
            return None
        if name in valid_names:
            return name
        lower_map = {n.lower(): n for n in valid_names}
        return lower_map.get(name.lower())

    def _run_top_agent_poll_blocking(self) -> None:
        """每 60 秒阶段评估（同步阻塞）。"""
        if not self.selected_strategy:
            return

        self._llm_infer_emit(
            f">>> TOP AGENT: phase assessment (game_time={self.time:.1f}s)"
        )

        obs_text = self._capture_observation_text()
        messages = build_phase_assessment_messages(
            race=self.race_name,
            obs_text=obs_text,
            instruct=self.instruct,
            strategy_name=self.selected_strategy,
            strategy_description=self.strategy_description,
            enable_phase_guidance=self.use_top_60_prompt,
            phase_guidance_text=self._top60_guidance_text,
        )

        try:
            raw = self._call_llm(messages, agent="top")
        except Exception as exc:
            logger.warning("[TopAgent] phase assessment failed: %s", exc)
            self._llm_infer_emit(f"    Top Agent poll FAILED: {exc!r}")
            return

        result = parse_phase_assessment(raw)
        if result:
            self.current_phase = result["phase"]
            self.current_focus = result["focus"]
            self._llm_infer_emit(
                f"    Top Agent: phase={self.current_phase}, "
                f"focus={self.current_focus!r}"
            )
        else:
            self._llm_infer_emit(f"    Top Agent parse failed: {raw!r}")

    # ------------------------------------------------------------------
    # Mid Agent + Down Agent Pipeline
    # ------------------------------------------------------------------

    def _run_mid_agent_pipeline_blocking(self, *, trigger_reason: str = "unknown") -> None:
        """Mid Agent → Down Agent 双阶段流水线（同步阻塞）。"""
        game_time = self.time
        pipeline_start = _wall_time.monotonic()
        obs_text: str = ""
        obs_snapshot: Optional[Dict[str, Any]] = None
        previous_natural_tasks = list(self.current_natural_tasks)
        mid_agent_text: str = ""
        mid_agent_tasks: List[str] = []
        down_agent_translations: List[Dict[str, Any]] = []
        parsed_tasks: List[Dict[str, Any]] = []
        error_text: Optional[str] = None

        self._llm_infer_emit(
            f">>> MID AGENT START (trigger={trigger_reason}, game_time={game_time:.1f}s, "
            f"active_task_count={len(self.active_tasks)})",
            include_active_tasks=True,
        )

        try:
            obs_text, obs_snapshot = self._capture_observation_bundle()
            self._llm_infer_emit(
                f"observation_at_decision_time (game_time={game_time:.1f}s):\n{obs_text}"
            )

            # ===================== Mid Agent: Planning ====================
            self._llm_infer_emit("    calling Mid Agent (planning)...")
            s1_start = _wall_time.monotonic()
            mid_agent_text = self._call_mid_agent(obs_text, previous_natural_tasks)
            s1_elapsed = _wall_time.monotonic() - s1_start

            if not mid_agent_text:
                self._llm_infer_emit(f"    Mid Agent returned EMPTY ({s1_elapsed:.2f}s).")
                return

            self._llm_infer_emit(
                f"    Mid Agent done in {s1_elapsed:.2f}s, {len(mid_agent_text)} chars."
            )
            self._llm_infer_emit(f"    Mid Agent output: {mid_agent_text.strip()!r}")

            parsed_mid_tasks = parse_planning_response(mid_agent_text)
            if parsed_mid_tasks is None:
                error_text = "invalid_mid_agent_json"
                self._llm_infer_emit("    Mid Agent output failed JSON validation.")
                return
            mid_agent_tasks = parsed_mid_tasks
            self._llm_infer_emit(
                f"    Mid Agent parsed {len(mid_agent_tasks)} natural-language tasks."
            )

            # ===================== Down Agent: Translation ================
            for index, natural_task in enumerate(mid_agent_tasks, start=1):
                self._llm_infer_emit(
                    f"    calling Down Agent #{index}/{len(mid_agent_tasks)} "
                    f"for task={natural_task!r}..."
                )
                translation_record: Dict[str, Any] = {
                    "raw": natural_task, "response": "", "parsed": None,
                }
                s2_start = _wall_time.monotonic()
                try:
                    down_text = self._call_down_agent(natural_task, obs_text)
                    translation_record["response"] = down_text
                    s2_elapsed = _wall_time.monotonic() - s2_start

                    if not down_text:
                        translation_record["error"] = "empty_response"
                        self._llm_infer_emit(
                            f"    Down Agent #{index} returned EMPTY ({s2_elapsed:.2f}s)."
                        )
                        continue

                    legal_keys = set((self._action_space_cache or {}).keys())
                    parsed_task = parse_translation_response(down_text, legal_keys)
                    if parsed_task is None:
                        translation_record["error"] = "invalid_json_or_action"
                        self._llm_infer_emit(f"    Down Agent #{index} failed validation.")
                        continue

                    translation_record["parsed"] = self._slim_action(parsed_task)
                    parsed_tasks.append(parsed_task)
                    self._llm_infer_emit(
                        f"    Down Agent #{index} accepted in {s2_elapsed:.2f}s: "
                        f"{translation_record['parsed']}"
                    )
                except Exception as exc:
                    translation_record["error"] = repr(exc)
                    self._llm_infer_emit(
                        f"    Down Agent #{index} EXCEPTION: {exc!r}"
                    )
                finally:
                    down_agent_translations.append(translation_record)

            self._replace_active_tasks(parsed_tasks)
            self.current_natural_tasks = list(mid_agent_tasks)
            self._llm_infer_emit(
                f"    REFRESHED active_tasks with {len(parsed_tasks)} parsed actions."
            )
        except Exception as exc:
            error_text = repr(exc)
            self._llm_infer_emit(f"    EXCEPTION: {exc!r}")
            logger.warning("[UniversalLLMBot] Pipeline failed: %s", exc)
        finally:
            total_elapsed = _wall_time.monotonic() - pipeline_start
            self._record_llm_interaction({
                "game_time": round(game_time, 2),
                "trigger_reason": trigger_reason,
                "wall_elapsed_seconds": round(total_elapsed, 3),
                "top_agent_strategy": self.selected_strategy,
                "top_agent_phase": self.current_phase,
                "top_agent_focus": self.current_focus,
                "observation_at_this_moment": obs_text,
                "observation_structured": obs_snapshot,
                "mid_agent_input_previous_tasks": previous_natural_tasks,
                "mid_agent_raw_response": mid_agent_text,
                "mid_agent_output_new_tasks": mid_agent_tasks,
                "down_agent_translations": down_agent_translations,
                "active_tasks_after_refresh": self._serialise_active_tasks(),
                "error": error_text,
            })
            self._llm_infer_emit(
                f"<<< MID AGENT END (total {total_elapsed:.2f}s wall, "
                f"active_task_count={len(self.active_tasks)})"
            )

    # --- 调用封装 ------------------------------------------------------

    def _call_llm(self, messages: List[Dict[str, str]], agent: str = "mid") -> str:
        """根据 agent 类型选择对应的 model_key 调用 LLM。"""
        key_map = {
            "top": self.top_model_key,
            "mid": self.mid_model_key,
            "down": self.down_model_key,
        }
        model_key = key_map.get(agent, "")

        if not model_key:
            logger.warning(
                "[UniversalLLMBot] No model_key for agent=%r; set top/mid/down_model_key.",
                agent,
            )
            return ""

        return call_openai(messages=messages, model_key=model_key)

    def _call_mid_agent(self, obs_text: str, previous_tasks: List[str]) -> str:
        """构造 Mid Agent Prompt 并调用 LLM。"""
        messages = build_planning_messages(
            race=self.race_name,
            obs_text=obs_text,
            previous_tasks=previous_tasks,
            strategy_description=self.strategy_description,
            phase=self.current_phase,
            focus=self.current_focus,
            enable_execution_guidance=self.use_mid_prompt,
            execution_guidance_text=self._mid_guidance_text,
        )
        return self._call_llm(messages, agent="mid")

    def _call_down_agent(self, task_description: str, obs_text: str) -> str:
        """构造 Down Agent Prompt 并调用 LLM。"""
        messages = build_translation_messages(
            race=self.race_name,
            task_description=task_description,
            obs_text=obs_text,
            action_space=self._action_space_cache or {},
        )
        return self._call_llm(messages, agent="down")

    # --- 观测 ---------------------------------------------------------

    def _capture_observation_bundle(self) -> Tuple[str, Optional[Dict[str, Any]]]:
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return "(LLMObservationRecorder unavailable)", None
        try:
            snapshot = recorder._build_snapshot()
            return recorder._generate_english_text_obs(snapshot), snapshot
        except Exception as exc:
            logger.warning("[UniversalLLMBot] failed to build observation: %s", exc)
            return "(observation unavailable)", None

    def _capture_observation_text(self) -> str:
        text, _ = self._capture_observation_bundle()
        return text

    # --- 任务管理 -----------------------------------------------------

    def _replace_active_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        self.active_tasks.clear()
        for idx, task in enumerate(tasks, start=1):
            self.active_tasks.append({
                "sequence": idx,
                "action": task.get("action"),
                "to_count": task.get("to_count"),
                "_get_action_fn": self._get_action_fn,
            })

    @staticmethod
    def _slim_action(task: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if task is None:
            return None
        slim = {"action": task.get("action"), "to_count": task.get("to_count")}
        if "sequence" in task:
            slim["sequence"] = task.get("sequence")
        return slim

    def _serialise_active_tasks(self) -> List[Dict[str, Any]]:
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
        tasks = self._serialise_active_tasks()
        return json.dumps(tasks, ensure_ascii=False) if tasks else "[]"

    # --- 日志与记录 ---------------------------------------------------

    def _record_llm_interaction(self, record: Dict[str, Any]) -> None:
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return
        append_func = getattr(recorder, "record_llm_interaction", None)
        if append_func is None:
            return
        try:
            append_func(record)
        except Exception as exc:
            logger.warning("[UniversalLLMBot] failed to record LLM interaction: %s", exc)

    def _llm_infer_emit(self, message: str, *args, include_active_tasks: bool = False) -> None:
        if args:
            message = message % args
        line = f"[UniversalLLMBot][LLM-INFER] {message}"
        if include_active_tasks:
            line += f" | active_tasks={self._active_tasks_snapshot()}"

        # 优先经由 knowledge.print (loguru, 进 .log 文件) 输出。
        # ★ 关键：在 ``super().on_start()`` 之前（即 Top Agent t=0 流程中），
        #   ``self.knowledge`` 尚未初始化；此时回退到直接写 ``sc2.main.logger``。
        #   游戏 ``.log`` 文件的 loguru sink 用 ``filter="sharpy"`` 筛选，因此我们
        #   通过 ``logger.patch`` 把 record name 改成以 ``sharpy.`` 开头，保证
        #   t=0 多轮交互的日志也能落到同一份 ``.log`` 文件中、格式与游戏中一致。
        try:
            self.knowledge.print(line, stats=False)
            return
        except Exception:
            pass

        try:
            from sc2.main import logger as _loguru_logger

            _loguru_logger.patch(
                lambda r: r.update(name="sharpy.universal_llm_bot")
            ).opt(depth=1).info(line)
        except Exception:
            logger.info("%s", line)

    # ------------------------------------------------------------------
    # Sharpy plan 入口
    # ------------------------------------------------------------------

    async def create_plan(self) -> BuildOrder:
        """组装 BuildOrder：补给 + Orbital + LLM 动态运营 + 动态战术层。"""
        base_tactics = self._load_dynamic_tactics()

        llm_executor = ActLLMOngoingTasks(
            active_tasks_ref=self.active_tasks,
        )

        empty = BuildOrder([])

        return BuildOrder(
            empty.depots,
            Step(
                None,
                MorphOrbitals(),
                skip_until=UnitReady(UnitTypeId.BARRACKS, 1),
            ),
            llm_executor,
            base_tactics,
        )


# ----------------------------------------------------------------------
# Ladder 兼容入口
# ----------------------------------------------------------------------


class LadderBot(UniversalLLMBot):
    @property
    def my_race(self):
        return _RACE_MAP.get(self.race_name, Race.Terran)
