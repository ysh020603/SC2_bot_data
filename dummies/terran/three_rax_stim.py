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


class ThreeRaxStim(KnowledgeBot):
    def __init__(self):
        super().__init__("Rusty 3 Rax Stim")

    async def create_plan(self) -> BuildOrder:
        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))

        scv = [
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22), skip=UnitExists(UnitTypeId.COMMANDCENTER, 2)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 2), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 34), skip=UnitExists(UnitTypeId.COMMANDCENTER, 3)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44)),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            StepBuildGas(1, Supply(16)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.REAPER, 1, only_once=True, priority=True)),
            Step(Supply(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            Step(UnitExists(UnitTypeId.REAPER, 1, include_killed=True), BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1)),
            GridBuilding(UnitTypeId.BARRACKS, 3, priority=True),
            BuildGas(2),
            BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            Step(UnitExists(UnitTypeId.MARINE, 16, include_pending=True), Expand(2)),
            Step(TechReady(UpgradeId.STIMPACK, 0.4), Tech(UpgradeId.SHIELDWALL)),
            Step(TechReady(UpgradeId.SHIELDWALL, 0.5), Tech(UpgradeId.PUNISHERGRENADES)),
            Step(UnitExists(UnitTypeId.MARINE, 18, include_pending=True), GridBuilding(UnitTypeId.FACTORY, 1)),
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            Step(UnitReady(UnitTypeId.STARPORT, 1), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1)),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            Step(All([Supply(34, SupplyType.Workers), UnitExists(UnitTypeId.MARINE, 40, include_pending=True)]), Expand(3)),
            GridBuilding(UnitTypeId.BARRACKS, 5),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            BuildGas(4),
        ]

        supply_buffer = BuildOrder(
            AutoDepot(),
            Step(All([Supply(45), Minerals(250)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
            Step(All([Supply(70), Minerals(350)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 12)),
            Step(All([Supply(100), Minerals(450)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 16)),
            Step(All([Supply(135), Minerals(550)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 20)),
        )

        marine_units = [
            Step(UnitReady(UnitTypeId.BARRACKSREACTOR, 1), TerranUnit(UnitTypeId.MARINE, 24, priority=True)),
            TerranUnit(UnitTypeId.MARINE, 180),
        ]

        marauder_units = [
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), TerranUnit(UnitTypeId.MARAUDER, 8, priority=True)),
            TerranUnit(UnitTypeId.MARAUDER, 24),
        ]

        air_units = [
            Step(UnitReady(UnitTypeId.STARPORT, 1), TerranUnit(UnitTypeId.MEDIVAC, 2, priority=True)),
            TerranUnit(UnitTypeId.MEDIVAC, 10),
        ]

        spend_money = BuildOrder(
            Step(All([Supply(55), UnitExists(UnitTypeId.BARRACKS, 5, include_pending=True)]), GridBuilding(UnitTypeId.BARRACKS, 8, priority=True)),
            Step(All([Supply(80), UnitExists(UnitTypeId.BARRACKS, 8, include_pending=True)]), GridBuilding(UnitTypeId.BARRACKS, 12, priority=True)),
            Step(All([Supply(70), UnitExists(UnitTypeId.BARRACKS, 8, include_pending=True)]), BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 8)),
            Step(All([Supply(85), UnitExists(UnitTypeId.STARPORT, 1, include_pending=True)]), GridBuilding(UnitTypeId.STARPORT, 2, priority=True)),
            Step(All([Supply(95), UnitExists(UnitTypeId.STARPORT, 2, include_pending=True)]), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 2)),
        )

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
            Step(
                All([
                    Any([TechReady(UpgradeId.STIMPACK, 0.35), Time(4 * 60 + 45)]),
                    UnitExists(UnitTypeId.MARINE, 16, include_pending=True),
                ]),
                PlanZoneAttack(4),
            ),
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            BuildOrder([]).depots,
            supply_buffer,
            scv,
            buildings,
            spend_money,
            marine_units,
            marauder_units,
            air_units,
            SequentialList(tactics),
        )


class LadderBot(ThreeRaxStim):
    @property
    def my_race(self):
        return Race.Terran
