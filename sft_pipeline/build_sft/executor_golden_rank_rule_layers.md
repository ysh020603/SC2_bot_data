# Executor Golden Rank — Rule Layers (L0–L4)

This document defines the five rule layers used by the Terran Executor golden-rank labeler. Each layer describes **what the rule means** and **when it applies**. It does not report dataset statistics.

For tooling, I/O format, and CLI usage, see [`executor_golden_rank.md`](executor_golden_rank.md). Implementation lives in `sft_pipeline/common/executor_golden_rank.py`.

## Scope

Golden rank applies only when the Executor must choose among **multiple producers** for a **train** ability (e.g. several `BARRACKS` can all run `BARRACKSTRAIN_MARINE`). `build`, `research`, `addon`, and `morph` actions are out of scope.

Listed candidates are pre-filtered by the rule layer so that every unit in the prompt can execute the requested ability at decision time.

## Composite ranking key

After L0 eligibility is resolved, remaining candidates are ordered by a lexicographic key:

```text
rank_key = (eligible, ready_score, addon_tier, base_tier)
```

Higher is better. Layers L1–L3 populate the trailing fields; L4 defines how ties on the full key are treated.

The **primary decisive layer** for a prompt is the highest layer that separates the winning candidate(s) from all other eligible candidates. If no layer separates them, the outcome falls under L4.

---

## L0 — Producer reservation (eligibility filter)

### Rule

When pending actions require a **bare** producer or an **unupgraded** base to remain available, candidates that would block those pending actions are marked **ineligible** before speed or tie-break scoring.

Reservation is triggered by conflict actions in `[Possible conflicts in pending actions]`:

| Conflict pattern | Reserved host | Candidates excluded |
|------------------|---------------|---------------------|
| `BUILD_TECHLAB_*` / `BUILD_REACTOR_*` | Host type parsed from the action suffix (e.g. `BARRACKS`) | Same host type with **no add-on** |
| `UPGRADETO*` | `COMMANDCENTER` | Unupgraded `COMMANDCENTER` |

The filter is **not** applied when `[Ability to execute]` is itself an add-on or upgrade action (the executor is choosing the host for that add-on/upgrade, not training on it).

### Properties

- **Purpose:** Align executor choice with scheduler conflicts — keep a bare Barracks/Factory/Starport for a pending TechLab/Reactor, or an unupgraded CC for a pending orbital/planetary upgrade.
- **Effect on `rank_key`:** Sets `eligible = false` for filtered candidates; they cannot win unless the fallback path is taken.
- **Cross-cutting:** L0 may run without changing the final winner if the best producer by L1–L3 was already eligible.
- **Fallback:** If every candidate is filtered out, all candidates are restored as eligible, `fallback_no_eligible` is set, and ranking continues with L1–L4 on the full set.
- **Typical producers affected:** Multi-`BARRACKS` / multi-`STARPORT` scenes with pending add-on builds; mixed `COMMANDCENTER` + `ORBITALCOMMAND` with pending `UPGRADETO*` (when that conflict appears in the prompt).

---

## L1 — Execution efficiency (`ready_score`)

### Rule

Among eligible candidates, prefer the producer that can **start or finish the current train order soonest**. Encoded as `ready_score` (higher is better).

| Candidate state | Score |
|-----------------|-------|
| `idle` | `1000` (Reactor hosts add `+1` → `1001`) |
| `busy`, training progress `P%` | `P × 10` (e.g. 97% → `970`) |
| `busy` + Reactor, progress `< 50%` | `max(P × 10, 500)` — second queue may free soon |

### Properties

- **Purpose:** Maximize training throughput for the immediate ability — idle beats busy; among busy units, higher completion progress wins.
- **Decisive when:** The winner’s `ready_score` is strictly greater than every other eligible candidate’s score.
- **Reactor nuance:** Idle Reactor gets a small bonus over idle non-Reactor; busy Reactor below 50% gets a floor score reflecting dual-queue potential.
- **Applies to:** All train abilities and all producer types (Barracks, Factory, Starport, Command Center, Orbital Command, Planetary Fortress).

---

## L2 — Add-on tier tie-break (`addon_tier`)

### Rule

When eligible candidates share the same `ready_score`, prefer the stronger add-on configuration:

```text
Reactor (3) > TechLab (2) > bare / no add-on (0)
```

Parsed from candidate status text: `has Reactor`, `has TechLab`, `has add-on` (mapped to tier `1`), or `no add-on`.

