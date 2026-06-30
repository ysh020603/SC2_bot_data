# Naming Stage: Data Construction and Training Notes

This document records practical lessons for the **Naming** LLM position only:

```text
strategy step + current observation -> canonical entity/count JSON
```

It complements:

- [`sft_pipeline/README.md`](../README.md) — pipeline overview and base SFT rules
- [`docs/cot_generation_validation_notes.md`](../../docs/cot_generation_validation_notes.md) — CoT injector design and smoke-test results
- [`sft_pipeline_outputs/<run_id>/naming_cot_curation_rules.md`](../../sft_pipeline_outputs/2026-06-24_terran_6bots_3maps_macro/naming_cot_curation_rules.md) — run-specific CoT curation snapshot (example run)

Empirical numbers below come mainly from run `2026-06-24_terran_6bots_3maps_macro` (4474 base naming samples). Run `2026-06-27_terran_4bots_3maps_macro` follows the same workflow; at the time of writing it had completed v8 labeling (177 victory sequences, 2762 steps).

---

## 1. What Naming Learns

### Online prompt (agent-aligned)

```text
system:
  Strategy Summary
  Canonical Terran Units / Upgrades
  name hints / jargon / upgrade categories

user:
  [Current Observation]
  [Strategy Step]
```

Prompts must be built with `SC2_Agent.naming_agent.build_naming_messages()` via `sft_pipeline/common/agent_reference.py`, not hand-written approximations.

### Gold target

Derived from `labeled_steps.jsonl.ordered_actions`:

```text
BARRACKSTRAIN_MARINE x4 -> {"name":"Marine","count":4}
BUILD_TECHLAB_BARRACKS  -> {"name":"BarracksTechLab","count":1}
RESEARCH_COMBATSHIELD   -> {"name":"ShieldWall","count":1}
```

Naming does **not** learn action order. Item order in the JSON is a stable sort, not execution order.

### Upstream constraints

- Only **Victory** games (`meta.result == "Victory"`).
- v8 Markdown **final summary step** has no action range and is **excluded** from offline SFT.
- Base thinking SFT ships with an **empty** `<think>` placeholder until CoT post-processing.

---

## 2. Train / Validation Split and Action Coverage

### Strategy-level split (recommended)

Split by **bot / strategy**, not by random steps, to avoid trajectory leakage.

A split used in macro sweep experiments:

| Train strategies | Validation strategies |
|------------------|----------------------|
| bio, safe_tvt_raven, three_rax_stim, two_base_tanks, tank_thor_mech, battle_cruisers | marine_rush, rusty, banshees, raven_liberator_tank |

After GRPO on this split, validation strategies did not show worse win rates than training strategies (no obvious strategy-level overfitting).

### What “Action coverage” means for Naming

In Naming, “action” should be read as **answer entity patterns**, not raw SC2 ability strings:

| Concept | Definition | Used for |
|---------|------------|----------|
| **Multiset** | `(canonical_name -> count)` tuple, order ignored | Nothink resampling |
| **Class** | `frozenset(items[].name)` — types only, counts ignored | CoT coverage stats, per-class caps |

**Core rule:** the training set should cover every **class** (or multiset) that appears in validation — ideally all classes seen in the full corpus. If validation contains `{Battlecruiser, FusionCore, ...}` combinations never seen in training, the model tends to hallucinate entities or drop ongoing production (especially `SCV`).

### How to enforce coverage

1. Build base SFT: `python3 -m sft_pipeline.build_sft.build_naming_sft`
2. After choosing a val strategy set, compute class/multiset histograms on train vs val.
3. For missing or sparse classes, use:
   - `extract_priority_naming_classes.py` — find gap prompts with zero or sparse CoT
   - Priority CoT inject with `--class-target-min 2` and `--skip-teacher`
4. Nothink resampling keeps **full multiset coverage** while down-weighting frequent patterns (see Section 4).

---

## 3. CoT Annotation: Large Models + Hard Rules

### Why CoT matters for small models (e.g. Qwen3-1.7B)

1.7B models lack SC2 domain knowledge. Typical failures:

- Extract only flashy combat units from the NL step
- Miss ongoing worker production (`SCV`)
- Under-count total planned increments

CoT SFT teaches **how to read** the strategy step and observation, not just memorize JSON shapes. Nothink SFT alone handles format; CoT compensates for scene knowledge gaps before GRPO or online play.

### Do not use 1.7B as the CoT generation backbone

Single-trajectory smoke test (`docs/cot_generation_validation_notes.md`):

```text
naming kept: 1 / 9 = 11.11%
```

Use same-family thinking models instead:

