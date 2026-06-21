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


class RavenLiberatorTank(KnowledgeBot):
    def __init__(self):
        super().__init__("Rusty Raven Liberator Tank")

    async def create_plan(self) -> BuildOrder:
        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))

        scv = [
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 16 + 6), skip=UnitExists(UnitTypeId.COMMANDCENTER, 2)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 60)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 70)),
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
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1),
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), TerranUnit(UnitTypeId.RAVEN, 1, priority=True)),
            Step(UnitExists(UnitTypeId.RAVEN, 1, include_pending=True, include_killed=True), GridBuilding(UnitTypeId.FACTORY, 2)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2),
            Step(UnitExists(UnitTypeId.RAVEN, 1, include_pending=True, include_killed=True), GridBuilding(UnitTypeId.STARPORT, 2)),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            GridBuilding(UnitTypeId.BARRACKS, 3),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(Supply(44, SupplyType.Workers), Expand(3)),
            BuildGas(6),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1)),
        ]

        units = [
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 4, priority=True)),
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), TerranUnit(UnitTypeId.RAVEN, 2, priority=True)),
            Step(UnitReady(UnitTypeId.STARPORTREACTOR, 1), TerranUnit(UnitTypeId.LIBERATOR, 4, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.MARINE, 50),
                TerranUnit(UnitTypeId.SIEGETANK, 16),
                TerranUnit(UnitTypeId.RAVEN, 3),
                TerranUnit(UnitTypeId.LIBERATOR, 10),
                TerranUnit(UnitTypeId.VIKINGFIGHTER, 10),
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
            Step(UnitExists(UnitTypeId.SIEGETANK, 4, include_pending=True), PlanZoneAttack(60)),
            PlanFinishEnemy(),
        ]

        return BuildOrder(BuildOrder([]).depots, scv, buildings, units, SequentialList(tactics))


class LadderBot(RavenLiberatorTank):
    @property
    def my_race(self):
        return Race.Terran
