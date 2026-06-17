"""Per-skill scouting and attacking tactics for the rusty strategy."""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists
from sharpy.plans.tactics import PlanFinishEnemy, PlanZoneAttack, WorkerScout
from sharpy.plans.tactics.terran import PlanZoneGatherTerran, ScanEnemy


class RustyScoutAttack(BuildOrder):
    """Macro scout and attack package modelled after dummies/terran/rusty.py."""

    def __init__(self, attack_value: int = 60):
        super().__init__(
            [
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                ScanEnemy(),
                PlanZoneGatherTerran(),
                Step(None, PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
