"""Per-skill scouting and attacking tactics for the banshees strategy."""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time, UnitExists
from sharpy.plans.tactics import PlanFinishEnemy, PlanZoneAttack, WorkerScout
from sharpy.plans.tactics.terran import PlanZoneGatherTerran, ScanEnemy


class BansheesScoutAttack(BuildOrder):
    """Banshee-style macro scout and attack package."""

    def __init__(self, attack_value: int = 50):
        super().__init__(
            [
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                PlanZoneGatherTerran(),
                Step(None, PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
