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
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44), skip=UnitExists(UnitTypeId.COMMANDCENTER, 3)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 56)),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            StepBuildGas(1, Supply(16)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.REAPER, 1, only_once=True, priority=True)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), DefensiveBuilding(UnitTypeId.BUNKER, DefensePosition.Entrance, 1)),
            Expand(2, priority=True),
            Step(Supply(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            GridBuilding(UnitTypeId.BARRACKS, 2),
            Step(UnitExists(UnitTypeId.REAPER, 1, include_killed=True), BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1)),
            BuildGas(2),
            GridBuilding(UnitTypeId.FACTORY, 1),
            BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.WIDOWMINE, 2, priority=True)),
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), DefensiveBuilding(UnitTypeId.MISSILETURRET, DefensePosition.CenterMineralLine, None)),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            Tech(UpgradeId.SHIELDWALL),
            GridBuilding(UnitTypeId.BARRACKS, 5),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            GridBuilding(UnitTypeId.FACTORY, 2),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            Step(All([Supply(40, SupplyType.Workers), UnitExists(UnitTypeId.MARINE, 36, include_pending=True), UnitExists(UnitTypeId.WIDOWMINE, 2, include_pending=True)]), Expand(3)),
        ]

        gas_plan = BuildOrder(
            Step(All([UnitReady(UnitTypeId.STARPORTREACTOR, 1), Supply(48)]), BuildGas(4)),
            Step(All([UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True), Supply(105)]), BuildGas(6)),
            Step(All([UnitExists(UnitTypeId.COMMANDCENTER, 4, include_pending=True), Supply(150)]), BuildGas(8)),
        )

        supply_buffer = BuildOrder(
            AutoDepot(),
            Step(All([Supply(45), Minerals(250)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
            Step(All([Supply(70), Minerals(350)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 12)),
            Step(All([Supply(100), Minerals(450)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 16)),
            Step(All([Supply(135), Minerals(550)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 20)),
        )

        marine_units = [
            Step(UnitExists(UnitTypeId.REAPER, 1, include_killed=True), TerranUnit(UnitTypeId.MARINE, 12, priority=True)),
            Step(UnitReady(UnitTypeId.BARRACKSREACTOR, 1), TerranUnit(UnitTypeId.MARINE, 24, priority=True)),
            TerranUnit(UnitTypeId.MARINE, 115),
        ]

        marauder_units = [
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), TerranUnit(UnitTypeId.MARAUDER, 4, priority=True)),
            TerranUnit(UnitTypeId.MARAUDER, 16),
        ]

        mine_units = [
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.WIDOWMINE, 4, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.WIDOWMINE, 16),
            ),
        ]

        tank_units = BuildOrder(
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 3, priority=True)),
            TerranUnit(UnitTypeId.SIEGETANK, 8),
        )

        air_units = BuildOrder(
            Step(UnitReady(UnitTypeId.STARPORTREACTOR, 1), TerranUnit(UnitTypeId.MEDIVAC, 4, priority=True)),
            TerranUnit(UnitTypeId.MEDIVAC, 10),
            Step(UnitExists(UnitTypeId.MEDIVAC, 4, include_pending=True), TerranUnit(UnitTypeId.VIKINGFIGHTER, 6)),
        )

        spend_money = BuildOrder(
            Step(All([Supply(65), UnitExists(UnitTypeId.BARRACKS, 5, include_pending=True)]), GridBuilding(UnitTypeId.BARRACKS, 7, priority=True)),
            Step(All([Supply(95), UnitExists(UnitTypeId.BARRACKS, 7, include_pending=True)]), GridBuilding(UnitTypeId.BARRACKS, 9, priority=True)),
            Step(All([Supply(75), UnitExists(UnitTypeId.BARRACKS, 7, include_pending=True)]), BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 7)),
            Step(All([Supply(85), UnitExists(UnitTypeId.FACTORY, 2, include_pending=True)]), GridBuilding(UnitTypeId.FACTORY, 3, priority=True)),
            Step(All([Supply(95), UnitExists(UnitTypeId.FACTORY, 3, include_pending=True)]), BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 2)),
            Step(All([Supply(95), UnitExists(UnitTypeId.STARPORT, 1, include_pending=True)]), GridBuilding(UnitTypeId.STARPORT, 2, priority=True)),
            Step(All([Supply(105), UnitExists(UnitTypeId.STARPORT, 2, include_pending=True)]), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 2)),
            Step(All([Supply(130), UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True)]), Expand(4)),
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
                    Any([TechReady(UpgradeId.STIMPACK, 0.7), Time(6 * 60 + 30)]),
                    UnitExists(UnitTypeId.MARINE, 36, include_pending=True),
                    UnitExists(UnitTypeId.WIDOWMINE, 4, include_pending=True),
                    UnitExists(UnitTypeId.MEDIVAC, 2, include_pending=True),
                ]),
                PlanZoneAttack(25),
            ),
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            BuildOrder([]).depots,
            supply_buffer,
            scv,
            buildings,
            gas_plan,
            air_units,
            tank_units,
            spend_money,
            marine_units,
            marauder_units,
            mine_units,
            SequentialList(tactics),
        )


class LadderBot(SafeTwoOneOneMine):
    @property
    def my_race(self):
        return Race.Terran
