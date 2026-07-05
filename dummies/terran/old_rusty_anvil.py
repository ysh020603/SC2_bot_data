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


class BuildAnvil(BuildOrder):
    def __init__(self):
        viking_counters = [
            UnitTypeId.COLOSSUS,
            UnitTypeId.MEDIVAC,
            UnitTypeId.RAVEN,
            UnitTypeId.VOIDRAY,
            UnitTypeId.CARRIER,
            UnitTypeId.TEMPEST,
            UnitTypeId.BROODLORD,
        ]

        scv = [
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(None, MorphPlanetary(1), skip_until=All([UnitReady(UnitTypeId.ENGINEERINGBAY, 1), UnitExists(UnitTypeId.COMMANDCENTER, 4, include_pending=True)])),
            Step(
                None,
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 16 + 6),
                skip=UnitExists(UnitTypeId.COMMANDCENTER, 2),
            ),
            Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 32 + 12)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3), ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 60)),
        ]

        dt_counter = [
            Step(
                Any([
                    EnemyBuildingExists(UnitTypeId.DARKSHRINE),
                    EnemyUnitExistsAfter(UnitTypeId.DARKTEMPLAR),
                    EnemyUnitExistsAfter(UnitTypeId.BANSHEE),
                ]),
                None,
            ),
            Step(None, GridBuilding(UnitTypeId.ENGINEERINGBAY, 1)),
            Step(None, DefensiveBuilding(UnitTypeId.MISSILETURRET, DefensePosition.Entrance, 2)),
            Step(None, DefensiveBuilding(UnitTypeId.MISSILETURRET, DefensePosition.CenterMineralLine, None)),
        ]

        buildings = [
            Step(Supply(13), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
            StepBuildGas(1, Supply(16)),
            Step(UnitExists(UnitTypeId.SUPPLYDEPOT), GridBuilding(UnitTypeId.BARRACKS, 1)),
            Step(UnitReady(UnitTypeId.BARRACKS, 0.25), GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
            StepBuildGas(1, Supply(18)),
            Step(UnitExists(UnitTypeId.MARINE, 1), Expand(2)),
            StepBuildGas(2, Supply(20)),
            Step(None, GridBuilding(UnitTypeId.FACTORY, 1), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(None, BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1)),
            Step(UnitExists(UnitTypeId.SIEGETANK, 1, include_killed=True), GridBuilding(UnitTypeId.FACTORY, 2)),
            BuildGas(4),
            Step(None, BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 2)),
            Step(None, GridBuilding(UnitTypeId.FACTORY, 3)),
            Step(None, BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, 1)),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(None, GridBuilding(UnitTypeId.STARPORT, 1)),
            Step(None, BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1)),
            Step(None, GridBuilding(UnitTypeId.BARRACKS, 2)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1)),
            Step(None, Tech(UpgradeId.SHIELDWALL)),
            Step(None, GridBuilding(UnitTypeId.STARPORT, 2)),
            Step(None, BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1)),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            Step(None, Expand(3)),
            DefensiveBuilding(UnitTypeId.MISSILETURRET, DefensePosition.CenterMineralLine, None),
            Step(Minerals(500), GridBuilding(UnitTypeId.FACTORY, 4)),
            BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 3),
            Step(Minerals(600), Expand(4)),
        ]

        upgrades = [
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TRANSFORMATIONSERVOS)),
            Tech(UpgradeId.SMARTSERVOS),
            Tech(UpgradeId.HIGHCAPACITYBARRELS),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL2),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2),
            Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL3),
            Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL3),
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), Tech(UpgradeId.RAVENCORVIDREACTOR)),
            Tech(UpgradeId.LIBERATORAGRANGEUPGRADE),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.HISECAUTOTRACKING)),
            Tech(UpgradeId.TERRANBUILDINGARMOR),
            Tech(UpgradeId.NEOSTEELFRAME),
        ]

        mech = [
            Step(
                UnitReady(UnitTypeId.FACTORY, 1),
                ActUnit(UnitTypeId.HELLION, UnitTypeId.FACTORY, 2),
                skip=UnitReady(UnitTypeId.FACTORYTECHLAB, 1),
            ),
            Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), ActUnit(UnitTypeId.SIEGETANK, UnitTypeId.FACTORY, 16)),
        ]

        hellbat_units = [
            Step(
                All([UnitReady(UnitTypeId.ARMORY, 1), UnitReady(UnitTypeId.FACTORYREACTOR, 1)]),
                ActUnit(UnitTypeId.HELLION, UnitTypeId.FACTORY, 12),
            ),
            Step(Minerals(400), ActUnit(UnitTypeId.HELLION, UnitTypeId.FACTORY, 20)),
        ]

        thor_units = [
            Step(
                All([UnitReady(UnitTypeId.ARMORY, 1), UnitReady(UnitTypeId.FACTORYTECHLAB, 2)]),
                ActUnit(UnitTypeId.THOR, UnitTypeId.FACTORY, 4),
            ),
        ]

        air = [
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), ActUnit(UnitTypeId.RAVEN, UnitTypeId.STARPORT, 2)),
            Step(UnitReady(UnitTypeId.STARPORT, 1), ActUnit(UnitTypeId.MEDIVAC, UnitTypeId.STARPORT, 2)),
            Step(None, ActUnit(UnitTypeId.VIKINGFIGHTER, UnitTypeId.STARPORT, 2)),
            Step(
                None,
                ActUnit(UnitTypeId.VIKINGFIGHTER, UnitTypeId.STARPORT, 6),
                skip_until=self.RequireAnyEnemyUnits(viking_counters, 2),
            ),
            Step(UnitReady(UnitTypeId.STARPORT, 1), ActUnit(UnitTypeId.MEDIVAC, UnitTypeId.STARPORT, 4)),
            Step(UnitExists(UnitTypeId.SIEGETANK, 8, include_pending=True), ActUnit(UnitTypeId.LIBERATOR, UnitTypeId.STARPORT, 4)),
        ]

        marines = [
            Step(UnitReady(UnitTypeId.BARRACKS, 1), ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 4)),
            Step(Minerals(250), ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 20)),
        ]

        super().__init__([scv, dt_counter, self.depots, buildings, upgrades, mech, hellbat_units, thor_units, air, marines])


class OldRustyAnvil(KnowledgeBot):
    def __init__(self):
        super().__init__("Old Rusty Anvil")

    async def pre_step_execute(self):
        pass

    async def create_plan(self) -> BuildOrder:
        self.attack = Step(None, PlanZoneAttack(random.randint(60, 90)))
        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))

        tactics = [
            MineOpenBlockedBase(),
            PlanCancelBuilding(),
            LowerDepots(),
            PlanZoneDefense(),
            worker_scout,
            CallMule(100),
            ScanEnemy(),
            DistributeWorkers(),
            Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
            ManTheBunkers(),
            Repair(),
            ContinueBuilding(),
            PlanZoneGatherTerran(),
            self.attack,
            PlanFinishEnemy(),
        ]

        return BuildOrder([BuildAnvil(), SequentialList(tactics)])


class LadderBot(OldRustyAnvil):
    @property
    def my_race(self):
        return Race.Terran
