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
            TerranUnit(UnitTypeId.SCV, 58),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            StepBuildGas(1, Supply(16)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.REAPER, 1, only_once=True, priority=True)),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), DefensiveBuilding(UnitTypeId.BUNKER, DefensePosition.Entrance, 1)),
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
            Step(UnitExists(UnitTypeId.RAVEN, 1, include_pending=True, include_killed=True), GridBuilding(UnitTypeId.STARPORT, 2)),
            Step(UnitExists(UnitTypeId.RAVEN, 1, include_pending=True, include_killed=True), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1)),
            Step(UnitExists(UnitTypeId.STARPORTREACTOR, 1, include_pending=True), GridBuilding(UnitTypeId.STARPORT, 3)),
            BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 2),
            Step(UnitExists(UnitTypeId.LIBERATOR, 2, include_pending=True), GridBuilding(UnitTypeId.FACTORY, 2)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2),
            Step(UnitExists(UnitTypeId.LIBERATOR, 4, include_pending=True), GridBuilding(UnitTypeId.FACTORY, 3)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 3),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(All([Supply(40, SupplyType.Workers), UnitExists(UnitTypeId.LIBERATOR, 2, include_pending=True), UnitExists(UnitTypeId.RAVEN, 1, include_pending=True)]), Expand(3)),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1)),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1)),
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

        bio_units = [
            Step(UnitExists(UnitTypeId.REAPER, 1, include_killed=True), TerranUnit(UnitTypeId.MARINE, 10, priority=True)),
            Step(UnitReady(UnitTypeId.BARRACKSREACTOR, 1), TerranUnit(UnitTypeId.MARINE, 16, priority=True)),
            TerranUnit(UnitTypeId.MARINE, 22),
        ]

        mech_units = [
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 3, priority=True)),
            BuildOrder(
                TerranUnit(UnitTypeId.SIEGETANK, 8),
            ),
        ]

        hellion_units = [
            Step(UnitReady(UnitTypeId.FACTORYREACTOR, 1), TerranUnit(UnitTypeId.HELLION, 24)),
        ]

        air_units = BuildOrder(
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), TerranUnit(UnitTypeId.RAVEN, 1, priority=True)),
            Step(UnitReady(UnitTypeId.STARPORTREACTOR, 1), TerranUnit(UnitTypeId.LIBERATOR, 4, priority=True)),
            Step(UnitExists(UnitTypeId.LIBERATOR, 2, include_pending=True), TerranUnit(UnitTypeId.VIKINGFIGHTER, 4, priority=True)),
            TerranUnit(UnitTypeId.RAVEN, 3),
            TerranUnit(UnitTypeId.VIKINGFIGHTER, 14),
            TerranUnit(UnitTypeId.LIBERATOR, 16),
        )

        spend_money = BuildOrder(
            Step(All([Supply(80), UnitExists(UnitTypeId.STARPORT, 3, include_pending=True)]), GridBuilding(UnitTypeId.STARPORT, 4, priority=True)),
            Step(All([Supply(100), UnitExists(UnitTypeId.STARPORT, 4, include_pending=True)]), GridBuilding(UnitTypeId.STARPORT, 5, priority=True)),
            Step(All([Supply(85), UnitExists(UnitTypeId.STARPORT, 3, include_pending=True)]), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 4)),
            Step(All([Supply(115), UnitExists(UnitTypeId.STARPORT, 5, include_pending=True)]), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 5)),
            Step(All([Supply(95), UnitExists(UnitTypeId.FACTORY, 3, include_pending=True)]), GridBuilding(UnitTypeId.FACTORY, 4, priority=True)),
            Step(All([Supply(105), UnitExists(UnitTypeId.FACTORY, 4, include_pending=True)]), BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1)),
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
                Any([
                    All([
                        UnitExists(UnitTypeId.RAVEN, 1, include_pending=True),
                        UnitExists(UnitTypeId.LIBERATOR, 4, include_pending=True),
                        UnitExists(UnitTypeId.VIKINGFIGHTER, 4, include_pending=True),
                        Supply(105),
                    ]),
                    All([
                        UnitExists(UnitTypeId.LIBERATOR, 4, include_pending=True),
                        UnitExists(UnitTypeId.VIKINGFIGHTER, 2, include_pending=True),
                        Time(10 * 60 + 30),
                    ]),
                ]),
                PlanZoneAttack(35),
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
            bio_units,
            mech_units,
            hellion_units,
            air_units,
            SequentialList(tactics),
        )


class LadderBot(RavenLiberatorTank):
    @property
    def my_race(self):
        return Race.Terran