| Gen model | Role |
|-----------|------|
| Qwen3-32B | Primary generator; highest quality |
| Qwen3-14B | Supplement |
| Qwen3-4B | Low-cost supplement |

Teacher (when enabled): Qwen3-32B or Qwen3.5-27B. Priority / gap passes may use `--skip-teacher` (rule pass only).

### CoT pipeline

```text
sc2_naming_qwen3_thinking_sft.json   # empty thinking placeholder
  -> inject_cot_sft.py                 # model re-answers from (system, human) only
  -> rule_check_naming                 # hard drop
  -> [optional] teacher judge
  -> recover_naming_cot_from_rejects.py
  -> build_naming_cot_curated_sft.py   # merge 4B/14B/32B + last-step
```

Entry point:

```bash
python3 -m sft_pipeline.build_sft.inject_cot_sft \
  --input  sft_pipeline_outputs/<run_id>/sft_agent_aligned \
  --output sft_pipeline_outputs/<run_id>/sft_agent_aligned_cot_qwen3-32b \
  --tasks naming \
  --gen-model-key Qwen3-32b_think \
  --teacher-model-key Qwen3-32b
```

### Quality filter (`rule_check_naming`)

Implemented in `inject_cot_sft.py`. Failures are `rule_drop` (no teacher call).

| Rule | Requirement |
|------|-------------|
| Parse | Valid `{"items":[{"name","count"},...]}` |
| Gold types | Every **name type** in gold must appear in the generated answer (`gold_names ⊆ gen_names`) |
| Canonical | All generated names ∈ `canonical_terran_names()` |
| Total count | `sum(generated.count)` ∈ **[5, 15]** (relaxed from original 10–15 for early-game steps) |
| CoT spam | CoT must not mention >10 canonical names outside gold ∪ generated |

**Not enforced:** exact per-name count match with gold; item order.

The practical quality bar for keeping a sample: the model names **all required entity types** from the gold label, uses only canonical names, and stays within a reasonable total increment band. Counts per type may differ from gold.

Common `rule_drop` reasons:

```text
generated answer misses gold item types: ['SCV']
generated total count must be 5-15, got 4
```

### Multi-model merge (example run `2026-06-24`)

After full inject + reject recovery:

| Source | Kept samples (approx.) |
|--------|------------------------|
| 32B full | 1648 |
| 14B full | 698 |
| 4B full | 578 |
| 32B gap / priority | + additional |

`build_naming_cot_curated_sft.py` merge rules:

- Dedupe key: **human prompt** (observation + strategy step)
- On duplicate prompt: keep **smallest** model (4B < 14B < 32B)
- Per **class**: cap at **3** samples; under-target classes keep all available
- **Last-step** rows sort first within a class

Final curated CoT (example): **1149** samples across **636** classes (from 4366 loaded CoT rows after dedupe/selection).

---

## 4. Imbalanced Distributions: Resample by Answer Result

Raw BO pipeline data is heavily skewed toward early-game multisets (`SCV + Barracks + SupplyDepot`) and under-represents late-game combinations.

### Nothink track: `resample_naming_sft.py`

Resample by **answer multiset frequency** with **step balancing**.

| Tier | Original freq | Keep policy |
|------|---------------|-------------|
| T0 | ≥ 50 | cap = **25** |
| T1 | ≥ 30 | cap = 15 |
| T2 | ≥ 10 | keep 50% |
| T3 | ≥ 5 | keep 75% |
| Rare / singleton | < 5 | **keep all** |

Step balance: `--step-balance-alpha 0.65` blends toward uniform `[Step N]` counts so the model does not only see opening steps.

Example outcome (`2026-06-24`):

```text
4474 original -> 2541 resampled
1867 unique multisets: full coverage retained
```

Script:

```bash
python3 -m sft_pipeline.build_sft.resample_naming_sft \
  --input  sft_agent_aligned/naming/sc2_naming_qwen3_nothink_sft.json \
  --output curated_nothink/resampled/sc2_naming_qwen3_nothink_resampled.json \
  --report curated_nothink/resampled/naming_resample_report.json \
  --target-size 3000 --t0-cap 25 --step-balance-alpha 0.65
```

A copy with run-specific docs also lives under `sft_pipeline_outputs/<run_id>/sft_agent_aligned/naming/curated_nothink/scripts/`.

### CoT track: class-level balancing

- Coverage stat: unique prompts with CoT per `frozenset(items[].name)`
- `extract_priority_naming_classes.py` targets classes with `cot_prompts == 0` or `<= sparse_max` (default 2)
- Curated merge: `per_class_target = 3`
- Priority inject: `--class-target-min 2`, stop when `existing + kept_this_run >= target`

