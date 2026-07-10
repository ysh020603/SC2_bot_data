# Ordering Answer Similarity: Kendall τ

This document explains how we measure **similarity between a generated `ordered_actions` sequence and the gold (reference) sequence** in the SC2 Ordering SFT pipeline.

We use **Kendall's τ (tau)** as the primary order-similarity metric. It is implemented in `sft_pipeline/build_sft/inject_ordering_cot_rounds.py` and is used as a **retention gate** during CoT annotation (`--min-kendall-tau`, e.g. `0.75`).

---

## 1. Problem setup

Each Ordering sample has:

- **Gold answer**: `gold = [a₁, a₂, …, aₙ]` — the reference order from labeled game data.
- **Generated answer**: `gen = [b₁, b₂, …, bₙ]` — produced by the thinking model.

Hard rules already require:

```text
Counter(gen) == Counter(gold)
```

So both sequences contain **exactly the same multiset of actions** (same action types and counts). What remains to measure is **how similar the relative ordering is**, not whether the bag of actions matches.

---

## 2. Why Kendall τ (not edit distance)?

| Metric | What it measures | Fit for Ordering |
|--------|------------------|------------------|
| **Levenshtein edit distance** | Token-level insert/delete/substitute cost | Penalizes any reorder heavily; treats legal permutations as many "errors" |
| **Exact match** | `gen == gold` | Too strict; many valid orderings differ from one gold path |
| **Kendall τ** | Concordance of **relative pairs** | Designed for rankings/permutations; natural for "how alike are these two orders?" |

Example intuition: if gold is `[SCV, SCV, MARINE, DEPOT]` and gen swaps two SCVs, edit distance may still count substitutions, while Kendall τ stays **1.0** (relative order among distinct positions unchanged for tied types handled via instance matching — see §3).

---

## 3. Step 1 — Align action instances (same multiset)

Actions can repeat (e.g. six `COMMANDCENTERTRAIN_SCV`). We must pair each generated instance with a **specific gold occurrence** of the same action type.

**Greedy matching rule** (in-order, first-available):

> For each action in `gen` left-to-right, assign it the **next unused** index of that action type in `gold`.

### Example A — Simple alignment

```text
gold = [DEPOT, SCV, SCV, BARRACKS]
gen  = [SCV, SCV, DEPOT, BARRACKS]
```

| gen position | action | matched gold index |
|--------------|--------|--------------------|
| 0 | SCV | 1 |
| 1 | SCV | 2 |
| 2 | DEPOT | 0 |
| 3 | BARRACKS | 3 |

Permutation of gold indices induced by `gen`: **`π = [1, 2, 0, 3]`**

---

## 4. Step 2 — Count concordant and discordant pairs

Consider every unordered pair of positions `(i, j)` with `i < j` in `gen` (length `n`).

- **Concordant**: `π[i] < π[j]` — the two items keep the same relative order as in gold.
- **Discordant**: `π[i] > π[j]` — their relative order is reversed vs gold.

```text
C = number of concordant pairs
D = number of discordant pairs
```

Total pairs: `P = n(n−1)/2`

**Kendall τ**:

```text
τ = (C − D) / P
```

Range:

| τ | Meaning |
|---|---------|
| **+1** | Identical order to gold (after instance matching) |
| **0** | About as many inversions as concordances (no net agreement) |
| **−1** | Completely reversed relative order |

Edge case: `n < 2` → define `τ = 1.0` (no pairs to compare).

### Example A (continued) — Compute τ

`π = [1, 2, 0, 3]`, `n = 4`, `P = 6`

| pair (i,j) | π[i], π[j] | concordant? |
|------------|------------|-------------|
| (0,1) | 1, 2 | yes |
| (0,2) | 1, 0 | no |
| (0,3) | 1, 3 | yes |
| (1,2) | 2, 0 | no |
| (1,3) | 2, 3 | yes |
| (2,3) | 0, 3 | yes |

`C = 4`, `D = 2` → **τ = (4−2)/6 = 0.333**

The multiset is correct, but the order is only weakly aligned with gold.

---

## 5. More worked examples

### Example B — Exact match (τ = 1)

```text
gold = [DEPOT, SCV, SCV, BARRACKS]
gen  = [DEPOT, SCV, SCV, BARRACKS]
π    = [0, 1, 2, 3]
```

Every pair is concordant → **τ = 1.0** → passes `--min-kendall-tau 0.75`.

---

### Example C — Block vs interleaved (τ differs)

**Gold (block-ordered)**:

```text
gold = [SCV, SCV, SCV, SCV, MARINE, MARINE, DEPOT]
```

**Gen A — same blocks (τ = 1)**:

```text
gen = [SCV, SCV, SCV, SCV, MARINE, MARINE, DEPOT]
τ = 1.0
```

**Gen B — interleaved (τ lower)**:

