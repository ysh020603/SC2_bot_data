from math import floor
from typing import Dict, Optional, Set

from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.pixel_map import PixelMap
from sharpy.sc2math import to_new_ticks

from sharpy.managers.core.roles import UnitTask
from sharpy.utils import map_to_point2s_center
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit

from .act_building import ActBuilding
from sharpy.interfaces import IBuildingSolver, IIncomeCalculator
from sharpy.managers.core import PathingManager

worker_trainers = {AbilityId.NEXUSTRAIN_PROBE, AbilityId.COMMANDCENTERTRAIN_SCV}

# 同一坐标连续失败超过该阈值后进入黑名单，强制遍历下一个备选格
INVALID_POSITION_THRESHOLD = 20

TERRAN_PRODUCTION_BUILDINGS = {
    UnitTypeId.BARRACKS,
    UnitTypeId.FACTORY,
    UnitTypeId.STARPORT,
}

TERRAN_PRODUCTION_TO_FLYING = {
    UnitTypeId.BARRACKS: UnitTypeId.BARRACKSFLYING,
    UnitTypeId.FACTORY: UnitTypeId.FACTORYFLYING,
    UnitTypeId.STARPORT: UnitTypeId.STARPORTFLYING,
}

TERRAN_FLYING_TO_PRODUCTION = {
    UnitTypeId.BARRACKSFLYING: UnitTypeId.BARRACKS,
    UnitTypeId.FACTORYFLYING: UnitTypeId.FACTORY,
    UnitTypeId.STARPORTFLYING: UnitTypeId.STARPORT,
}

TERRAN_ADDONS = {
    UnitTypeId.TECHLAB,
    UnitTypeId.REACTOR,
    UnitTypeId.BARRACKSTECHLAB,
    UnitTypeId.BARRACKSREACTOR,
    UnitTypeId.FACTORYTECHLAB,
    UnitTypeId.FACTORYREACTOR,
    UnitTypeId.STARPORTTECHLAB,
    UnitTypeId.STARPORTREACTOR,
}

TERRAN_ADDON_CLEARANCE_RADIUS = 3.0


class WorkerStuckStatus:
    def __init__(self):
        self.tag_stuck: Optional[int] = None
        self.last_move_detected_time: Optional[float] = None
        self.current_tag: Optional[int] = None
        self.last_moved_position: Optional[Point2] = None
        self.last_iteration_asked = 0

    def need_new_worker(self, current_worker: Unit, time: float, target: Point2, iteration: int) -> bool:
        if self.last_iteration_asked < iteration - 1:
            # reset
            self.tag_stuck = None
            self.current_tag = current_worker.tag
            self.last_move_detected_time = time
            self.last_moved_position = current_worker.position
            return False

        self.last_iteration_asked = iteration

        if current_worker.tag == self.current_tag:
            if target.distance_to_point2(current_worker.position) < 2.5:
                return False  # Worker is close enough to destination, not stuck
            if self.last_moved_position is None:
                self.last_moved_position = current_worker.position
            elif self.last_moved_position.distance_to_point2(current_worker.position) > 0.5:
                self.last_move_detected_time = time
                self.last_moved_position = current_worker.position
            elif time - self.last_move_detected_time > 1:
                self.tag_stuck = self.current_tag
                return True
        elif self.tag_stuck == current_worker.tag:
            return True

        # reset
        self.tag_stuck = None
        self.current_tag = current_worker.tag
        self.last_move_detected_time = time
        self.last_moved_position = current_worker.position
        return False


