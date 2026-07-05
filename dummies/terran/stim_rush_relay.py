import random

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from sharpy.combat import MoveType
from sharpy.interfaces import IZoneManager
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, Step, SequentialList, StepBuildGas
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.require import *
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *
from sharpy.utils import select_build_index


class DodgeRampAttack(PlanZoneAttack):
    async def execute(self) -> bool:
        base_ramp = self.zone_manager.expansion_zones[-1].ramp
        for effect in self.ai.state.effects:
            if effect.id != "FORCEFIELD":
                continue
            pos: Point2 = base_ramp.bottom_center
            for epos in effect.positions:
                if pos.distance_to_point2(epos) < 5:
                    return await self.small_retreat()

        return await super().execute()

    async def small_retreat(self):
        attacking_units = self.roles.attacking_units
        natural = self.zone_manager.expansion_zones[-2]

        for unit in attacking_units:
            self.combat.add_unit(unit)

        self.combat.execute(natural.gather_point, MoveType.DefensiveRetreat)
        return False


class StimRushRelay(KnowledgeBot):
    tactic_index: int
    zone_manager: IZoneManager

    def __init__(self, build_name: str = "default"):
        super().__init__("Stim Rush Relay")
        self.build_name = build_name

    async def on_start(self):
        await super().on_start()
        self.zone_manager = self.knowledge.get_required_manager(IZoneManager)

    async def pre_step_execute(self):
        if self.tactic_index != 1 and self.time < 5 * 60:
            self.knowledge.gather_point = self.zone_manager.expansion_zones[-2].gather_point

    async def create_plan(self) -> BuildOrder:
        if self.build_name == "default":
            self.tactic_index = select_build_index(self.knowledge, "build.stimrush", 0, 1)
        else:
            self.tactic_index = int(self.build_name)

        if self.tactic_index == 0:
            self.knowledge.print("2-Rax pressure -> Stim expand", "Build")
            self.attack = DodgeRampAttack(26)
            chunk = [
                Step(Supply(14), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 1), GridBuilding(UnitTypeId.BARRACKS, 1)),
                BuildGas(1),
                Step(UnitExists(UnitTypeId.MARINE, 2), GridBuilding(UnitTypeId.BARRACKS, 2)),
                Step(None, GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
                Expand(2),
            ]
        else:
            self.knowledge.print("Reaper expand -> Stim timing", "Build")
            self.attack = DodgeRampAttack(30)
            chunk = [
                Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 1), GridBuilding(UnitTypeId.BARRACKS, 1)),
                BuildGas(1),
                Step(UnitReady(UnitTypeId.BARRACKS, 1), ActUnit(UnitTypeId.REAPER, UnitTypeId.BARRACKS, 1)),
                Expand(2),
                Step(None, GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
                GridBuilding(UnitTypeId.BARRACKS, 2),
            ]

        follow_up = [
            BuildGas(2),
            Step(None, BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1)),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            GridBuilding(UnitTypeId.BARRACKS, 3),
            Step(None, BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1)),
            Step(None, GridBuilding(UnitTypeId.FACTORY, 1)),
            Step(None, GridBuilding(UnitTypeId.STARPORT, 1), skip_until=UnitReady(UnitTypeId.FACTORY, 1)),
            Tech(UpgradeId.SHIELDWALL),
            Step(None, BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1)),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            Step(None, GridBuilding(UnitTypeId.BARRACKS, 5)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 2)),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 2), Tech(UpgradeId.PUNISHERGRENADES)),
            Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            BuildGas(3),
            Step(Minerals(400), GridBuilding(UnitTypeId.BARRACKS, 7)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4)),
            Expand(3),
            BuildGas(4),
            Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2),
            Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL2),
        ]

        mine_block = [
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                ActUnit(UnitTypeId.WIDOWMINE, UnitTypeId.FACTORY, 4),
            ),
        ]

        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
        self.distribute_workers = DistributeWorkers()

        self.second_attack = PlanZoneAttack(50)

        tactics = [
            MineOpenBlockedBase(),
            PlanCancelBuilding(),
            LowerDepots(),
            PlanZoneDefense(),
            worker_scout,
            Step(None, CallMule(50), skip=Time(5 * 60)),
            Step(None, CallMule(100), skip_until=Time(5 * 60)),
            Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
            self.distribute_workers,
            Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
            ManTheBunkers(),
            Repair(),
            ContinueBuilding(),
            PlanZoneGatherTerran(),
            Step(
                TechReady(UpgradeId.STIMPACK, 0.75),
                self.attack,
            ),
            Step(
                UnitExists(UnitTypeId.MEDIVAC, 2),
                self.second_attack,
            ),
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            AutoDepot(),
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            [Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44))],
            chunk,
            follow_up,
            mine_block,
            [
                ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 8),
                ActUnit(UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, 6),
                ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 20),
                ActUnit(UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, 12),
                ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 60),
            ],
            [
                Step(UnitReady(UnitTypeId.STARPORT, 1), ActUnit(UnitTypeId.MEDIVAC, UnitTypeId.STARPORT, 6)),
            ],
            SequentialList(tactics),
        )


class LadderBot(StimRushRelay):
    @property
    def my_race(self):
        return Race.Terran
