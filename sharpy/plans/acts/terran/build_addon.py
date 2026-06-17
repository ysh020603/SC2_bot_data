import warnings
from typing import Dict, TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from sharpy.plans.acts.act_base import ActBase
from sc2.unit import Unit

if TYPE_CHECKING:
    from sharpy.knowledges import Knowledge


TERRAN_ADDON_CLEARANCE_RADIUS = 3.0


class BuildAddon(ActBase):
    """Act of starting to build new buildings up to specified count"""

    def __init__(self, unit_type: UnitTypeId, unit_from_type: UnitTypeId, to_count: int):
        assert unit_type is not None and isinstance(unit_type, UnitTypeId)
        assert unit_from_type is not None and isinstance(unit_from_type, UnitTypeId)
        assert to_count is not None and isinstance(to_count, int)

        self.unit_from_type = unit_from_type
        self.unit_type = unit_type
        self.to_count = to_count

        self.tried_to_build_dict: Dict[int, float] = {}

        super().__init__()

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)

    async def execute(self) -> bool:
        count = self.get_quick_count(self.unit_type)
        if count >= self.to_count:
            return True  # Step is done

        pending_orders = sum(1 for tried_at in self.tried_to_build_dict.values() if tried_at + 15 > self.ai.time)
        if count + pending_orders >= self.to_count:
            return False

        unit = self.ai._game_data.units[self.unit_type.value]
        cost = self.ai._game_data.calculate_ability_cost(unit.creation_ability)

        if not self.knowledge.can_afford(self.unit_type):
            self.knowledge.reserve(cost.minerals, cost.vespene)
            return False

        builder: Unit
        for builder in self.cache.own(self.unit_from_type).ready.idle:
            if builder.add_on_tag == 0 and not builder.is_flying:

                # if self.tried_to_build_dict.get(builder.tag, 0) + 0.5 > ai.time:
                # continue # Prevent crashes by only trying to build twice per seconds

                center: Point2 = builder.add_on_position

                # 挂件不能直接用 UnitTypeId.*TECHLAB 做 can_place_single 查询：SC2
                # placement query 对挂件 unit type 基本返回 False。这里沿用 Sharpy
                # 的稳定做法，用 2x2 supply depot footprint 验证右侧挂件区域。
                # 起飞问题由调度器改走 BuildAddon 路径规避：不再裸发 BUILD_TECHLAB_*
                # ability 给建筑。
                if await self._can_build_addon_here(builder, center):
                    self.tried_to_build_dict[builder.tag] = self.ai.time
                    self.print(f"{self.unit_type} to {center}")
                    builder.build(self.unit_type)
                    return False
                else:
                    self.print("no space")
        return False

    async def _can_build_addon_here(self, builder: Unit, center: Point2) -> bool:
        if self.tried_to_build_dict.get(builder.tag, 0) + 0.5 > self.ai.time:
            return False

        if not await self.ai.find_placement(UnitTypeId.SUPPLYDEPOT, center, 0, False):
            return False

        if self._is_addon_slot_too_close_to_static_objects(center, builder.tag):
            return False

        for structure in self.ai.structures:
            if structure.tag == builder.tag or structure.is_flying:
                continue

            half_size = 1.0 if structure.type_id in self._addon_types() else 1.5
            if self._blocks_terran_addon_clearance(structure.position, half_size, center):
                return False

        if self.ai.units.not_flying.closer_than(1.2, center).exists:
            return False

        return True

    def _is_addon_slot_too_close_to_static_objects(self, center: Point2, builder_tag: int) -> bool:
        for group_name in ("structures", "mineral_field", "vespene_geyser"):
            group = getattr(self.ai, group_name, None)
            if not group:
                continue
            for unit in group.closer_than(TERRAN_ADDON_CLEARANCE_RADIUS, center):
                if getattr(unit, "tag", None) == builder_tag:
                    continue
                return True
        return False

    @staticmethod
    def _footprints_overlap(a: Point2, a_half_size: float, b: Point2, b_half_size: float) -> bool:
        limit = a_half_size + b_half_size + 0.1
        return abs(a.x - b.x) <= limit and abs(a.y - b.y) <= limit

    @staticmethod
    def _blocks_terran_addon_clearance(point: Point2, half_size: float, addon_center: Point2) -> bool:
        if BuildAddon._footprints_overlap(point, half_size, addon_center, 1.0):
            return True
        return point.distance_to(addon_center) < TERRAN_ADDON_CLEARANCE_RADIUS

    @staticmethod
    def _addon_types() -> set:
        return {
            UnitTypeId.TECHLAB,
            UnitTypeId.REACTOR,
            UnitTypeId.BARRACKSTECHLAB,
            UnitTypeId.BARRACKSREACTOR,
            UnitTypeId.FACTORYTECHLAB,
            UnitTypeId.FACTORYREACTOR,
            UnitTypeId.STARPORTTECHLAB,
            UnitTypeId.STARPORTREACTOR,
        }

    def get_quick_count(self, unit_type: UnitTypeId) -> int:
        """Calculates how many buildings there are already, including pending structures."""
        return self.cache.own(unit_type).amount
