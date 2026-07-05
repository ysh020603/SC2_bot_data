import json
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Union

from sc2.data import Result
from sc2.constants import abilityid_to_unittypeid
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.unit_command import UnitCommand

from config import get_config
from sharpy.managers.core.manager_base import ManagerBase
from sharpy.tools.data_ref_loader import get_data_ref_loader
from sharpy.tools.obs_entities import collect_entities

if TYPE_CHECKING:
    from sharpy.knowledges import Knowledge

DEFAULT_OUTPUT_DIR = "ability_sequences"
PENDING_EXPIRE_SECONDS = 8.0
BUILD_PENDING_EXPIRE_SECONDS = 90.0
TRAIN_PENDING_EXPIRE_SECONDS = 180.0
MORPH_PENDING_EXPIRE_SECONDS = 120.0
RESEARCH_PENDING_EXPIRE_SECONDS = 300.0
BUILD_CONFIRM_DISTANCE = 1.5
TRAIN_PRODUCER_DISTANCE = 8.0

MORPH_RESULT_TYPES = {
    AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND: UnitTypeId.ORBITALCOMMAND,
    AbilityId.UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS: UnitTypeId.PLANETARYFORTRESS,
}


@dataclass
class PendingAction:
    attempt_id: int
    issued_index: int
    action: UnitCommand
    resolved_ability: str
    unit_tag: int
    unit_type: UnitTypeId
    unit_position: Point2
    target_key: Optional[Union[int, Tuple[float, float]]]
    expected_position: Optional[Point2]
    issued_iteration: int
    issued_time: float
    baseline_effect_tags: Set[int]
    obs: Dict[str, Any]
    local_obs: Dict[str, Any]
    executor_context: Optional[Dict[str, Any]] = None
    confirmation: Optional[Dict[str, Any]] = None


