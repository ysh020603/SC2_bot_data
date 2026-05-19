from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans import BuildOrder
from sharpy.plans.sequential_list import SequentialList
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, Time, UnitReady
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


class BioTactics(SequentialList):
    """常规生化部队战术列表"""
    def __init__(self, attack_value: int = 26):
        scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
        
        super().__init__(
            [
                *BuildOrder([]).depots,
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
                PlanZoneAttack(attack_value),
                PlanFinishEnemy(),
            ]
        )
