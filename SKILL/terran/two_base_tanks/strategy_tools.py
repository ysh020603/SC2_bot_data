"""Resource-free tool package for the two_base_tanks strategy."""

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
    WorkerScout,
)
from sharpy.plans.tactics.terran import (
    CallMule,
    ContinueBuilding,
    LowerDepots,
    PlanZoneGatherTerran,
    ScanEnemy,
)


class TwoBaseTanksStrategyTools(BuildOrder):
    """Two-base tank strategy tools that do not spend minerals, gas, or supply."""

    def __init__(self, attack_value: int = 40):
        super().__init__(
            SequentialList([
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                PlanZoneDefense(),
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.BARRACKS, 1)),
                ScanEnemy(120),
                CallMule(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                PlanZoneAttack(attack_value),
                PlanFinishEnemy(),
            ])
        )
