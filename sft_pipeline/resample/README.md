# SFT Data Resampling

Category-organized resampling scripts for the three SC2 agent SFT stages.
Raw pipeline data is heavily skewed (early-game openings dominate), so each stage
down-weights frequent patterns while preserving coverage.

```
resample/
├── multiset_balancing.py                     # shared helpers for Ordering
├── naming/
│   ├── resample_naming_sft.py                # (a) normal-step: name-set class balance
│   ├── build_naming_prompt_answer_dataset.py # (b) last-step: plain merge
│   └── build_naming_cot_curated_sft.py       # (b) last-step + per-class CoT cap
├── ordering/
│   └── resample_ordering_sft.py              # multiset + step balance + action floor
└── executor/
    └── resample_executor_golden.py           # L0–L4 rule-layer + per-action balance
```

Pipeline order: **Naming → Ordering → Executor**.
Naming has two sampling points: *normal step* (`[Step N]` rows) and *last step*
(online victory QA for the final macro cycle).

---

## Stage 1a — Naming, normal step (`naming/resample_naming_sft.py`)

Resample by **name-set class** — only which canonical names appear in the answer.
**Count and order are ignored.**

- **Class** = `frozenset(items[].name)`
  e.g. `{Marine, Barracks}` covers both `Marine×4 + Barracks×1` and `Marine×2 + Barracks×1`.
- **Rare classes** (original freq ≤ `--rare-max`): **kept in full** (coverage bucket).
- **Common classes** (freq > `--rare-max`): each capped at `--per-class-cap` (down-sample only).
- Within a class, selection spreads across distinct `[Step N]` for diversity.
- Handles thinking (`<think>…</think>`) and nothink formats.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `--rare-max` | 2 | freq ≤ R → rare, keep all samples |
| `--per-class-cap` | 8 | max kept per common class |
| `--seed` | 42 | reproducible tie-break |

Example on `2026-07-05_terran_new7bots_3maps_macro` (R=2, K=8):

```text
5040 original -> 3327 resampled
1361 unique classes: full coverage retained
  rare:   983 classes / 1192 samples (kept in full)
  common: 378 classes / 2135 samples (132 classes capped)
head class {Barracks, Refinery, SCV, SupplyDepot}: 268 -> 8
```

```bash
python3 -m sft_pipeline.resample.naming.resample_naming_sft \
  --input  <run>/sft_agent_aligned/naming/sc2_naming_qwen3_thinking_sft.json \
  --output <run>/.../curated_by_class/sc2_naming_resampled.json \
  --report <run>/.../curated_by_class/naming_resample_report.json \
  --rare-max 2 --per-class-cap 8
```

Equivalent entry point: `python3 -m sft_pipeline.build_sft.resample_naming_sft` (thin wrapper).

---

## Stage 1b — Naming, last step (`naming/build_*.py`)

The v8 Markdown **final step** has no offline gold action range. Last-step samples
come from a **separate online victory QA sweep** and are **merged on top of**
resampled BO data, not a replacement.

Source (default):
`SC2-Agent-260510/game_records/qwen_think_hybrid_v7_terran_sweep_last_step_victory_qa.jsonl`

- **`build_naming_prompt_answer_dataset.py`** — plain `{prompt, answer}` merge.
- **`build_naming_cot_curated_sft.py`** — CoT merge; per-class cap (default 3);
  dedupe by prompt (smallest model wins: 4b < 14b < 32b).

---

## Stage 2 — Ordering (`ordering/resample_ordering_sft.py`)

Buckets by **`ordered_actions` multiset** with step balancing and an action-coverage floor.
Shared cap/step logic lives in `multiset_balancing.py`.

```bash
python3 -m sft_pipeline.resample.ordering.resample_ordering_sft \
  --input <ordering_nothink_sft.json> \
  --output <ordering_resampled.json> \
  --report <ordering_resample_report.json> \
  --target-size 3000 --step-balance-alpha 0.65
```

---

## Stage 3 — Executor (`executor/resample_executor_golden.py`)

Balances golden-rank QA across **rule layers L0–L4** and **per-layer abilities**.

```bash
python3 -m sft_pipeline.resample.executor.resample_executor_golden \
  --input  <run>/executor_qa_golden.jsonl \
  --output-dir <run>/resampled \
  --layer-target 200
```

---

## Summary

| Stage | Bucket key | Balancing | Rare policy |
|-------|-----------|-----------|-------------|
| Naming (step) | `frozenset(name)` | rare full + common cap | freq≤R kept; common capped at K |
| Naming (last step) | `frozenset(name)` | per-class cap (CoT) | under-target class kept |
| Ordering | `ordered_actions` multiset | cap + step + action floor | singleton kept; action floor boost |
| Executor | `(rule_layer, ability)` | equal per layer + action | oversample with cap |

See also `sft_pipeline/build_sft/naming_data_and_training_notes.md`.
