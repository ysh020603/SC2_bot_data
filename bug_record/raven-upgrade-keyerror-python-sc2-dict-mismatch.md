# Raven upgrade `KeyError` from `python-sc2` dict mismatch

**Status:** Fixed for Agent runtime dependency loading (2026-07-06)  
**Discovered:** 2026-07-06  
**Affected strategies (confirmed):** `raven_screams`, `two_base_matrix_tanks`  
**Severity:** High — BO-list matches crash at runtime (`exit=1`)

---

## Summary

BO-list runs for Terran strategies that research **Raven Corvid Reactor** crash with:

```text
KeyError: UpgradeId.RAVENCORVIDREACTOR
```

The failure happens when `ExecutionScheduler` issues a research action and `Tech.__init__` looks up the upgrade in `UPGRADE_RESEARCHED_FROM`. The **conda-installed** `python-sc2` package used at Agent runtime is missing several upgrade→building mappings that exist in the **repo-bundled** `python-sc2` submodule.

The Agent now owns a complete `python-sc2` snapshot and validates its import source at startup. A Raven fallback remains in Agent `Tech` as defense in depth. The outer collection tree continues to use its own complete snapshot; the two repositories no longer depend on each other's runtime directory.

---

## Symptoms

| Item | Detail |
|------|--------|
| Batch log | `game_records/bo_exec_27b_14strat_k_tv_r20_stdout.log` — `raven_screams` / `two_base_matrix_tanks` consistently `exit=1` |
| Example run dir | `SC2-Agent-260510/game_records/bo_exec_27b_14strat_k_tv_r20/..._run91/` |
| Failing action | `RESEARCH_RAVENCORVIDREACTOR` |
| Game time at crash | ~9–10 minutes (after BO sequence reaches the research step) |

### Stack trace (abbreviated)

```text
ExecutionScheduler._issue_build_or_research
  → _create_sharpy_act
    → mapping.make_research_act(pa.target_result)
      → Tech(upgrade)
        → UPGRADE_RESEARCHED_FROM[self.upgrade_type]
KeyError: UpgradeId.RAVENCORVIDREACTOR
```

**Crash site in Agent tree:**

- `SC2-Agent-260510/SC2_Agent/execution/scheduler.py` — `_create_sharpy_act()` (~L1128–1129)
- `SC2-Agent-260510/SC2_Agent/execution/mapping.py` — `make_research_act()` (~L115–122)
- `SC2-Agent-260510/sharpy/plans/acts/tech.py` — `Tech.__init__()` (lookup before fix)

---

## Root cause

### 1. Research execution path

BO-list mode loads actions from `BO.json`, classifies them in the scheduler, and for research uses:

```python
# SC2-Agent-260510/SC2_Agent/execution/mapping.py
def make_research_act(target_result):
    upgrade = upgrade_for(target_result or "")
    return Tech(upgrade)
```

For `RESEARCH_RAVENCORVIDREACTOR`:

| Step | Value |
|------|-------|
| BO action name | `RESEARCH_RAVENCORVIDREACTOR` |
| `target_result` (from `cost_for_action`) | `RavenCorvidReactor` |
| `UpgradeId` | `RAVENCORVIDREACTOR` |
| Expected building | `UnitTypeId.STARPORTTECHLAB` |

**Data references:**

- `SC2-Agent-260510/BO_list/terran/raven_screams/BO.json` — contains `RESEARCH_RAVENCORVIDREACTOR` (~L115)
- `SC2-Agent-260510/BO_list/terran/two_base_matrix_tanks/BO.json` — contains `RESEARCH_RAVENCORVIDREACTOR` (~L63)
- `SC2-Agent-260510/SC2_Agent/data_tools/data_base_add_graph.json` — node `RESEARCH_RAVENCORVIDREACTOR` / `RavenCorvidReactor`
- `SC2-Agent-260510/SC2_Agent/data_tools/sc2_data_common.py` — `"RESEARCH_RAVENCORVIDREACTOR": "Starport"`

**Dummy reference (works when run as KnowledgeBot, not BO-list):**

- `dummies/terran/raven_screams.py` — `Tech(UpgradeId.RAVENCORVIDREACTOR)` gated on `UnitReady(UnitTypeId.STARPORTTECHLAB, 1)` (~L63)
- `dummies/terran/two_base_matrix_tanks.py` — same pattern (~L107)

### 2. `python-sc2` version split

`Tech` resolves the research building from:

```python
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
```

| Source | Path | `RAVENCORVIDREACTOR` present? |
|--------|------|-------------------------------|
| Repo submodule | `python-sc2/sc2/dicts/upgrade_researched_from.py` (~L77–79) | **Yes** → `STARPORTTECHLAB` |
| Conda runtime (observed) | `/data2/shy/python-sc2/sc2/dicts/upgrade_researched_from.py` | **No** |

Repo dict has **15** upgrade entries not present in the conda package (conda has 0 extras). Missing entries include:

- `RAVENCORVIDREACTOR`, `RAVENENHANCEDMUNITIONS`, `RAVENRECALIBRATEDEXPLOSIVES`
- `TERRANSHIPARMORSLEVEL1/2/3`, `TERRANVEHICLEARMORSLEVEL1`
- `DURABLEMATERIALS`, `TRANSFORMATIONSERVOS`, `NEOSTEELFRAME`
- `CYCLONELOCKONRANGEUPGRADE`, `CYCLONERAPIDFIRELAUNCHERS`, `ARMORPIERCINGROCKETS`
- `MEDIVACINCREASESPEEDBOOST`, `MEDIVACRAPIDDEPLOYMENT`

