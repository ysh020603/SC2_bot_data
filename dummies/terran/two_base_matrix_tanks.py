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


class TwoBaseMatrixTanks(KnowledgeBot):
    def __init__(self):
        super().__init__("Two Base Matrix Tanks")

    async def create_plan(self) -> BuildOrder:
        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))

        scv = [
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(
                None,
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 16 + 6),
                skip=UnitExists(UnitTypeId.COMMANDCENTER, 2),
            ),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 32 + 12)),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            StepBuildGas(1, Supply(16)),
            Expand(2),
            Step(Supply(16), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            StepBuildGas(2, UnitExists(UnitTypeId.MARINE, 1, include_pending=True)),
            Step(None, GridBuilding(UnitTypeId.FACTORY, 1), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(
                None,
                BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
                skip_until=UnitReady(UnitTypeId.FACTORY, 1),
            ),
            Step(Supply(28), GridBuilding(UnitTypeId.SUPPLYDEPOT, 4)),
            Step(None, GridBuilding(UnitTypeId.FACTORY, 2)),
            Step(None, BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2)),
            Step(None, GridBuilding(UnitTypeId.STARPORT, 1)),
            Step(None, BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1)),
            Step(Supply(38), GridBuilding(UnitTypeId.SUPPLYDEPOT, 5)),
            DefensiveBuilding(UnitTypeId.BUNKER, DefensePosition.Entrance, 1),
            Step(None, GridBuilding(UnitTypeId.BARRACKS, 2)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1)),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), Tech(UpgradeId.STIMPACK)),
            Tech(UpgradeId.SHIELDWALL),
            BuildGas(3),
            Step(None, GridBuilding(UnitTypeId.ENGINEERINGBAY, 1)),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1)),
            Step(None, Expand(3), skip_until=RequireCustom(self.should_expand)),
            BuildGas(4),
            Step(Supply(45), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
            Step(None, GridBuilding(UnitTypeId.BARRACKS, 4)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 2)),
            Step(None, GridBuilding(UnitTypeId.ARMORY, 1)),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1)),
            Step(Supply(75), GridBuilding(UnitTypeId.SUPPLYDEPOT, 10)),
            Step(None, Tech(UpgradeId.DRILLCLAWS), skip_until=UnitReady(UnitTypeId.ARMORY, 1)),
        ]

        gas_plan = BuildOrder(
            Step(All([UnitReady(UnitTypeId.COMMANDCENTER, 2), Supply(34)]), BuildGas(4)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True), BuildGas(6)),
        )

        supply_buffer = BuildOrder(
            AutoDepot(),
            Step(All([Supply(55), Minerals(250)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 9)),
            Step(All([Supply(80), Minerals(350)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 12)),
            Step(All([Supply(110), Minerals(450)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 16)),
        )

        tank_units = [
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), TerranUnit(UnitTypeId.SIEGETANK, 6, priority=True)),
            TerranUnit(UnitTypeId.SIEGETANK, 14),
        ]

        mine_units = [
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 2), TerranUnit(UnitTypeId.WIDOWMINE, 4, priority=True)),
            TerranUnit(UnitTypeId.WIDOWMINE, 8),
        ]

        air_units = BuildOrder(
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), TerranUnit(UnitTypeId.RAVEN, 1, priority=True)),
            Step(UnitExists(UnitTypeId.RAVEN, 1, include_pending=True), TerranUnit(UnitTypeId.LIBERATOR, 2, priority=True)),
            TerranUnit(UnitTypeId.MEDIVAC, 2),
            TerranUnit(UnitTypeId.LIBERATOR, 4),
            TerranUnit(UnitTypeId.VIKINGFIGHTER, 2),
        )

        marine_units = [
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.MARINE, 4, priority=True)),
            Step(Minerals(250), TerranUnit(UnitTypeId.MARINE, 30)),
            TerranUnit(UnitTypeId.MARINE, 60),
        ]

        upgrades = [
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), Tech(UpgradeId.RAVENCORVIDREACTOR)),
            Step(UnitExists(UnitTypeId.LIBERATOR, 2, include_pending=True), Tech(UpgradeId.LIBERATORAGRANGEUPGRADE)),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.DURABLEMATERIALS)),
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
            Step(
                Any([
                    All([
                        TechReady(UpgradeId.STIMPACK, 0.9),
                        UnitExists(UnitTypeId.SIEGETANK, 4, include_pending=True),
                        UnitExists(UnitTypeId.RAVEN, 1, include_pending=True),
                    ]),
                    All([
                        UnitExists(UnitTypeId.SIEGETANK, 6, include_pending=True),
                        Time(9 * 60),
                    ]),
                ]),
                PlanZoneAttack(60),
            ),
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            BuildOrder([]).depots,
            supply_buffer,
            scv,
            buildings,
            gas_plan,
            upgrades,
            tank_units,
            mine_units,
            air_units,
            marine_units,
            SequentialList(tactics),
        )

    def should_expand(self, knowledge):
        count = 0
        for zone in knowledge.zone_manager.our_zones:
            if zone.our_townhall is not None:
                count += zone.our_townhall.surplus_harvesters
        return count > 5


class LadderBot(TwoBaseMatrixTanks):
    @property
    def my_race(self):
        return Race.Terran
