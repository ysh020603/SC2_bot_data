# Tulu-3 SFT Mixture: General Capability Retention Data

This document describes the `allenai/tulu-3-sft-mixture` dataset, why we keep a local copy under this repository, and how it should be used in our training pipeline.

## Purpose in this project

`allenai/tulu-3-sft-mixture` is the main supervised fine-tuning (SFT) mixture released by AI2 / AllenAI for the Tulu-3 post-training series. In our setup it is **not** the primary task dataset.

We use it as **general capability retention data**:

```text
domain / task-specific data  +  general capability retention data
```

After domain SFT (and later RL / GRPO), models often lose general instruction-following, reasoning, coding, and refusal behavior. Mixing a controlled subset of Tulu-3 SFT Mixture helps reduce that degradation while we continue training on StarCraft II / agent-aligned task data.

Recommended high-level mix (example):

```text
task-specific data : 70%
general mix data   : 30%
```

Do **not** dump the full ~939k-sample mixture into every training run for small models (e.g. 0.6B / 1.7B). Prefer stratified sampling by `source`, then merge into a smaller `general_mix.jsonl`.

## Dataset basics

| Item | Value |
| --- | --- |
| Hugging Face id | `allenai/tulu-3-sft-mixture` |
| Hub URL | https://huggingface.co/datasets/allenai/tulu-3-sft-mixture |
| Role in Tulu-3 | Main SFT mixture |
| Approx. size | ~939k samples (local snapshot: **939,343** rows) |
| On-disk size (this machine) | ~1.4G (`6` parquet shards under `data/`) |
| Primary split | `train` |
| Local path in this repo | `extra_data/base_data/tulu-3-sft-mixture/` |

Each sample mainly contains:

```text
id
messages
source
```

- `messages`: chat-style SFT turns (user prompt + assistant response)
- `source`: original sub-corpus tag (use this for category mapping / stratified sampling)
- The dataset is **not** pre-split into `math / code / chat / safety` folders; map categories from `source` if needed

This is **training data**, not an evaluation benchmark. Keep using MMLU, HellaSwag, ARC-Challenge, GSM8K, IFEval, etc. to measure general-capability drift. Do not mix evaluation benchmark items into the training set (data contamination).

## Local layout

Downloaded snapshot:

```text
extra_data/base_data/tulu-3-sft-mixture/
```

Optional Hugging Face cache (if you also use `datasets.load_dataset`):

```text
extra_data/hf_cache/
```

Both paths are git-ignored. The dataset is large; only documentation and ignore rules should be tracked in git.

## How this copy was downloaded

On machines with unstable Hugging Face access (common in mainland China), use HF-Mirror:

```bash
export HF_ENDPOINT=https://hf-mirror.com

huggingface-cli download allenai/tulu-3-sft-mixture \
  --repo-type dataset \
  --local-dir /data2/SC2_2606/sharpy-sc2/extra_data/base_data/tulu-3-sft-mixture \
  --resume-download
```

If Hugging Face is reachable directly, omit `HF_ENDPOINT` and use the same `huggingface-cli download` command.

Alternative: cache via `datasets`:

```bash
export HF_HOME=/data2/SC2_2606/sharpy-sc2/extra_data/hf_cache
export HF_HUB_CACHE=/data2/SC2_2606/sharpy-sc2/extra_data/hf_cache/hub
export HF_DATASETS_CACHE=/data2/SC2_2606/sharpy-sc2/extra_data/hf_cache/datasets
# optional: export HF_ENDPOINT=https://hf-mirror.com

python - <<'PY'
from datasets import load_dataset

ds = load_dataset("allenai/tulu-3-sft-mixture", split="train")
print(ds)
print(ds[0].keys())
print(ds[0]["source"])
PY
```

After the files exist locally, prefer offline mode during training / preprocessing:

```bash
export HF_HUB_OFFLINE=1
```

Or load parquet / json files directly from `extra_data/base_data/tulu-3-sft-mixture/` without contacting the Hub.

## Suggested sampling for general mix

1. Inspect `source` frequencies.
2. Map sources to coarse buckets (chat / math-reasoning / code / knowledge / format-safety).
3. Sample by target ratios, for example:

```text
general chat / instruction : 30%
math / reasoning           : 20%
code                       : 15%
knowledge QA               : 15%
format / safety refusal    : 20%
```

4. Write `general_mix.jsonl`.
5. Mix with task-specific SFT data at a controlled ratio.

If the target model is no-thinking instruct, down-weight very long explicit chain-of-thought samples from math / reasoning / code sources.

Quick source histogram (after Hub cache or local load is available):

```bash
python - <<'PY'
from datasets import load_dataset
from collections import Counter

ds = load_dataset("allenai/tulu-3-sft-mixture", split="train")
for source, count in Counter(ds["source"]).most_common(30):
    print(f"{source}\t{count}")
PY
```

## Notes

- Keep this dataset as a **support** corpus for capability retention, not as a replacement for SC2 / agent task data.
- Re-download with `--resume-download` if the snapshot is incomplete.
- Confirm disk space before downloading or unpacking archives.
- Do not commit the dataset files; they are excluded in `.gitignore`.
