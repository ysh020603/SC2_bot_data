"""Universal LLM Bot with a forced-strategy macro pipeline.

A match must specify a strategy folder name via ``force_strategy`` /
``--force-strategy``. Runtime reads that folder's ``Top_agent_0.md`` steps and
feeds them through the five-stage macro pipeline:

    strategy step -> Naming Agent -> DATA_TOOLS mapping -> Ordering Agent
    -> Supply Planner -> ExecutionScheduler

The old interactive opening strategy chooser has been removed.
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
from sharpy.managers.core.manager_base import ManagerBase
from sharpy.managers.extensions import BuildDetector
from sharpy.plans import BuildOrder, SequentialList
from sharpy.plans.acts import ActBase

from API_Tools.llm_caller import call_openai
from SC2_Agent.top_agent import parse_top_agent_0_md
from SC2_Agent.mid_agent import (
    build_planning_messages,
    parse_planning_response,
)
from SC2_Agent.down_agent import (
    build_translation_messages,
    parse_translation_response,
)

# --- 新五阶段增量驱动流水线 ---
from SC2_Agent.naming_agent import (
    build_naming_messages,
    parse_naming_response,
)
from SC2_Agent.ordering_agent import (
    build_ordering_messages,
    parse_ordering_response,
)
from SC2_Agent.executor_agent import (
    build_executor_messages,
    parse_executor_response,
)
from SC2_Agent.data_tools import (
    ALIAS_MAP,
    actions_for_entities,
    check_action_prerequisites,
    cost_for_action,
    detect_action_conflicts,
    is_known_terran_entity,
    plan_supply_with_trace,
    resolve_alias,
    tech_chain_relations,
    terran_unit_names,
    terran_upgrade_names,
)
from SC2_Agent.data_tools.obs_entities import obs_entities as collect_obs_entities
from SC2_Agent.execution.scheduler import ExecutionScheduler

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

    #: 同一任务预留日志的最小间隔（游戏内秒），避免每帧刷屏。
    _RESERVE_LOG_DEBOUNCE_SEC: float = 5.0
    _RESERVE_WAIT_REASON: str = (
        "前置条件已满足，正在等待经济/人口积累以执行"
    )

    def __init__(
        self,
        active_tasks_ref: List[Dict[str, Any]],
        log_emit: Optional[Callable[[str], None]] = None,
    ):
        super().__init__()
        self.active_tasks = active_tasks_ref
        self._log_emit = log_emit

    @staticmethod
    def _reserved_snapshot(knowledge: Any) -> Tuple[int, int, int]:
        """读取 Sharpy 预留池快照（矿/气）及当前可用人口余量。"""
        return (
            knowledge.reserved_minerals,
            knowledge.reserved_gas,
            knowledge.ai.supply_left,
        )

    def _format_task_label(self, task: Dict[str, Any], action_key: str) -> str:
        to_count = task.get("to_count")
        priority = task.get("priority", False)
        if to_count is not None:
            label = f"{action_key}(to_count={to_count})"
            if priority:
                label += ", priority=True"
            return label
        return action_key

    def _maybe_log_resource_reservation(
        self,
        task: Dict[str, Any],
        action_key: str,
        delta_minerals: int,
        delta_gas: int,
        supply_left_before: int,
        supply_left_after: int,
    ) -> None:
        if delta_minerals <= 0 and delta_gas <= 0:
            return

        game_time = self.ai.time
        last_log_time = task.get("_last_reserve_log_time", -self._RESERVE_LOG_DEBOUNCE_SEC)
        if game_time - last_log_time < self._RESERVE_LOG_DEBOUNCE_SEC:
            return
        task["_last_reserve_log_time"] = game_time

        parts: List[str] = []
        if delta_minerals > 0:
            parts.append(f"矿+{delta_minerals}")
        if delta_gas > 0:
            parts.append(f"气+{delta_gas}")

        supply_delta = supply_left_before - supply_left_after
        if supply_delta > 0:
            parts.append(f"人口占用+{supply_delta}")

        task_label = self._format_task_label(task, action_key)
        supply_hint = ""
        if supply_left_after <= 4:
            supply_hint = f"（当前可用人口余量 {supply_left_after}）"

        message = (
            f"[任务 {task_label}] 预留了 {', '.join(parts)}，原因："
            f"{self._RESERVE_WAIT_REASON}{supply_hint}"
        )
        if self._log_emit is not None:
            self._log_emit(message)
        else:
            self.knowledge.print(
                f"[ActLLMOngoingTasks][RESERVE] {message}",
                stats=False,
            )

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
                is_priority = bool(task.get("priority", False))
                try:
                    if is_priority:
                        act = get_action_fn(action_key, to_count, priority=True)
                    else:
                        act = get_action_fn(action_key, to_count)
                except Exception as exc:
                    logger.warning(
                        "Failed to instantiate act for action=%s to_count=%s priority=%s: %s",
                        action_key, to_count, is_priority, exc,
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

            baseline_min, baseline_gas, supply_before = self._reserved_snapshot(
                self.knowledge
            )
            try:
                await act.execute()
            except Exception as exc:
                logger.warning("Act execute failed for action=%s: %s", action_key, exc)
            else:
                after_min, after_gas, supply_after = self._reserved_snapshot(
                    self.knowledge
                )
                self._maybe_log_resource_reservation(
                    task,
                    action_key,
                    after_min - baseline_min,
                    after_gas - baseline_gas,
                    supply_before,
                    supply_after,
                )

        return True


# ======================================================================
# 默认空战术（当无策略可加载时的兜底）
# ======================================================================


class EmptyTactics(BuildOrder):
    """种族无关的最小兜底战术列表（并行执行）——当 SKILL 目录无对应策略时使用。

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

    MID_AGENT_POLL_INTERVAL: float = 30.0
    #: 宏观决策触发周期（秒）：每隔该时间或 Action 序列执行完触发一次增量流水线。
    MACRO_POLL_INTERVAL: float = 60.0
    #: 序列执行完后再次触发的最小间隔（秒），避免空序列时每帧反复触发 LLM。
    MACRO_MIN_RETRIGGER: float = 5.0
    #: Waiting actions are abandoned after this many game seconds (0 = never).
    WAIT_ABANDON_SEC: float = 60.0
    #: Supply 注入阈值：保证模拟人口余量不低于该值。
    SUPPLY_THRESHOLD: float = 8.0
    zone_manager: IZoneManager

    def __init__(
        self,
        race_name: str = "terran",
        mid_model_key: str = "",
        down_model_key: str = "",
        record_dir: str = "",
        *,
        naming_model_key: str = "",
        ordering_model_key: str = "",
        executor_model_key: str = "",
        force_strategy: Optional[str] = None,
    ):
        super().__init__("Universal LLM Bot")
        self.race_name = race_name.strip().lower()
        self.mid_model_key = mid_model_key.strip()
        self.down_model_key = down_model_key.strip()
        # 新五阶段流水线的各 LLM 调用点（可分别指定模型）。
        self.naming_model_key = naming_model_key.strip()
        self.ordering_model_key = ordering_model_key.strip()
        self.executor_model_key = executor_model_key.strip()
        self.record_dir = record_dir.strip()
        force = (force_strategy or "").strip()
        self.force_strategy: Optional[str] = force if force and force.lower() != "none" else None

        # --- Forced strategy state ---
        self.selected_strategy: Optional[str] = None
        self.strategy_description: str = ""
        self.strategy_steps: List[Dict[str, Any]] = []
        self._next_strategy_step_index: int = 0

        # --- 命令式执行调度器 ---
        self.scheduler: Optional[ExecutionScheduler] = None
        self._last_macro_time: float = -self.MACRO_POLL_INTERVAL
        self._macro_cycle_count: int = 0
        #: 单等待者预取：已为该 waiter 追加过下一步 macro，避免重复 append。
        self._prefetched_while_waiter_key: Optional[Tuple[str, float]] = None
        # 记录每一次 LLM 调用的完整 prompt 与 output，落到专门的 *.llm_calls.json。
        self._llm_call_records: List[Dict[str, Any]] = []
        self._llm_call_seq: int = 0

        # --- Mid/Down Agent 状态（保留兼容，新流水线不再使用） ---
        self.active_tasks: List[Dict[str, Any]] = []
        self.current_natural_tasks: List[str] = []
        self._last_mid_agent_time: float = -self.MID_AGENT_POLL_INTERVAL

        # --- 种族动态模块 ---
        self._get_action_fn: Optional[Callable] = None
        self._get_action_space_fn: Optional[Callable] = None
        self._action_supports_priority_fn: Optional[Callable[[str], bool]] = None
        self._action_space_cache: Optional[Dict[str, str]] = None
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
            self._action_supports_priority_fn = getattr(mod, "action_supports_priority", None)
        except ImportError:
            logger.warning("Action module %s not found; action space will be empty.", module_path)
            self._get_action_fn = None
            self._get_action_space_fn = None
            self._action_supports_priority_fn = None

        self._action_space_cache = (
            self._get_action_space_fn() if self._get_action_space_fn else {}
        )

    # ------------------------------------------------------------------
    # SKILL 目录工具
    # ------------------------------------------------------------------

    @property
    def _skill_root(self) -> str:
        """``SKILL/`` 根目录绝对路径。"""
        return os.path.normpath(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                os.pardir, os.pardir,
                "SKILL",
            )
        )

    @property
    def _skill_race_dir(self) -> str:
        """``SKILL/{race}/`` 绝对路径。"""
        return os.path.normpath(os.path.join(self._skill_root, self.race_name))

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def configure_managers(self) -> Optional[List[ManagerBase]]:
        """Register optional extension managers not in KnowledgeBot defaults."""
        return [BuildDetector()]

    async def on_start(self):
        # The strategy must be fixed before super().on_start(), because
        # knowledge.start() -> ActManager.post_start() -> create_plan().
        self._load_race_action_module()

        if not self.force_strategy:
            raise ValueError(
                "UniversalLLMBot requires force_strategy. "
                "Pass --force-strategy <strategy_folder>."
            )
        self._apply_forced_strategy(self.force_strategy)

        self._refresh_strategy_steps()
        await super().on_start()

        self.zone_manager = self.knowledge.get_required_manager(IZoneManager)
        self.llm_observation_recorder.interval_seconds = self.MACRO_POLL_INTERVAL
        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

    async def on_end(self, game_result):
        # 先让父类（含 LLMObservationRecorder）落盘，再写一份完整的 LLM 调用记录。
        try:
            await super().on_end(game_result)
        finally:
            self._flush_llm_call_log()

    async def pre_step_execute(self):
        """每帧 Tick 入口：检查宏观增量流水线的触发条件。

        触发机制（三选一）：
        1. 距上次触发已超过 ``MACRO_POLL_INTERVAL`` 秒（固定周期）。
        2. 当前 Action 序列已全部执行完（``scheduler.is_drained()``），且距上次触发
           至少 ``MACRO_MIN_RETRIGGER`` 秒（``replace`` 安装下一步）。
        3. 仅剩一个 ``WAITING`` 动作（``scheduler.is_single_waiter_remaining()``），且距上次
           触发至少 ``MACRO_MIN_RETRIGGER`` 秒 → ``append`` 预取下一步（最后一步除外）。
        """
        since = self.time - self._last_macro_time
        trigger_reason: Optional[str] = None
        install_mode = "replace"

        if self.scheduler is not None and not self.scheduler.is_single_waiter_remaining():
            self._prefetched_while_waiter_key = None

        scheduler_drained = self.scheduler is None or self.scheduler.is_drained()
        single_waiter = (
            self.scheduler is not None and self.scheduler.is_single_waiter_remaining()
        )

        if self._macro_cycle_count == 0 and scheduler_drained:
            trigger_reason = "initial_step"
        elif since >= self.MACRO_MIN_RETRIGGER and scheduler_drained:
            trigger_reason = "sequence_drained"
        elif since >= self.MACRO_MIN_RETRIGGER and single_waiter:
            current_step = self._current_strategy_step()
            waiter_key = self.scheduler.waiter_identity() if self.scheduler else None
            if (
                current_step is not None
                and not current_step.get("is_last")
                and waiter_key is not None
                and waiter_key != self._prefetched_while_waiter_key
            ):
                trigger_reason = "single_waiter_prefetch"
                install_mode = "append"
                self._prefetched_while_waiter_key = waiter_key

        if trigger_reason is not None:
            self._last_macro_time = self.time
            self._run_macro_pipeline_blocking(
                trigger_reason=trigger_reason,
                install_mode=install_mode,
            )

    # ------------------------------------------------------------------
    # Forced strategy
    # ------------------------------------------------------------------

    def _apply_forced_strategy(self, name: str) -> None:
        """Load the explicitly selected strategy folder for this match."""
        race_dir = self._skill_race_dir
        target_dir = os.path.join(race_dir, name)
        md_path = os.path.join(target_dir, "Top_agent_0.md")

        if not os.path.isdir(target_dir):
            raise FileNotFoundError(f"Strategy folder not found: {target_dir}")
        if not os.path.isfile(md_path):
            raise FileNotFoundError(f"Strategy file not found: {md_path}")

        try:
            with open(md_path, "r", encoding="utf-8") as f:
                raw = f.read()
            parsed = parse_top_agent_0_md(raw)
            detail = parsed.get("detail") or raw.strip()
        except Exception as exc:
            raise RuntimeError(f"Failed to read strategy file {md_path}: {exc}") from exc

        self.selected_strategy = name
        self.strategy_description = detail
        self._llm_infer_emit(
            f">>> STRATEGY: forced '{name}' "
            f"(description={len(detail)} chars)"
        )

        self._record_llm_interaction({
            "game_time": 0.0,
            "trigger_reason": "forced_strategy",
            "wall_elapsed_seconds": 0.0,
            "strategy": {
                "race": self.race_name,
                "selected_strategy": name,
                "strategy_description": detail,
            },
        })

    def _refresh_strategy_steps(self) -> None:
        self.strategy_steps = self._parse_strategy_steps(self.strategy_description)
        self._next_strategy_step_index = 0
        if self.strategy_steps:
            self._llm_infer_emit(
                f"    [strategy_steps] loaded {len(self.strategy_steps)} step(s) "
                f"from strategy '{self.selected_strategy or 'unknown'}'."
            )
        elif self.strategy_description.strip():
            self.strategy_steps = [{"number": 1, "text": self.strategy_description.strip()}]
            self._llm_infer_emit(
                "    [strategy_steps] no [Step N] markers found; using full strategy detail as one repeat step."
            )
        else:
            self._llm_infer_emit("    [strategy_steps] no strategy steps available.")

    @staticmethod
    def _parse_strategy_steps(text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        pattern = re.compile(r"(?is)\[Step\s*(\d+)\]\s*(.*?)(?=\n\s*\[Step\s*\d+\]|\Z)")
        steps: List[Dict[str, Any]] = []
        for match in pattern.finditer(text):
            step_text = " ".join(match.group(2).strip().split())
            if not step_text:
                continue
            steps.append({"number": int(match.group(1)), "text": step_text})
        return steps

    def _current_strategy_step(self) -> Optional[Dict[str, Any]]:
        if not self.strategy_steps:
            self._refresh_strategy_steps()
        if not self.strategy_steps:
            return None
        idx = min(self._next_strategy_step_index, len(self.strategy_steps) - 1)
        step = dict(self.strategy_steps[idx])
        step["index"] = idx
        step["is_last"] = idx >= len(self.strategy_steps) - 1
        return step

    def _advance_strategy_step_after_install(self, step: Dict[str, Any]) -> None:
        idx = int(step.get("index", self._next_strategy_step_index))
        if idx < len(self.strategy_steps) - 1:
            self._next_strategy_step_index = idx + 1
        else:
            self._next_strategy_step_index = len(self.strategy_steps) - 1

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

            self._llm_infer_emit("    calling Mid Agent (planning)...")
            s1_start = _wall_time.monotonic()
            mid_agent_text = self._call_mid_agent(
                obs_text,
                previous_natural_tasks,
            )
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

    # ------------------------------------------------------------------
    # 新五阶段增量驱动流水线（取代 Mid→Down→ActLLMOngoingTasks）
    # ------------------------------------------------------------------

    def _run_macro_pipeline_blocking(
        self,
        *,
        trigger_reason: str = "unknown",
        install_mode: str = "replace",
    ) -> None:
        """增量Agent → 命名Agent → 工具映射 → 排序Agent → Supply注入 → 调度器（同步阻塞）。"""
        game_time = self.time
        pipeline_start = _wall_time.monotonic()
        self._macro_cycle_count += 1

        obs_text: str = ""
        obs_snapshot: Optional[Dict[str, Any]] = None
        record: Dict[str, Any] = {
            "game_time": round(game_time, 2),
            "trigger_reason": trigger_reason,
            "cycle": self._macro_cycle_count,
            "top_agent_strategy": self.selected_strategy,
        }

        self._llm_infer_emit(
            f">>> MACRO PIPELINE START (trigger={trigger_reason}, cycle={self._macro_cycle_count}, "
            f"game_time={game_time:.1f}s)"
        )

        try:
            obs_text, obs_snapshot = self._capture_observation_bundle()
            record["observation_at_this_moment"] = obs_text
            record["observation_structured"] = obs_snapshot

            # 每次策略 step 下发前都把当前 obs 完整打印到 .log，便于回看 Stage2 识别依据。
            self._llm_infer_emit("    [Observation @ decision]")
            for _line in (obs_text or "").splitlines():
                self._llm_infer_emit(f"      {_line}")

            pending_summary = (
                self.scheduler.pending_summary_text() if self.scheduler else "  (empty)"
            )

            # ---------- Strategy step source ----------
            record["pending_actions_before_step"] = pending_summary
            current_step = self._current_strategy_step()
            if current_step is None:
                self._llm_infer_emit("    No strategy step available; skipping cycle.")
                record["error"] = "strategy_step_empty"
                return

            mode = install_mode
            plan_text = str(current_step["text"])
            record["mode"] = mode
            record["strategy_step"] = {
                "number": current_step.get("number"),
                "index": current_step.get("index"),
                "is_last": current_step.get("is_last"),
                "text": plan_text,
            }
            record["strategy_step_text"] = plan_text
            self._llm_infer_emit(
                f"    Strategy step {current_step.get('number')} "
                f"(index={current_step.get('index')}, repeat_last={current_step.get('is_last')}): "
                f"{plan_text}"
            )

            # ---------- 阶段2：命名 Agent（从一段计划中抽取标准名+数量）----------
            name_msgs = build_naming_messages(
                race=self.race_name,
                plan_text=plan_text,
                terran_unit_names=terran_unit_names(),
                terran_upgrade_names=terran_upgrade_names(),
                alias_pairs=ALIAS_MAP,
                obs_text=obs_text,
            )
            name_raw = self._call_llm(name_msgs, agent="naming")
            record["naming_raw"] = name_raw
            items = parse_naming_response(name_raw) or []
            # 别名归一 + 合法性校验
            valid_items: List[Dict[str, Any]] = []
            for it in items:
                canonical = resolve_alias(it["name"])
                if canonical == "SupplyDepot":
                    self._llm_infer_emit("    Stage2 dropped SupplyDepot (handled by Stage5).")
                    continue
                if is_known_terran_entity(canonical):
                    valid_items.append({"name": canonical, "count": it["count"]})
                else:
                    self._llm_infer_emit(f"    Stage2 dropped unknown entity: {it['name']!r}")
            record["named_items"] = valid_items
            self._llm_infer_emit(f"    Stage2 named items: {valid_items}")
            if not valid_items:
                record["error"] = "naming_empty"
                return

            # ---------- 阶段3：工具映射（无 LLM）----------
            qty_map: Dict[str, int] = {}
            for it in valid_items:
                action_name = self._primary_action_for_entity(it["name"])
                if action_name is None:
                    self._llm_infer_emit(f"    Stage3 no action for entity {it['name']!r}")
                    continue
                qty_map[action_name] = qty_map.get(action_name, 0) + int(it["count"])
            actions = list(qty_map.keys())
            record["mapped_actions"] = qty_map
            self._llm_infer_emit(f"    Stage3 mapped actions: {qty_map}")
            if not actions:
                record["error"] = "mapping_empty"
                return
            # 按数量展开成扁平列表（同一 action 重复 count 次）；Stage4 直接排这个
            # 扁平列表，无需再带 xN。
            expanded_actions: List[str] = []
            for name in actions:
                expanded_actions.extend([name] * max(int(qty_map.get(name, 1)), 1))
            record["expanded_actions"] = list(expanded_actions)
            self._llm_infer_emit(f"    Stage3 expanded: {expanded_actions}")

            # ---------- 阶段4：排序 Agent（带前置/冲突/成本提示）----------
            entities = collect_obs_entities(self)
            prereq_hints = self._build_prereq_hints(entities, actions)
            conflict_hints = self._build_conflict_hints(actions)
            cost_hints = self._build_cost_hints(actions)
            order_msgs = build_ordering_messages(
                race=self.race_name,
                actions=expanded_actions,
                obs_text=obs_text,
                prereq_hints=prereq_hints,
                conflict_hints=conflict_hints,
                cost_hints=cost_hints,
                strategy_step_text=plan_text,
            )
            order_raw = self._call_llm(order_msgs, agent="ordering")
            record["ordering_raw"] = order_raw
            llm_ordered = parse_ordering_response(order_raw, legal_actions=set(actions)) or []
            # 多重集对账：以 Stage3 的展开列表为准，按 LLM 给出的顺序消费配额，
            # 多出的丢弃、缺的按原始顺序补在末尾。保证每个 action 出现次数 = qty_map。
            ordered = self._reconcile_multiset(llm_ordered, expanded_actions)
            if not ordered:
                ordered = list(expanded_actions)
                self._llm_infer_emit("    Stage4 ordering invalid; falling back to expanded order.")
            record["ordered_actions"] = ordered
            self._llm_infer_emit(f"    Stage4 ordered: {ordered}")

            # ---------- 阶段5：Supply 注入（无 LLM）----------
            ordered_with_supply, supply_trace = plan_supply_with_trace(
                ordered, self, threshold=self.SUPPLY_THRESHOLD
            )
            record["ordered_with_supply"] = ordered_with_supply
            record["supply_trace"] = supply_trace
            self._llm_infer_emit(f"    Stage5 with supply: {ordered_with_supply}")
            self._llm_infer_emit("    Stage5 supply derivation:")
            for _tl in supply_trace:
                self._llm_infer_emit(f"      {_tl}")

            # ---------- 装入调度器 ----------
            # 把扁平列表按「连续相同」折叠回 (name, count) 交给调度器：
            # build/research 用绝对 to_count，需要单条带数量；train 折叠成连发也正确。
            pairs: List[Tuple[str, int]] = self._collapse_runs(ordered_with_supply)
            # prefetch ???append ??????????????????
            # ? set_actions ? P1 ??????????3.3 / ?11.5??
            if mode == "append" and self.scheduler is not None:
                pairs = self._guard_prefetch_build_quantity(pairs)
            if self.scheduler is not None:
                self.scheduler.set_actions(pairs, mode=mode)
            self._advance_strategy_step_after_install(current_step)
            record["next_strategy_step_index"] = self._next_strategy_step_index
            record["installed_pairs"] = pairs
            self._llm_infer_emit(
                f"    Installed {len(pairs)} planned actions into scheduler (mode={mode}): "
                + str([f"{n} x{q}" for n, q in pairs])
            )
        except Exception as exc:
            record["error"] = repr(exc)
            self._llm_infer_emit(f"    MACRO PIPELINE EXCEPTION: {exc!r}")
            logger.warning("[UniversalLLMBot] Macro pipeline failed: %s", exc)
        finally:
            record["wall_elapsed_seconds"] = round(_wall_time.monotonic() - pipeline_start, 3)
            self._record_llm_interaction(record)
            self._llm_infer_emit(
                f"<<< MACRO PIPELINE END (total {record['wall_elapsed_seconds']:.2f}s wall)"
            )

    # --- 阶段4 多重集对账 / 折叠 ---------------------------------------

    # --- prefetch guard (P2) -----------------------------------------------

    def _guard_prefetch_build_quantity(
        self, pairs: List[Tuple[str, int]]
    ) -> List[Tuple[str, int]]:
        """When prefetch-append occurs while existing same-type builds are
        not yet DONE, skip redundant new entries to avoid stacking independent
        PlannedActions (sec 3.3 / 11.5). The P1 merge in set_actions handles
        quantity boost at the queue level."""
        from SC2_Agent.execution import mapping

        active_build_types: set = set()
        active_names: set = set()
        for a in self.scheduler.actions:
            if a.is_terminal():
                continue
            if a.category in (mapping.CAT_BUILD, mapping.CAT_ADDON):
                active_names.add(a.action_name)
                unit_type = mapping.unit_type_for(a.target_result or "")
                if unit_type is not None:
                    active_build_types.add(unit_type)

        if not active_names:
            return pairs

        adjusted: List[Tuple[str, int]] = []
        for name, qty in pairs:
            unit_type = mapping.unit_type_for(name)
            if unit_type is not None and unit_type in active_build_types:
                continue
            if name in active_names:
                continue
            adjusted.append((name, qty))
        return adjusted


    @staticmethod
    def _reconcile_multiset(
        llm_ordered: List[str], expanded: List[str]
    ) -> List[str]:
        """以 ``expanded`` 为准的多重集，按 ``llm_ordered`` 的顺序输出。

        - LLM 给出的顺序逐个消费 ``expanded`` 的配额（出现次数）；
        - 超过配额的重复项丢弃；非法/不在配额内的忽略；
        - 最后把 LLM 漏掉的（配额未消费完的）按 ``expanded`` 原顺序补在末尾。
        这样保证每个 action 的出现次数严格等于 Stage3 展开的数量。
        """
        from collections import Counter

        remaining = Counter(expanded)
        out: List[str] = []
        for name in llm_ordered:
            if remaining.get(name, 0) > 0:
                out.append(name)
                remaining[name] -= 1
        # 补齐被漏掉的（保持原始展开顺序）
        for name in expanded:
            if remaining.get(name, 0) > 0:
                out.append(name)
                remaining[name] -= 1
        return out

    @staticmethod
    def _collapse_runs(flat: List[str]) -> List[Tuple[str, int]]:
        """????????????????????????????????"""
        from collections import Counter

        merged = Counter(flat)
        # ?? flat ???????
        pairs: List[Tuple[str, int]] = []
        seen: set = set()
        for name in flat:
            if name not in seen:
                seen.add(name)
                pairs.append((name, merged[name]))
        return pairs
    def _primary_action_for_entity(self, entity_name: str) -> Optional[str]:
        """把一个标准实体名映射到「主」Action 标准名（优先 Build/Train/Research/Morph）。"""
        try:
            mapping = actions_for_entities([entity_name], executor_race="Terran")
        except Exception as exc:
            logger.debug("actions_for_entities failed for %s: %s", entity_name, exc)
            return None
        entries = mapping.get(entity_name) or []
        if not entries:
            return None

        def rank(entry: Dict[str, Any]) -> Tuple[int, str]:
            kind = entry.get("target_kind") or ""
            order = {"Build": 0, "BuildOnUnit": 0, "BuildInstant": 0, "Train": 1, "Research": 2, "Morph": 3}
            return order.get(kind, 9), entry.get("ability_name") or ""

        entries = sorted(entries, key=rank)
        return entries[0].get("ability_name")

    # --- 阶段4 提示构造 ------------------------------------------------

    def _build_prereq_hints(self, entities: List[str], actions: List[str]) -> str:
        lines: List[str] = []
        try:
            result = check_action_prerequisites(entities, actions)
            for rep in result.get("ordered_reports", []):
                missing = rep.get("missing_requirements") or []
                if missing:
                    names = ", ".join(m.get("entity_name", "?") for m in missing)
                    lines.append(f"  - {rep['ability_name']} still missing: {names}")
        except Exception as exc:
            logger.debug("prereq hints failed: %s", exc)
        try:
            for rel in tech_chain_relations(actions):
                lines.append(
                    f"  - {rel['action']} requires {rel['depends_on']} first (via {rel['via_entity']})"
                )
        except Exception as exc:
            logger.debug("tech_chain_relations failed: %s", exc)
        return "\n".join(lines)

    def _build_conflict_hints(self, actions: List[str]) -> str:
        lines: List[str] = []
        try:
            result = detect_action_conflicts(actions)
            for c in result.get("conflicts", []):
                a, b = c["actions"]
                shared = ", ".join(c.get("shared_resources", []))
                lines.append(f"  - {a} and {b} share producer(s): {shared}")
        except Exception as exc:
            logger.debug("conflict hints failed: %s", exc)
        return "\n".join(lines)

    def _build_cost_hints(self, actions: List[str]) -> str:
        lines: List[str] = []
        for a in actions:
            try:
                info = cost_for_action(a)
                cost = info.get("cost") or {}
                frames = float(cost.get("time", 0) or 0)
                seconds = frames / 22.4 if frames else 0.0
                lines.append(
                    f"  - {a}: minerals {cost.get('minerals', 0)}, gas {cost.get('gas', 0)}, "
                    f"supply {cost.get('supply', 0)}, ~{seconds:.0f}s"
                )
            except Exception as exc:
                logger.debug("cost hint failed for %s: %s", a, exc)
        return "\n".join(lines)

    # --- 执行者 Agent 回调（注入调度器，仅 train/addon/morph 多候选时调用）------

    def _executor_llm_select(
        self,
        *,
        ability_name: str,
        candidate_text: str,
        cost_hint: str,
        pending_summary: str,
        waiting_summary: str,
        conflict_hints: str,
        legal_tags: Optional[set] = None,
        tag_map: Optional[Dict[int, int]] = None,
    ) -> Optional[int]:
        """供 ``ExecutionScheduler`` 调用：用执行者 Agent 从候选中挑一个 tag。"""
        try:
            msgs = build_executor_messages(
                ability_name=ability_name,
                candidate_units_text=candidate_text,
                cost_hint=cost_hint,
                pending_actions_summary=pending_summary,
                waiting_actions_summary=waiting_summary,
                executor_conflict_hints=conflict_hints,
            )
            raw = self._call_llm(msgs, agent="executor")
            return parse_executor_response(raw, legal_tags=legal_tags, tag_map=tag_map)
        except Exception as exc:
            logger.debug("executor LLM select failed for %s: %s", ability_name, exc)
            return None

    # --- 调用封装 ------------------------------------------------------

    def _call_llm(self, messages: List[Dict[str, str]], agent: str = "mid") -> str:
        """根据 agent 类型选择对应的 model_key 调用 LLM。"""
        key_map = {
            "mid": self.mid_model_key,
            "down": self.down_model_key,
            "naming": self.naming_model_key,
            "ordering": self.ordering_model_key,
            "executor": self.executor_model_key,
        }
        model_key = key_map.get(agent, "")

        if not model_key:
            logger.warning(
                "[UniversalLLMBot] No model_key for agent=%r; set top/mid/down_model_key.",
                agent,
            )
            return ""

        response = call_openai(messages=messages, model_key=model_key)
        self._record_llm_call(agent=agent, model_key=model_key, messages=messages, output=response)
        return response

    def _record_llm_call(
        self,
        *,
        agent: str,
        model_key: str,
        messages: List[Dict[str, str]],
        output: str,
    ) -> None:
        """把一次 LLM 调用的 prompt 与 output 追加到内存，并增量落盘到专门 JSON。"""
        self._llm_call_seq += 1
        try:
            game_time = round(float(getattr(self, "time", 0.0)), 2)
        except Exception:
            game_time = None
        self._llm_call_records.append(
            {
                "seq": self._llm_call_seq,
                "game_time": game_time,
                "macro_cycle": self._macro_cycle_count,
                "agent": agent,
                "model_key": model_key,
                "prompt": list(messages),
                "output": output,
            }
        )
        # 增量落盘：即便对局中途被 kill，也能保留已发生的调用记录。
        self._flush_llm_call_log()

    def _llm_call_log_path(self) -> Optional[str]:
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return None
        try:
            base = recorder._resolve_output_path()
        except Exception:
            return None
        root, _ext = os.path.splitext(base)
        return root + ".llm_calls.json"

    def _flush_llm_call_log(self) -> None:
        path = self._llm_call_log_path()
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {
                "match": os.path.basename(path)[: -len(".llm_calls.json")],
                "llm_call_count": len(self._llm_call_records),
                "calls": self._llm_call_records,
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.debug("[UniversalLLMBot] failed to flush llm_calls log: %s", exc)

    def _call_mid_agent(
        self,
        obs_text: str,
        previous_tasks: List[str],
    ) -> str:
        """构造 Mid Agent Prompt 并调用 LLM。"""
        messages = build_planning_messages(
            race=self.race_name,
            obs_text=obs_text,
            previous_tasks=previous_tasks,
            strategy_description=self.strategy_description,
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
            action_key = task.get("action")
            priority = bool(task.get("priority", False))
            if priority and action_key and self._action_supports_priority_fn is not None:
                if not self._action_supports_priority_fn(action_key):
                    logger.warning(
                        "Stripping priority for action %r (does not support priority).",
                        action_key,
                    )
                    priority = False
            self.active_tasks.append({
                "sequence": idx,
                "action": action_key,
                "to_count": task.get("to_count"),
                "priority": priority,
                "_get_action_fn": self._get_action_fn,
            })

    @staticmethod
    def _slim_action(task: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if task is None:
            return None
        slim = {
            "action": task.get("action"),
            "to_count": task.get("to_count"),
            "priority": bool(task.get("priority", False)),
        }
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
                "priority": bool(task.get("priority", False)),
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
        # During pre-start strategy loading, ``self.knowledge`` is not
        # initialized yet, so fall back to ``sc2.main.logger``.
        #   游戏 ``.log`` 文件的 loguru sink 用 ``filter="sharpy"`` 筛选，因此我们
        #   通过 ``logger.patch`` 把 record name 改成以 ``sharpy.`` 开头，保证
        #   pre-start logs also land in the same match ``.log`` file.
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
        """组装 BuildOrder：命令式执行调度器 + 通用后台战术 + 按 skill 注入的侦察/攻击。

        三条并行轨：
        * ``ExecutionScheduler``：增量流水线产出的命令式 Action 序列的执行器。
        * ``BackgroundTactics``：策略无关、只含不耗矿/气/supply 的运营 + 防守工具。
        * scout/attack 战术：**按所选 skill 注入**——优先用
          ``SKILL/{race}/{strategy}/scout_attack.py``，缺省回退
          ``SKILL/{race}/scout_attack_default.py``。这样不同策略可定义各自的
          侦察与进攻触发逻辑。
        """
        self.scheduler = ExecutionScheduler(wait_abandon_sec=self.WAIT_ABANDON_SEC)
        # 注入执行者 Agent 回调（train/addon/morph 多候选时由 LLM 选执行单位）。
        self.scheduler.executor_llm = self._executor_llm_select

        background = self._load_background_tactics()
        scout_attack = self._load_scout_attack_tactics()

        return BuildOrder([
            self.scheduler,
            background,
            scout_attack,
        ])

    def _load_background_tactics(self) -> BuildOrder:
        """加载策略无关的后台战术（运营 + 防守，无资源消耗）。失败回退 EmptyTactics。"""
        try:
            from SKILL.terran.background_tactics import BackgroundTactics

            return BackgroundTactics()
        except Exception as exc:
            logger.warning("Failed to load BackgroundTactics; using EmptyTactics: %s", exc)
            return EmptyTactics()

    def _load_scout_attack_tactics(self) -> BuildOrder:
        """按所选 skill 注入侦察/攻击战术。

        查找顺序：
        1. ``SKILL.{race}.{selected_strategy}.scout_attack`` —— skill 自定义；
        2. ``SKILL.{race}.scout_attack_default`` —— 通用兜底；
        3. ``EmptyTactics`` —— 兜底失败。

        模块中第一个 ``BuildOrder`` / ``SequentialList`` 子类（非基类本身）即视为
        侦察/攻击战术执行器。
        """
        candidates: List[str] = []
        if self.selected_strategy:
            candidates.append(f"SKILL.{self.race_name}.{self.selected_strategy}.scout_attack")
        candidates.append(f"SKILL.{self.race_name}.scout_attack_default")

        for module_path in candidates:
            tactics = self._instantiate_tactics_from_module(module_path)
            if tactics is not None:
                self._llm_infer_emit(f"    [ScoutAttack] loaded from {module_path}")
                return tactics
        logger.warning("No scout/attack tactics found; using EmptyTactics.")
        return EmptyTactics()

    def _instantiate_tactics_from_module(self, module_path: str) -> Optional[BuildOrder]:
        """import 模块并实例化其中第一个 BuildOrder/SequentialList 子类。失败返回 None。"""
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            return None
        except Exception as exc:
            logger.warning("Error importing tactics module %s: %s", module_path, exc)
            return None

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and (
                (issubclass(attr, BuildOrder) and attr is not BuildOrder)
                or (issubclass(attr, SequentialList) and attr is not SequentialList)
            ):
                try:
                    return attr()
                except TypeError:
                    try:
                        return attr(20)
                    except Exception as exc:
                        logger.warning("Failed to instantiate %s: %s", attr_name, exc)
        return None


# ----------------------------------------------------------------------
# Ladder 兼容入口
# ----------------------------------------------------------------------


class LadderBot(UniversalLLMBot):
    @property
    def my_race(self):
        return _RACE_MAP.get(self.race_name, Race.Terran)
