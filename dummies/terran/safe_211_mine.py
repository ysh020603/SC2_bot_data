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


class SafeTwoOneOneMine(KnowledgeBot):
    def __init__(self):
        super().__init__("Rusty Safe 2-1-1 Mine")

    async def create_plan(self) -> BuildOrder:
        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))

        scv = [
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 16 + 6), skip=UnitExists(UnitTypeId.COMMANDCENTER, 2)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 48)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 60)),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            StepBuildGas(1, Supply(16)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.REAPER, 1, only_once=True, priority=True)),
            Expand(2, priority=True),
            Step(Supply(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            GridBuilding(UnitTypeId.FACTORY, 1),
            BuildGas(2),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.WIDOWMINE, 2, priority=True)),
            GridBuilding(UnitTypeId.BARRACKS, 2),
            BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            Tech(UpgradeId.SHIELDWALL),
            Step(Supply(44, SupplyType.Workers), Expand(3)),
            GridBuilding(UnitTypeId.BARRACKS, 5),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            BuildGas(4),
        ]

        units = [
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.WIDOWMINE, 4, priority=True)),
            Step(UnitReady(UnitTypeId.BARRACKSREACTOR, 1), TerranUnit(UnitTypeId.MARINE, 24, priority=True)),
            Step(UnitReady(UnitTypeId.STARPORT, 1), TerranUnit(UnitTypeId.MEDIVAC, 2, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.MARINE, 100),
                TerranUnit(UnitTypeId.MARAUDER, 16),
                TerranUnit(UnitTypeId.WIDOWMINE, 8),
                TerranUnit(UnitTypeId.MEDIVAC, 6),
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
            Step(All([TechReady(UpgradeId.STIMPACK), UnitExists(UnitTypeId.MEDIVAC, 2, include_pending=True)]), PlanZoneAttack(40)),
            PlanFinishEnemy(),
        ]

        return BuildOrder(BuildOrder([]).depots, scv, buildings, units, SequentialList(tactics))


class LadderBot(SafeTwoOneOneMine):
    @property
    def my_race(self):
        return Race.Terran
