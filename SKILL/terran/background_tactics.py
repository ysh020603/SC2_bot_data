"""Background (always-on) Terran tactics for the LLM increment-driven pipeline.

The new command-style pipeline (`ExecutionScheduler`) owns ALL resource spending
(structures / units / upgrades / add-ons / base morphs / supply depots). The
background layer therefore keeps ONLY tools that do **not** spend minerals, gas or
supply, so it never competes with the scheduler for resources.

Scope of THIS module (strategy-agnostic, always on):

* free operations: ``MineOpenBlockedBase``, ``PlanCancelBuilding``, ``LowerDepots``,
                   ``DistributeWorkers``, ``SpeedMining``, ``ContinueBuilding``,
                   ``CallMule``
* defense:         ``PlanZoneDefense``, ``PlanWorkerOnlyDefense``, ``ManTheBunkers``

**Scouting and attacking are intentionally NOT here.** They are strategy-specific
and are injected per skill from ``SKILL/terran/<strategy>/scout_attack.py`` (with a
generic fallback in ``SKILL/terran/scout_attack_default.py``). See
``_load_scout_attack_tactics`` in ``dummies/generic/universal_llm_bot.py``.

Explicitly EXCLUDED (they spend resources and are handled by the pipeline +
supply_planner instead): ``AutoDepot`` (minerals), ``MorphOrbitals`` (minerals),
``Repair`` (minerals/gas), ``DefensiveBuilding`` (minerals/gas).

See ``SKILL/terran/scout_and_attack_tools.md`` §2.2 for the full classification.
"""

from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time
from sharpy.plans.acts import MineOpenBlockedBase
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanWorkerOnlyDefense,
    PlanZoneDefense,
    SpeedMining,
)
from sharpy.plans.tactics.terran import (
    CallMule,
    ContinueBuilding,
    LowerDepots,
    ManTheBunkers,
)


class BackgroundTactics(BuildOrder):
    """Always-on, resource-free, strategy-agnostic Terran background behaviours.

    Contains only economy/upkeep operations and defense. Scouting and attacking
    are injected separately per selected skill (see module docstring).
    """

    def __init__(self):
        super().__init__(
            [
                # --- free operations ---
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ContinueBuilding(),
                # 兜底：把意外起飞的 Barracks/Factory/Starport 召回降落，避免
                # LLM 观测里看到 *Flying 类型时误判「主体建筑缺失」从而重复建造。
                # --- MULE energy economy (no minerals/gas/supply) ---
                Step(None, CallMule(50), skip=Time(5 * 60)),
                Step(None, CallMule(100), skip_until=Time(5 * 60)),
                # --- defense (always on, resource-free) ---
                PlanZoneDefense(),
                PlanWorkerOnlyDefense(),
                ManTheBunkers(),
            ]
        )
