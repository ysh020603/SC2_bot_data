"""Per-skill scouting and attacking tactics for the terran_silver_bio strategy."""

from sharpy.plans import BuildOrder
from sharpy.plans.tactics import PlanFinishEnemy
from sharpy.plans.tactics.terran import PlanZoneGatherTerran
from sharpy.plans.tactics.weak import WeakAttack


class TerranSilverBioScoutAttack(BuildOrder):
    """Silver bio attack package using Sharpy's weak attack tactic."""

    def __init__(self, attack_value: int = 30):
        super().__init__(
            [
                PlanZoneGatherTerran(),
                WeakAttack(attack_value),
                PlanFinishEnemy(),
            ]
        )
