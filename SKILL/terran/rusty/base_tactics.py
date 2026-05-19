import random
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans import BuildOrder
from sharpy.plans.sequential_list import SequentialList
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


class RustyTactics(SequentialList):
    """老式重工战术列表"""
    def __init__(self, attack_value: int = None):
        if attack_value is None:
            attack_value = random.randint(50, 80)
            
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
                CallMule(100),
                ScanEnemy(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ManTheBunkers(),
                Repair(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                Step(None, PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
