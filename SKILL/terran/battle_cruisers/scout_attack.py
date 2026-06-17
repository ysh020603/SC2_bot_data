"""battle_cruisers — per-skill scouting & attacking tactics.

Injected by the bot when the ``battle_cruisers`` strategy is selected (see
``_load_scout_attack_tactics`` in ``dummies/generic/universal_llm_bot.py``). It
replaces the generic ``scout_attack_default.DefaultScoutAttackTactics`` with a
macro-oriented scout/attack package tailored to Battlecruiser armies.

Modelled after the hand-written dummy ``dummies/terran/battle_cruisers.py``:

* early worker scout once the first Supply Depot exists, scan past 5 min;
* gather the army at the forward gather point;
* ``TacticalJumpIn`` — once 2+ Battlecruisers exist, warp them behind the enemy
  main mineral line via ``EFFECT_TACTICALJUMP`` before the general attack;
* ``PlanZoneAttack`` at a HIGH power threshold (macro timing, default ``60``);
* ``PlanFinishEnemy`` to close out a crippled opponent.

All tools are resource-free (no minerals/gas/supply), so they never compete with
the command-style ``ExecutionScheduler``.
"""

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.acts import ActBase
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time, UnitExists
from sharpy.plans.tactics import (
    PlanFinishEnemy,
    PlanZoneAttack,
    WorkerScout,
)
from sharpy.plans.tactics.terran import PlanZoneGatherTerran, ScanEnemy


class TacticalJumpIn(ActBase):
    """Warp Battlecruisers behind the enemy main via Tactical Jump.

    Copied/adapted from ``dummies/terran/battle_cruisers.py`` ``JumpIn`` so the
    battle-cruiser LLM strategy keeps the same pre-attack warp behaviour as the
    hand-written bot. Runs once when at least two Battlecruisers are ready.
    """

    def __init__(self):
        self.done = False
        super().__init__()

    async def execute(self) -> bool:
        if self.done:
            return True
        bcs = self.cache.own(UnitTypeId.BATTLECRUISER)
        if bcs.amount > 1:
            self.done = True
            jump_target = self.zone_manager.enemy_main_zone.behind_mineral_position_center
            for bc in bcs:
                self.knowledge.cooldown_manager.used_ability(bc.tag, AbilityId.EFFECT_TACTICALJUMP)
                bc(AbilityId.EFFECT_TACTICALJUMP, jump_target)
        return True


class BattleCruisersScoutAttack(BuildOrder):
    """Macro scout + attack package for the ``battle_cruisers`` skill.

    :param attack_value: army power threshold to trigger the attack. Kept HIGH for
                         macro timing (attack with a sizeable BC ball). Default ``60``.
    :param use_tactical_jump: when ``True``, warp BCs behind enemy main once 2+
                                Battlecruisers exist, before ``PlanZoneAttack``.
    """

    def __init__(self, attack_value: int = 60, use_tactical_jump: bool = True):
        steps = [
            # --- scouting ---
            Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
            Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
            # --- gather / attack ---
            PlanZoneGatherTerran(),
        ]
        if use_tactical_jump:
            steps.append(Step(None, TacticalJumpIn()))
        steps.extend(
            [
                Step(None, PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
        super().__init__(steps)
