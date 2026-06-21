from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, Step, SequentialList, StepBuildGas
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.require import *
from sharpy.plans.require.supply import SupplyType
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


class TankThorMech(KnowledgeBot):
    def __init__(self):
        super().__init__("Rusty Tank Thor Mech")

    async def create_plan(self) -> BuildOrder:
        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))

        scv = [
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 16 + 6), skip=UnitExists(UnitTypeId.COMMANDCENTER, 2)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 60)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 72)),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            StepBuildGas(1, Supply(16)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.REAPER, 1, only_once=True, priority=True)),
            Expand(2, priority=True),
            Step(Supply(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            BuildGas(2),
            GridBuilding(UnitTypeId.FACTORY, 1),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 2, priority=True)),
            DefensiveBuilding(UnitTypeId.BUNKER, DefensePosition.Entrance, 1),
            GridBuilding(UnitTypeId.FACTORY, 2),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(Supply(44, SupplyType.Workers), Expand(3)),
            GridBuilding(UnitTypeId.FACTORY, 4),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 3),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
            BuildGas(6),
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1)),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),
            GridBuilding(UnitTypeId.ARMORY, 2),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL2),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2),
            Step(Minerals(600), GridBuilding(UnitTypeId.FACTORY, 6)),
            Step(Minerals(900), GridBuilding(UnitTypeId.FACTORY, 8)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 6),
            Step(Minerals(500), Expand(4)),
        ]

        units = [
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 4, priority=True)),
            Step(UnitReady(UnitTypeId.ARMORY, 1), TerranUnit(UnitTypeId.THOR, 2, priority=True)),
            Step(UnitReady(UnitTypeId.STARPORT, 1), TerranUnit(UnitTypeId.VIKINGFIGHTER, 4, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.SIEGETANK, 24),
                TerranUnit(UnitTypeId.THOR, 16),
                TerranUnit(UnitTypeId.HELLION, 24),
                TerranUnit(UnitTypeId.VIKINGFIGHTER, 12),
                TerranUnit(UnitTypeId.LIBERATOR, 6),
            ),
        ]

        tactics = [
            MineOpenBlockedBase(),
            PlanCancelBuilding(),
            LowerDepots(),
            PlanZoneDefense(),
            worker_scout,
            Step(None, CallMule(50), skip=Time(5 * 60)),
            Step(None, CallMule(100), skip_until=Time(5 * 60)),
            Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
            DistributeWorkers(),
            Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
            ManTheBunkers(),
            Repair(),
            ContinueBuilding(),
            PlanZoneGatherTerran(),
            Step(All([UnitExists(UnitTypeId.SIEGETANK, 6, include_pending=True), UnitExists(UnitTypeId.THOR, 2, include_pending=True)]), PlanZoneAttack(80)),
            PlanFinishEnemy(),
        ]

        return BuildOrder(BuildOrder([]).depots, scv, buildings, units, SequentialList(tactics))


class LadderBot(TankThorMech):
    @property
    def my_race(self):
        return Race.Terran
