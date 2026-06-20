"""Resource-free tool package for the terran_silver_bio strategy."""

from sharpy.plans import BuildOrder, SequentialList
from sharpy.plans.acts import MineOpenBlockedBase
from sharpy.plans.tactics import DistributeWorkers, PlanFinishEnemy
from sharpy.plans.tactics.terran import CallMule, ContinueBuilding, LowerDepots, PlanZoneGatherTerran
from sharpy.plans.tactics.weak import WeakAttack, WeakDefense


class TerranSilverBioStrategyTools(BuildOrder):
    """Silver bio tools that do not spend minerals, gas, or supply."""

    def __init__(self, attack_value: int = 30):
        super().__init__(
            SequentialList([
                MineOpenBlockedBase(),
                LowerDepots(),
                WeakDefense(),
                CallMule(0),
                DistributeWorkers(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                WeakAttack(attack_value),
                PlanFinishEnemy(),
            ])
        )
