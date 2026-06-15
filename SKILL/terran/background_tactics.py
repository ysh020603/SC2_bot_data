"""Background (always-on) Terran tactics for the LLM increment-driven pipeline.

The new command-style pipeline (`ExecutionScheduler`) owns ALL resource spending
(structures / units / upgrades / add-ons / base morphs / supply depots). The
background layer therefore keeps ONLY tools that do **not** spend minerals, gas or
supply, so it never competes with the scheduler for resources:

* scouting:        ``WorkerScout``, ``ScanEnemy``
* defense:         ``PlanZoneDefense``, ``PlanWorkerOnlyDefense``, ``ManTheBunkers``
* gather/attack:   ``PlanZoneGatherTerran``, ``PlanZoneAttack``, ``PlanFinishEnemy``
* free operations: ``CallMule``, ``LowerDepots``, ``MineOpenBlockedBase``,
                   ``SpeedMining``, ``ContinueBuilding``, ``DistributeWorkers``,
                   ``PlanCancelBuilding``

Explicitly EXCLUDED (they spend resources and are handled by the pipeline +
supply_planner instead): ``AutoDepot`` (minerals), ``MorphOrbitals`` (minerals),
``Repair`` (minerals/gas), ``DefensiveBuilding`` (minerals/gas).

See ``SKILL/terran/scout_and_attack_tools.md`` §2.2 for the full classification.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time, UnitExists
from sharpy.plans.acts import MineOpenBlockedBase
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanWorkerOnlyDefense,
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


class BackgroundTactics(BuildOrder):
    """Always-on, resource-free Terran background behaviours (parallel)."""

    def __init__(self, attack_value: int = 60):
        super().__init__(
            [
                # --- free operations ---
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ContinueBuilding(),
                # --- MULE energy economy (no minerals/gas/supply) ---
                Step(None, CallMule(50), skip=Time(5 * 60)),
                Step(None, CallMule(100), skip_until=Time(5 * 60)),
                # --- scouting ---
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                # --- defense ---
                PlanZoneDefense(),
                PlanWorkerOnlyDefense(),
                ManTheBunkers(),
                # --- gather / attack ---
                PlanZoneGatherTerran(),
                PlanZoneAttack(attack_value),
                PlanFinishEnemy(),
            ]
        )