---

## 5. Last-Step Data: Online-Only Supplement

### Why it is special

The v8 Markdown **final step** is a strategic style summary without an action range. Offline `labeled_steps.jsonl` cannot produce gold naming targets for “last macro cycle” behavior.

Online Naming at the **last strategy step** sees open-ended NL (large task counts, diverse entity mixes). This distribution differs sharply from mid-game `[Step N]` rows.

### Source

```text
SC2-Agent-260510/game_records/qwen_think_hybrid_v7_terran_sweep_last_step_victory_qa.jsonl
```

Filters:

- `agent == "naming"`
- Game **Victory**, last strategy step (pre-filtered by the QA extractor)
- Gold: `pipeline_named_items` when present, else parsed model `answer`

These prompts come from a **separate online sweep** — **zero overlap** with pipeline `user` messages on the `2026-06-24` run.

### How to include in training

| Track | Filter | Example count |
|-------|--------|---------------|
| Nothink merged | `sum(items[].count) ∈ [8, 20]` | 126 |
| Nothink full prompt/answer dataset | all naming QA rows | 829 |
| CoT curated | 8–20 tasks + non-empty cot | 90 |

Builders:

- `build_naming_prompt_answer_dataset.py` — plain `{prompt, answer}` merge
- `build_naming_cot_curated_sft.py` — `load_laststep_cot_source()` with default `min_tasks=8`, `max_tasks=20`

Last-step samples teach **reasonable extrapolation** from strong NL steps (e.g. “transition to BC deathball” → Refinery, SCV, Banshee, Battlecruiser, upgrades). Merge them **on top of** resampled BO data, not as a replacement.

---

## 6. Recommended End-to-End Workflow

```text
1. Collect victory trajectories
   bo_collection_runs/<run_id>/

2. v8 step labeling
   python3 -m sft_pipeline.label_steps.build_v8_steps
   -> sft_pipeline_outputs/<run_id>/v8_steps/json/labeled_steps.jsonl

3. Base agent-aligned SFT
   python3 -m sft_pipeline.build_sft.build_naming_sft
   -> sft_agent_aligned/naming/sc2_naming_qwen3_{thinking,nothink}_sft.json

4. Choose train/val strategy split; audit class/multiset coverage on val

5. Nothink curated train set
   resample_naming_sft.py
   + build_laststep_naming_sft.py (8–20 tasks)
   + merge_naming_sharegpt.py
   -> curated_nothink/merged/sc2_naming_qwen3_nothink_merged_sft.json

6. CoT curated train set (optional, for thinking / GRPO prep)
   inject_cot_sft (32B / 14B / 4B)
   -> recover_naming_cot_from_rejects
   -> extract_priority_naming_classes + priority inject
   -> build_naming_cot_curated_sft
   -> curated_cot/merged/sc2_naming_qwen3_thinking_cot_curated_sft.json

7. Train Qwen3-1.7B (or target size)
   - Nothink merged: format + entity mapping
   - CoT curated: SC2 reasoning prior to GRPO
```

---

## 7. Quick Reference

| Topic | Practice |
|-------|----------|
| Train / val | Split by **strategy bot**; ensure train **classes** cover val |
| CoT generator | Qwen3 **32B / 14B / 4B**, not 1.7B |
| CoT quality gate | Gold **entity types** must all appear in generated answer; canonical names; total count 5–15 |
| Distribution | Resample by answer **multiset** freq + step balance (α≈0.65); CoT cap **3 / class** |
| Last step | Online victory QA only; prefer task count **8–20** for curated tracks |
| Prompt alignment | Always `build_naming_messages()` from `SC2-Agent-260510` |

---

## 8. Related Code

| Module | Role |
|--------|------|
| `build_sft/build_all.py` | Base naming SFT from `labeled_steps.jsonl` |
| `build_sft/inject_cot_sft.py` | CoT generation, `rule_check_naming`, teacher, class targets |
| `build_sft/recover_naming_cot_from_rejects.py` | Re-rule-check stored rejects without new API calls |
| `build_sft/extract_priority_naming_classes.py` | Sparse / missing class subset for priority inject |
| `build_sft/build_naming_cot_curated_sft.py` | Merge multi-model CoT + last-step |
| `build_sft/build_naming_prompt_answer_dataset.py` | Plain prompt/answer dataset (no ShareGPT) |
| `build_sft/resample_naming_sft.py` | Multiset + step resampling for nothink |
| `common/agent_reference.py` | Agent-aligned naming prompts and canonical names |
