"""Resource-free tool package for the marine_rush strategy.

Derived from ``dummies/terran/marine_rush.py``. The original dummy has multiple
build candidates; this package fixes the tool behavior to candidate 0 and uses
its lowest attack threshold, ``DodgeRampAttack(3)``. Resource-spending tools such
as ``Repair`` are intentionally omitted.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.combat import MoveType
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


class DodgeRampAttack(PlanZoneAttack):
    """PlanZoneAttack that retreats from force fields on our base ramp."""

    async def execute(self) -> bool:
        try:
            base_ramp = self.zone_manager.expansion_zones[-1].ramp
            for effect in self.ai.state.effects:
                if effect.id != "FORCEFIELD":
                    continue
                pos: Point2 = base_ramp.bottom_center
                for epos in effect.positions:
                    if pos.distance_to_point2(epos) < 5:
                        return await self.small_retreat()
        except Exception:
            pass
        return await super().execute()

    async def small_retreat(self) -> bool:
        attacking_units = self.roles.attacking_units
        natural = self.zone_manager.expansion_zones[-2]
        for unit in attacking_units:
            self.combat.add_unit(unit)
        self.combat.execute(natural.gather_point, MoveType.DefensiveRetreat)
        return False


class MarineRushStrategyTools(BuildOrder):
    """Marine-rush strategy tools that do not spend minerals, gas, or supply."""

    def __init__(self, attack_value: int = 3):
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
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ManTheBunkers(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                Step(None, DodgeRampAttack(attack_value)),
                PlanFinishEnemy(),
            ])
        )