### Properties

- **Purpose:** When speed is equal, favor producers with better training capacity (Reactor) or tech access (TechLab) over bare structures.
- **Decisive when:** `ready_score` ties and `addon_tier` strictly separates winner from the best loser.
- **Applies to:** Add-on host structures — `BARRACKS`, `FACTORY`, `STARPORT`. Non-host producers (bases) use `addon_tier = 0` for all candidates; L2 does not differentiate them.

---

## L3 — Base tier tie-break (`base_tier`)

### Rule

When eligible candidates still tie on `ready_score` and `addon_tier`, prefer the higher base tier:

```text
ORBITALCOMMAND / PLANETARYFORTRESS (2) > COMMANDCENTER (1)
```

Non-base producers use `base_tier = 0`.

### Properties

- **Purpose:** When training SCVs from multiple bases at equal speed and add-on parity, prefer upgraded command structures over unupgraded Command Centers.
- **Decisive when:** L1 and L2 tie and `base_tier` strictly separates winner from the best loser.
- **Applies to:** Primarily `COMMANDCENTERTRAIN_SCV` when both `COMMANDCENTER` and `ORBITALCOMMAND` (or `PLANETARYFORTRESS`) appear as candidates. Distinct from L0’s upgrade **reservation** filter: L3 is a tie-break among eligible bases, not a hard exclusion.

---

## L4 — Multi-optimal retention (full tie)

### Rule

Every eligible candidate whose `rank_key` equals the maximum `rank_key` is included in `golden_tags`. No further tie-breaking by tag ID for golden label purposes (tag is used only in `sort_key` for stable ordering).

### Properties

- **Purpose:** Acknowledge that multiple producers are equally optimal under the rule stack; all are valid golden answers.
- **Decisive when:** Two or more eligible candidates share the same `(eligible, ready_score, addon_tier, base_tier)`.
- **Typical tie shapes:**
  - Multiple idle bare structures of the same type.
  - Multiple idle Reactors (or TechLabs) with identical scores and tiers.
  - Multiple busy structures at the same training progress and same add-on/base tier.
- **Output:** `len(golden_tags) >= 2`; evaluation should accept any listed tag.

---

## Layer interaction summary

```text
Prompt
  │
  ▼
L0  Filter ineligible producers (reservation); optional fallback if none remain
  │
  ▼
L1  Compare ready_score among eligible candidates
  │     └─ unique max → winner(s) at L1
  ▼
L2  Compare addon_tier among those tied on L1
  │     └─ unique max → winner(s) at L2
  ▼
L3  Compare base_tier among those tied on L1–L2
  │     └─ unique max → winner(s) at L3
  ▼
L4  All candidates tied on full rank_key → all kept in golden_tags
```

| Layer | Field / flag | Question answered |
|-------|----------------|-------------------|
| L0 | `eligible`, `reservation_active`, `fallback_no_eligible` | Must we reserve a bare host or unupgraded CC for a pending action? |
| L1 | `ready_score` | Who can train this ability soonest? |
| L2 | `addon_tier` | At equal speed, who has the better add-on? |
| L3 | `base_tier` | At equal speed and add-on, who is the better base? |
| L4 | `golden_tags` (multiple) | Are several producers equally optimal? |

## Applicability by producer family

| Producer family | L0 | L1 | L2 | L3 | L4 |
|-----------------|----|----|----|----|-----|
| `BARRACKS` (train) | Add-on reservation | Yes | Yes | — | Yes |
| `FACTORY` (train) | Add-on reservation | Yes | Yes | — | Yes |
| `STARPORT` (train) | Add-on reservation | Yes | Yes | — | Yes |
| `COMMANDCENTER` / `ORBITALCOMMAND` / `PLANETARYFORTRESS` (SCV) | Upgrade reservation (when conflict present) | Yes | — | Yes | Yes |

A dash (—) means the layer is structurally inactive for that family under normal prompts (tiers are equal for all candidates).

## Related artifacts

| Artifact | Path |
|----------|------|
| Ranking implementation | `sft_pipeline/common/executor_golden_rank.py` |
| Batch annotator | `sft_pipeline/build_sft/build_executor_golden_rank.py` |
| Layer-balanced resampler | `sft_pipeline/build_sft/resample_executor_golden.py` |
| Unit tests | `sft_pipeline/tests/test_executor_golden_rank.py` |
| Golden rank overview (中文) | `sft_pipeline/build_sft/executor_golden_rank.md` |
