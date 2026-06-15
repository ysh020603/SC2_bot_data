"""Default (strategy-agnostic) Terran scouting & attacking tactics.

These are the GENERIC scout/attack behaviours used when the selected skill does
NOT provide its own ``scout_attack.py``. They are intentionally conservative
(macro-friendly): scout with a worker once a depot exists, scan past 5 minutes,
gather the army, and only commit to an attack once a reasonable power threshold
is reached.

A skill can override this by placing a module ``scout_attack.py`` inside its own
folder (``SKILL/terran/<strategy>/scout_attack.py``) that defines a
``BuildOrder``/``SequentialList`` subclass. The bot's ``_load_scout_attack_tactics``
prefers the per-skill module and falls back to this one.

All tools here are resource-free (no minerals/gas/supply), so they never compete
with the command-style ``ExecutionScheduler`` for resources.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time, UnitExists
from sharpy.plans.tactics import (
    PlanFinishEnemy,
    PlanZoneAttack,
    WorkerScout,
)
from sharpy.plans.tactics.terran import PlanZoneGatherTerran, ScanEnemy


class DefaultScoutAttackTactics(BuildOrder):
    """Generic, macro-friendly scout + attack package (resource-free).

    :param attack_value: army power threshold that triggers ``PlanZoneAttack``.
                         Higher = more conservative (attacks later).
    """

    def __init__(self, attack_value: int = 60):
        super().__init__(
            [
                # --- scouting ---
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                # --- gather / attack ---
                PlanZoneGatherTerran(),
                PlanZoneAttack(attack_value),
                PlanFinishEnemy(),
            ]
        )
