import random
from typing import Optional, List

from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from sharpy.knowledges import KnowledgeBot
from sharpy.managers.core import ManagerBase
from sharpy.plans import BuildOrder, Step, SequentialList, StepBuildGas
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.require import *
from sharpy.plans.require.supply import SupplyType
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *
from sharpy.utils import select_build_index


class JumpIn(ActBase):
    def __init__(self):
        self.done = False
        super().__init__()

    async def execute(self) -> bool:
        if self.done:
            return True
        bcs = self.cache.own(UnitTypeId.BATTLECRUISER)
        if bcs.amount > 1:
            self.done = True
            for bc in bcs:
                self.knowledge.cooldown_manager.used_ability(bc.tag, AbilityId.EFFECT_TACTICALJUMP)
                bc(AbilityId.EFFECT_TACTICALJUMP, self.zone_manager.enemy_main_zone.behind_mineral_position_center)
        return True


class YamatoRustFleet(KnowledgeBot):
    jump: int

    def __init__(self, build_name: str = "default"):
        super().__init__("Yamato Rust Fleet")
        self.build_name = build_name

    async def pre_step_execute(self):
        pass

    def configure_managers(self) -> Optional[List[ManagerBase]]:
        return super().configure_managers()

    async def create_plan(self) -> BuildOrder:
        attack_value = random.randint(50, 80)
        self.attack = Step(None, PlanZoneAttack(attack_value))

        if self.build_name == "default":
            self.jump = select_build_index(self.knowledge, "build.yamato", 0, 1)
        else:
            self.jump = int(self.build_name)

        if self.jump == 0:
            self.knowledge.print(f"Jump, att at {attack_value}", "Build")
        else:
            self.knowledge.print(f"No jump, att at {attack_value}", "Build")

        worker_scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
        self.distribute_workers = DistributeWorkers(4)

        scv = [
            Step(None, MorphOrbitals(2), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            Step(None, MorphPlanetary(1), skip_until=All([UnitReady(UnitTypeId.ENGINEERINGBAY, 1), UnitExists(UnitTypeId.COMMANDCENTER, 4, include_pending=True)])),
            TerranUnit(UnitTypeId.SCV, 60),
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
            Step(None, GridBuilding(UnitTypeId.BARRACKS, 2)),
            BuildGas(3),
            Step(None, BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, 1)),
            Step(UnitReady(UnitTypeId.STARPORT, 1), GridBuilding(UnitTypeId.FUSIONCORE, 1)),
            Step(None, BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 1)),
            StepBuildGas(4, None, UnitExists(UnitTypeId.BATTLECRUISER, 1, include_killed=True, include_pending=True)),
            Step(UnitExists(UnitTypeId.BATTLECRUISER, 1, include_killed=True), GridBuilding(UnitTypeId.BARRACKS, 3)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, 1)),
            Step(None, BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, 1)),
            Step(None, GridBuilding(UnitTypeId.STARPORT, 2)),
            Step(UnitReady(UnitTypeId.STARPORT, 2), BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, 1)),
            Step(None, GridBuilding(UnitTypeId.STARPORT, 3)),
            Step(UnitReady(UnitTypeId.STARPORT, 3), BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, 2)),
            Step(None, Tech(UpgradeId.SHIELDWALL)),
            GridBuilding(UnitTypeId.ENGINEERINGBAY, 1),
            GridBuilding(UnitTypeId.ARMORY, 1),
            Step(Minerals(600), GridBuilding(UnitTypeId.BARRACKS, 4)),
            Expand(3),
            DefensiveBuilding(UnitTypeId.MISSILETURRET, DefensePosition.CenterMineralLine, None),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True), Expand(4)),
        ]

        upgrades = [
            Step(UnitReady(UnitTypeId.FUSIONCORE, 1), Tech(UpgradeId.BATTLECRUISERENABLESPECIALIZATIONS)),
            Step(UnitReady(UnitTypeId.ARMORY, 1), Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL1)),
            Tech(UpgradeId.TERRANSHIPARMORSLEVEL1),
            Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL2),
            Tech(UpgradeId.TERRANSHIPARMORSLEVEL2),
            Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL3),
            Tech(UpgradeId.TERRANSHIPARMORSLEVEL3),
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), Tech(UpgradeId.RAVENCORVIDREACTOR)),
            Tech(UpgradeId.LIBERATORAGRANGEUPGRADE),
            Step(UnitReady(UnitTypeId.ENGINEERINGBAY, 1), Tech(UpgradeId.HISECAUTOTRACKING)),
            Tech(UpgradeId.TERRANBUILDINGARMOR),
            Tech(UpgradeId.NEOSTEELFRAME),
        ]

        bc_units = [
            Step(
                None,
                TerranUnit(UnitTypeId.BATTLECRUISER, 10, priority=True),
                skip_until=UnitReady(UnitTypeId.FUSIONCORE, 1),
            ),
        ]

        raven_block = [
            Step(
                Any([
                    EnemyBuildingExists(UnitTypeId.DARKSHRINE),
                    EnemyUnitExistsAfter(UnitTypeId.DARKTEMPLAR),
                    EnemyUnitExistsAfter(UnitTypeId.BANSHEE),
                    UnitExists(UnitTypeId.BATTLECRUISER, 2, include_pending=True),
                ]),
                None,
            ),
            Step(UnitReady(UnitTypeId.STARPORTTECHLAB, 1), TerranUnit(UnitTypeId.RAVEN, 2, priority=True)),
        ]

        air_support = BuildOrder(
            Step(UnitReady(UnitTypeId.STARPORTREACTOR, 1), TerranUnit(UnitTypeId.VIKINGFIGHTER, 6, priority=True)),
            TerranUnit(UnitTypeId.LIBERATOR, 4),
            TerranUnit(UnitTypeId.VIKINGFIGHTER, 10),
        )

        ground_units = BuildOrder(
            TerranUnit(UnitTypeId.SIEGETANK, 8),
            Step(UnitReady(UnitTypeId.BARRACKS, 1), TerranUnit(UnitTypeId.MARINE, 30)),
            Step(Minerals(350), TerranUnit(UnitTypeId.MARINE, 50)),
        )

        gas_plan = BuildOrder(
            Step(All([UnitReady(UnitTypeId.COMMANDCENTER, 2), Supply(34)]), BuildGas(4)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 3, include_pending=True), BuildGas(6)),
            Step(UnitExists(UnitTypeId.COMMANDCENTER, 4, include_pending=True), BuildGas(8)),
        )

        supply_buffer = BuildOrder(
            AutoDepot(),
            Step(All([Supply(50), Minerals(250)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
            Step(All([Supply(80), Minerals(350)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 12)),
            Step(All([Supply(110), Minerals(450)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 16)),
            Step(All([Supply(145), Minerals(550)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 20)),
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
            Step(None, JumpIn(), RequireCustom(lambda k: self.jump == 0)),
            self.attack,
            PlanFinishEnemy(),
        ]

        return BuildOrder(
            supply_buffer,
            scv,
            buildings,
            gas_plan,
            upgrades,
            bc_units,
            raven_block,
            air_support,
            ground_units,
            SequentialList(tactics),
        )


class LadderBot(YamatoRustFleet):
    @property
    def my_race(self):
        return Race.Terran
