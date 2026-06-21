import json
import os
import random
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Union

from sc2.data import Result
from sc2.constants import abilityid_to_unittypeid
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
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

MORPH_RESULT_TYPES = {
    AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND: UnitTypeId.ORBITALCOMMAND,
    AbilityId.UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS: UnitTypeId.PLANETARYFORTRESS,
}


@dataclass
class PendingAction:
    action: UnitCommand
    resolved_ability: str
    unit_tag: int
    target_key: Optional[int]
    issued_iteration: int
    issued_time: float
    executor_context: Optional[Dict[str, Any]] = None


class AbilityRecorderManager(ManagerBase):
    """Records macro ability sequences with paired global/local observations."""

    def __init__(self) -> None:
        super().__init__()
        config = get_config()
        self.enabled = config["general"].getboolean("write_ability_sequence", fallback=True)
        self.output_dir = config["general"].get("ability_sequence_dir", DEFAULT_OUTPUT_DIR)
        self.data_ref_path = config["general"].get(
            "data_ref_path", os.path.join("data_ref", "data_base_add_graph.json")
        )
        self.sequence: List[Dict[str, Any]] = []
        self._other_abilities: Set[str] = set()
        self._pending: Dict[Tuple[int, str, Optional[int]], PendingAction] = {}
        self._seq = 0

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.enabled = self.knowledge.config["general"].getboolean("write_ability_sequence", fallback=True)
        self.output_dir = self.knowledge.config["general"].get("ability_sequence_dir", DEFAULT_OUTPUT_DIR)
        self.data_ref_path = self.knowledge.config["general"].get(
            "data_ref_path", os.path.join("data_ref", "data_base_add_graph.json")
        )
        if self.sequence:
            self._seq = len(self.sequence)
        else:
            self._other_abilities = set()
            self._pending = {}
            self._seq = 0

    async def update(self):
        pass

    async def post_update(self):
        if not self.enabled or not self._pending:
            return

        resolved: List[Tuple[Tuple[int, str, Optional[int]], PendingAction]] = []
        for key, pending in self._pending.items():
            if self._is_committed(pending):
                self._commit(pending)
                resolved.append((key, pending))
            elif self._is_expired(pending):
                resolved.append((key, pending))

        for key, _ in resolved:
            self._pending.pop(key, None)

    def record(self, action: UnitCommand) -> None:
        if not self.enabled:
            return

        bot = self.ai if hasattr(self, "ai") else action.unit._bot_object
        ability_name = action.ability.name
        loader = get_data_ref_loader(self.data_ref_path)

        # 对于“升级/变形”类全局能力（例如 CC -> OC / OC -> PF），bot 可能会在
        # 动作已经处于执行中时反复下发同一条命令。
        # 如果 actor.orders 已经包含该 ability，就直接跳过 pending 创建，避免重复记录。
        if (
            action.unit is not None
            and action.ability in MORPH_RESULT_TYPES
            and self._unit_has_ability_order(action.unit, action.ability)
        ):
            return

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

        key = (action.unit.tag, resolved_ability, self._target_key(action.target))
        if key in self._pending:
            return

        self._pending[key] = PendingAction(
            action=action,
            resolved_ability=resolved_ability,
            unit_tag=action.unit.tag,
            target_key=self._target_key(action.target),
            issued_iteration=getattr(getattr(self, "knowledge", None), "iteration", 0),
            issued_time=bot.time,
            executor_context=self._capture_train_executor_context(
                bot,
                action,
                resolved_ability,
                semantic_target,
            ),
        )

    def _target_key(self, target: Optional[Union[Unit, Point2]]) -> Optional[int]:
        if isinstance(target, Unit):
            return target.tag
        return None

    def _find_unit(self, tag: int) -> Optional[Unit]:
        unit = self.ai.unit_cache.by_tag(tag)
        if unit is not None:
            return unit
        return self.ai.units.find_by_tag(tag)

    def _unit_has_ability_order(self, unit: Unit, ability_id: AbilityId) -> bool:
        for order in unit.orders:
            if order.ability.id == ability_id:
                return True
        return False

    def _is_expired(self, pending: PendingAction) -> bool:
        return self.ai.time - pending.issued_time > PENDING_EXPIRE_SECONDS

    def _is_committed(self, pending: PendingAction) -> bool:
        actor = self._find_unit(pending.unit_tag)
        if actor is None:
            return False

        ability_id = pending.action.ability
        if self._unit_has_ability_order(actor, ability_id):
            return True

        morph_result = MORPH_RESULT_TYPES.get(ability_id)
        if morph_result is not None and actor.type_id == morph_result:
            return True

        target = pending.action.target
        if isinstance(target, Unit):
            host = self._find_unit(target.tag)
        elif pending.resolved_ability.startswith(("BUILD_TECHLAB", "BUILD_REACTOR")):
            host = actor
        else:
            host = None

        if host is not None:
            if self._unit_has_ability_order(host, ability_id):
                return True
            if pending.resolved_ability.startswith(("BUILD_TECHLAB", "BUILD_REACTOR")):
                if host.add_on_tag:
                    addon = self._find_unit(host.add_on_tag)
                    if addon is not None and not addon.is_ready:
                        return True

        return False

    def _commit(self, pending: PendingAction) -> None:
        action = pending.action
        bot = self.ai
        loader = get_data_ref_loader(self.data_ref_path)
        semantic_target = loader.get_semantic_target(pending.resolved_ability)
        if semantic_target is None:
            return

        entry: Dict[str, Any] = {
            "seq": self._seq,
            "game_time": round(bot.time, 2),
            "ability": pending.resolved_ability,
            "semantic_target": semantic_target,
            "obs": self._capture_obs(bot),
            "local_obs": collect_entities(bot, self.data_ref_path),
        }
        if pending.executor_context is not None:
            entry["executor_context"] = pending.executor_context
        place = self._serialize_place(action.target, semantic_target["type"])
        if place is not None:
            entry["place"] = place
        self.sequence.append(entry)
        self._seq += 1

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
        if not self.sequence and not self._other_abilities:
            return

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
        path = os.path.join(self.output_dir, file_name)

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
                "recorded_at": datetime.now().isoformat(),
            },
            "sequence": self.sequence,
            "other_abilities": sorted(self._other_abilities),
            "order_list": order_list,
        }

        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        self.print(
            f"Saved {len(self.sequence)} macro actions and {len(self._other_abilities)} other abilities to {path}",
            stats=False,
        )
