"""Ordering CoT annotation with Qwen32B, no teacher, per-balance-class multi-round sampling.

Pipeline for one input file (already class-tagged by prepare_ordering_cot_classes.py):

  for round in 1..max_rounds:
    for class C in priority order:
      if kept[C] >= target[C]: skip (stop spending API on satisfied classes)
      resample every still-pending sample of C concurrently (max_workers)
        - generate CoT + answer with the thinking model (no gold given)
        - run the EXISTING rule_check_ordering hard rules only (no teacher)
        - on pass: keep, write generated.answer as the final answer
        - on fail: stay pending, retried in a later round
      stop consuming results for C once its target is reached
    stop early when all classes reach target, or a whole round adds nothing

Only kept samples are persisted; rejects are counted but not marked done, so the
same sample can be re-sampled across rounds until it passes or rounds run out.
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.inject_cot_sft import (
    _build_kept_sample,
    _extract_generated,
    _messages_for_generation,
    _ordered_actions,
    _parse_answer,
    _safe_model_name,
    _sample_parts,
    rule_check_ordering,
)
from sft_pipeline.common.io import append_jsonl, read_json, read_jsonl, write_json

from API_Tools.llm_caller import call_openai_detailed

DEFAULT_CLASS_PRIORITY = ["C1_block", "C3_interleaved_early_common", "C2_interleaved_early_rare", "C4_interleaved_late"]


def _gold_ranks_for_gen(gen: list[str], gold: list[str]) -> list[int]:
    pools: dict[str, deque[int]] = defaultdict(deque)
    for i, action in enumerate(gold):
        pools[action].append(i)
    return [pools[action].popleft() for action in gen]


def kendall_tau(gen: list[str], gold: list[str]) -> float:
    """Kendall τ between gen and gold (same multiset assumed)."""
    ranks = _gold_ranks_for_gen(gen, gold)
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
    return (concordant - discordant) / pairs if pairs else 1.0


def _generate_one(
    index: int,
    sample: dict[str, Any],
    *,
    gen_model_key: str,
    config_path: str | None,
    gen_temperature: float | None,
    max_generation_attempts: int,
    min_kendall_tau: float | None,
) -> dict[str, Any]:
    """Generate + hard-rule-check a single sample. Returns a result dict."""
    try:
        system, user, gold_text = _sample_parts(sample)
        gold_answer = _parse_answer("ordering", gold_text)
    except Exception as exc:  # noqa: BLE001
        return {"index": index, "kept": False, "stage": "gold_parse", "error": str(exc)}

    last_error = ""
    last_reasons: list[str] = []
    attempts = max(1, max_generation_attempts)
    for _ in range(attempts):
        call = call_openai_detailed(
            _messages_for_generation(system, user),
            model_key=gen_model_key,
            config_path=config_path,
            temperature=gen_temperature,
        )
        if call.get("error"):
            last_error = str(call.get("error"))
            continue
        try:
            candidate = _extract_generated("ordering", call)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue
        # Prereq/tech-chain hints live in the SYSTEM prompt, while [Actions to order]
        # lives in USER. Pass both so the prereq-order rule ("X requires Y first" =>
        # Y must be ordered before X) is actually enforced.
        rule = rule_check_ordering(f"{system}\n{user}", gold_answer, candidate.answer, candidate.cot)
        if not rule.passed:
            last_reasons = rule.reasons
            last_error = "; ".join(rule.reasons)
            continue
        gen_actions = _ordered_actions(candidate.answer)
        gold_actions = _ordered_actions(gold_answer)
        tau = kendall_tau(gen_actions, gold_actions)
        if min_kendall_tau is not None and tau <= min_kendall_tau:
            last_reasons = [f"kendall_tau {tau:.4f} <= {min_kendall_tau}"]
            last_error = last_reasons[0]
            continue
        return {
            "index": index,
            "kept": True,
            "stage": "rule_pass",
            "cot": candidate.cot,
            "answer": candidate.answer,
            "rule_reasons": rule.reasons,
            "kendall_tau": tau,
        }

    return {
        "index": index,
        "kept": False,
        "stage": "rule_check" if last_reasons else "generation",
        "error": last_error,
        "rule_reasons": last_reasons,
    }


def _load_kept_from_audit(audit_file: Path) -> dict[int, dict[str, Any]]:
    kept: dict[int, dict[str, Any]] = {}
    if not audit_file.exists():
        return kept
    for row in read_jsonl(audit_file):
        idx = row.get("index")
        if idx is not None:
            kept[int(idx)] = row
    return kept


def run(
    *,
    input_path: Path,
    manifest_path: Path,
    output_dir: Path,
    gen_model_key: str,
    config_path: str | None,
    max_workers: int,
    max_generation_attempts: int,
    max_rounds: int,
    balance_class_target: int,
    annotate_all: bool,
    min_kendall_tau: float | None,
    class_priority: list[str],
    gen_temperature: float | None,
    limit: int | None,
    smoke_per_class: int | None,
    no_resume: bool,
) -> dict[str, Any]:
    samples = read_json(input_path)
    if not isinstance(samples, list):
        raise ValueError(f"{input_path} must contain a JSON list")

    manifest_rows = list(read_jsonl(manifest_path))
    class_of: dict[int, str] = {int(r["index"]): str(r["balance_class"]) for r in manifest_rows}
    meta_of: dict[int, dict[str, Any]] = {int(r["index"]): r for r in manifest_rows}

    if limit is not None:
        allowed = set(range(limit))
        class_of = {i: c for i, c in class_of.items() if i in allowed}

    members: dict[str, list[int]] = defaultdict(list)
    for idx, cls in class_of.items():
        members[cls].append(idx)
    for cls in members:
        members[cls].sort()

    # Smoke mode: keep only the first K indices of each balance class so a single
    # run exercises every class (C1..C4), multi-round resampling, and the prereq rule.
    if smoke_per_class is not None:
        allowed_idx: set[int] = set()
        for cls in members:
            members[cls] = members[cls][:smoke_per_class]
            allowed_idx.update(members[cls])
        class_of = {i: c for i, c in class_of.items() if i in allowed_idx}

    ordered_classes = [c for c in class_priority if c in members]
    ordered_classes += [c for c in sorted(members) if c not in ordered_classes]

    pool = {c: len(members[c]) for c in ordered_classes}
    # annotate_all: target = full pool (label every sample that passes rules), still
    # advancing all classes together via round-robin. Otherwise cap at balance target.
    target = {c: (pool[c] if annotate_all else min(pool[c], balance_class_target)) for c in ordered_classes}

    task_dir = output_dir / "ordering"
    task_dir.mkdir(parents=True, exist_ok=True)
    audit_file = task_dir / "cot_audit.jsonl"
    reject_file = task_dir / "cot_rejected_detail.jsonl"
    rounds_dir = output_dir / "round_reports"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    progress_file = output_dir / "cot_progress.json"
    gen_name = _safe_model_name(gen_model_key)
    output_file = task_dir / f"sc2_ordering_qwen3_thinking_cot_{gen_name}_sft.json"

    if no_resume:
        for path in (audit_file, reject_file):
            if path.exists():
                path.unlink()

    kept_rows = _load_kept_from_audit(audit_file)
    kept_indices = set(kept_rows)
    kept_count: dict[str, int] = defaultdict(int)
    for idx in kept_indices:
        cls = class_of.get(idx)
        if cls is not None:
            kept_count[cls] += 1

    write_lock = threading.Lock()

    def _persist(row: dict[str, Any]) -> None:
        with write_lock:
            if row["kept"]:
                append_jsonl(
                    audit_file,
                    {
                        "index": row["index"],
                        "balance_class": class_of.get(row["index"]),
                        "generated_cot": row["cot"],
                        "final_answer": row["answer"],
                        "rule_reasons": row.get("rule_reasons", []),
                        "kendall_tau": row.get("kendall_tau"),
                    },
                )
            else:
                append_jsonl(
                    reject_file,
                    {
                        "index": row["index"],
                        "balance_class": class_of.get(row["index"]),
                        "stage": row.get("stage"),
                        "error": row.get("error", ""),
                        "rule_reasons": row.get("rule_reasons", []),
                        "kendall_tau": row.get("kendall_tau"),
                    },
                )

    def _write_progress(round_no: int, status: str) -> None:
        state = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "round": round_no,
            "max_rounds": max_rounds,
            "gen_model_key": gen_model_key,
            "min_kendall_tau": min_kendall_tau,
            "balance_class_target": balance_class_target,
            "classes": {
                c: {"pool": pool[c], "target": target[c], "kept": kept_count.get(c, 0)}
                for c in ordered_classes
            },
            "total_kept": sum(kept_count.values()),
        }
        tmp = progress_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(progress_file)

    round_reports: list[dict[str, Any]] = []
    _write_progress(0, "start")

    def _interleave(pending_by_class: dict[str, list[int]]) -> list[int]:
        """Round-robin across classes (priority order) so all classes advance together."""
        queues = {c: list(pending_by_class.get(c, [])) for c in ordered_classes}
        stream: list[int] = []
        while any(queues[c] for c in ordered_classes):
            for c in ordered_classes:
                if queues[c]:
                    stream.append(queues[c].pop(0))
        return stream

    for round_no in range(1, max_rounds + 1):
        if all(kept_count.get(c, 0) >= target[c] for c in ordered_classes):
            break
        pending_by_class = {
            c: [i for i in members[c] if i not in kept_indices and kept_count.get(c, 0) < target[c]]
            for c in ordered_classes
        }
        stream = iter(_interleave(pending_by_class))
        round_new_total = 0
        round_stats: dict[str, dict[str, Any]] = {
            c: {"attempted": 0, "new_kept": 0, "rejects": Counter()} for c in ordered_classes
        }
        in_flight: dict[Any, tuple[int, str]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:

            def _submit_next() -> bool:
                for idx in stream:
                    cls = class_of[idx]
                    if kept_count.get(cls, 0) >= target[cls]:
                        continue  # class already satisfied; skip remaining of it
                    future = executor.submit(
                        _generate_one,
                        idx,
                        samples[idx],
                        gen_model_key=gen_model_key,
                        config_path=config_path,
                        gen_temperature=gen_temperature,
                        max_generation_attempts=max_generation_attempts,
                        min_kendall_tau=min_kendall_tau,
                    )
                    in_flight[future] = (idx, cls)
                    return True
                return False

            while len(in_flight) < max_workers and _submit_next():
                pass

            while in_flight:
                done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                for future in done:
                    idx, cls = in_flight.pop(future)
                    result = future.result()
                    _persist(result)
                    round_stats[cls]["attempted"] += 1
                    if result["kept"]:
                        with write_lock:
                            if result["index"] not in kept_indices:
                                kept_indices.add(result["index"])
                                kept_count[cls] += 1
                                round_new_total += 1
                                round_stats[cls]["new_kept"] += 1
                    else:
                        reason = (result.get("rule_reasons") or [result.get("stage", "unknown")])[0]
                        round_stats[cls]["rejects"][reason] += 1
                while len(in_flight) < max_workers and _submit_next():
                    pass
                _write_progress(round_no, "running")

        round_report = {
            "round": round_no,
            "classes": {
                c: {
                    "attempted": round_stats[c]["attempted"],
                    "new_kept": round_stats[c]["new_kept"],
                    "kept_total": kept_count.get(c, 0),
                    "target": target[c],
                    "pool": pool[c],
                    "top_reject_reasons": dict(round_stats[c]["rejects"].most_common(8)),
                }
                for c in ordered_classes
            },
            "new_kept_total": round_new_total,
            "total_kept": sum(kept_count.values()),
        }
        round_reports.append(round_report)
        write_json(rounds_dir / f"round_{round_no:02d}.json", round_report)

        if round_new_total == 0:
            break

    kept_final = sorted(_load_kept_from_audit(audit_file).values(), key=lambda r: int(r["index"]))
    kept_samples = [
        _build_kept_sample(samples[int(r["index"])], "ordering", str(r.get("generated_cot") or ""), r["final_answer"])
        for r in kept_final
        if int(r["index"]) < len(samples)
    ]
    write_json(output_file, kept_samples)

    by_tier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r, sample in zip(kept_final, kept_samples):
        idx = int(r["index"])
        by_tier[meta_of[idx]["tier"]].append(sample)
        by_class[str(r.get("balance_class"))].append(sample)
    for tier, rows in by_tier.items():
        write_json(task_dir / f"sc2_ordering_cot_tier_{tier}.json", rows)
    for cls, rows in by_class.items():
        write_json(task_dir / f"sc2_ordering_cot_class_{cls}.json", rows)

    report = {
        "input": str(input_path.resolve()),
        "manifest": str(manifest_path.resolve()),
        "gen_model_key": gen_model_key,
        "max_rounds": max_rounds,
        "rounds_run": len(round_reports),
        "max_generation_attempts": max_generation_attempts,
        "balance_class_target": balance_class_target,
        "min_kendall_tau": min_kendall_tau,
        "annotate_all": annotate_all,
        "class_priority": ordered_classes,
        "pool": pool,
        "target": target,
        "kept": {c: kept_count.get(c, 0) for c in ordered_classes},
        "total_kept": sum(kept_count.values()),
        "output_file": str(output_file.resolve()),
    }
    write_json(output_dir / "cot_injection_report.json", report)
    _write_progress(len(round_reports), "done")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Ordering CoT annotation (no teacher, per-class multi-round).")
    parser.add_argument("--input", required=True, help="Ordering SFT json (class-tagged source).")
    parser.add_argument("--manifest", required=True, help="Class manifest JSONL from prepare_ordering_cot_classes.")
    parser.add_argument("--output", required=True, help="Output directory.")
    parser.add_argument("--gen-model-key", default="Qwen3-32b_think", help="Thinking generation model key.")
    parser.add_argument("--config-path", default=None)
    parser.add_argument("--max-workers", type=int, default=25)
    parser.add_argument("--max-generation-attempts", type=int, default=2, help="Generation+rule retries per call.")
    parser.add_argument("--max-rounds", type=int, default=5, help="Per-class resampling rounds.")
    parser.add_argument("--balance-class-target", type=int, default=150, help="Kept target per balance class.")
    parser.add_argument(
        "--min-kendall-tau",
        type=float,
        default=None,
        help="Require Kendall tau(gen, gold) > this threshold to keep (e.g. 0.75).",
    )
    parser.add_argument(
        "--annotate-all",
        action="store_true",
        help="Ignore the per-class cap and annotate every sample (target = pool size), still round-robin balanced.",
    )
    parser.add_argument(
        "--class-priority",
        default=",".join(DEFAULT_CLASS_PRIORITY),
        help="Comma-separated balance-class order (earlier = higher priority).",
    )
    parser.add_argument("--gen-temperature", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Only consider indices [0, limit) (smoke runs).")
    parser.add_argument(
        "--smoke-per-class",
        type=int,
        default=None,
        help="Smoke mode: keep only first K indices of each balance class (covers all classes).",
    )
    parser.add_argument("--no-resume", action="store_true", help="Ignore existing audit/reject and restart.")
    args = parser.parse_args()

    class_priority = [c.strip() for c in args.class_priority.split(",") if c.strip()]
    report = run(
        input_path=Path(args.input),
        manifest_path=Path(args.manifest),
        output_dir=Path(args.output),
        gen_model_key=args.gen_model_key,
        config_path=args.config_path,
        max_workers=args.max_workers,
        max_generation_attempts=args.max_generation_attempts,
        max_rounds=args.max_rounds,
        balance_class_target=args.balance_class_target,
        annotate_all=args.annotate_all,
        min_kendall_tau=args.min_kendall_tau,
        class_priority=class_priority,
        gen_temperature=args.gen_temperature,
        limit=args.limit,
        smoke_per_class=args.smoke_per_class,
        no_resume=args.no_resume,
    )
    print("=== Ordering CoT rounds done ===")
    print(f"total kept: {report['total_kept']}")
    for cls in report["class_priority"]:
        print(f"  {cls}: kept {report['kept'][cls]} / target {report['target'][cls]} (pool {report['pool'][cls]})")
    print(f"output -> {report['output_file']}")


if __name__ == "__main__":
    main()
