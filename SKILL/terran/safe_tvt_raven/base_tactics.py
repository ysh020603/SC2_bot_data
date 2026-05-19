from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import TechReady, UnitReady
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


class SafeTvTRavenTactics(BuildOrder):
    """TvT 安全铁鸦开局战术列表（并行执行）"""
    def __init__(self, attack_value: int = 4):
        super().__init__(
            [
                # AutoDepot(),
                Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
                CallMule(50),
                LowerDepots(),
                MineOpenBlockedBase(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                Repair(),
                ContinueBuilding(),
                PlanZoneGather(),
                PlanWorkerOnlyDefense(),
                PlanZoneDefense(),
                # 兴奋剂研发完毕后进攻
                Step(TechReady(UpgradeId.STIMPACK), PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
