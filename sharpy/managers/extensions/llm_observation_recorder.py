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
from typing import Dict, List, Optional, TYPE_CHECKING

from sc2.data import Race, Result

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

    async def on_end(self, game_result: Result):
        if not self.enabled or not self.record_history:
            return

        try:
            output_path = self._resolve_output_path()
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            payload = {
                "metadata": self._build_metadata(game_result),
                "records": self.record_history,
            }

            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)

            self.print(
                f"LLM observations saved to {output_path} "
                f"({len(self.record_history)} snapshots).",
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
            "army": self._extract_own_army_state(),
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

    def _extract_own_army_state(self) -> Dict[str, int]:
        """Aggregated own unit/structure counts keyed by ``UnitTypeId.name``."""
        composition: Dict[str, int] = {}
        if self.cache is None:
            return composition

        for unit_type, units in self.cache.own_unit_cache.items():
            amount = units.amount
            if amount <= 0:
                continue
            composition[unit_type.name] = composition.get(unit_type.name, 0) + amount
        return composition

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

        This method intentionally only reads from ``snapshot`` so the prompt
        template can be modified without touching extraction logic.
        """
        eco = snapshot["economy"]
        army = snapshot["army"]
        enemy = snapshot["enemy"]
        mc = snapshot["map_control"]
        combat = snapshot["combat"]
        flags = snapshot["memory_flags"]

        lines: List[str] = []

        lines.append(
            f"Time: {snapshot['time_formatted']} ({snapshot['time']:.1f}s)."
        )

        lines.append(
            "Economy: "
            f"{eco['minerals']} minerals, {eco['vespene']} vespene; "
            f"income {eco['minerals_per_min']:.0f} mins/min, "
            f"{eco['vespene_per_min']:.0f} gas/min. "
            f"Supply: {eco['supply_used']}/{eco['supply_cap']} "
            f"(workers {eco['supply_workers']}, army {eco['supply_army']})."
        )

        if army:
            ordered = sorted(army.items(), key=lambda kv: kv[1], reverse=True)
            army_str = ", ".join(f"{count} {name}" for name, count in ordered)
        else:
            army_str = "no units"
        lines.append(f"Own forces: {army_str}.")

        if enemy:
            ordered = sorted(enemy.items(), key=lambda kv: kv[1], reverse=True)
            enemy_str = ", ".join(f"{count} {name}" for name, count in ordered)
        else:
            enemy_str = "nothing scouted yet"
        lines.append(f"Enemy intelligence: {enemy_str}.")

        lines.append(
            "Map control: "
            f"{mc['own_bases']} own bases, "
            f"{mc['known_enemy_bases']} known enemy bases, "
            f"{mc['neutral_expansions']} neutral expansions remaining."
        )

        lines.append(
            "Analysis: "
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
            lines.append("Threat flags: " + "; ".join(flag_parts) + ".")
        else:
            lines.append("Threat flags: none.")

        return " ".join(lines)

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
