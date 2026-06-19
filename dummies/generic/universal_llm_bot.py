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
from typing import Any, Dict, List, Optional, Set, Tuple

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId

from sharpy.interfaces import IZoneManager
from sharpy.knowledges import KnowledgeBot
from sharpy.managers.core.manager_base import ManagerBase
from sharpy.managers.extensions import BuildDetector
from sharpy.plans import BuildOrder, SequentialList

from API_Tools.llm_caller import call_openai
from SC2_Agent.top_agent import parse_top_agent_0_md

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
    SUPPLY_DEPOT_ACTION,
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
    """Forced-strategy LLM bot using the five-stage macro pipeline."""

    #: 宏观决策触发周期（秒）：每隔该时间或 Action 序列执行完触发一次增量流水线。
    MACRO_POLL_INTERVAL: float = 60.0
    #: 序列执行完后再次触发的最小间隔（秒），避免空序列时每帧反复触发 LLM。
    MACRO_MIN_RETRIGGER: float = 5.0
    #: Waiting actions are abandoned after this many game seconds (0 = never).
    WAIT_ABANDON_SEC: float = 60.0
    #: Supply 注入阈值：保证模拟人口余量不低于该值。
    SUPPLY_THRESHOLD: float = 8.0
    #: 实验参数：True = 从 LLM 输出中提取 depot 并通过算法重新插入（稳定托管）；
    #: False = 直接执行 LLM 排好的序列（含 LLM 自行放置的 depot，实验性）。
    SUPPLY_MANAGED: bool = True
    zone_manager: IZoneManager

    def __init__(
        self,
        race_name: str = "terran",
        record_dir: str = "",
        *,
        naming_model_key: str = "",
        ordering_model_key: str = "",
        executor_model_key: str = "",
        force_strategy: Optional[str] = None,
    ):
        super().__init__("Universal LLM Bot")
        self.race_name = race_name.strip().lower()
        self.naming_model_key = naming_model_key.strip()
        self.ordering_model_key = ordering_model_key.strip()
        self.executor_model_key = executor_model_key.strip()
        self.record_dir = record_dir.strip()
        force = (force_strategy or "").strip()
        self.force_strategy: Optional[str] = force if force and force.lower() != "none" else None

        # --- Forced strategy state ---
        self.selected_strategy: Optional[str] = None
        self.strategy_description: str = ""
        self.strategy_summary: str = ""
        self.strategy_steps: List[Dict[str, Any]] = []
        self._next_strategy_step_index: int = 0

        # --- 命令式执行调度器 ---
        self.scheduler: Optional[ExecutionScheduler] = None
        self._last_macro_time: float = -self.MACRO_POLL_INTERVAL
        self._macro_cycle_count: int = 0
        # 记录每一次 LLM 调用的完整 prompt 与 output，落到专门的 *.llm_calls.json。
        self._llm_call_records: List[Dict[str, Any]] = []
        self._llm_call_seq: int = 0

        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

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

        触发机制（全程 append，不再使用 replace）。``ExecutionScheduler`` 已
        将 ``WAITING`` 动作放在独立的 ``waiter`` 槽中；宏触发会同时考虑
        ``actions`` 列表和 ``waiter``，避免关键等待动作被下一步掩盖：

        1. 首次且 ``actions`` 列表为空 → ``initial_step``。
        2. ``actions`` 列表全部 terminal → ``sequence_drained`` 装下一步。
        3. ``actions`` 中无可执行动作（只剩 deferred）→ ``executable_drained``。
        """
        since = self.time - self._last_macro_time
        trigger_reason: Optional[str] = None

        scheduler_drained = self.scheduler is None or self.scheduler.is_drained()
        macro_drained = self.scheduler is None or self.scheduler.is_drained_for_macro()
        executable_drained = (
            self.scheduler is not None and self.scheduler.has_no_executable_actions()
        )

        if self._macro_cycle_count == 0 and scheduler_drained:
            trigger_reason = "initial_step"
        elif since >= self.MACRO_MIN_RETRIGGER and scheduler_drained:
            trigger_reason = "sequence_drained"
        elif since >= self.MACRO_MIN_RETRIGGER and (macro_drained or executable_drained):
            trigger_reason = "executable_drained"

        if trigger_reason is not None:
            self._last_macro_time = self.time
            self._run_macro_pipeline_blocking(
                trigger_reason=trigger_reason,
                install_mode="append",
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
            summary = (parsed.get("summary") or "").strip()
        except Exception as exc:
            raise RuntimeError(f"Failed to read strategy file {md_path}: {exc}") from exc

        self.selected_strategy = name
        self.strategy_description = detail
        self.strategy_summary = summary
        self._llm_infer_emit(
            f">>> STRATEGY: forced '{name}' "
            f"(description={len(detail)} chars, summary={len(summary)} chars)"
        )

        self._record_llm_interaction({
            "game_time": 0.0,
            "trigger_reason": "forced_strategy",
            "wall_elapsed_seconds": 0.0,
            "strategy": {
                "race": self.race_name,
                "selected_strategy": name,
                "strategy_description": detail,
                "strategy_summary": summary,
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
        total = len(self.strategy_steps)
        idx = min(self._next_strategy_step_index, total - 1)
        step = dict(self.strategy_steps[idx])
        step["index"] = idx
        step["is_last"] = idx >= total - 1
        step["phase"] = "step"
        return step

    def _advance_strategy_step_after_install(self, step: Dict[str, Any]) -> None:
        idx = int(step.get("index", self._next_strategy_step_index))
        total = len(self.strategy_steps)
        if total <= 0:
            return
        if idx >= total - 1:
            # 到达最后一个 step 后不再推进，后续 cycle 复用同一份 step 文本。
            self._next_strategy_step_index = total - 1
        else:
            self._next_strategy_step_index = idx + 1

    # ------------------------------------------------------------------
    # 五阶段增量驱动流水线
    # ------------------------------------------------------------------

    def _run_macro_pipeline_blocking(
        self,
        *,
        trigger_reason: str = "unknown",
        install_mode: str = "append",
    ) -> None:
        """增量Agent → 命名Agent → 工具映射 → 排序Agent → Supply注入 → 调度器（同步阻塞）。

        All step transitions use ``mode="append"`` so that waiting/deferred
        actions from prior steps are never dropped.
        """
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
            phase = current_step.get("phase", "step")
            record["strategy_step"] = {
                "number": current_step.get("number"),
                "index": current_step.get("index"),
                "is_last": current_step.get("is_last"),
                "phase": phase,
                "text": plan_text,
            }
            record["strategy_step_text"] = plan_text
            self._llm_infer_emit(
                f"    Strategy step {current_step.get('number')} "
                f"(index={current_step.get('index')}, is_last={current_step.get('is_last')}): "
                f"{plan_text}"
            )

            # ---------- 阶段2：命名 Agent（从一段计划中抽取标准名+数量）----------
            name_msgs = build_naming_messages(
                race=self.race_name,
                plan_text=plan_text,
                terran_unit_names=terran_unit_names(),
                terran_upgrade_names=terran_upgrade_names(),
                obs_text=obs_text,
                strategy_summary=self.strategy_summary,
            )
            name_raw = self._call_llm(name_msgs, agent="naming")
            record["naming_raw"] = name_raw
            items = parse_naming_response(name_raw) or []
            # 别名归一 + 合法性校验；SupplyDepot 与其他实体一样正常流入 valid_items
            valid_items: List[Dict[str, Any]] = []
            macro_normalizations: List[Dict[str, str]] = []
            for it in items:
                raw_name = str(it.get("name", ""))
                resolved = self._generic_addon_from_raw(raw_name) or resolve_alias(raw_name)
                canonical = self._normalize_macro_entity_name(resolved, plan_text)
                if canonical != resolved:
                    macro_normalizations.append({
                        "raw": raw_name,
                        "resolved": resolved,
                        "normalized": canonical,
                    })
                    self._llm_infer_emit(
                        f"    Stage2 normalized macro entity {resolved!r} -> {canonical!r}."
                    )
                if is_known_terran_entity(canonical):
                    valid_items.append({"name": canonical, "count": it["count"]})
                else:
                    self._llm_infer_emit(f"    Stage2 dropped unknown entity: {it['name']!r}")
            record["macro_normalizations"] = macro_normalizations
            record["named_items"] = valid_items
            self._llm_infer_emit(f"    Stage2 named items: {valid_items}")
            if not valid_items:
                if self._is_supply_depot_step(plan_text):
                    self._llm_infer_emit(
                        "    Stage2 found no remaining depot demand; advancing supply-only step."
                    )
                    self._advance_strategy_step_after_install(current_step)
                    record["next_strategy_step_index"] = self._next_strategy_step_index
                    record["installed_pairs"] = []
                    record["scheduler_active_after_install"] = (
                        [
                            a.short_label()
                            for a in self.scheduler.all_planned_actions()
                            if not a.is_terminal()
                        ]
                        if self.scheduler is not None
                        else []
                    )
                    return
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
                strategy_summary=self.strategy_summary,
            )
            order_raw = self._call_llm(order_msgs, agent="ordering")
            record["ordering_raw"] = order_raw
            llm_ordered = parse_ordering_response(order_raw, legal_actions=set(actions)) or []
            ordered, ordering_gaps = self._filter_llm_ordered_actions(
                llm_ordered, expanded_actions
            )
            record["ordering_gaps"] = ordering_gaps
            if ordering_gaps:
                self._llm_infer_emit(f"    Stage4 ordering gaps: {ordering_gaps}")
            if not ordered:
                record["error"] = "ordering_empty"
                self._llm_infer_emit(
                    "    Stage4 ordering invalid; no fallback installed."
                )
                return
            record["ordered_actions"] = ordered
            self._llm_infer_emit(f"    Stage4 ordered: {ordered}")

            # ---------- 阶段5：Supply 处理 ----------
            record["supply_managed"] = self.SUPPLY_MANAGED
            llm_depot_count = ordered.count(SUPPLY_DEPOT_ACTION)
            record["llm_depot_count"] = llm_depot_count

            if self.SUPPLY_MANAGED:
                # 提取 LLM 输出中的 depot，用 Supply Planner 算法重新插入
                non_supply = [a for a in ordered if a != SUPPLY_DEPOT_ACTION]
                ordered_with_supply, supply_trace = plan_supply_with_trace(
                    non_supply, self, threshold=self.SUPPLY_THRESHOLD
                )
                algo_depot_count = ordered_with_supply.count(SUPPLY_DEPOT_ACTION)
                record["algo_depot_count"] = algo_depot_count
                self._llm_infer_emit(
                    f"    Stage5 SUPPLY_MANAGED=True: LLM placed {llm_depot_count} depot(s), "
                    f"algo placed {algo_depot_count} depot(s)"
                )
            else:
                # 直接使用 LLM 排好的序列（含 LLM 自行放置的 depot）
                ordered_with_supply = ordered
                supply_trace = [
                    f"SUPPLY_MANAGED=False: using LLM supply placement directly "
                    f"({llm_depot_count} depot(s) placed by LLM)"
                ]
                self._llm_infer_emit(
                    f"    Stage5 SUPPLY_MANAGED=False: using LLM ordering as-is "
                    f"({llm_depot_count} depot(s))"
                )

            record["ordered_with_supply"] = ordered_with_supply
            record["supply_trace"] = supply_trace
            self._llm_infer_emit(f"    Stage5 with supply: {ordered_with_supply}")
            self._llm_infer_emit("    Stage5 supply derivation:")
            for _tl in supply_trace:
                self._llm_infer_emit(f"      {_tl}")

            # ---------- 装入调度器 ----------
            # 每个 action 保持 qty=1 的扁平列表，保留 LLM 排出的执行顺序。
            pairs: List[Tuple[str, int]] = self._collapse_runs(ordered_with_supply)
            if self.scheduler is not None:
                pairs = self._guard_prefetch_build_quantity(pairs)
            if self.scheduler is not None:
                self.scheduler.set_actions(pairs, mode=mode)
                scheduler_active = [
                    a.short_label()
                    for a in self.scheduler.all_planned_actions()
                    if not a.is_terminal()
                ]
            else:
                scheduler_active = []
            self._advance_strategy_step_after_install(current_step)
            record["next_strategy_step_index"] = self._next_strategy_step_index
            record["installed_pairs"] = pairs
            record["scheduler_active_after_install"] = scheduler_active
            self._llm_infer_emit(
                f"    Requested {len(pairs)} planned action(s) for scheduler (mode={mode}): "
                + str([f"{n} x{q}" for n, q in pairs])
            )
            self._llm_infer_emit(
                f"    Scheduler active queue after install: {scheduler_active}"
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
        """Compatibility hook; scheduler now defers conflicting builds itself."""
        return pairs

    @staticmethod
    def _addon_host_from_plan(plan_text: str, addon_kind: str = "") -> str:
        text = (plan_text or "").lower()
        host_patterns = (
            ("Starport", r"\bstar\s*ports?\b|\bstarports?\b"),
            ("Factory", r"\bfactor(?:y|ies)\b"),
            ("Barracks", r"\bbarracks\b|\brax\b"),
        )
        addon_patterns = {
            "TechLab": r"\btech\s*labs?\b|\btechlabs?\b",
            "Reactor": r"\breactors?\b",
        }
        addon_pattern = addon_patterns.get(addon_kind)
        if addon_pattern:
            best: Optional[Tuple[int, str]] = None
            for addon_match in re.finditer(addon_pattern, text):
                start = max(0, addon_match.start() - 64)
                end = min(len(text), addon_match.end() + 64)
                window = text[start:end]
                for host, pattern in host_patterns:
                    for host_match in re.finditer(pattern, window):
                        distance = abs((start + host_match.start()) - addon_match.start())
                        if best is None or distance < best[0]:
                            best = (distance, host)
            if best is not None:
                return best[1]

        hosts = [host for host, pattern in host_patterns if re.search(pattern, text)]
        if len(hosts) == 1:
            return hosts[0]
        return "Barracks"

    @staticmethod
    def _generic_addon_from_raw(name: str) -> Optional[str]:
        normalized = re.sub(r"[\s_-]+", "", (name or "").lower())
        if normalized == "techlab":
            return "TechLab"
        if normalized == "reactor":
            return "Reactor"
        return None

    @classmethod
    def _normalize_macro_entity_name(cls, canonical: str, plan_text: str) -> str:
        """Convert alternate unit states into producible macro targets."""
        alternate_forms = {
            "SupplyDepotLowered": "SupplyDepot",
            "BarracksFlying": "Barracks",
            "FactoryFlying": "Factory",
            "StarportFlying": "Starport",
            "CommandCenterFlying": "CommandCenter",
            "OrbitalCommandFlying": "OrbitalCommand",
            "SiegeTankSieged": "SiegeTank",
        }
        if canonical in alternate_forms:
            return alternate_forms[canonical]

        host = cls._addon_host_from_plan(plan_text, canonical)
        generic_addons = {
            "TechLab": f"{host}TechLab",
            "Reactor": f"{host}Reactor",
        }
        return generic_addons.get(canonical, canonical)

    @staticmethod
    def _is_supply_depot_step(plan_text: str) -> bool:
        """True for strategy steps that only ask for supply-depot headroom."""
        text = (plan_text or "").lower()
        if not re.search(r"\bsupply\s*depots?(?:lowered)?\b|\bdepots?\b", text):
            return False
        non_supply_terms = (
            "scv",
            "worker",
            "marine",
            "marauder",
            "siege tank",
            "tank",
            "barracks",
            "factory",
            "refinery",
            "command center",
            "orbital",
            "starport",
            "tech lab",
            "reactor",
            "research",
            "train",
            "expand",
            "expansion",
        )
        return not any(term in text for term in non_supply_terms)

    @staticmethod
    def _filter_llm_ordered_actions(
        llm_ordered: List[str], expanded: List[str]
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Keep only the legal actions the ordering LLM actually returned."""
        from collections import Counter

        remaining = Counter(expanded)
        out: List[str] = []
        dropped: List[str] = []
        for name in llm_ordered:
            if remaining.get(name, 0) > 0:
                out.append(name)
                remaining[name] -= 1
            else:
                dropped.append(name)
        missing = []
        for name, count in remaining.items():
            if count > 0:
                missing.append({"action": name, "count": count})
        gaps = {
            "missing": missing,
            "dropped": dropped,
        }
        return out, gaps

    @staticmethod
    def _collapse_runs(flat: List[str]) -> List[Tuple[str, int]]:
        """Keep every action as an individual (name, 1) entry preserving LLM order."""
        return [(name, 1) for name in flat]
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

    def _call_llm(self, messages: List[Dict[str, str]], agent: str = "naming") -> str:
        """根据 agent 类型选择对应的 model_key 调用 LLM。"""
        key_map = {
            "naming": self.naming_model_key,
            "ordering": self.ordering_model_key,
            "executor": self.executor_model_key,
        }
        model_key = key_map.get(agent, "")

        if not model_key:
            logger.warning(
                "[UniversalLLMBot] No model_key for agent=%r; set naming/ordering/executor model key.",
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

    def _llm_infer_emit(self, message: str, *args) -> None:
        if args:
            message = message % args
        line = f"[UniversalLLMBot][LLM-INFER] {message}"

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
        """组装 BuildOrder：命令式执行调度器 + 当前策略自己的免费工具包。

        两条并行轨：
        * ``ExecutionScheduler``：增量流水线产出的命令式 Action 序列的执行器。
        * strategy tools：按所选策略只加载
          ``SKILL/{race}/{strategy}/strategy_tools.py``。该文件必须只包含不耗
          minerals / gas / supply 的工具，避免和 ``ExecutionScheduler`` 抢资源。
        """
        self.scheduler = ExecutionScheduler(wait_abandon_sec=self.WAIT_ABANDON_SEC)
        # 注入执行者 Agent 回调（train/addon/morph 多候选时由 LLM 选执行单位）。
        self.scheduler.executor_llm = self._executor_llm_select

        strategy_tools = self._load_strategy_tools()

        return BuildOrder([
            self.scheduler,
            strategy_tools,
        ])

    def _load_strategy_tools(self) -> BuildOrder:
        """Load the selected strategy's resource-free tool package.

        Only ``SKILL.{race}.{selected_strategy}.strategy_tools`` is loaded. There is
        no global tactic fallback here: if a strategy is selected, its own Python
        file is the sole source of non-resource tools for that match.
        """
        if self.selected_strategy:
            module_path = f"SKILL.{self.race_name}.{self.selected_strategy}.strategy_tools"
            tactics = self._instantiate_tactics_from_module(module_path)
            if tactics is not None:
                self._llm_infer_emit(f"    [StrategyTools] loaded from {module_path}")
                return tactics
        logger.warning("No strategy tool package found; using EmptyTactics.")
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
