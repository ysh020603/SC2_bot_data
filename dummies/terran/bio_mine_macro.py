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
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44), skip=UnitExists(UnitTypeId.COMMANDCENTER, 3)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 60)),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            StepBuildGas(1, Supply(16)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.REAPER, 1, only_once=True, priority=True)),
            Expand(2, priority=True),
            Step(Supply(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1),
            GridBuilding(UnitTypeId.BARRACKS, 3),
            BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            Tech(UpgradeId.SHIELDWALL),
            BuildGas(2),
            GridBuilding(UnitTypeId.FACTORY, 1),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
            GridBuilding(UnitTypeId.FACTORY, 2),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 2),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
            GridBuilding(UnitTypeId.BARRACKS, 5),
            BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 4),
            Step(All([Supply(40, SupplyType.Workers), UnitExists(UnitTypeId.MARINE, 32, include_pending=True), UnitExists(UnitTypeId.WIDOWMINE, 4, include_pending=True)]), Expand(3)),
            GridBuilding(UnitTypeId.FACTORY, 4),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 2),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2)),
            Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL2),
            Step(Minerals(500), GridBuilding(UnitTypeId.BARRACKS, 7)),
        ]

        gas_plan = BuildOrder(
            Step(All([UnitReady(UnitTypeId.COMMANDCENTER, 2), Supply(36)]), BuildGas(4)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True), BuildGas(6)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 4, include_pending=True), BuildGas(8)),
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
            Step(UnitReady(UnitTypeId.BARRACKSREACTOR, 1), TerranUnit(UnitTypeId.MARINE, 26, priority=True)),
            TerranUnit(UnitTypeId.MARINE, 120),
        ]

        marauder_units = [
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), TerranUnit(UnitTypeId.MARAUDER, 8, priority=True)),
            TerranUnit(UnitTypeId.MARAUDER, 18),
        ]

        mine_units = [
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.WIDOWMINE, 4, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.WIDOWMINE, 36),
            ),
        ]

        air_units = BuildOrder(
            Step(UnitReady(UnitTypeId.STARPORT, 1), TerranUnit(UnitTypeId.MEDIVAC, 2, priority=True)),
            TerranUnit(UnitTypeId.MEDIVAC, 10),
            Step(UnitExists(UnitTypeId.MEDIVAC, 6, include_pending=True), TerranUnit(UnitTypeId.VIKINGFIGHTER, 10)),
        )

        tank_units = BuildOrder(
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 8, priority=True)),
            TerranUnit(UnitTypeId.SIEGETANK, 14),
        )

        spend_money = BuildOrder(
            Step(All([Supply(60), UnitExists(UnitTypeId.FACTORY, 2, include_pending=True)]), GridBuilding(UnitTypeId.FACTORY, 4, priority=True)),
            Step(All([Supply(70), UnitExists(UnitTypeId.BARRACKS, 5, include_pending=True)]), GridBuilding(UnitTypeId.BARRACKS, 8, priority=True)),
            Step(All([Supply(105), UnitExists(UnitTypeId.BARRACKS, 8, include_pending=True)]), GridBuilding(UnitTypeId.BARRACKS, 10, priority=True)),
            Step(All([Supply(70), UnitExists(UnitTypeId.BARRACKS, 8, include_pending=True)]), BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 7)),
            Step(All([Supply(80), UnitExists(UnitTypeId.FACTORY, 4, include_pending=True)]), BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 3)),
            Step(All([Supply(85), UnitExists(UnitTypeId.FACTORY, 4, include_pending=True)]), BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 3)),
            Step(All([Supply(95), UnitExists(UnitTypeId.STARPORT, 1, include_pending=True)]), GridBuilding(UnitTypeId.STARPORT, 2, priority=True)),
            Step(All([Supply(110), UnitExists(UnitTypeId.STARPORT, 2, include_pending=True)]), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 2)),
            Step(All([Supply(125), UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True)]), Expand(4)),
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
                    UnitExists(UnitTypeId.MARINE, 45, include_pending=True),
                    UnitExists(UnitTypeId.WIDOWMINE, 4, include_pending=True),
                ]),
                PlanZoneAttack(10),
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
            mine_units,
            spend_money,
            marine_units,
            marauder_units,
            SequentialList(tactics),
        )


class LadderBot(BioMineMacro):
    @property
    def my_race(self):
        return Race.Terran
