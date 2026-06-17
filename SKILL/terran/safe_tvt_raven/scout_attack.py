"""Per-skill scouting and attacking tactics for the safe_tvt_raven strategy."""

from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import TechReady
from sharpy.plans.tactics import PlanFinishEnemy, PlanZoneAttack, PlanZoneGather


class SafeTvTRavenScoutAttack(BuildOrder):
    """TvT raven attack package that moves out after Stimpack is ready."""

    def __init__(self, attack_value: int = 4):
        super().__init__(
            [
                PlanZoneGather(),
                Step(TechReady(UpgradeId.STIMPACK), PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
