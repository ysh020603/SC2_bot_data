"""Run thinking-model CoT generation on executor golden resampled data."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.inject_cot_sft import (
    GeneratedResult,
    _candidate_tags,
    _extract_generated,
    _model_call_meta,
)
from sft_pipeline.build_sft.templates import assistant_value, sharegpt_sample
from sft_pipeline.common.executor_golden_rank import classify_executor_rule_layer_from_prompt
from sft_pipeline.common.io import append_jsonl, read_jsonl, reset_jsonl, write_json

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = REPO_ROOT / "SC2-Agent-260510"
DEFAULT_CONFIG = AGENT_ROOT / "API_config" / "config.json"
DEFAULT_INPUT = (
    REPO_ROOT
    / "sft_pipeline_outputs/executor_golden_rank/qwen17b_grpo_naming_27b_exec_10strat_macro_r5/resampled/executor_qa_golden_resampled.jsonl"
)
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT.parent / "cot_eval"

if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from API_Tools.llm_caller import call_openai_detailed  # noqa: E402

DEFAULT_MODEL_KEYS = ("Qwen3-1.7b_think", "Qwen3-4b_think")
_TAG_RE = re.compile(r"\btag\s*=\s*(?P<tag>\d+)\b", re.I)


def rule_check_generated_executor(user: str, gold_tags: list[int], generated: Any, cot: str) -> dict[str, Any]:
    reasons: list[str] = []
    if not isinstance(generated, list) or len(generated) != 1:
        reasons.append("generated answer must be a JSON list with exactly one tag")
    else:
        try:
            gen_tag = int(generated[0])
        except (TypeError, ValueError):
            reasons.append("generated tag must be an integer")
            gen_tag = None
        candidates = _candidate_tags(user)
        if not candidates:
            reasons.append("no candidate tags found in prompt")
        elif gen_tag is not None and gen_tag not in candidates:
            reasons.append(f"generated tag {gen_tag} is not in candidates")
        cot_tags = sorted({int(match.group("tag")) for match in _TAG_RE.finditer(cot)})
        if cot_tags and gen_tag is not None and gen_tag not in cot_tags:
            reasons.append(f"CoT mentions tag choices {cot_tags} but generated answer is {gen_tag}")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "metrics": {
            "golden_tags": gold_tags,
            "generated_tag": int(generated[0]) if isinstance(generated, list) and generated else None,
            "candidate_tags": sorted(_candidate_tags(user)),
        },
    }


@dataclass
class EvalResult:
    index: int
    ok: bool
    audit: dict[str, Any]
    sft_sample: dict[str, Any] | None = None


def _safe_model_slug(model_key: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in model_key).strip("_").lower()


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _golden_tags(record: dict[str, Any]) -> list[int]:
    tags = record.get("golden_tags") or record.get("golden_rank", {}).get("golden_tags") or []
    return [int(tag) for tag in tags]


def _ability(record: dict[str, Any]) -> str:
    ability = record.get("ability")
    if ability:
        return str(ability)
    system = str(record.get("system") or "")
    user = str(record.get("user") or "")
    parsed, _ = classify_executor_rule_layer_from_prompt(system, user)
    return parsed


def process_record(
    index: int,
    record: dict[str, Any],
    *,
    model_key: str,
    config_path: str,
    max_retries: int,
) -> EvalResult:
    system = str(record.get("system") or "")
    user = str(record.get("user") or "")
    gold_tags = _golden_tags(record)
    ability = _ability(record)
    _, rule_layer = classify_executor_rule_layer_from_prompt(system, user)

    base_audit: dict[str, Any] = {
        "index": index,
        "model_key": model_key,
        "ability": ability,
        "rule_layer": rule_layer,
        "golden_tags": gold_tags,
        "strategy": record.get("strategy"),
        "record_id": record.get("record_id"),
    }

    last_error = ""
    generated: GeneratedResult | None = None
    call_meta: dict[str, Any] | None = None
    rule_metrics: dict[str, Any] = {}

    for _ in range(max(1, max_retries)):
        call = call_openai_detailed(
            _messages(system, user),
            model_key=model_key,
            config_path=config_path,
        )
        call_meta = _model_call_meta(call)
        if call.get("error"):
            last_error = str(call.get("error"))
            continue
        try:
            candidate = _extract_generated("executor", call)
        except Exception as exc:
            last_error = str(exc)
            continue
        rule_metrics = rule_check_generated_executor(user, gold_tags, candidate.answer, candidate.cot)
        generated = candidate
        break

    if generated is None:
        audit = {
            **base_audit,
            "status": "failed",
            "error": last_error,
            "generation": call_meta,
            "rule": rule_metrics or None,
        }
        return EvalResult(index=index, ok=False, audit=audit)

    gen_tags = [int(tag) for tag in generated.answer] if isinstance(generated.answer, list) else []
    gen_tag = gen_tags[0] if gen_tags else None
    golden_hit = gen_tag in set(gold_tags) if gen_tag is not None else False

    audit = {
        **base_audit,
        "status": "ok",
        "generated_cot": generated.cot,
        "generated_answer": generated.answer,
        "generated_answer_text": generated.answer_text,
        "generated_tag": gen_tag,
        "golden_hit": golden_hit,
        "rule": rule_metrics,
        "generation": call_meta,
        "raw_content": generated.call.get("raw_content"),
    }

    sft_sample = sharegpt_sample(
        "executor",
        "thinking",
        user,
        generated.answer,
        reasoning=generated.cot,
        system=system,
    )
    return EvalResult(index=index, ok=True, audit=audit, sft_sample=sft_sample)


def _load_completed_indices(audit_path: Path) -> set[int]:
    if not audit_path.exists():
        return set()
    done: set[int] = set()
    for row in read_jsonl(audit_path):
        if row.get("status") == "ok":
            done.add(int(row["index"]))
    return done


def run_model_eval(
    records: list[dict[str, Any]],
    *,
    model_key: str,
    output_dir: Path,
    config_path: str,
    max_workers: int,
    max_retries: int,
    resume: bool,
) -> dict[str, Any]:
    model_dir = output_dir / _safe_model_slug(model_key)
    model_dir.mkdir(parents=True, exist_ok=True)
    audit_path = model_dir / "cot_eval_audit.jsonl"
    failed_path = model_dir / "cot_eval_failed.jsonl"
    sft_path = model_dir / "sc2_executor_qwen3_thinking_cot_generated_sft.json"

    if not resume:
        reset_jsonl(audit_path)
        reset_jsonl(failed_path)

    completed = _load_completed_indices(audit_path) if resume else set()
    pending = [(idx, record) for idx, record in enumerate(records) if idx not in completed]

    kept_samples: list[dict[str, Any]] = []
    if resume and audit_path.exists():
        for row in read_jsonl(audit_path):
            if row.get("status") != "ok":
                continue
            idx = int(row["index"])
            record = records[idx]
            kept_samples.append(
                sharegpt_sample(
                    "executor",
                    "thinking",
                    str(record.get("user") or ""),
                    row.get("generated_answer"),
                    reasoning=str(row.get("generated_cot") or ""),
                    system=str(record.get("system") or ""),
                )
            )

    lock = threading.Lock()
    stats = Counter()
    layer_stats = Counter()
    ability_stats = Counter()

    def _handle(result: EvalResult) -> None:
        nonlocal kept_samples
        with lock:
            if result.ok:
                append_jsonl(audit_path, result.audit)
                if result.sft_sample is not None:
                    kept_samples.append(result.sft_sample)
                stats["ok"] += 1
                if result.audit.get("golden_hit"):
                    stats["golden_hit"] += 1
                if result.audit.get("rule", {}).get("passed"):
                    stats["rule_pass"] += 1
                layer_stats[result.audit.get("rule_layer", "")] += 1
                ability_stats[result.audit.get("ability", "")] += 1
            else:
                append_jsonl(failed_path, result.audit)
                stats["failed"] += 1

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(
                process_record,
                idx,
                record,
                model_key=model_key,
                config_path=config_path,
                max_retries=max_retries,
            ): idx
            for idx, record in pending
        }
        for future in as_completed(futures):
            _handle(future.result())

    kept_samples.sort(key=lambda sample: sample.get("conversations", [{}])[0].get("value", ""))
    write_json(sft_path, kept_samples)

    total = len(records)
    ok = stats["ok"] + len(completed)
    report = {
        "model_key": model_key,
        "output_dir": str(model_dir),
        "total": total,
        "ok": ok,
        "failed": stats["failed"],
        "golden_hit": stats["golden_hit"],
        "golden_hit_rate": round(stats["golden_hit"] / stats["ok"], 4) if stats["ok"] else 0.0,
        "rule_pass": stats["rule_pass"],
        "rule_pass_rate": round(stats["rule_pass"] / stats["ok"], 4) if stats["ok"] else 0.0,
        "by_rule_layer": dict(sorted(layer_stats.items())),
        "by_ability": dict(sorted(ability_stats.items())),
        "outputs": {
            "audit_jsonl": str(audit_path),
            "failed_jsonl": str(failed_path),
            "sft_json": str(sft_path),
        },
    }
    write_json(model_dir / "cot_eval_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Executor golden CoT eval with Qwen3 thinking models.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--config-path",
        type=Path,
        default=DEFAULT_CONFIG,
        help="SC2-Agent-260510 API_config/config.json",
    )
    parser.add_argument(
        "--model-keys",
        nargs="+",
        default=list(DEFAULT_MODEL_KEYS),
        help="Thinking model keys from llm_agents_pool",
    )
    parser.add_argument("--max-workers", type=int, default=25, help="Concurrent requests per model")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="Optional cap for smoke tests")
    parser.add_argument("--resume", action="store_true", help="Skip indices already present in cot_eval_audit.jsonl")
    parser.add_argument("--no-resume", action="store_true", help="Overwrite previous outputs")
    parser.add_argument(
        "--sequential-models",
        action="store_true",
        help="Run model keys one after another instead of in parallel",
    )
    args = parser.parse_args()

    config_path = str(args.config_path.resolve())
    if not Path(config_path).exists():
        raise SystemExit(f"config not found: {config_path}")

    records = list(read_jsonl(args.input.resolve()))
    if args.limit > 0:
        records = records[: args.limit]

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    resume = args.resume and not args.no_resume

    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input.resolve()),
        "output_dir": str(output_dir),
        "config_path": config_path,
        "record_count": len(records),
        "model_keys": args.model_keys,
        "models": {},
    }

    def _run_one(model_key: str) -> tuple[str, dict[str, Any]]:
        report = run_model_eval(
            records,
            model_key=model_key,
            output_dir=output_dir,
            config_path=config_path,
            max_workers=args.max_workers,
            max_retries=args.max_retries,
            resume=resume,
        )
        return model_key, report

    if args.sequential_models or len(args.model_keys) <= 1:
        for model_key in args.model_keys:
            key, report = _run_one(model_key)
            summary["models"][key] = report
            print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        with ThreadPoolExecutor(max_workers=len(args.model_keys)) as model_pool:
            futures = [model_pool.submit(_run_one, model_key) for model_key in args.model_keys]
            for future in as_completed(futures):
                key, report = future.result()
                summary["models"][key] = report
                print(json.dumps(report, ensure_ascii=False, indent=2))

    write_json(output_dir / "cot_eval_summary.json", summary)
    print(f"summary -> {output_dir / 'cot_eval_summary.json'}")


if __name__ == "__main__":
    main()
