from typing import List

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


class BlueflameLocks(KnowledgeBot):
    def __init__(self):
        super().__init__("Blueflame Locks")

    async def create_plan(self) -> BuildOrder:
        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
        self.distribute_workers = DistributeWorkers()

        scv = [
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 2),
                MorphOrbitals(3),
                skip_until=UnitReady(UnitTypeId.BARRACKS, 1),
            ),
            Step(None, MorphPlanetary(2), skip_until=UnitReady(UnitTypeId.ENGINEERINGBAY, 1)),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 40)),
            Step(
                UnitExists(UnitTypeId.COMMANDCENTER, 3),
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 66),
            ),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(Supply(16), Expand(2)),
            Step(Supply(18), GridBuilding(UnitTypeId.BARRACKS, 1)),
            BuildGas(1),
            Step(Supply(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            Step(None, BuildGas(2), skip_until=UnitExists(UnitTypeId.MARINE, 2)),
            Step(None, GridBuilding(UnitTypeId.FACTORY, 1), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1),
            BuildGas(4),
            Step(None, Expand(3)),
            GridBuilding(UnitTypeId.FACTORY, 2),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1),
            Step(
                None,
                Tech(UpgradeId.CYCLONELOCKONDAMAGEUPGRADE),
                skip_until=UnitReady(UnitTypeId.FACTORYTECHLAB, 1),
            ),
            GridBuilding(UnitTypeId.FACTORY, 3),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2),
            Step(None, Tech(UpgradeId.HIGHCAPACITYBARRELS), skip_until=UnitReady(UnitTypeId.FACTORYTECHLAB, 2)),
            BuildGas(5),
            GridBuilding(UnitTypeId.ARMORY, 1),
            StepBuildGas(6, None, Gas(100)),
            Step(Minerals(400), GridBuilding(UnitTypeId.FACTORY, 5)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 3),
            BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 2),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(Minerals(400), Expand(4)),
            GridBuilding(UnitTypeId.STARPORT, 1),
            BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1),
            BuildGas(8),
            Step(Minerals(500), GridBuilding(UnitTypeId.FACTORY, 7)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 5),
            Step(Supply(130), GridBuilding(UnitTypeId.ARMORY, 2)),
        ]

        upgrades = [
            Step(None, Tech(UpgradeId.ARMORPIERCINGROCKETS)),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.SMARTSERVOS)),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL2),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL3),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL3),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.HISECAUTOTRACKING)),
            Tech(UpgradeId.TERRANBUILDINGARMOR),
        ]

        cyclone_units = [
            ActUnit(UnitTypeId.CYCLONE, UnitTypeId.FACTORY, 4),
            ActUnitOnce(UnitTypeId.HELLION, UnitTypeId.FACTORY, 2),
            ActUnit(UnitTypeId.CYCLONE, UnitTypeId.FACTORY, 12, priority=True),
        ]

        hellion_units = [
            Step(
                UnitReady(UnitTypeId.FACTORYREACTOR, 1),
                ActUnit(UnitTypeId.HELLION, UnitTypeId.FACTORY, 12),
                skip_until=Minerals(200),
            ),
            Step(Minerals(350), ActUnit(UnitTypeId.HELLION, UnitTypeId.FACTORY, 24)),
        ]

        thor_units = [
            Step(
                All([UnitReady(UnitTypeId.ARMORY, 1), UnitReady(UnitTypeId.FACTORYTECHLAB, 3)]),
                TerranUnit(UnitTypeId.THOR, 6, priority=True),
            ),
        ]

        air_units = [
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), TerranUnit(UnitTypeId.RAVEN, 2, priority=True)),
            TerranUnit(UnitTypeId.VIKINGFIGHTER, 2),
            TerranUnit(UnitTypeId.LIBERATOR, 2),
        ]

        gas_plan = BuildOrder(
            Step(All([UnitReady(UnitTypeId.COMMANDCENTER, 2), Supply(34)]), BuildGas(5)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True), BuildGas(6)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 4, include_pending=True), BuildGas(8)),
        )

        supply_buffer = BuildOrder(
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 1), None),
            Step(SupplyLeft(6), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 2), None),
            Step(SupplyLeft(14), GridBuilding(UnitTypeId.SUPPLYDEPOT, 4)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 4), None),
            Step(SupplyLeft(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 6)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 5), None),
            Step(SupplyLeft(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 7), None),
            Step(SupplyLeft(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 10)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 9), None),
            Step(SupplyLeft(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 12)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 11), None),
            Step(SupplyLeft(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 14)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 13), None),
            Step(SupplyLeft(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 16)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 16), GridBuilding(UnitTypeId.SUPPLYDEPOT, 20)),
        )

        self.attack = PlanZoneAttack(50)

        tactics = [
            MineOpenBlockedBase(),
            PlanCancelBuilding(),
            LowerDepots(),
            PlanZoneDefense(),
            worker_scout,
            Step(None, CallMule(50), skip=Time(5 * 60)),
            Step(None, CallMule(100), skip_until=Time(5 * 60)),
            Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
            self.distribute_workers,
            Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
            ManTheBunkers(),
            Repair(),
            ContinueBuilding(),
            PlanZoneGatherTerran(),
            Step(
                Any([
                    All([
                        TechReady(UpgradeId.CYCLONELOCKONDAMAGEUPGRADE, 0.95),
                        UnitExists(UnitTypeId.CYCLONE, 6, include_pending=True),
                    ]),
                    All([
                        UnitExists(UnitTypeId.THOR, 2, include_pending=True),
                        UnitExists(UnitTypeId.CYCLONE, 8, include_pending=True),
                    ]),
                    Time(10 * 60),
                ]),
                self.attack,
            ),
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            Step(UnitExists(UnitTypeId.BARRACKS, 1), SequentialList(supply_buffer)),
            scv,
            buildings,
            gas_plan,
            upgrades,
            ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 4),
            cyclone_units,
            hellion_units,
            thor_units,
            air_units,
            SequentialList(tactics),
        )


class LadderBot(BlueflameLocks):
    @property
    def my_race(self):
        return Race.Terran
