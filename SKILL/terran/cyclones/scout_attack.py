"""Per-skill scouting and attacking tactics for the cyclones strategy."""

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import TechReady, Time, UnitExists
from sharpy.plans.tactics import PlanFinishEnemy, PlanZoneAttack, WorkerScout
from sharpy.plans.tactics.terran import PlanZoneGatherTerran, ScanEnemy


class CyclonesScoutAttack(BuildOrder):
    """Cyclone scout and attack package gated by Mag-Field Accelerator."""

    def __init__(self, attack_value: int = 40):
        super().__init__(
            [
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                PlanZoneGatherTerran(),
                Step(
                    TechReady(UpgradeId.CYCLONELOCKONDAMAGEUPGRADE, 0.95),
                    PlanZoneAttack(attack_value),
                ),
                PlanFinishEnemy(),
            ]
        )
