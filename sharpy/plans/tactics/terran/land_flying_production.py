"""``LandFlyingProduction`` — recover Terran production buildings that ended up
flying (e.g. an old replay left them airborne, or the SC2 engine momentarily
lifted one before our addon predicate caught it).

This is a free, always-on tactic: it never spends resources. It scans for
``BARRACKSFLYING`` / ``FACTORYFLYING`` / ``STARPORTFLYING`` and orders each to
``LAND`` on the closest empty grid slot from
``BuildingSolver.buildings3x3``. That keeps the LLM observation honest (it sees
``Barracks`` rather than ``BarracksFlying``) and prevents wasted re-builds.
"""

from typing import Optional, Set

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit

from sharpy.managers.core import BuildingSolver
from sharpy.plans.acts import ActBase

_FLYING_TO_LAND = {
    UnitTypeId.BARRACKSFLYING: AbilityId.LAND_BARRACKS,
    UnitTypeId.FACTORYFLYING: AbilityId.LAND_FACTORY,
    UnitTypeId.STARPORTFLYING: AbilityId.LAND_STARPORT,
}

_ADDON_TYPES = {
    UnitTypeId.TECHLAB,
    UnitTypeId.REACTOR,
    UnitTypeId.BARRACKSTECHLAB,
    UnitTypeId.BARRACKSREACTOR,
    UnitTypeId.FACTORYTECHLAB,
    UnitTypeId.FACTORYREACTOR,
    UnitTypeId.STARPORTTECHLAB,
    UnitTypeId.STARPORTREACTOR,
}


class LandFlyingProduction(ActBase):
    """Land any flying Barracks/Factory/Starport onto a free 3x3 grid slot."""

    building_solver: BuildingSolver

    def __init__(self):
        super().__init__()
        self._managed_tags: Set[int] = set()

    async def start(self, knowledge):
        await super().start(knowledge)
        self.building_solver = knowledge.get_required_manager(BuildingSolver)

    async def execute(self) -> bool:
        flying_types = set(_FLYING_TO_LAND)
        flyers = self.cache.own(flying_types)
        self._clear_finished_targets()
        if not flyers:
            return True

        for flyer in flyers:  # type: Unit
            if flyer.is_using_ability(AbilityId.LAND_BARRACKS) or flyer.is_using_ability(
                AbilityId.LAND_FACTORY
            ) or flyer.is_using_ability(AbilityId.LAND_STARPORT):
                continue
            land_ability = _FLYING_TO_LAND.get(flyer.type_id)
            if land_ability is None:
                continue

            occupied = self._occupied_positions(flyer.tag)
            slot = self.building_solver.structure_target_move_location.get(flyer.tag)
            if slot is None or not await self._can_land_with_addon_space(slot, occupied, land_ability):
                slot = await self._closest_free_slot(flyer.position, occupied, land_ability)
            if slot is None:
                continue

            self._managed_tags.add(flyer.tag)
            self.building_solver.structure_target_move_location[flyer.tag] = slot
            await self._move_or_land(flyer, slot, land_ability)
        return True

    def _clear_finished_targets(self) -> None:
        for tag, target in list(self.building_solver.structure_target_move_location.items()):
            unit = self.cache.by_tag(tag)
            if unit is None:
                self.building_solver.structure_target_move_location.pop(tag, None)
                self._managed_tags.discard(tag)

        for tag in list(self._managed_tags):
            target = self.building_solver.structure_target_move_location.get(tag)
            unit = self.cache.by_tag(tag)
            if unit is None or (target is not None and not unit.is_flying and unit.position == target):
                self.building_solver.structure_target_move_location.pop(tag, None)
                self._managed_tags.discard(tag)

    def _occupied_positions(self, current_tag: int) -> Set[Point2]:
        occupied: Set[Point2] = {b.position for b in self.ai.structures if not b.is_flying}
        for tag, target in self.building_solver.structure_target_move_location.items():
            if tag != current_tag:
                occupied.add(target)
        return occupied

    async def _move_or_land(self, flyer: Unit, slot: Point2, land_ability: AbilityId) -> None:
        if flyer.distance_to(slot) > 2:
            if not flyer.is_moving or not isinstance(flyer.order_target, Point2) or flyer.order_target != slot:
                flyer.move(slot)
            return
        flyer(land_ability, slot)

    async def _closest_free_slot(
        self, near: Point2, occupied: Set[Point2], land_ability: AbilityId
    ) -> Optional[Point2]:
        for point in self._candidate_slots(near):
            if await self._can_land_with_addon_space(point, occupied, land_ability):
                return point
        return None

    def _candidate_slots(self, near: Point2):
        seen = set()
        for point in sorted(self.building_solver.buildings3x3, key=lambda p: near.distance_to(p)):
            seen.add((point.x, point.y))
            yield point

        base_x = round(near.x)
        base_y = round(near.y)
        for radius in range(2, 31, 2):
            for dx in range(-radius, radius + 1, 2):
                for dy in (-radius, radius):
                    key = (base_x + dx, base_y + dy)
                    if key not in seen:
                        seen.add(key)
                        yield Point2(key)
            for dy in range(-radius + 2, radius - 1, 2):
                for dx in (-radius, radius):
                    key = (base_x + dx, base_y + dy)
                    if key not in seen:
                        seen.add(key)
                        yield Point2(key)

    async def _can_land_with_addon_space(
        self, point: Point2, occupied: Set[Point2], land_ability: AbilityId
    ) -> bool:
        if point in occupied:
            return False
        if self.ai.structures.not_flying.closer_than(1, point).exists:
            return False
        if self.ai.units.not_flying.closer_than(1.5, point).exists:
            return False
        if not (await self.ai.can_place(land_ability, [point]))[0]:
            return False

        addon_center = point.offset(Point2((2.5, -0.5)))
        addon_at_slot = self.ai.structures.of_type(_ADDON_TYPES).filter(
            lambda structure: structure.add_on_land_position == point
        )
        if not addon_at_slot and not await self.ai.find_placement(UnitTypeId.SUPPLYDEPOT, addon_center, 0, False):
            return False
        if self.ai.units.not_flying.closer_than(1.2, addon_center).exists:
            return False

        for structure in self.ai.structures:
            if structure.is_flying:
                continue
            if structure.type_id in _ADDON_TYPES and structure.add_on_land_position == point:
                continue
            half_size = 1.0 if structure.type_id in _ADDON_TYPES else 1.5
            if self._footprints_overlap(addon_center, 1.0, structure.position, half_size):
                return False
        return True

    @staticmethod
    def _footprints_overlap(a: Point2, a_half_size: float, b: Point2, b_half_size: float) -> bool:
        limit = a_half_size + b_half_size + 0.1
        return abs(a.x - b.x) <= limit and abs(a.y - b.y) <= limit
