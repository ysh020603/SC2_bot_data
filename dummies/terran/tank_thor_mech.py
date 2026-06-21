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
            TerranUnit(UnitTypeId.SCV, 58),
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
            Step(All([Supply(38, SupplyType.Workers), UnitExists(UnitTypeId.SIEGETANK, 2, include_pending=True)]), Expand(3)),
            GridBuilding(UnitTypeId.FACTORY, 4),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 3),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 4),
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1)),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),
            GridBuilding(UnitTypeId.ARMORY, 2),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL2),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2),
            Step(Minerals(400), GridBuilding(UnitTypeId.FACTORY, 6)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 5),
            Step(Minerals(650), GridBuilding(UnitTypeId.FACTORY, 8)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 6),
            Step(Minerals(500), Expand(4)),
        ]

        gas_plan = BuildOrder(
            Step(All([UnitReady(UnitTypeId.COMMANDCENTER, 2), Supply(34)]), BuildGas(4)),
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

        tank_units = [
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 10, priority=True)),
            TerranUnit(UnitTypeId.SIEGETANK, 20),
        ]

        thor_units = [
            Step(UnitReady(UnitTypeId.ARMORY, 1), TerranUnit(UnitTypeId.THOR, 10, priority=True)),
            TerranUnit(UnitTypeId.THOR, 18),
        ]

        hellion_units = [
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.HELLION, 8)),
        ]

        air_units = [
            Step(UnitReady(UnitTypeId.STARPORT, 1), TerranUnit(UnitTypeId.VIKINGFIGHTER, 4, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.VIKINGFIGHTER, 12),
                TerranUnit(UnitTypeId.LIBERATOR, 6),
            ),
        ]

        spend_money = BuildOrder(
            Step(All([Supply(55), UnitExists(UnitTypeId.FACTORY, 4, include_pending=True)]), GridBuilding(UnitTypeId.FACTORY, 6, priority=True)),
            Step(All([Supply(80), UnitExists(UnitTypeId.FACTORY, 6, include_pending=True)]), GridBuilding(UnitTypeId.FACTORY, 10, priority=True)),
            Step(All([Supply(110), UnitExists(UnitTypeId.FACTORY, 10, include_pending=True)]), GridBuilding(UnitTypeId.FACTORY, 14, priority=True)),
            Step(All([Supply(75), UnitExists(UnitTypeId.FACTORY, 6, include_pending=True)]), BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 8)),
            Step(All([Supply(105), UnitExists(UnitTypeId.FACTORY, 10, include_pending=True)]), BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 12)),
            Step(All([Supply(95), UnitExists(UnitTypeId.STARPORT, 1, include_pending=True)]), GridBuilding(UnitTypeId.STARPORT, 2, priority=True)),
            Step(All([Supply(115), UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True)]), Expand(4)),
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
                Any([
                    All([
                        UnitExists(UnitTypeId.SIEGETANK, 8, include_pending=True),
                        UnitExists(UnitTypeId.THOR, 4, include_pending=True),
                    ]),
                    Supply(155),
                    Time(10 * 60 + 30),
                ]),
                PlanZoneAttack(30),
            ),
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            BuildOrder([]).depots,
            supply_buffer,
            scv,
            buildings,
            gas_plan,
            spend_money,
            tank_units,
            thor_units,
            hellion_units,
            air_units,
            SequentialList(tactics),
        )


class LadderBot(TankThorMech):
    @property
    def my_race(self):
        return Race.Terran
