"""marine_rush — per-skill scouting & attacking tactics.

Injected by the bot when the ``marine_rush`` strategy is selected (see
``_load_scout_attack_tactics`` in ``dummies/generic/universal_llm_bot.py``). It
replaces the generic ``scout_attack_default.DefaultScoutAttackTactics`` with an
aggressive, rush-oriented scout/attack package.

Modelled after the hand-written dummy ``dummies/terran/marine_rush.py``:

* early worker scout once the first Supply Depot exists, scan past 5 min;
* gather the army at the forward gather point;
* ``DodgeRampAttack`` — a ``PlanZoneAttack`` that dodges enemy FORCEFIELD on the
  ramp (small retreat) and attacks at a LOW power threshold (rush aggression);
* ``PlanFinishEnemy`` to all-in once the enemy is crippled.

All tools are resource-free (no minerals/gas/supply), so they never compete with
the command-style ``ExecutionScheduler``.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.combat import MoveType
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time, UnitExists
from sharpy.plans.tactics import (
    PlanFinishEnemy,
    PlanZoneAttack,
    WorkerScout,
)
from sharpy.plans.tactics.terran import PlanZoneGatherTerran, ScanEnemy


class DodgeRampAttack(PlanZoneAttack):
    """``PlanZoneAttack`` that retreats from enemy FORCEFIELD on our base ramp.

    Copied/adapted from ``dummies/terran/marine_rush.py`` so the marine-rush LLM
    strategy keeps the same ramp-dodging behaviour as the hand-written bot.
    """

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
            # Be defensive: never let micro-dodging crash the background layer.
            pass
        return await super().execute()

    async def small_retreat(self) -> bool:
        attacking_units = self.roles.attacking_units
        natural = self.zone_manager.expansion_zones[-2]
        for unit in attacking_units:
            self.combat.add_unit(unit)
        self.combat.execute(natural.gather_point, MoveType.DefensiveRetreat)
        return False


class MarineRushScoutAttack(BuildOrder):
    """Aggressive rush scout + attack package for the ``marine_rush`` skill.

    :param attack_value: army power threshold to trigger the attack. Kept LOW for
                         rush aggression (attack early). Default ``10``.
    """

    def __init__(self, attack_value: int = 10):
        super().__init__(
            [
                # --- scouting ---
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                # --- gather / aggressive attack ---
                PlanZoneGatherTerran(),
                Step(None, DodgeRampAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
