"""Per-skill scouting and attacking tactics for the one_base_turtle strategy."""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists
from sharpy.plans.tactics import PlanFinishEnemy, PlanZoneAttack
from sharpy.plans.tactics.terran import PlanZoneGatherTerran


class OneBaseTurtleScoutAttack(BuildOrder):
    """Turtle attack package that waits for enough marines to guard tanks."""

    def __init__(self, attack_value: int = 4, marine_count: int = 18):
        super().__init__(
            [
                PlanZoneGatherTerran(),
                Step(
                    UnitExists(UnitTypeId.MARINE, marine_count, include_killed=True),
                    PlanZoneAttack(attack_value),
                ),
                PlanFinishEnemy(),
            ]
        )
