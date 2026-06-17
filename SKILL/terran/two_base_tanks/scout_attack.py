"""Per-skill scouting and attacking tactics for the two_base_tanks strategy."""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists
from sharpy.plans.tactics import PlanFinishEnemy, PlanZoneAttack, WorkerScout
from sharpy.plans.tactics.terran import PlanZoneGatherTerran, ScanEnemy


class TwoBaseTanksScoutAttack(BuildOrder):
    """Two-base tank scout and attack package."""

    def __init__(self, attack_value: int = 60):
        super().__init__(
            [
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.BARRACKS, 1)),
                ScanEnemy(120),
                PlanZoneGatherTerran(),
                PlanZoneAttack(attack_value),
                PlanFinishEnemy(),
            ]
        )
