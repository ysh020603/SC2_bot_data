# BO Collection: Inflated Build-Order Actions vs Actual Structures

**Discovered:** 2026-07-04  
**Scope:** Terran BO-list strategies populated from `2026-06-24_terran_6bots_3maps_macro`  
**Status:** Open (bot build-plan design; collection faithfully records bot behavior)

---

## Summary

Several `BO.json` files under `SC2-Agent-260510/BO_list/terran/` contain far more `TERRANBUILD_SUPPLYDEPOT` (and similar batch-build) entries than the number of Supply Depots that actually completed in the source game. This is **not** a recording bug in the collection pipeline. The `AbilityRecorderManager` correctly logs every macro build command the bot issues and commits. The root cause is **Sharpy dummy bot build plans** that use `GridBuilding` with high target counts, causing the bot to queue many build orders in rapid succession before earlier structures finish.

When `populate_bo_list_from_collection.py` copies `order_list` verbatim into `BO.json`, these queued commands become the canonical build order for BO-list execution — which does not match what the bot actually built in-game.

---

## How BO Files Are Produced

1. **Collection run:** `tools/collect_terran_bo.py` plays dummy bots and writes sequence JSON via `AbilityRecorderManager`.
2. **Selection:** `SC2-Agent-260510/tools/populate_bo_list_from_collection.py` picks the highest-difficulty Terran-opponent victory on `KairosJunctionLE` and copies `order_list` → `BO.json`.
3. **Manifest:** Selection metadata is stored in `SC2-Agent-260510/BO_list/terran/populate_manifest.json`.

Relevant code:

| Component | Path |
|-----------|------|
| Ability recording | `sharpy/managers/extensions/ability_recorder.py` |
| Batch building logic | `sharpy/plans/acts/grid_building.py` |
| Auto depot prediction | `sharpy/plans/acts/terran/auto_depot.py` |
| Default depot chain | `sharpy/plans/build_order.py` (`.depots` property) |
| BO populate script | `SC2-Agent-260510/tools/populate_bo_list_from_collection.py` |
| Collection entry | `tools/collect_terran_bo.py` |

---

## Issue 1: `tank_thor_mech` — Severe Depot / Factory Spam

### Affected files

| Role | Path |
|------|------|
| BO list (output) | `SC2-Agent-260510/BO_list/terran/tank_thor_mech/BO.json` |
| Source sequence | `bo_collection_runs/2026-06-24_terran_6bots_3maps_macro/KairosJunctionLE/tank_thor_mech/sequences/mechthor-ai.terran.hard.macro_KairosJunctionLE_2026-06-24 11_21_08_815141.json` |
| Match log | `bo_collection_runs/2026-06-24_terran_6bots_3maps_macro/KairosJunctionLE/tank_thor_mech/logs/mechthor-ai.terran.hard.macro_KairosJunctionLE_2026-06-24 11_18_21_628150.log` |
| Match replay | `bo_collection_runs/2026-06-24_terran_6bots_3maps_macro/KairosJunctionLE/tank_thor_mech/replays/mechthor-ai.terran.hard.macro_KairosJunctionLE_2026-06-24 11_18_21_628150.SC2Replay` |
| Bot build plan | `dummies/terran/tank_thor_mech.py` |
| Manifest entry | `SC2-Agent-260510/BO_list/terran/populate_manifest.json` (strategy: `tank_thor_mech`) |

### Match metadata

- **Bot:** `mechthor` (`TankThorMech`, display name "Rusty Tank Thor Mech")
- **Opponent:** `ai.terran.hard.macro`
- **Map:** `KairosJunctionLE`
- **Result:** Victory (~13:39)

### Recorded vs actual

| Metric | In `order_list` / `BO.json` | At game end (log) |
|--------|-------------------------------|-------------------|
| `TERRANBUILD_SUPPLYDEPOT` | **55** (27.5% of 200 actions) | **20** Supply Depots alive |
| `TERRANBUILD_FACTORY` | **25** | **9** Factories alive |
| Longest consecutive depot run | **15** (indices 48–62, ~4:40 game time) | — |

At the longest depot burst, supply was **46/54** (8 supply left) — the bot was queueing depots far ahead of need, not reacting to supply block.

End-game economy from log: **6323 minerals, 1650 gas, 200/200 supply** — severe resource float.

### Root cause (bot)

In `dummies/terran/tank_thor_mech.py`, three overlapping depot mechanisms run in parallel:

```python
# dummies/terran/tank_thor_mech.py — supply_buffer excerpt
supply_buffer = BuildOrder(
    AutoDepot(),
    Step(All([Supply(45), Minerals(250)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
    Step(All([Supply(70), Minerals(350)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 12)),
    Step(All([Supply(100), Minerals(450)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 16)),
    Step(All([Supply(135), Minerals(550)]), GridBuilding(UnitTypeId.SUPPLYDEPOT, 20)),
)
```

The final `BuildOrder` also includes `BuildOrder([]).depots` (default depot chain from `sharpy/plans/build_order.py`) and `spend_money` steps with `GridBuilding(FACTORY, 6/10/14)`.

`GridBuilding` issues a new build command on every execution tick while `current_count < to_count`, so one logical step produces many recorded actions.

### Collection verdict

- `order_list` == `sequence[].ability` — **consistent, no transform error**
- `BO.json` == source `order_list` — **faithful copy**
- Problem is **bot behavior**, not recorder corruption

---

## Issue 2: `two_base_tanks` — Milder Same Pattern

### Affected files

