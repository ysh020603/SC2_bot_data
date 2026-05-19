from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.sequential_list import SequentialList
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *


class TwoBaseTanksTactics(SequentialList):
    def __init__(self):
        super().__init__(
            [
            *BuildOrder([]).depots,
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
