"""Resource-free tool package for the one_base_turtle strategy."""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder, SequentialList
from sharpy.plans.acts import MineOpenBlockedBase
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    SpeedMining,
)
from sharpy.plans.tactics.terran import (
    CallMule,
    ContinueBuilding,
    LowerDepots,
    ManTheBunkers,
    PlanZoneGatherTerran,
)


class OneBaseTurtleStrategyTools(BuildOrder):
    """One-base turtle tools that do not spend minerals, gas, or supply."""

    def __init__(self, attack_value: int = 4, marine_count: int = 18):
        super().__init__(
            SequentialList([
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                ManTheBunkers(),
                LowerDepots(),
                PlanZoneDefense(),
                CallMule(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                Step(
                    UnitExists(UnitTypeId.MARINE, marine_count, include_killed=True),
                    PlanZoneAttack(attack_value),
                ),
                PlanFinishEnemy(),
            ])
        )
