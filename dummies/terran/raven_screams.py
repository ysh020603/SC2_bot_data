import random

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


class RavenScreams(KnowledgeBot):
    def __init__(self):
        super().__init__("Raven Screams")

    async def create_plan(self) -> BuildOrder:
        attack_value = random.randint(40, 60)
        self.attack = Step(None, PlanZoneAttack(attack_value))
        self.knowledge.print(f"Att at {attack_value}", "Build")

        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
        self.distribute_workers = DistributeWorkers(4)

        scv = [
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            TerranUnit(UnitTypeId.SCV, 56),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 0.95), GridBuilding(UnitTypeId.BARRACKS, 1)),
            BuildGas(1),
            Expand(2),
            Step(Supply(20), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            BuildGas(2),
            Step(None, GridBuilding(UnitTypeId.FACTORY, 1), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(UnitReady(UnitTypeId.FACTORY, 1), GridBuilding(UnitTypeId.STARPORT, 1)),
            DefensiveBuilding(UnitTypeId.BUNKER, DefensePosition.Entrance, 1),
            Step(None, BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1)),
            Step(None, BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1)),
            StepBuildGas(3, None, Gas(150)),
            Step(None, GridBuilding(UnitTypeId.BARRACKS, 2)),
            StepBuildGas(4, None, Gas(100)),
            Step(UnitExists(UnitTypeId.BANSHEE, 2, include_killed=True), GridBuilding(UnitTypeId.STARPORT, 2)),
            Step(UnitReady(UnitTypeId.STARPORT, 2), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1)),
            Step(None, Tech(UpgradeId.SHIELDWALL)),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(Minerals(600), GridBuilding(UnitTypeId.BARRACKS, 4)),
            Expand(3),
        ]

        upgrades = [
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), Tech(UpgradeId.BANSHEECLOAK)),
            Tech(UpgradeId.BANSHEESPEED),
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), Tech(UpgradeId.RAVENCORVIDREACTOR)),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL1)),
            Tech(UpgradeId.TERRANSHIPARMORSLEVEL1),
            Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL2),
            Step(UnitExists(UnitTypeId.LIBERATOR, 2, include_pending=True), Tech(UpgradeId.LIBERATORAGRANGEUPGRADE)),
        ]

        banshee_units = [
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), TerranUnit(UnitTypeId.BANSHEE, 4, priority=True)),
            TerranUnit(UnitTypeId.BANSHEE, 8),
        ]

        raven_units = [
            Step(
                UnitExists(UnitTypeId.BANSHEE, 2, include_pending=True),
                TerranUnit(UnitTypeId.RAVEN, 2, priority=True),
            ),
            TerranUnit(UnitTypeId.RAVEN, 3),
        ]

        air_support = BuildOrder(
            Step(UnitReady(UnitTypeId.STARPORTREACTOR, 1), TerranUnit(UnitTypeId.LIBERATOR, 4, priority=True)),
            TerranUnit(UnitTypeId.VIKINGFIGHTER, 4),
            TerranUnit(UnitTypeId.LIBERATOR, 8),
        )

        ground_units = BuildOrder(
            TerranUnit(UnitTypeId.SIEGETANK, 6),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.MARINE, 16)),
            Step(UnitReady(UnitTypeId.BARRACKSTECHLAB, 1), TerranUnit(UnitTypeId.MARAUDER, 4)),
            Step(Minerals(300), TerranUnit(UnitTypeId.MARINE, 40)),
        )

        gas_plan = BuildOrder(
            Step(All([UnitReady(UnitTypeId.COMMANDCENTER, 2), Supply(34)]), BuildGas(4)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True), BuildGas(6)),
        )

        supply_buffer = BuildOrder(
            AutoDepot(),
            Step(All([Supply(50), Minerals(250)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
            Step(All([Supply(75), Minerals(350)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 12)),
            Step(All([Supply(105), Minerals(450)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 16)),
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
            self.distribute_workers,
            Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
            ManTheBunkers(),
            Repair(),
            ContinueBuilding(),
            PlanZoneGatherTerran(),
            self.attack,
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            supply_buffer,
            scv,
            buildings,
            gas_plan,
            upgrades,
            banshee_units,
            raven_units,
            air_support,
            ground_units,
            SequentialList(tactics),
        )


class LadderBot(RavenScreams):
    @property
    def my_race(self):
        return Race.Terran
