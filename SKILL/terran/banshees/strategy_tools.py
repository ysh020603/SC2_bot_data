"""Resource-free tool package for the banshees strategy.

Derived from ``dummies/terran/banshees.py``. Resource-spending tools such as
``Repair`` are intentionally omitted. The original random attack threshold
``random.randint(4, 7) * 10`` is fixed to its lowest value, ``40``.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder, SequentialList
from sharpy.plans.acts import MineOpenBlockedBase
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time, UnitExists
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    SpeedMining,
    WorkerScout,
)
from sharpy.plans.tactics.terran import (
    CallMule,
    ContinueBuilding,
    LowerDepots,
    ManTheBunkers,
    PlanZoneGatherTerran,
    ScanEnemy,
)


class BansheesStrategyTools(BuildOrder):
    """Banshee strategy tools that do not spend minerals, gas, or supply."""

    def __init__(self, attack_value: int = 40):
        super().__init__(
            SequentialList([
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                PlanZoneDefense(),
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(None, CallMule(50), skip=Time(5 * 60)),
                Step(None, CallMule(100), skip_until=Time(5 * 60)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                DistributeWorkers(4),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ManTheBunkers(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                Step(None, PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ])
        )