```text
gen = [SCV, SCV, MARINE, DEPOT, SCV, MARINE, SCV]
```

After instance matching, many pairs that were in order in gold become inverted → **τ ≪ 1** (often below 0.75).

This matches the empirical pattern: **Tier-1 block** samples tend to have higher τ than **Tier-2 interleaved** samples.

---

### Example D — Only swapping within same action type

```text
gold = [SCV, SCV, MARINE, DEPOT]
gen  = [SCV, SCV, MARINE, DEPOT]   # same as gold
τ = 1.0
```

```text
gold = [SCV, SCV, MARINE, DEPOT]
gen  = [SCV, SCV, MARINE, DEPOT]   # swap two SCVs only — same multiset
# If gen = [SCV, SCV, ...] with SCVs at positions 0,1 vs gold 0,1 — identical
```

When two instances of the **same** action type swap but map to the **same gold index slots** in the greedy pairing, τ behavior depends on which instance is matched to which gold slot. Our **left-to-right greedy** rule matches gen's first `SCV` to gold's first `SCV`, etc., so swapping two identical-type tokens that occupy the same "slot group" can still yield τ = 1. Swapping across groups that cross other action types reduces τ.

**Practical case — crossing types**:

```text
gold = [SCV, MARINE, SCV]
gen  = [MARINE, SCV, SCV]
π    = [1, 0, 2]   # after matching
```

Pairs: (0,1): 1>0 discordant; (0,2): 1<2 concordant; (1,2): 0<2 concordant  
`C=2, D=1, P=3` → **τ = 1/3 ≈ 0.33** → fails τ > 0.75.

---

## 6. Use in CoT annotation pipeline

Retention flow per sample:

```text
1. Model generates CoT + ordered_actions (no gold shown)
2. Hard rules (rule_check_ordering):
   - parse OK
   - len(gen) == len(gold)
   - Counter(gen) == Counter(gold)
   - prereq hints satisfied (ACTION requires DEP first, from system+user)
3. Kendall τ(gen, gold) computed
4. Keep iff τ > min_kendall_tau (default e.g. 0.75)
5. On failure: retry same sample (up to max_generation_attempts per call,
   then again in later rounds until kept or max_rounds)
```

CLI:

```bash
python3 -m sft_pipeline.build_sft.inject_ordering_cot_rounds \
  --min-kendall-tau 0.75 \
  ...
```

Audit records store `kendall_tau` per kept row in `cot_audit.jsonl`.

---

## 7. Reference implementation (Python)

```python
from collections import defaultdict, deque

def gold_ranks_for_gen(gen: list[str], gold: list[str]) -> list[int]:
  pools: dict[str, deque[int]] = defaultdict(deque)
  for i, action in enumerate(gold):
    pools[action].append(i)
  return [pools[action].popleft() for action in gen]

def kendall_tau(gen: list[str], gold: list[str]) -> float:
  ranks = gold_ranks_for_gen(gen, gold)
  n = len(ranks)
  if n < 2:
    return 1.0
  concordant = discordant = 0
  for i in range(n):
    for j in range(i + 1, n):
      if ranks[i] < ranks[j]:
        concordant += 1
      elif ranks[i] > ranks[j]:
        discordant += 1
  pairs = n * (n - 1) / 2
  return (concordant - discordant) / pairs
```

---

## 8. Interpreting τ on our dataset (empirical)

On the first Qwen3-32B run (no τ gate, 2108 kept, multiset-only rules):

| Class | Mean τ | τ = 1 share |
|-------|--------|-------------|
| C1_block | ~0.51 | ~17% |
| C3_early_common | ~0.46 | ~1% |
| C2_early_rare | ~0.32 | ~0.1% |
| C4_late | ~0.40 | ~0.1% |
| **All** | **~0.38** | **~1%** |

Requiring **τ > 0.75** keeps roughly the **top ~10–15%** of order quality (by this metric), which is why annotation with that gate is much slower but produces answers closer to gold ordering.

---

## 9. Related metrics (not used as gate)

| Metric | Formula / idea | Notes |
|--------|----------------|-------|
| **Inversion rate** | `D / (C+D)` | Linearly related to τ; τ = 1 − 2×inversion_rate when P>0 |
| **Normalized edit distance** | `edit_dist / n` | Used in early analysis; harsher on permutations |
| **Exact match rate** | `gen == gold` | Strict; ~1% on multiset-passing samples |

---

## 10. Summary

1. **Multiset match** is enforced by hard rules (action counts must match prompt/gold).
2. **Kendall τ** measures **relative order agreement** after pairing repeated actions to gold instances.
3. **τ = 1** means identical order; **τ > 0.75** is a practical quality bar for keeping CoT training data.
4. Prefer τ over raw edit distance when evaluating or filtering **ordering** tasks where the action bag is fixed but permutations differ.
