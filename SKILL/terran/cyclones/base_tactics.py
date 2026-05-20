from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, Time, TechReady, UnitReady
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


class CyclonesTactics(BuildOrder):
    """飓风战术/后台宏机制列表（并行执行）"""
    def __init__(self, attack_value: int = 40):
        scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
        
        super().__init__(
            [
                AutoDepot(),
                Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                PlanZoneDefense(),
                scout,
                Step(None, CallMule(50), skip=Time(5 * 60)),
                Step(None, CallMule(100), skip_until=Time(5 * 60)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ManTheBunkers(),
                Repair(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                # 升级完锁定伤害后开始进攻
                Step(TechReady(UpgradeId.CYCLONELOCKONDAMAGEUPGRADE, 0.95), PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