class GridBuilding(ActBuilding):

    building_solver: IBuildingSolver
    income_calculator: IIncomeCalculator
    pather: Optional[PathingManager]
    last_iteration_moved: int

    def __init__(
        self,
        unit_type: UnitTypeId,
        to_count: int = 1,
        iterator: Optional[int] = None,
        priority: bool = False,
        allow_wall: bool = True,
        consider_worker_production: bool = True,
    ):
        super().__init__(unit_type, to_count)
        self.allow_wall = allow_wall
        assert isinstance(priority, bool)
        self.priority = priority
        self.only_roles = [UnitTask.Idle, UnitTask.Building, UnitTask.Gathering]
        self.builder_tag: Optional[int] = None
        # ??????????? DONE ?? WARN ??????7.4 / ?11.8?
        self.actual_placements: int = 0
        self.iterator: Optional[int] = iterator
        self.consider_worker_production = consider_worker_production
        self.building_solver: IBuildingSolver = None
        self.make_pylon = None
        self.last_iteration_moved = -10
        self.worker_stuck: WorkerStuckStatus = WorkerStuckStatus()
        # 动态容错黑名单：记录各建造点连续失败/被选中的帧数
        self.invalid_positions: Dict[Point2, int] = {}

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.building_solver = self.knowledge.get_required_manager(IBuildingSolver)
        self.pather = self.knowledge.get_manager(PathingManager)
        self.income_calculator = self.knowledge.get_required_manager(IIncomeCalculator)
        # 只有神族需要水晶塔供能；人族/虫族建筑找不到落点时不应去造 Pylon，
        # 否则会陷入「反复尝试造 Pylon」的空转（人族根本无法建造）。
        if self.knowledge.my_race == Race.Protoss and self.unit_type != UnitTypeId.PYLON:
            self.make_pylon: Optional[GridBuilding] = GridBuilding(UnitTypeId.PYLON, 0, 2)
            await self.make_pylon.start(knowledge)

    async def execute(self) -> bool:
        count = self._get_terran_equivalent_count() if self.unit_type in TERRAN_PRODUCTION_BUILDINGS else self.get_count(
            self.unit_type, include_pending=False, include_not_ready=True
        )

        if count >= self.to_count:
            if self.builder_tag is not None:
                self.clear_worker()

            return True  # Step is done

        if (
            count + self.pending_build(self.unit_type) >= self.to_count
        ):
            if self.builder_tag is not None:
                worker = self.cache.by_tag(self.builder_tag)
                if worker is not None:
                    self.set_worker(worker)
                    await self.debug_draw()
            return True  # Building is ordered

        if self.knowledge.my_race == Race.Protoss:
            position = self.position_protoss(count)
        elif self.knowledge.my_race == Race.Terran:
            position = await self.position_terran(count)
        else:
            position = self.position_zerg(count)

        if position is None:
            if self.make_pylon is not None:
                self.make_pylon.to_count = len(self.cache.own(UnitTypeId.PYLON).ready) + 1
                await self.make_pylon.execute()
            else:
                self.print(f"Can't find free position to build {self.unit_type.name} in!")
            return False  # Stuck and cannot proceed

        worker = self.get_worker_builder(position, self.builder_tag, self.only_roles)  # type: Unit

        if worker is None:
            self.builder_tag = None
            return False  # Cannot proceed

        if self.worker_stuck.need_new_worker(worker, self.ai.time, position, self.knowledge.iteration):
            self.print(f"Worker {worker.tag} was found stuck!")
            self.roles.set_task(UnitTask.Reserved, worker)  # Set temp reserved for the stuck worker.
            worker = self.get_worker_builder(position, None, self.only_roles)

        if self.has_build_order(worker):
            self.set_worker(worker)
            return False

        d = worker.distance_to(position)
        time = d / to_new_ticks(worker.movement_speed)

        if self.last_iteration_moved >= self.knowledge.iteration - 1:
            # stop indecisiveness
            time += 5

        unit = self.ai._game_data.units[self.unit_type.value]
        cost = self.ai._game_data.calculate_ability_cost(unit.creation_ability)

        wait_time = self.prequisite_progress()

        adjusted_income = self.income_calculator.mineral_income * 0.93  # 14 / 15 = 0.933333

        if self.knowledge.can_afford(self.unit_type, check_supply_cost=False):
            if wait_time <= 0:
                self.set_worker(worker)
                if worker.tag not in self.ai.unit_tags_received_action and not self.has_build_order(worker):
                    # No duplicate builds
                    pos_formatted = f"({position.x:.1f}, {position.y:.1f})"
                    self.print(f"{self.unit_type.name} at {pos_formatted}")
                    if self.knowledge.my_race == Race.Protoss:
                        await self.build_protoss(worker, count, position)
                    elif self.knowledge.my_race == Race.Terran:
                        await self.build_terran(worker, count, position)
                    else:
                        await self.build_zerg(worker, count, position)
                return False

            if self.priority and wait_time < time:
                # Go wait
                self.set_worker(worker)
                self.knowledge.reserve(cost.minerals, cost.vespene)
                if not self.has_build_order(worker):
                    worker.move(self.adjust_build_to_move(position))
                    self.last_iteration_moved = self.knowledge.iteration

        elif self.priority and wait_time < time:
            available_minerals = self.ai.minerals - self.knowledge.reserved_minerals
            available_gas = self.ai.vespene - self.knowledge.reserved_gas

            if self.consider_worker_production and adjusted_income > 0:
                for town_hall in self.ai.townhalls:  # type: Unit
                    # TODO: Zerg(?)
                    if town_hall.orders:
                        starting_next_probe_in = -50 / adjusted_income
                        order = town_hall.orders[0]  # Only consider first order
                        if order.ability.id in worker_trainers:
                            starting_next_probe_in += 12 * (1 - order.progress)

                        if starting_next_probe_in < time:
                            available_minerals -= 50  # should start producing workers soon now
                    else:
                        available_minerals -= 50  # should start producing workers soon now

            if (
                available_minerals + time * adjusted_income >= cost.minerals
                and available_gas + time * self.income_calculator.gas_income >= cost.vespene
            ):
                # Go wait
                self.set_worker(worker)
                self.knowledge.reserve(cost.minerals, cost.vespene)

                if not self.has_build_order(worker):
                    worker.move(self.adjust_build_to_move(position))
                    self.last_iteration_moved = self.knowledge.iteration

        return False

    def adjust_build_to_move(self, position: Point2) -> Point2:
        closest_zone: Optional[Point2] = None
        if self.pather:
            zone_index = self.pather.map.get_zone(position)
            if zone_index > 0:
                closest_zone = self.zone_manager.expansion_zones[zone_index - 1].center_location

        if closest_zone is None:
            closest_zone = position.closest(map_to_point2s_center(self.zone_manager.expansion_zones))

        return position.towards(closest_zone, 1)

    async def debug_actions(self):
        if self.builder_tag is not None:
            worker: Unit = self.cache.by_tag(self.builder_tag)

            if worker and worker.orders:
                moving_status = ""
                for order in worker.orders:
                    if moving_status != "":
                        moving_status += ", "
                    moving_status += order.ability.id.name
                self.client.debug_text_world(moving_status, worker.position3d)

    def set_worker(self, worker: Optional[Unit]) -> bool:
        if worker:
            self.roles.set_task(UnitTask.Building, worker)
            self.builder_tag = worker.tag
            return True

        self.builder_tag = None
        return False

    def clear_worker(self):
        if self.builder_tag is not None:
            self.roles.clear_task(self.builder_tag)
            self.builder_tag = None

    def position_protoss(self, count) -> Optional[Point2]:
        is_pylon = self.unit_type == UnitTypeId.PYLON
        buildings = self.ai.structures
        matrix = self.ai.state.psionic_matrix
        future_position = None

        iterator = self.get_iterator(is_pylon, count)

        if is_pylon:
            for point in self.building_solver.buildings2x2[::iterator]:
                if not buildings.closer_than(1, point):
                    return point
        else:
            pylons = self.cache.own(UnitTypeId.PYLON).not_ready
            for point in self.building_solver.buildings3x3[::iterator]:
                if not self.allow_wall:
                    if point in self.building_solver.wall3x3:
                        continue
                if not buildings.closer_than(1, point) and matrix.covers(point):
                    return point

                if future_position is None and pylons and point.distance_to_closest(pylons) <= 7:
                    future_position = point

        return future_position

    def position_zerg(self, count) -> Optional[Point2]:
        buildings = self.ai.structures
        creep = self.ai.state.creep
        future_position = None

        for point in self.building_solver.buildings3x3:
            if not buildings.closer_than(1, point) and self.is_on_creep(creep, point):
                return point

        return future_position

    def _is_position_blacklisted(self, point: Point2) -> bool:
        return self.invalid_positions.get(point, 0) >= INVALID_POSITION_THRESHOLD

    def _bump_invalid_position(self, point: Point2) -> None:
        self.invalid_positions[point] = self.invalid_positions.get(point, 0) + 1

    def _get_terran_equivalent_count(self) -> int:
        flying_type = TERRAN_PRODUCTION_TO_FLYING.get(self.unit_type)
        if flying_type is None:
            return self.get_count(self.unit_type, include_pending=False, include_not_ready=True)
        grounded = self.get_count(self.unit_type, include_pending=False, include_not_ready=True)
        flying = self.cache.own(flying_type).amount
        return grounded + flying

    async def _validate_terran_point(
        self,
        point: Point2,
        buildings,
        blocking_units,
        creation_ability: AbilityId,
        collision_radius: float,
    ) -> bool:
        """
        三步协同校验：黑名单 -> 建筑/单位碰撞 -> SC2 内核 can_place。
        任一失败则累加失败计数，便于后续帧拉黑并尝试列表中的下一个格子。
        """
        if self._is_position_blacklisted(point):
            return False

        if buildings.closer_than(1, point):
            self._bump_invalid_position(point)
            return False

        if blocking_units.closer_than(collision_radius, point).exists:
            self._bump_invalid_position(point)
            return False

        building_half_size = 1.0 if self.unit_type == UnitTypeId.SUPPLYDEPOT else 1.5
        if self._blocks_reserved_terran_production_body(point, building_half_size):
            self._bump_invalid_position(point)
            return False

        if self._blocks_reserved_terran_addon_slot(point, building_half_size):
            self._bump_invalid_position(point)
            return False

        if self.unit_type in TERRAN_PRODUCTION_BUILDINGS:
            if not await self._has_free_terran_addon_slot(point, buildings, blocking_units):
                self._bump_invalid_position(point)
                return False
            if not await self.ai.find_placement(self.unit_type, point, 0, False, addon_place=True):
                self._bump_invalid_position(point)
                return False
        elif not (await self.ai.can_place(creation_ability, [point]))[0]:
            self._bump_invalid_position(point)
            return False

        return True

    def _blocks_reserved_terran_addon_slot(self, point: Point2, building_half_size: float) -> bool:
        """True if this candidate would occupy another producer's addon slot."""
        for addon_center in self._reserved_terran_addon_positions():
            if self._blocks_terran_addon_clearance(point, building_half_size, addon_center):
                return True
        return False

    def _blocks_reserved_terran_production_body(self, point: Point2, building_half_size: float) -> bool:
        for body_center in self._reserved_terran_production_positions():
            if self._footprints_overlap(point, building_half_size, body_center, 1.5):
                return True
        return False

    async def _has_free_terran_addon_slot(self, point: Point2, buildings, blocking_units) -> bool:
        addon_center = point.offset(Point2((2.5, -0.5)))

        if not await self.ai.find_placement(UnitTypeId.SUPPLYDEPOT, addon_center, 0, False):
            return False

        if self._is_addon_slot_too_close_to_static_objects(addon_center):
            return False

        for structure in buildings:
            if structure.is_flying:
                continue
            half_size = 1.0 if structure.type_id in TERRAN_ADDONS else 1.5
            if self._blocks_terran_addon_clearance(structure.position, half_size, addon_center):
                return False

        for reserved_addon_center in self._reserved_terran_addon_positions():
            if addon_center.distance_to(reserved_addon_center) < TERRAN_ADDON_CLEARANCE_RADIUS:
                return False

        for reserved_landing in self.building_solver.structure_target_move_location.values():
            if self._blocks_terran_addon_clearance(reserved_landing, 1.5, addon_center):
                return False
            if self._blocks_terran_addon_clearance(point, 1.5, reserved_landing.offset(Point2((2.5, -0.5)))):
                return False

        return not blocking_units.closer_than(1.2, addon_center).exists

    def _is_addon_slot_too_close_to_static_objects(self, addon_center: Point2) -> bool:
        for group_name in ("structures", "mineral_field", "vespene_geyser"):
            group = getattr(self.ai, group_name, None)
            if not group:
                continue
            if group.closer_than(TERRAN_ADDON_CLEARANCE_RADIUS, addon_center).exists:
                return True
        return False

    def _reserved_terran_addon_positions(self) -> Set[Point2]:
        positions: Set[Point2] = set()
        production_types = TERRAN_PRODUCTION_BUILDINGS
        for building in self.ai.structures.of_type(production_types):
            if building.is_flying:
                continue
            positions.add(building.add_on_position)
        for tag, landing_position in self.building_solver.structure_target_move_location.items():
            positions.add(landing_position.offset(Point2((2.5, -0.5))))

        production_abilities = {
            self.ai._game_data.units[unit_type.value].creation_ability.id
            for unit_type in production_types
        }
        for worker in self.ai.workers:
            for order in worker.orders:
                if order.ability.id not in production_abilities:
                    continue
                build_position = Point2.from_proto(order.target)
                positions.add(build_position.offset(Point2((2.5, -0.5))))
        return positions

    def _reserved_terran_production_positions(self) -> Set[Point2]:
        positions: Set[Point2] = set(self.building_solver.structure_target_move_location.values())
        production_abilities = {
            self.ai._game_data.units[unit_type.value].creation_ability.id
            for unit_type in TERRAN_PRODUCTION_BUILDINGS
        }
        for worker in self.ai.workers:
            for order in worker.orders:
                if order.ability.id not in production_abilities:
                    continue
                positions.add(Point2.from_proto(order.target))
        return positions

    @staticmethod
    def _footprints_overlap(a: Point2, a_half_size: float, b: Point2, b_half_size: float) -> bool:
        limit = a_half_size + b_half_size + 0.1
        return abs(a.x - b.x) <= limit and abs(a.y - b.y) <= limit

    @staticmethod
    def _blocks_terran_addon_clearance(point: Point2, half_size: float, addon_center: Point2) -> bool:
        if GridBuilding._footprints_overlap(point, half_size, addon_center, 1.0):
            return True
        return point.distance_to(addon_center) < TERRAN_ADDON_CLEARANCE_RADIUS

    async def position_terran(self, count) -> Optional[Point2]:
        is_depot = self.unit_type == UnitTypeId.SUPPLYDEPOT
        buildings = self.ai.structures
        future_position = None

        # 步骤一：过滤会阻挡地面建造的地面单位（排除飞行单位与当前建造 SCV）
        blocking_units = self.ai.units.not_flying
        if self.builder_tag is not None:
            blocking_units = blocking_units.tags_not_in({self.builder_tag})

        unit_data = self.ai._game_data.units[self.unit_type.value]
        creation_ability: AbilityId = unit_data.creation_ability.id
        collision_radius = 1.2 if is_depot else 1.5

        if is_depot:
            for point in self.building_solver.buildings2x2:
                if await self._validate_terran_point(
                    point, buildings, blocking_units, creation_ability, collision_radius
                ):
                    return point
        else:
            pylons = self.cache.own(UnitTypeId.PYLON).not_ready
            reserved_landing_locations: Set[Point2] = set(self.building_solver.structure_target_move_location.values())
            for point in self.building_solver.buildings3x3:
                if not self.allow_wall:
                    if point in self.building_solver.wall3x3:
                        continue
                if point in reserved_landing_locations:
                    continue
                if point in self.building_solver.free_addon_locations:
                    continue

                if await self._validate_terran_point(
                    point, buildings, blocking_units, creation_ability, collision_radius
                ):
                    return point

                if future_position is None and pylons and point.distance_to_closest(pylons) <= 7:
                    future_position = point

        return future_position

    def get_iterator(self, is_pylon, count):
        if self.iterator is None:
            if is_pylon and count < 14:
                return 2
            return 1

        return self.iterator

    async def build_protoss(self, worker: Unit, count, position: Point2):
        if self.has_build_order(worker):
            # TODO: is this correct?
            worker.build(self.unit_type, position, queue=True)

        # TODO: Remake the error handling with frame delay
        worker.build(self.unit_type, position)
        self.actual_placements += 1

    async def build_zerg(self, worker: Unit, count, position: Point2):
        # try the selected position first
        # TODO: Remake the error handling with frame delay
        worker.build(self.unit_type, position)
        self.actual_placements += 1

    async def build_terran(self, worker: Unit, count, position: Point2):
        # try the selected position first
        # TODO: Remake the error handling with frame delay
        worker.build(self.unit_type, position)
        self.actual_placements += 1

    def is_on_creep(self, creep: PixelMap, point: Point2) -> bool:
        x_original = floor(point.x) - 1
        y_original = floor(point.y) - 1
        for x in range(x_original, x_original + 5):
            for y in range(y_original, y_original + 5):
                if not creep.is_set(Point2((x, y))):
                    return False
        return True

    def prequisite_progress(self) -> float:
        """Return progress in realtime seconds"""
        # Protoss:
        if self.unit_type == UnitTypeId.GATEWAY or self.unit_type == UnitTypeId.FORGE:
            return self.building_progress(UnitTypeId.PYLON)

        if self.unit_type == UnitTypeId.CYBERNETICSCORE:
            return min(self.building_progress(UnitTypeId.GATEWAY), self.building_progress(UnitTypeId.WARPGATE))

        if self.unit_type == UnitTypeId.TWILIGHTCOUNCIL:
            return self.building_progress(UnitTypeId.CYBERNETICSCORE)

        if self.unit_type == UnitTypeId.TEMPLARARCHIVE:
            return self.building_progress(UnitTypeId.TWILIGHTCOUNCIL)

        if self.unit_type == UnitTypeId.DARKSHRINE:
            return self.building_progress(UnitTypeId.TWILIGHTCOUNCIL)

        if self.unit_type == UnitTypeId.STARGATE:
            return self.building_progress(UnitTypeId.CYBERNETICSCORE)

        if self.unit_type == UnitTypeId.FLEETBEACON:
            return self.building_progress(UnitTypeId.STARGATE)

        if self.unit_type == UnitTypeId.ROBOTICSFACILITY:
            return self.building_progress(UnitTypeId.CYBERNETICSCORE)

        if self.unit_type == UnitTypeId.ROBOTICSBAY:
            return self.building_progress(UnitTypeId.ROBOTICSFACILITY)

        if self.unit_type == UnitTypeId.PHOTONCANNON:
            return self.building_progress(UnitTypeId.FORGE)

        if self.unit_type == UnitTypeId.SHIELDBATTERY:
            return self.building_progress(UnitTypeId.CYBERNETICSCORE)

        # Terran:
        if self.unit_type == UnitTypeId.BARRACKS:
            return self.building_progress(UnitTypeId.SUPPLYDEPOT)
        if self.unit_type == UnitTypeId.FACTORY:
            return self.building_progress(UnitTypeId.BARRACKS)
        if self.unit_type == UnitTypeId.ARMORY:
            return self.building_progress(UnitTypeId.FACTORY)
        if self.unit_type == UnitTypeId.STARPORT:
            return self.building_progress(UnitTypeId.FACTORY)
        if self.unit_type == UnitTypeId.FUSIONCORE:
            return self.building_progress(UnitTypeId.STARPORT)

        return 0