### 3. Historical Agent import bug

The Agent previously inserted a relative path:

```python
sys.path.insert(1, "python-sc2")  # ~L40
```

At the time of the crash, `SC2-Agent-260510/python-sc2/` did not exist. Python therefore imported `sc2` from the conda environment, which lacked the mappings above.

This is now fixed by:

- vendoring the fixed snapshot under `SC2-Agent-260510/python-sc2/`;
- loading it through `SC2-Agent-260510/sc2_runtime.py` using a path derived from `__file__`;
- rejecting an already-imported conda/site-packages `sc2`;
- checking all 15 previously missing mappings during startup.

---

## Fix applied

**File:** `SC2-Agent-260510/sharpy/plans/acts/tech.py`

The Agent retains `_UPGRADE_FALLBACK` for the three Raven upgrades. Mapping resolution now raises a clear `RuntimeError` if neither the bundled dictionary nor fallback can resolve a research building; `None` is not accepted.

```python
_UPGRADE_FALLBACK = {
    UpgradeId.RAVENCORVIDREACTOR: UnitTypeId.STARPORTTECHLAB,
    UpgradeId.RAVENENHANCEDMUNITIONS: UnitTypeId.STARPORTTECHLAB,
    UpgradeId.RAVENRECALIBRATEDEXPLOSIVES: UnitTypeId.STARPORTTECHLAB,
}
```

**Runtime ownership:**

| Location | State |
|----------|-------|
| `SC2-Agent-260510/python-sc2` | Agent-owned complete snapshot; required at startup |
| `sharpy-sc2/python-sc2` | Collection-owned complete snapshot |
| `sharpy-sc2/sharpy/plans/acts/tech.py` | Still uses the collection snapshot's complete dictionary |
| Conda `python-sc2` | Unchanged and no longer accepted by Agent runtime |

---

## Equivalence notes

### Raven research (`RAVENCORVIDREACTOR`)

After the Agent `tech.py` patch, the **in-game effect** matches the repo path: both resolve to `STARPORTTECHLAB` and call the same `Tech.execute()` logic.

### Not fully equivalent across trees

| Layer | Agent (`SC2-Agent-260510`) | Outer / collection (`obs_system` + repo root) |
|-------|----------------------------|-----------------------------------------------|
| `python-sc2` dict | Agent-local complete snapshot + startup validation | Collection-local complete snapshot |
| `tech.py` | Fallback plus explicit failure on unresolved mapping | Original hard lookup against complete local dictionary |
| `scheduler.py` | ~1413 lines (newer executor) | `obs_system/SC2_Agent/execution/scheduler.py` ~414 lines |
| `mapping.py` | `make_addon_act`, `Expand(..., priority=True, ...)` | Simpler `Expand(to_count)` only |

Other BO research actions that **do** map correctly on conda (e.g. `RESEARCH_COMBATSHIELD` → `ShieldWall`, `BARRACKSTECHLABRESEARCH_STIMPACK` → `Stimpack`) are unaffected.

---

## Related data-layer gaps (separate issue)

These BO actions resolve to `target_result=None` in `cost_for_action()` and cannot construct a `Tech` act in **either** tree:

- `RESEARCH_TERRANSHIPWEAPONS`
- `RESEARCH_TERRANINFANTRYWEAPONS`
- `RESEARCH_TERRANINFANTRYARMOR`

**Seen in:** e.g. `SC2-Agent-260510/BO_list/terran/stim_rush_relay/BO.json`

Scheduler abandons them with `"abandoned: cannot map to sharpy act"` (`scheduler.py` ~L1028–1030).

---

## Recommended follow-ups

1. **Fix `target_result=None` research actions** in the BO conversion path for level-style upgrades (`RESEARCH_TERRANSHIPWEAPONS`, etc.). This remains a separate open issue.
2. **Keep snapshots intentional** — when either repository updates `python-sc2`, record its source version and run the dependency self-checks documented under both `docs/` trees.
3. **Keep BO preflight strict** — reject unmappable research commands before launching a game rather than allowing timeout-based abandonment.

---

## Quick navigation index

| Topic | Path |
|-------|------|
| Partial fix | `SC2-Agent-260510/sharpy/plans/acts/tech.py` |
| Unpatched outer `Tech` | `sharpy/plans/acts/tech.py` |
| Research → `Tech` bridge | `SC2-Agent-260510/SC2_Agent/execution/mapping.py` |
| Scheduler research issue | `SC2-Agent-260510/SC2_Agent/execution/scheduler.py` |
| Action cost / `target_result` | `SC2-Agent-260510/SC2_Agent/data_tools/action_cost.py` |
| Complete upgrade dict (repo) | `python-sc2/sc2/dicts/upgrade_researched_from.py` |
| BO: raven_screams | `SC2-Agent-260510/BO_list/terran/raven_screams/BO.json` |
| BO: two_base_matrix_tanks | `SC2-Agent-260510/BO_list/terran/two_base_matrix_tanks/BO.json` |
| Dummy: raven_screams | `dummies/terran/raven_screams.py` |
| Dummy: two_base_matrix_tanks | `dummies/terran/two_base_matrix_tanks.py` |
| Agent runtime bootstrap | `SC2-Agent-260510/sc2_runtime.py` |
| Agent-local dependency provenance | `SC2-Agent-260510/python-sc2/VENDORED_FROM.md` |
| Obs-system executor (older) | `obs_system/SC2_Agent/execution/scheduler.py` |
| Example crash log | `SC2-Agent-260510/game_records/bo_exec_27b_14strat_k_tv_r20/.../...run91.log` |
