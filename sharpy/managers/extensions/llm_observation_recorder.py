"""LLM Observation Recorder.

This manager periodically captures the structured game state (every
``interval_seconds`` of in-game time) and produces a paired English text
summary that is suitable for feeding into an LLM. All snapshots are buffered
in memory and persisted as a single JSON file when the game ends, so that
in-game I/O does not stall step execution.

The recorder is built around four concerns kept strictly decoupled:

1. Timing control - ``last_recorded_time`` plus a step-size check.
2. Modular extractors - one method per data domain returning a plain ``Dict``.
3. Dual-state formatting - a master snapshot dict, plus a text observation
   derived only from that dict.
4. Persistence - everything is buffered and written once in ``on_end``.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from sc2.data import Race, Result
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from sharpy.managers.core.manager_base import ManagerBase

if TYPE_CHECKING:
    from sharpy.knowledges import Knowledge


DEFAULT_INTERVAL_SECONDS: float = 20.0
DEFAULT_OUTPUT_FOLDER: str = "games"
DEFAULT_FILENAME_PREFIX: str = "Replay"

# Short race tags used inside the auto-generated file name (PvT, ZvP, ...).
_RACE_SHORT: Dict[Race, str] = {
    Race.Protoss: "P",
    Race.Terran: "T",
    Race.Zerg: "Z",
    Race.Random: "R",
}

# Worker types per race, used to scope "workers en route" detection.
_WORKER_TYPES: Dict[Race, UnitTypeId] = {
    Race.Protoss: UnitTypeId.PROBE,
    Race.Terran: UnitTypeId.SCV,
    Race.Zerg: UnitTypeId.DRONE,
}

# Vespene geyser type ids - if a worker is ordered to build on top of one of
# these, the structure foundation does NOT yet exist on the map, so the worker
# is still "en route" rather than actively constructing.
_VESPENE_GEYSER_TYPES: Set[UnitTypeId] = {
    UnitTypeId.VESPENEGEYSER,
    UnitTypeId.RICHVESPENEGEYSER,
    UnitTypeId.PROTOSSVESPENEGEYSER,
    UnitTypeId.PURIFIERVESPENEGEYSER,
    UnitTypeId.SHAKURASVESPENEGEYSER,
}


class LLMObservationRecorder(ManagerBase):
    """Capture, format and persist LLM-friendly game observations.

    The recorder hooks into the regular manager update cycle but only does
    real work every ``interval_seconds`` of in-game time. Each trigger builds
    a master snapshot via the modular ``_extract_*`` methods and derives an
    English text observation from it. Both representations are appended to
    ``record_history`` and flushed to disk in :py:meth:`on_end`.
    """

    def __init__(
        self,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        output_folder: str = DEFAULT_OUTPUT_FOLDER,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self.interval_seconds: float = interval_seconds
        self.output_folder: str = output_folder
        self.enabled: bool = enabled

        # Initialised so that the first capture happens close to t=0 once the
        # game timer crosses ``interval_seconds``.
        self.last_recorded_time: float = -interval_seconds

        # In-memory buffer of all captured snapshots. Flushed to JSON once on
        # ``on_end`` to avoid I/O stalls during the game.
        self.record_history: List[Dict] = []
        self.llm_interactions: List[Dict] = []

        # Optional override: when set, the JSON file is written next to the
        # SC2Replay using exactly the same prefix (replay path with ``.json``
        # extension instead of ``.SC2Replay``). The bot loader fills this in
        # automatically; setting it manually is also supported.
        self.replay_save_path: Optional[str] = None

        # Optional override: an explicit absolute output path for the JSON.
        # Takes precedence over both ``replay_save_path`` and auto-naming.
        self.output_path: Optional[str] = None

        # Cached references resolved during ``start``. They are intentionally
        # optional so the recorder degrades gracefully when a bot does not
        # register every helper manager.
        self._income_calculator = None
        self._enemy_units_manager = None
        self._lost_units_manager = None
        self._game_analyzer = None
        self._memory_manager = None
        self._build_detector = None

        # Lookups populated lazily in ``start`` from python-sc2's auto-generated
        # ability dictionaries. Used to translate worker / building orders into
        # human-readable "what is being built / trained / researched" strings.
        self._build_ability_to_structure: Dict[AbilityId, UnitTypeId] = {}
        self._train_ability_to_unit: Dict[AbilityId, UnitTypeId] = {}
        self._research_ability_to_upgrade: Dict[AbilityId, UpgradeId] = {}

    # ------------------------------------------------------------------
    # Manager lifecycle
    # ------------------------------------------------------------------

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)

        from sharpy.interfaces import (
            IIncomeCalculator,
            IEnemyUnitsManager,
            ILostUnitsManager,
            IGameAnalyzer,
            IMemoryManager,
        )
        from sharpy.managers.extensions.build_detector import BuildDetector

        self._income_calculator = knowledge.get_manager(IIncomeCalculator)
        self._enemy_units_manager = knowledge.get_manager(IEnemyUnitsManager)
        self._lost_units_manager = knowledge.get_manager(ILostUnitsManager)
        self._game_analyzer = knowledge.get_manager(IGameAnalyzer)
        self._memory_manager = knowledge.get_manager(IMemoryManager)
        self._build_detector = knowledge.get_manager(BuildDetector)

        self._build_ability_lookups()

    def _build_ability_lookups(self) -> None:
        """Populate the build / train / research ability dictionaries.

        We dynamically derive these from python-sc2's auto-generated
        ``TRAIN_INFO`` and ``RESEARCH_INFO`` so we do not need to hard-code a
        long list (and stay correct as the SC2 data files are regenerated).
        """
        try:
            from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
            from sc2.dicts.unit_research_abilities import RESEARCH_INFO
        except Exception as exc:
            self.print(
                f"LLMObservationRecorder could not load ability dicts: {exc}",
                stats=False,
                log_level=logging.WARNING,
            )
            return

        worker_types = set(_WORKER_TYPES.values())

        for trainer, produced in TRAIN_INFO.items():
            for produced_type, info in produced.items():
                ability = info.get("ability")
                if ability is None:
                    continue
                # Worker entries (SCV/Probe/Drone) produce structures; everyone
                # else produces non-structure units.
                if trainer in worker_types:
                    self._build_ability_to_structure[ability] = produced_type
                else:
                    self._train_ability_to_unit[ability] = produced_type

        for _building, upgrades in RESEARCH_INFO.items():
            for upgrade_id, info in upgrades.items():
                ability = info.get("ability")
                if ability is not None:
                    self._research_ability_to_upgrade[ability] = upgrade_id

    async def update(self):
        if not self.enabled:
            return

        if self.ai.time - self.last_recorded_time < self.interval_seconds:
            return

        try:
            snapshot = self._build_snapshot()
            text_obs = self._generate_english_text_obs(snapshot)
            self.record_history.append(
                {
                    "game_time_seconds": round(self.ai.time, 2),
                    "structured_state": snapshot,
                    "text_observation": text_obs,
                }
            )
        except Exception as exc:
            self.print(
                f"LLMObservationRecorder failed to capture snapshot: {exc}",
                stats=False,
                log_level=logging.WARNING,
            )
        finally:
            self.last_recorded_time = self.ai.time

    async def post_update(self):
        # Nothing to render in-game.
        pass

    def record_llm_interaction(self, record: Dict) -> None:
        """Append one LLM response and the action history at response time."""
        if not self.enabled:
            return
        self.llm_interactions.append(record)

    async def on_end(self, game_result: Result):
        if not self.enabled or (not self.record_history and not self.llm_interactions):
            return

        try:
            output_path = self._resolve_output_path()
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            payload = {
                "metadata": self._build_metadata(game_result),
                "records": self.record_history,
                "llm_interactions": self.llm_interactions,
            }

            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)

            self.print(
                f"LLM observations saved to {output_path} "
                f"({len(self.record_history)} snapshots, "
                f"{len(self.llm_interactions)} LLM interactions).",
                stats=False,
            )
        except Exception as exc:
            self.print(
                f"LLMObservationRecorder failed to save: {exc}",
                stats=False,
                log_level=logging.WARNING,
            )

    # ------------------------------------------------------------------
    # Snapshot pipeline (Step 1: Build Structured Data)
    # ------------------------------------------------------------------

    def _build_snapshot(self) -> Dict:
        """Aggregate all extractors into a single master snapshot dict."""
        return {
            "time": round(self.ai.time, 2),
            "time_formatted": self.ai.time_formatted,
            "economy": self._extract_economy_state(),
            "own_forces": self._extract_own_forces_infrastructure(),
            "enemy": self._extract_enemy_intelligence(),
            "map_control": self._extract_map_control(),
            "combat": self._extract_combat_analysis(),
            "memory_flags": self._extract_memory_flags(),
        }

    def _extract_economy_state(self) -> Dict:
        """Current resources, supply usage and per-minute incomes."""
        mineral_per_min = 0.0
        gas_per_min = 0.0
        if self._income_calculator is not None:
            # IncomeCalculator stores per-second mining estimates; LLM prompts
            # are easier to reason about with per-minute numbers.
            mineral_per_min = round(self._income_calculator.mineral_income * 60, 1)
            gas_per_min = round(self._income_calculator.gas_income * 60, 1)

        return {
            "minerals": int(self.ai.minerals),
            "vespene": int(self.ai.vespene),
            "supply_used": int(self.ai.supply_used),
            "supply_cap": int(self.ai.supply_cap),
            "supply_left": int(self.ai.supply_left),
            "supply_workers": int(self.ai.supply_workers),
            "supply_army": int(self.ai.supply_army),
            "minerals_per_min": mineral_per_min,
            "vespene_per_min": gas_per_min,
        }

    def _extract_own_forces_infrastructure(self) -> Dict[str, Dict[str, int]]:
        """Classify own units / structures into four tiers.

        Returned shape::

            {
                "completed":         {<UnitTypeId.name>: count, ...},
                "under_construction":{<UnitTypeId.name>: count, ...},  # buildings 0<bp<1
                "workers_en_route":  {<UnitTypeId.name>: count, ...},  # SCV/Drone/Probe traveling
                "active_queues":     {<"Training X" | "Researching Y">: count, ...},
            }

        The four-tier breakdown lets the LLM (or downstream consumer) tell
        apart "the building is finished" / "the foundation is laid and being
        hammered" / "a worker is on its way to lay the foundation" / "a queue
        is producing units or upgrades inside an existing building". This is
        what stops the LLM from re-issuing the same construction order while
        the previous one is still in flight.
        """
        result: Dict[str, Dict[str, int]] = {
            "completed": {},
            "under_construction": {},
            "workers_en_route": {},
            "active_queues": {},
        }

        if self.cache is None:
            return result

        completed = result["completed"]
        under_construction = result["under_construction"]
        active_queues = result["active_queues"]

        # Per-type list of partial structures' centre points, used below to
        # decide whether a worker's BUILD_X target points at an already-laid
        # foundation (i.e. the worker is hammering / redundantly queued, not
        # really "en route").
        partial_positions: Dict[UnitTypeId, List] = {}

        for unit_type, units in self.cache.own_unit_cache.items():
            for unit in units:
                if unit.is_structure:
                    if unit.is_ready:
                        completed[unit_type.name] = completed.get(unit_type.name, 0) + 1
                    else:
                        # 0 < build_progress < 1: the foundation has been laid.
                        under_construction[unit_type.name] = (
                            under_construction.get(unit_type.name, 0) + 1
                        )
                        partial_positions.setdefault(unit_type, []).append(unit.position)
                else:
                    if unit.is_ready:
                        completed[unit_type.name] = completed.get(unit_type.name, 0) + 1
                    else:
                        # Non-structure with build_progress < 1: a Zerg unit
                        # currently morphing inside an egg / cocoon. Surface
                        # this as part of the active production queue.
                        key = f"Training {unit_type.name}"
                        active_queues[key] = active_queues.get(key, 0) + 1

        # Workers en route: a worker counts as "en route" only if its BUILD_X
        # target is NOT on top of an already-laid foundation of the same type.
        #
        # The position-based dedup handles three subtly-different cases that
        # plain subtraction (worker_count - partial_count) gets wrong:
        #
        #   1. Terran SCV hammering an in-progress foundation keeps its
        #      ``TERRANBUILD_X`` order, with target == the foundation's
        #      Point2. So its target lands on the partial structure's
        #      position and we correctly skip it.
        #   2. Refinery / Extractor / Assimilator: the worker's target is a
        #      vespene geyser tag whose ``position`` matches the gas
        #      building's centre, so the same position-overlap rule fires
        #      cleanly without a special tag-based code path.
        #   3. Multiple workers redundantly dispatched to the SAME placement
        #      (sharpy's GridBuilding can do this when a previous order has
        #      not yet started construction): subtraction would report
        #      ``2 - 1 = 1`` extra en-route worker even though no second
        #      structure is coming. Position-overlap correctly skips both
        #      workers because they target the same Point2.
        my_race = self.knowledge.my_race if self.knowledge is not None else None
        worker_type = _WORKER_TYPES.get(my_race) if my_race else None
        if worker_type is not None and self._build_ability_to_structure:
            workers = self.cache.own(worker_type)
            workers_en_route = result["workers_en_route"]
            for worker in workers:
                if not worker.orders:
                    continue
                first_order = worker.orders[0]
                ability_id = self._safe_ability_id(first_order.ability)
                if ability_id is None:
                    continue
                struct_type = self._build_ability_to_structure.get(ability_id)
                if struct_type is None:
                    continue

                # Resolve the order's target into a Point2-like position.
                target = first_order.target
                target_pos = None
                if isinstance(target, int):
                    target_unit = self.cache.by_tag(target)
                    if target_unit is None:
                        # Unknown target tag - safest to skip rather than
                        # over-report.
                        continue
                    target_pos = target_unit.position
                elif target is not None:
                    target_pos = target  # Point2 placement spot

                if target_pos is None:
                    continue

                # If any partial structure of the matching type sits on this
                # exact spot, the worker is hammering it (or a redundant
                # duplicate) - do not count as en route.
                candidates = partial_positions.get(struct_type, [])
                if any(self._positions_match(target_pos, p) for p in candidates):
                    continue

                workers_en_route[struct_type.name] = (
                    workers_en_route.get(struct_type.name, 0) + 1
                )

        # Active queues from completed production / research buildings. Each
        # queued action is one entry in ``unit.orders``.
        for unit_type, units in self.cache.own_unit_cache.items():
            for unit in units:
                if not unit.is_structure or not unit.is_ready or not unit.orders:
                    continue
                for order in unit.orders:
                    ability_id = self._safe_ability_id(order.ability)
                    if ability_id is None:
                        continue

                    produced_unit = self._train_ability_to_unit.get(ability_id)
                    if produced_unit is not None:
                        key = f"Training {produced_unit.name}"
                        active_queues[key] = active_queues.get(key, 0) + 1
                        continue

                    upgrade = self._research_ability_to_upgrade.get(ability_id)
                    if upgrade is not None:
                        key = f"Researching {upgrade.name}"
                        active_queues[key] = active_queues.get(key, 0) + 1

        return result

    @staticmethod
    def _positions_match(p1, p2, tol: float = 1.0) -> bool:
        """Return True if two Point2-like values lie within ``tol`` of each other.

        Hand-rolled distance check so we don't have to care which Point2
        helper the underlying lib exposes - we only need ``.x`` / ``.y``.
        """
        if p1 is None or p2 is None:
            return False
        try:
            dx = float(p1.x) - float(p2.x)
            dy = float(p1.y) - float(p2.y)
        except Exception:
            return False
        return (dx * dx + dy * dy) < tol * tol

    @staticmethod
    def _safe_ability_id(ability) -> Optional[AbilityId]:
        """Defensively pull an ``AbilityId`` from a UnitOrder's ability field."""
        if ability is None:
            return None
        # ``UnitOrder.ability`` is normally an ``AbilityData``; ``exact_id``
        # gives us the un-remapped AbilityId. Fall back to ``id`` and finally
        # to the raw value if either path raises.
        for attr in ("exact_id", "id"):
            try:
                value = getattr(ability, attr, None)
                if isinstance(value, AbilityId):
                    return value
            except Exception:
                continue
        try:
            return AbilityId(ability)  # type: ignore[arg-type]
        except Exception:
            return None

    def _extract_enemy_intelligence(self) -> Dict[str, int]:
        """Aggregated counts of enemy units/buildings ever observed."""
        composition: Dict[str, int] = {}
        if self._enemy_units_manager is None:
            return composition

        # ``unit_types`` is a KeysView, materialise to avoid mutation issues.
        for unit_type in list(self._enemy_units_manager.unit_types):
            count = self._enemy_units_manager.unit_count(unit_type)
            if count > 0:
                composition[unit_type.name] = count
        return composition

    def _extract_map_control(self) -> Dict:
        """Counts of own/enemy/neutral expansion zones."""
        own_bases = 0
        known_enemy_bases = 0
        neutral_zones = 0

        if self.zone_manager is not None and self.zone_manager.expansion_zones:
            for zone in self.zone_manager.expansion_zones:
                if zone.is_ours:
                    own_bases += 1
                elif zone.is_enemys:
                    known_enemy_bases += 1
                else:
                    neutral_zones += 1

        return {
            "own_bases": own_bases,
            "known_enemy_bases": known_enemy_bases,
            "neutral_expansions": neutral_zones,
        }

    def _extract_combat_analysis(self) -> Dict:
        """Framework-level advantage estimates plus accumulated losses."""
        result: Dict = {
            "advantage_predicted": "Even",
            "army_advantage": "Even",
            "income_advantage": "Even",
            "our_army_power": 0.0,
            "enemy_army_power": 0.0,
            "enemy_air": "NoAir",
            "own_lost_minerals": 0,
            "own_lost_gas": 0,
            "enemy_lost_minerals": 0,
            "enemy_lost_gas": 0,
        }

        if self._game_analyzer is not None:
            try:
                result["advantage_predicted"] = self._game_analyzer.our_army_predict.name
                result["army_advantage"] = self._game_analyzer.our_army_advantage.name
                result["income_advantage"] = self._game_analyzer.our_income_advantage.name
                if self._game_analyzer.our_power is not None:
                    result["our_army_power"] = round(self._game_analyzer.our_power.power, 1)
                if self._game_analyzer.enemy_power is not None:
                    result["enemy_army_power"] = round(self._game_analyzer.enemy_power.power, 1)
                result["enemy_air"] = self._game_analyzer.enemy_air.name
            except Exception:
                # Game analyzer reads heavy state; missing data should not
                # break the whole recorder, so we silently skip.
                pass

        if self._lost_units_manager is not None:
            try:
                own_min, own_gas = self._lost_units_manager.calculate_own_lost_resources()
                enemy_min, enemy_gas = self._lost_units_manager.calculate_enemy_lost_resources()
                result["own_lost_minerals"] = int(own_min)
                result["own_lost_gas"] = int(own_gas)
                result["enemy_lost_minerals"] = int(enemy_min)
                result["enemy_lost_gas"] = int(enemy_gas)
            except Exception:
                pass

        return result

    def _extract_memory_flags(self) -> Dict:
        """Boolean flags summarising tactical signals from extension managers."""
        flags: Dict = {
            "is_rushing": False,
            "rush_build": "Macro",
            "macro_build": "StandardMacro",
            "enemy_cloak_threat": False,
            "has_proxy_buildings": False,
            "remembered_enemy_units": 0,
        }

        if self._build_detector is not None:
            try:
                flags["is_rushing"] = bool(self._build_detector.rush_detected)
                flags["rush_build"] = self._build_detector.rush_build.name
                flags["macro_build"] = self._build_detector.macro_build.name
            except Exception:
                pass

        if self._enemy_units_manager is not None:
            try:
                flags["enemy_cloak_threat"] = bool(self._enemy_units_manager.enemy_cloak_trigger)
            except Exception:
                pass

        if self._memory_manager is not None:
            try:
                flags["remembered_enemy_units"] = len(self._memory_manager.ghost_units)
            except Exception:
                pass

        flags["has_proxy_buildings"] = self._detect_proxy_buildings()
        return flags

    def _detect_proxy_buildings(self) -> bool:
        """Heuristic: any enemy structure within ~60 tiles of our main base."""
        if self.zone_manager is None:
            return False

        own_main = self.zone_manager.zones.get(self.ai.start_location)
        if own_main is None:
            return False

        center = own_main.center_location
        for structure in self.ai.enemy_structures:
            if structure.distance_to(center) < 60:
                return True
        return False

    # ------------------------------------------------------------------
    # Text generation (Step 2: Generate English Text Observation)
    # ------------------------------------------------------------------

    def _generate_english_text_obs(self, snapshot: Dict) -> str:
        """Render the structured snapshot into an LLM-friendly English prompt.

        Output is divided into ``[Tag]``-labelled sections so an LLM can
        attend to the relevant block when answering queries like "what
        forces do I have" or "what is the enemy doing". This method
        intentionally only reads from ``snapshot`` so the prompt template can
        be modified without touching extraction logic.
        """
        eco = snapshot["economy"]
        own = snapshot["own_forces"]
        enemy = snapshot["enemy"]
        mc = snapshot["map_control"]
        combat = snapshot["combat"]
        flags = snapshot["memory_flags"]

        # [Time]
        time_section = (
            f"[Time] {snapshot['time_formatted']} ({snapshot['time']:.1f}s)."
        )

        # [Economy]
        economy_section = (
            "[Economy] "
            f"{eco['minerals']} minerals, {eco['vespene']} vespene; "
            f"income {eco['minerals_per_min']:.0f} mins/min, "
            f"{eco['vespene_per_min']:.0f} gas/min. "
            f"Supply: {eco['supply_used']}/{eco['supply_cap']} "
            f"(workers {eco['supply_workers']}, army {eco['supply_army']})."
        )

        # [Own Forces & Infrastructure] - the four-tier breakdown.
        own_lines: List[str] = ["[Own Forces & Infrastructure]"]
        own_lines.append(
            f"  Completed: {self._format_count_dict(own.get('completed'), empty='nothing built yet')}."
        )
        own_lines.append(
            f"  Under Construction: "
            f"{self._format_count_dict(own.get('under_construction'), empty='none')}."
        )
        own_lines.append(
            f"  Workers En Route: "
            f"{self._format_count_dict(own.get('workers_en_route'), empty='none')}."
        )
        own_lines.append(
            f"  Active Queues: "
            f"{self._format_active_queues(own.get('active_queues'))}."
        )
        own_section = "\n".join(own_lines)

        # [Enemy Intelligence]
        enemy_section = (
            "[Enemy Intelligence] "
            f"{self._format_count_dict(enemy, empty='nothing scouted yet')}."
        )

        # [Map Control]
        map_section = (
            "[Map Control] "
            f"{mc['own_bases']} own bases, "
            f"{mc['known_enemy_bases']} known enemy bases, "
            f"{mc['neutral_expansions']} neutral expansions remaining."
        )

        # [Combat Analysis]
        combat_section = (
            "[Combat Analysis] "
            f"army advantage = {combat['army_advantage']}, "
            f"income advantage = {combat['income_advantage']}, "
            f"predicted = {combat['advantage_predicted']}. "
            f"Power: {combat['our_army_power']:.0f} vs "
            f"{combat['enemy_army_power']:.0f}. "
            f"Losses: own {combat['own_lost_minerals']} minerals/"
            f"{combat['own_lost_gas']} gas, "
            f"enemy {combat['enemy_lost_minerals']} minerals/"
            f"{combat['enemy_lost_gas']} gas."
        )

        # [Threat Flags]
        flag_parts: List[str] = []
        if flags["is_rushing"]:
            flag_parts.append(f"enemy rush detected ({flags['rush_build']})")
        if flags["macro_build"] != "StandardMacro":
            flag_parts.append(f"enemy macro build = {flags['macro_build']}")
        if flags["has_proxy_buildings"]:
            flag_parts.append("proxy buildings spotted near base")
        if flags["enemy_cloak_threat"]:
            flag_parts.append("enemy cloak/burrow threat detected")
        if flag_parts:
            threat_section = "[Threat Flags] " + "; ".join(flag_parts) + "."
        else:
            threat_section = "[Threat Flags] none."

        return "\n".join(
            [
                time_section,
                economy_section,
                own_section,
                enemy_section,
                map_section,
                combat_section,
                threat_section,
            ]
        )

    @staticmethod
    def _format_count_dict(
        data: Optional[Dict[str, int]], empty: str = "none"
    ) -> str:
        """Render ``{name: count}`` as ``count1 name1, count2 name2`` sorted."""
        if not data:
            return empty
        ordered = sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))
        return ", ".join(f"{count} {name}" for name, count in ordered)

    @staticmethod
    def _format_active_queues(
        data: Optional[Dict[str, int]], empty: str = "none"
    ) -> str:
        """Render queue keys like ``"Training MARINE"`` / ``"Researching X"``.

        Training items get ``Training N <name>`` (count makes sense - several
        marines may sit in queue). Research items get ``Researching <name>``
        because each research action is a single 0/1 toggle on the building.
        """
        if not data:
            return empty
        ordered = sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))
        parts: List[str] = []
        for key, count in ordered:
            verb, _, name = key.partition(" ")
            if verb == "Researching" and name:
                parts.append(f"Researching {name}")
            elif verb == "Training" and name:
                parts.append(f"Training {count} {name}")
            else:
                parts.append(f"{count} {key}")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _build_metadata(self, game_result: Optional[Result]) -> Dict:
        my_race = self.knowledge.my_race
        enemy_race = self.knowledge.enemy_race
        return {
            "map_name": self.ai.game_info.map_name,
            "my_race": my_race.name if my_race else "Unknown",
            "enemy_race": enemy_race.name if enemy_race else "Unknown",
            "matchup": (
                f"{_RACE_SHORT.get(my_race, '?')}v"
                f"{_RACE_SHORT.get(enemy_race, '?')}"
            ),
            "opponent_id": getattr(self.ai, "opponent_id", None),
            "bot_name": getattr(self.ai, "name", None),
            "game_duration_seconds": round(self.ai.time, 2),
            "game_duration_formatted": self.ai.time_formatted,
            "result": game_result.name if game_result is not None else "Unknown",
            "interval_seconds": self.interval_seconds,
            "record_count": len(self.record_history),
            "llm_interaction_count": len(self.llm_interactions),
        }

    def _resolve_output_path(self) -> str:
        """Resolve the JSON output path with three-tier precedence.

        1. Explicit ``output_path`` if provided.
        2. ``replay_save_path`` (mirrors the SC2Replay prefix) if provided.
        3. Auto-generated ``Replay_<timestamp>_<matchup>_<map>.json`` in
           ``output_folder`` so files remain easy to correlate even when no
           replay path was injected.
        """
        if self.output_path:
            return self.output_path

        if self.replay_save_path:
            base, _ext = os.path.splitext(self.replay_save_path)
            return base + ".json"

        my_race = self.knowledge.my_race
        enemy_race = self.knowledge.enemy_race
        matchup = (
            f"{_RACE_SHORT.get(my_race, '?')}v"
            f"{_RACE_SHORT.get(enemy_race, '?')}"
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        map_name = self.ai.game_info.map_name.replace(" ", "")
        filename = f"{DEFAULT_FILENAME_PREFIX}_{timestamp}_{matchup}_{map_name}.json"
        return os.path.join(self.output_folder, filename)
