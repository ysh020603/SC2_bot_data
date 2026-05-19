from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *


class TwoBaseTanksTactics(BuildOrder):
    """双矿坦克战术列表（并行执行）"""
    def __init__(self):
        super().__init__(
            [
            # AutoDepot(),
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            MineOpenBlockedBase(),
            PlanCancelBuilding(),
            LowerDepots(),
            PlanZoneDefense(),
            Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.BARRACKS, 1)),
            ScanEnemy(120),
            CallMule(),
            DistributeWorkers(),
            Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
            Repair(),
            ContinueBuilding(),
            PlanZoneGatherTerran(),
            PlanZoneAttack(60),
            PlanFinishEnemy(),
        ]
        )