| Role | Path |
|------|------|
| BO list (output) | `SC2-Agent-260510/BO_list/terran/two_base_tanks/BO.json` |
| Source sequence | `bo_collection_runs/2026-06-24_terran_6bots_3maps_macro/KairosJunctionLE/two_base_tanks/sequences/tank-ai.terran.hard.macro_KairosJunctionLE_2026-06-24 11_16_40_138706.json` |
| Match log | `bo_collection_runs/2026-06-24_terran_6bots_3maps_macro/KairosJunctionLE/two_base_tanks/logs/tank-ai.terran.hard.macro_KairosJunctionLE_2026-06-24 11_14_28_906191.log` |
| Match replay | `bo_collection_runs/2026-06-24_terran_6bots_3maps_macro/KairosJunctionLE/two_base_tanks/replays/tank-ai.terran.hard.macro_KairosJunctionLE_2026-06-24 11_14_28_906191.SC2Replay` |
| Bot build plan | `dummies/terran/two_base_tanks.py` |
| Manifest entry | `SC2-Agent-260510/BO_list/terran/populate_manifest.json` (strategy: `two_base_tanks`) |

### Match metadata

- **Bot:** `tank` (`TwoBaseTanks`)
- **Opponent:** `ai.terran.hard.macro`
- **Map:** `KairosJunctionLE`
- **Result:** Victory (~12:06)

### Recorded vs actual

| Metric | In `order_list` / `BO.json` | At game end (log) |
|--------|-------------------------------|-------------------|
| `TERRANBUILD_SUPPLYDEPOT` | **31** (14.7% of 211 actions) | **14** Supply Depots alive |
| `TERRANBUILD_BARRACKS` | **12** (includes 6 consecutive at ~6:00) | **5** Barracks alive |
| `BARRACKSTRAIN_MARINE` | **89** (42.2%) | 53 Marines alive (95 trained total) |
| Longest consecutive depot run | **15** (indices 115–129, ~7:42) | — |

The late depot burst at index 115 occurred at **110/110 supply (supply blocked)** — unlike `tank_thor_mech`, this burst had a real trigger, but the bot still over-queued (15 commands for ~14 final depots).

Early game (first ~60 actions) is structurally sound; quality degrades after ~6:00 with barracks batching and marine/tank training spam.

### Root cause (bot)

In `dummies/terran/two_base_tanks.py`:

```python
# Batch depot targets
Step(Supply(45), GridBuilding(UnitTypeId.SUPPLYDEPOT, 8)),
Step(Supply(75), GridBuilding(UnitTypeId.SUPPLYDEPOT, 10)),
Step(Supply(85), GridBuilding(UnitTypeId.SUPPLYDEPOT, 14)),

# Batch barracks
Step(None, GridBuilding(UnitTypeId.BARRACKS, 5)),

# Unlimited parallel unit production
Step(Minerals(250), ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 100)),
Step(UnitReady(UnitTypeId.FACTORYTECHLAB, 1), ActUnit(UnitTypeId.SIEGETANK, UnitTypeId.FACTORY, 20)),
```

End-game economy from log: **2080 minerals, 1284 gas** at victory.

---

## Cross-Strategy Comparison

From `SC2-Agent-260510/BO_list/terran/*/BO.json` on the same collection run:

| Strategy | BO length | Depot actions | Depot % | Factory actions |
|----------|-----------|---------------|---------|-----------------|
| `bio` | 436 | 27 | 6.2% | 1 |
| `two_base_tanks` | 211 | 31 | 14.7% | 3 |
| `tank_thor_mech` | 200 | 55 | 27.5% | 25 |

All 15 victory sequences on `KairosJunctionLE` for both strategies show **max consecutive depot runs of 9–18**, indicating systemic bot behavior rather than a single bad game.

---

## Secondary Issue: `results.json` sequence_file Mismatch

In `bo_collection_runs/2026-06-24_terran_6bots_3maps_macro/KairosJunctionLE/*/results.json`, some match entries point `sequence_file` to the wrong JSON (e.g. a zerg veryeasy file listed for a terran hard match). This did **not** affect BO population because `populate_bo_list_from_collection.py` resolves sequences by filename glob (`{bot_key}-{opponent}_*.json`), not by the `sequence_file` field in `results.json`.

---

## Impact on BO-List Execution

When `UniversalLLMBot` runs with `--bo-list`, it loads `BO.json` and feeds actions to `ExecutionScheduler` in chunks (see `SC2-Agent-260510/dummies/generic/universal_llm_bot.py` and `SC2-Agent-260510/README.md`).

If the BO contains 55 depot build commands but the source game only ever had 20 depots, an executor following the BO literally will attempt to place far more depots than the reference match — wasting minerals and misrepresenting the intended macro rhythm.

---

## Recommended Fixes

1. **Bot build plans (primary):** Remove redundant depot layers in `tank_thor_mech.py` (`AutoDepot` + `supply_buffer` + `.depots`); replace batch `GridBuilding(SUPPLYDEPOT, N)` with supply-left-triggered single depots; cap `ActUnit` production targets.
2. **Re-collect:** Run `tools/collect_terran_bo.py` / `sft_pipeline.collect.run_collect` after bot fixes.
3. **Re-populate:** Run `SC2-Agent-260510/tools/populate_bo_list_from_collection.py` to regenerate `BO.json` files.
4. **Optional post-processing:** Deduplicate or collapse consecutive identical build commands in `order_list` before writing BO (would change semantics; prefer fixing bots first).
5. **Optional populate filter:** Reject sequences where depot action count exceeds completed depot count by a threshold, or prefer sequences with lower depot/factory action ratios.

---

## Key Takeaway

> The recorder logs **build commands issued**, not **structures completed**.  
> `BO.json` is a verbatim copy of those commands.  
> When dummy bots spam `GridBuilding` targets, the BO inflates accordingly — the game did not build that many structures, but the bot did order them.