class AbilityRecorderManager(ManagerBase):
    """Records macro ability sequences with paired global/local observations."""

    def __init__(self) -> None:
        super().__init__()
        config = get_config()
        self.enabled = config["general"].getboolean("write_ability_sequence", fallback=True)
        self.output_dir = config["general"].get("ability_sequence_dir", DEFAULT_OUTPUT_DIR)
        self.output_path = config["general"].get("ability_sequence_path", fallback=None)
        self.match_id = config["general"].get("ability_sequence_match_id", fallback=None)
        self.data_ref_path = config["general"].get(
            "data_ref_path", os.path.join("data_ref", "data_base_add_graph.json")
        )
        self.sequence: List[Dict[str, Any]] = []
        self._other_abilities: Set[str] = set()
        self._pending: Dict[int, PendingAction] = {}
        self._claimed_effect_tags: Set[int] = set()
        self._expired_unconfirmed_count = 0
        self._superseded_unconfirmed_count = 0
        self._next_attempt_id = 0
        self._next_issued_index = 0
        self._snapshot_game_loop: Optional[int] = None
        self._snapshot_obs: Dict[str, Any] = {"structured": {}, "text": ""}
        self._snapshot_local_obs: Dict[str, Any] = {}
        self._seq = 0

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.enabled = self.knowledge.config["general"].getboolean("write_ability_sequence", fallback=True)
        self.output_dir = self.knowledge.config["general"].get("ability_sequence_dir", DEFAULT_OUTPUT_DIR)
        self.output_path = self.knowledge.config["general"].get(
            "ability_sequence_path", fallback=None
        )
        self.match_id = self.knowledge.config["general"].get(
            "ability_sequence_match_id", fallback=None
        )
        self.data_ref_path = self.knowledge.config["general"].get(
            "data_ref_path", os.path.join("data_ref", "data_base_add_graph.json")
        )
        if self.sequence:
            self._seq = len(self.sequence)
        else:
            self._other_abilities = set()
            self._pending = {}
            self._claimed_effect_tags = set()
            self._expired_unconfirmed_count = 0
            self._superseded_unconfirmed_count = 0
            self._next_attempt_id = 0
            self._next_issued_index = 0
            self._snapshot_game_loop = None
            self._seq = 0

    async def update(self):
        pass

    async def post_update(self):
        self._resolve_pending()

    def _resolve_pending(self) -> None:
        if not self.enabled or not self._pending:
            return

        resolved: List[int] = []
        for attempt_id, pending in list(self._pending.items()):
            semantic_type = self._semantic_type(pending)
            if semantic_type in ("Build", "BuildOnUnit", "BuildInstant"):
                effect = self._find_build_effect(pending)
                if effect is not None:
                    self._confirm_structure(pending, effect)
                    resolved.append(attempt_id)
                    continue
            if self._is_expired(pending):
                self._expired_unconfirmed_count += 1
                resolved.append(attempt_id)

        for attempt_id in resolved:
            self._pending.pop(attempt_id, None)

    def record(self, action: UnitCommand) -> None:
        if not self.enabled:
            return

        bot = self.ai if hasattr(self, "ai") else action.unit._bot_object
        ability_name = action.ability.name
        loader = get_data_ref_loader(self.data_ref_path)

        target_for_resolve: Optional[object] = action.target
        if ability_name in ("BUILD_TECHLAB", "BUILD_REACTOR") and (
            target_for_resolve is None or not hasattr(target_for_resolve, "type_id")
        ):
            # SC2 对“挂载/添加附件”类命令通常是由宿主建筑（barracks/factory/starport）
            # 作为 action.unit 发起，而 target 可能是 Point2 或 None。
            # 因此这里改用 action.unit 来决定后缀。
            target_for_resolve = action.unit

        resolved_ability = loader.resolve_recorded_ability_name(ability_name, target_for_resolve)

        if not loader.should_record_in_sequence(resolved_ability):
            self._other_abilities.add(ability_name)
            return

        semantic_target = loader.get_semantic_target(resolved_ability)
        if semantic_target is None:
            self._other_abilities.add(ability_name)
            return

        obs, local_obs = self._capture_issue_snapshot(bot)
        attempt_id = self._next_attempt_id
        self._next_attempt_id += 1
        issued_index = self._next_issued_index
        self._next_issued_index += 1
        expected_position = self._expected_effect_position(action, semantic_target)

        self._pending[attempt_id] = PendingAction(
            attempt_id=attempt_id,
            issued_index=issued_index,
            action=action,
            resolved_ability=resolved_ability,
            unit_tag=action.unit.tag,
            unit_type=action.unit.type_id,
            unit_position=action.unit.position,
            target_key=self._target_key(action.target),
            expected_position=expected_position,
            issued_iteration=getattr(getattr(self, "knowledge", None), "iteration", 0),
            issued_time=bot.time,
            baseline_effect_tags=self._effect_tags_for(semantic_target),
            obs=obs,
            local_obs=local_obs,
            executor_context=self._capture_train_executor_context(
                bot,
                action,
                resolved_ability,
                semantic_target,
            ),
        )

    def _capture_issue_snapshot(self, bot: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        game_loop = int(getattr(getattr(bot, "state", None), "game_loop", -1))
        if self._snapshot_game_loop != game_loop:
            self._snapshot_game_loop = game_loop
            self._snapshot_obs = self._capture_obs(bot)
            try:
                self._snapshot_local_obs = collect_entities(bot, self.data_ref_path)
            except Exception:
                self._snapshot_local_obs = {}
        return self._snapshot_obs, self._snapshot_local_obs

    def _expected_effect_position(
        self, action: UnitCommand, semantic_target: Dict[str, Any]
    ) -> Optional[Point2]:
        target = action.target
        if isinstance(target, Point2):
            return target
        if isinstance(target, Unit):
            return target.position
        if semantic_target.get("type") == "BuildInstant":
            return action.unit.position.offset(Point2((2.5, -0.5)))
        return None

    def _target_key(
        self, target: Optional[Union[Unit, Point2]]
    ) -> Optional[Union[int, Tuple[float, float]]]:
        if isinstance(target, Unit):
            return target.tag
        if isinstance(target, Point2):
            return (round(float(target.x), 2), round(float(target.y), 2))
        return None

    def _find_unit(self, tag: int) -> Optional[Unit]:
        cache = getattr(self.ai, "unit_cache", None)
        if cache is not None:
            unit = cache.by_tag(tag)
            if unit is not None:
                return unit
        for collection_name in ("all_own_units", "units", "structures"):
            collection = getattr(self.ai, collection_name, None)
            if collection is None:
                continue
            finder = getattr(collection, "find_by_tag", None)
            if finder is not None:
                unit = finder(tag)
                if unit is not None:
                    return unit
            else:
                for unit in collection:
                    if getattr(unit, "tag", None) == tag:
                        return unit
        return None

    def _semantic_target(self, pending: PendingAction) -> Dict[str, Any]:
        loader = get_data_ref_loader(self.data_ref_path)
        return loader.get_semantic_target(pending.resolved_ability) or {}

    def _semantic_type(self, pending: PendingAction) -> Optional[str]:
        return self._semantic_target(pending).get("type")

    def _produced_unit_type(self, semantic_target: Dict[str, Any]) -> Optional[UnitTypeId]:
        produces_name = semantic_target.get("produces_name")
        if not produces_name:
            return None
        return getattr(UnitTypeId, str(produces_name).upper(), None)

    def _effect_tags_for(self, semantic_target: Dict[str, Any]) -> Set[int]:
        if semantic_target.get("type") not in ("Build", "BuildOnUnit", "BuildInstant"):
            return set()

        produced_type = self._produced_unit_type(semantic_target)
        if produced_type is None:
            return set()
        return {
            unit.tag
            for unit in getattr(self.ai, "structures", [])
            if self._matches_produced_type(unit.type_id, produced_type)
        }

    def _matches_produced_type(
        self, actual_type: UnitTypeId, produced_type: UnitTypeId
    ) -> bool:
        if actual_type == produced_type:
            return True
        # The data graph remaps host-specific addons such as BARRACKSTECHLAB
        # to the generic TECHLAB / REACTOR result type.
        if produced_type in (UnitTypeId.TECHLAB, UnitTypeId.REACTOR):
            return actual_type.name.endswith(produced_type.name)
        return False

    def _is_expired(self, pending: PendingAction) -> bool:
        semantic_type = self._semantic_type(pending)
        if semantic_type in ("Build", "BuildOnUnit", "BuildInstant"):
            timeout = BUILD_PENDING_EXPIRE_SECONDS
        elif semantic_type == "Train":
            timeout = TRAIN_PENDING_EXPIRE_SECONDS
        elif semantic_type == "Morph":
            timeout = MORPH_PENDING_EXPIRE_SECONDS
        elif semantic_type == "Research":
            timeout = RESEARCH_PENDING_EXPIRE_SECONDS
        else:
            timeout = PENDING_EXPIRE_SECONDS
        return self.ai.time - pending.issued_time > timeout

    def _find_build_effect(self, pending: PendingAction) -> Optional[Unit]:
        semantic_target = self._semantic_target(pending)
        produced_type = self._produced_unit_type(semantic_target)
        if produced_type is None:
            return None

        candidates = [
            unit
            for unit in getattr(self.ai, "structures", [])
            if self._matches_produced_type(unit.type_id, produced_type)
            and unit.tag not in pending.baseline_effect_tags
            and unit.tag not in self._claimed_effect_tags
        ]
        if not candidates:
            return None

        actor = self._find_unit(pending.unit_tag)
        if semantic_target.get("type") == "BuildInstant" and actor is not None:
            add_on_tag = int(getattr(actor, "add_on_tag", 0) or 0)
            for candidate in candidates:
                if candidate.tag == add_on_tag:
                    return candidate

        if pending.expected_position is None:
            return None

        close_candidates = [
            unit
            for unit in candidates
            if unit.position.distance_to(pending.expected_position) <= BUILD_CONFIRM_DISTANCE
        ]
        if not close_candidates:
            return None
        return min(
            close_candidates,
            key=lambda unit: unit.position.distance_to(pending.expected_position),
        )

    def _build_pending_matches(self, pending: PendingAction, unit: Unit) -> bool:
        semantic_target = self._semantic_target(pending)
        if semantic_target.get("type") not in ("Build", "BuildOnUnit", "BuildInstant"):
            return False
        produced_type = self._produced_unit_type(semantic_target)
        if produced_type is None or not self._matches_produced_type(unit.type_id, produced_type):
            return False
        if unit.tag in pending.baseline_effect_tags:
            return False
        if pending.expected_position is None:
            return False
        return unit.position.distance_to(pending.expected_position) <= BUILD_CONFIRM_DISTANCE

    def on_building_construction_started(self, unit: Unit) -> None:
        self._confirm_build_event(unit, "structure_started")

    def on_building_construction_complete(self, unit: Unit) -> None:
        # Construction-start is the primary receipt. Completion is a safe fallback
        # for addons and rare engine frames where start was not surfaced.
        self._confirm_build_event(unit, "structure_completed")

    def _confirm_build_event(self, unit: Unit, kind: str) -> None:
        if not self.enabled or unit.tag in self._claimed_effect_tags:
            return
        candidates = [
            pending
            for pending in self._pending.values()
            if self._build_pending_matches(pending, unit)
        ]
        if not candidates:
            return
        # Retried placement commands share the same effect. The latest issued
        # candidate is the one closest to the engine accepting that placement.
        pending = max(candidates, key=lambda item: item.issued_index)
        self._confirm_structure(pending, unit, kind)

    def _confirm_structure(
        self, pending: PendingAction, unit: Unit, kind: str = "structure_started"
    ) -> None:
        self._claimed_effect_tags.add(unit.tag)
        confirmation = {
            "kind": kind,
            "game_time": round(self.ai.time, 2),
            "entity_tag": unit.tag,
            "entity_type": unit.type_id.name,
            "actor_tag": pending.unit_tag,
        }
        self._materialize(pending, confirmation)

        # One SC2 structure can satisfy only one attempt. Remove placement retries
        # aimed at this exact result so they cannot be matched to a later building.
        produced_type = self._produced_unit_type(self._semantic_target(pending))
        for other_id, other in list(self._pending.items()):
            if other_id == pending.attempt_id:
                continue
            other_type = self._produced_unit_type(self._semantic_target(other))
            if other_type != produced_type:
                continue
            if other.expected_position is None or pending.expected_position is None:
                continue
            if other.expected_position.distance_to(pending.expected_position) <= BUILD_CONFIRM_DISTANCE:
                self._pending.pop(other_id, None)
                self._superseded_unconfirmed_count += 1

    def on_unit_created(self, unit: Unit) -> None:
        if not self.enabled:
            return
        candidates: List[Tuple[Tuple[float, float, int], PendingAction, float]] = []
        for pending in self._pending.values():
            semantic_target = self._semantic_target(pending)
            if semantic_target.get("type") != "Train":
                continue
            produced_type = self._produced_unit_type(semantic_target)
            if produced_type is None or not self._matches_produced_type(unit.type_id, produced_type):
                continue
            actor = self._find_unit(pending.unit_tag)
            actor_position = getattr(actor, "position", pending.unit_position)
            distance = float(actor_position.distance_to(unit.position))
            age = max(0.0, float(self.ai.time - pending.issued_time))
            expected = self._unit_build_time_seconds(produced_type)
            candidates.append(((distance, abs(age - expected), pending.issued_index), pending, distance))
        if not candidates:
            return

        _, pending, distance = min(candidates, key=lambda item: item[0])
        confidence = "high" if distance <= TRAIN_PRODUCER_DISTANCE else "low"
        if confidence != "high":
            # A remote match proves that the action happened, but cannot safely
            # teach which one of several producers was selected.
            pending.executor_context = None
        self._materialize(
            pending,
            {
                "kind": "unit_created",
                "game_time": round(self.ai.time, 2),
                "entity_tag": unit.tag,
                "entity_type": unit.type_id.name,
                "actor_tag": pending.unit_tag,
                "producer_distance": round(distance, 2),
                "producer_match_confidence": confidence,
            },
        )

    def _unit_build_time_seconds(self, unit_type: UnitTypeId) -> float:
        try:
            proto = self.ai._game_data.units[unit_type.value]._proto
            return float(proto.build_time) / 22.4
        except Exception:
            return 0.0

    def on_unit_type_changed(self, unit: Unit, previous_type: UnitTypeId) -> None:
        if not self.enabled:
            return
        candidates = []
        for pending in self._pending.values():
            if self._semantic_type(pending) != "Morph" or pending.unit_tag != unit.tag:
                continue
            produced_type = self._produced_unit_type(self._semantic_target(pending))
            if produced_type is not None and self._matches_produced_type(unit.type_id, produced_type):
                candidates.append(pending)
        if not candidates:
            return
        pending = min(candidates, key=lambda item: item.issued_index)
        self._materialize(
            pending,
            {
                "kind": "unit_type_changed",
                "game_time": round(self.ai.time, 2),
                "entity_tag": unit.tag,
                "entity_type_before": previous_type.name,
                "entity_type": unit.type_id.name,
                "actor_tag": pending.unit_tag,
            },
        )

    @staticmethod
    def _normalized_name(value: Any) -> str:
        name = getattr(value, "name", str(value))
        return re.sub(r"[^A-Z0-9]", "", name.upper())

    def on_upgrade_complete(self, upgrade: UpgradeId) -> None:
        if not self.enabled:
            return
        upgrade_name = self._normalized_name(upgrade)
        candidates = []
        for pending in self._pending.values():
            semantic_target = self._semantic_target(pending)
            if semantic_target.get("type") != "Research":
                continue
            if self._normalized_name(semantic_target.get("upgrade_name", "")) == upgrade_name:
                candidates.append(pending)
        if not candidates:
            return
        pending = min(candidates, key=lambda item: item.issued_index)
        self._materialize(
            pending,
            {
                "kind": "upgrade_completed",
                "game_time": round(self.ai.time, 2),
                "upgrade": upgrade.name,
                "actor_tag": pending.unit_tag,
            },
        )

    def _materialize(self, pending: PendingAction, confirmation: Dict[str, Any]) -> None:
        action = pending.action
        loader = get_data_ref_loader(self.data_ref_path)
        semantic_target = loader.get_semantic_target(pending.resolved_ability)
        if semantic_target is None:
            return

        entry: Dict[str, Any] = {
            "_issued_index": pending.issued_index,
            "game_time": round(pending.issued_time, 2),
            "issued_time": round(pending.issued_time, 2),
            "confirmed_time": round(self.ai.time, 2),
            "ability": pending.resolved_ability,
            "semantic_target": semantic_target,
            "obs": pending.obs,
            "local_obs": pending.local_obs,
            "confirmation": confirmation,
        }
        if pending.executor_context is not None:
            entry["executor_context"] = pending.executor_context
        place = self._serialize_place(action.target, semantic_target["type"])
        if place is not None:
            entry["place"] = place
        self.sequence.append(entry)
        self._pending.pop(pending.attempt_id, None)

    def _capture_train_executor_context(
        self,
        bot: Any,
        action: UnitCommand,
        resolved_ability: str,
        semantic_target: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Capture executor-choice context only for train actions with >1 candidates.

        The real executor LLM is only useful when several producers could issue
        the same train command. For offline SFT data we save the selected
        producer plus a lightweight snapshot of same-type candidate producers.
        Addon and morph actions intentionally do not use this path.
        """
        if semantic_target.get("type") != "Train":
            return None

        trainer_types = self._trainer_types_for_ability(action.ability)
        if not trainer_types:
            return None

        candidates: List[Dict[str, Any]] = []
        seen_tags: Set[int] = set()
        for unit in list(getattr(bot, "units", [])) + list(getattr(bot, "structures", [])):
            tag = getattr(unit, "tag", None)
            if tag is None or tag in seen_tags:
                continue
            seen_tags.add(tag)
            if getattr(unit, "type_id", None) not in trainer_types:
                continue
            if getattr(unit, "build_progress", 1.0) < 1.0:
                continue
            candidates.append(self._serialize_executor_candidate(unit))

        if len(candidates) <= 1:
            return None

        return {
            "ability_name": resolved_ability,
            "selected_tag": action.unit.tag,
            "selected_type": action.unit.type_id.name,
            "candidate_executors": candidates,
            "candidate_count": len(candidates),
            "cost_hint": self._format_action_cost_hint(bot, action),
            "pending_actions_summary": "",
            "waiting_actions_summary": "",
            "executor_conflict_hints": "",
            "note": "Captured by AbilityRecorder for train actions with more than one same-producer candidate.",
        }

    def _trainer_types_for_ability(self, ability_id: AbilityId) -> Set[UnitTypeId]:
        try:
            from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
        except Exception:
            return set()

        trainers: Set[UnitTypeId] = set()
        for trainer_type, produced in TRAIN_INFO.items():
            for _produced_type, info in produced.items():
                if info.get("ability") == ability_id:
                    trainers.add(trainer_type)
        return trainers

    def _serialize_executor_candidate(self, unit: Unit) -> Dict[str, Any]:
        orders: List[Dict[str, Any]] = []
        for order in getattr(unit, "orders", []) or []:
            ability = getattr(order, "ability", None)
            ability_id = getattr(ability, "id", None)
            orders.append(
                {
                    "ability": getattr(ability_id, "name", str(ability_id)),
                    "progress": round(float(getattr(order, "progress", 0.0) or 0.0), 3),
                }
            )

        addon = None
        if getattr(unit, "has_techlab", False):
            addon = "TechLab"
        elif getattr(unit, "has_reactor", False):
            addon = "Reactor"
        elif getattr(unit, "has_add_on", False):
            addon = "AddOn"

        return {
            "tag": unit.tag,
            "type": unit.type_id.name,
            "is_idle": bool(getattr(unit, "is_idle", False)),
            "add_on": addon,
            "add_on_tag": int(getattr(unit, "add_on_tag", 0) or 0),
            "orders": orders,
        }

    def _format_action_cost_hint(self, bot: Any, action: UnitCommand) -> str:
        try:
            cost = bot._game_data.calculate_ability_cost(action.ability)
            minerals = int(getattr(cost, "minerals", 0) or 0)
            gas = int(getattr(cost, "vespene", 0) or 0)
        except Exception:
            minerals = 0
            gas = 0

        supply = 0
        try:
            unit_type = abilityid_to_unittypeid.get(action.ability)
            if unit_type is not None:
                supply = int(bot.calculate_supply_cost(unit_type) or 0)
        except Exception:
            supply = 0

        return f"minerals {minerals}, gas {gas}, supply {supply}"

    def _capture_obs(self, bot) -> Dict[str, Any]:
        recorder = getattr(bot, "llm_observation_recorder", None)
        if recorder is None:
            return {"structured": {}, "text": ""}
        try:
            return recorder.capture_observation_bundle(bot)
        except Exception:
            return {"structured": {}, "text": ""}

    def _serialize_place(
        self, target: Optional[Union[Unit, Point2]], semantic_type: str
    ) -> Optional[Dict[str, Any]]:
        if semantic_type not in ("Build", "BuildOnUnit"):
            return None

        if isinstance(target, Point2):
            return {"x": round(float(target.x), 2), "y": round(float(target.y), 2)}

        if isinstance(target, Unit):
            return {
                "unit_type": target.type_id.name,
                "tag": target.tag,
                "x": round(float(target.position.x), 2),
                "y": round(float(target.position.y), 2),
            }

        return None

    async def on_end(self, game_result: Result):
        if not self.enabled:
            return
        self._resolve_pending()
        if not self.sequence and not self._other_abilities:
            return

        self.sequence.sort(key=lambda entry: entry.get("_issued_index", 0))
        for seq, entry in enumerate(self.sequence):
            entry["seq"] = seq
            entry.pop("_issued_index", None)
        self._seq = len(self.sequence)

        if not os.path.isdir(self.output_dir):
            os.makedirs(self.output_dir)

        opponent_id = getattr(self.ai, "opponent_id", "unknown")
        localized_map_name = self.ai.game_info.map_name.replace(" ", "")
        configured_map_name = getattr(self.ai, "ability_sequence_map_name", None)
        if not configured_map_name:
            config = getattr(self.ai, "config", None)
            if config is not None:
                try:
                    configured_map_name = config.get("general", "ability_sequence_map_name", fallback=None)
                except Exception:
                    configured_map_name = None
        map_name = str(configured_map_name or localized_map_name).replace(" ", "")
        timestamp = datetime.now().strftime("%Y-%m-%d %H_%M_%S")
        randomizer = random.randint(0, 999999)
        file_name = f"{opponent_id}_{map_name}_{timestamp}_{randomizer}.json"
        path = self.output_path or os.path.join(self.output_dir, file_name)
        parent_dir = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent_dir, exist_ok=True)

        order_list = [entry["ability"] for entry in self.sequence]

        payload = {
            "meta": {
                "bot_name": self.ai.name,
                "opponent_id": opponent_id,
                "map": map_name,
                "map_localized": localized_map_name,
                "my_race": self.knowledge.my_race.name,
                "enemy_race": self.knowledge.enemy_race.name,
                "result": game_result.name,
                "game_duration": round(self.ai.time, 2),
                "sequence_count": len(self.sequence),
                "order_list_count": len(order_list),
                "other_abilities_count": len(self._other_abilities),
                "confirmation_schema": "sc2-outcome-v2",
                "match_id": self.match_id,
                "expired_unconfirmed_action_count": self._expired_unconfirmed_count,
                "superseded_unconfirmed_action_count": self._superseded_unconfirmed_count,
                "pending_unconfirmed_action_count": len(self._pending),
                "recorded_at": datetime.now().isoformat(),
            },
            "sequence": self.sequence,
            "other_abilities": sorted(self._other_abilities),
            "order_list": order_list,
        }

        temp_path = f"{path}.tmp.{os.getpid()}"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)

        self.print(
            f"Saved {len(self.sequence)} macro actions and {len(self._other_abilities)} other abilities to {path}",
            stats=False,
        )
