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


class BioMineMacro(KnowledgeBot):
    def __init__(self):
        super().__init__("Rusty Bio Mines")

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
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1),
            GridBuilding(UnitTypeId.FACTORY, 1),
            BuildGas(2),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            GridBuilding(UnitTypeId.BARRACKS, 3),
            BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 2),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            Tech(UpgradeId.SHIELDWALL),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            Step(Supply(44, SupplyType.Workers), Expand(3)),
            GridBuilding(UnitTypeId.BARRACKS, 5),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            BuildGas(6),
            GridBuilding(UnitTypeId.FACTORY, 2),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 2),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2)),
            Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL2),
            Step(Minerals(500), GridBuilding(UnitTypeId.BARRACKS, 8)),
        ]

        units = [
            Step(UnitReady(UnitTypeId.STARPORT, 1), TerranUnit(UnitTypeId.MEDIVAC, 2, priority=True)),
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.WIDOWMINE, 4, priority=True)),
            Step(UnitReady(UnitTypeId.BARRACKSREACTOR, 1), TerranUnit(UnitTypeId.MARINE, 30, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.MARINE, 120),
                TerranUnit(UnitTypeId.MARAUDER, 20),
                TerranUnit(UnitTypeId.WIDOWMINE, 24),
                TerranUnit(UnitTypeId.MEDIVAC, 8),
                TerranUnit(UnitTypeId.VIKINGFIGHTER, 6),
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
            Step(UnitExists(UnitTypeId.WIDOWMINE, 4, include_pending=True), PlanZoneAttack(45)),
            PlanFinishEnemy(),
        ]

        return BuildOrder(BuildOrder([]).depots, scv, buildings, units, SequentialList(tactics))


class LadderBot(BioMineMacro):
    @property
    def my_race(self):
        return Race.Terran
