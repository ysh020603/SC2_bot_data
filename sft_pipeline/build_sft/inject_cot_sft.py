from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from sft_pipeline.build_sft.templates import assistant_value
from sft_pipeline.common.agent_reference import canonical_terran_names
from sft_pipeline.common.io import read_json, write_json, write_jsonl

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from API_Tools.llm_caller import call_openai_detailed, load_agent_pool  # noqa: E402


TaskName = Literal["naming", "ordering", "executor"]
TeacherDecision = Literal["drop", "use_gold_answer", "use_generated_answer"]

TASK_FILENAMES: dict[TaskName, str] = {
    "naming": "naming/sc2_naming_qwen3_thinking_sft.json",
    "ordering": "ordering/sc2_ordering_qwen3_thinking_sft.json",
    "executor": "executor/sc2_executor_qwen3_thinking_sft.json",
}

GENERATION_INSTRUCTION = """You are solving the original StarCraft II SFT task.
Use the original system and user prompt. Do not rely on any hidden gold answer.
Think through the task, then provide the final answer in the exact format requested
by the task.

If your API supports hidden/separated reasoning, use it normally.
Otherwise return:
<think>
your reasoning
</think>

FINAL_ANSWER:
your final answer only"""

TEACHER_SYSTEM = """You are a strict StarCraft II SFT quality judge.
Decide whether the generated chain-of-thought should be kept, and which final
answer should be paired with it.

The generated CoT itself is a training target. If it contains any substantive
factual, logical, game-rule, prerequisite, ordering, or tag-selection error, you
must choose drop. Do not salvage a wrong CoT by pairing it with the gold answer
or generated answer.

You must choose exactly one decision:
- drop: the reasoning is wrong, conflicts with the prompt, omits a decisive
  constraint, or cannot safely train.
- use_gold_answer: the reasoning does not conflict with the gold answer, and the
  gold answer is the safer final target.
- use_generated_answer: the generated answer passes hard rules and is better
  supported by the prompt/reasoning than the gold answer.

Return JSON only with keys:
decision, score, reason

score must be between 0 and 1."""

_THINK_RE = re.compile(r"<think\b[^>]*>(?P<reasoning>.*?)</think>", re.I | re.S)
_FINAL_LABEL_RE = re.compile(r"(?:^|\n)\s*(?:FINAL_ANSWER|Final Answer|Answer)\s*:\s*", re.I)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.I | re.S)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.S)
_ACTION_COUNT_RE = re.compile(r"^\s*(?P<action>[A-Z0-9_]+)(?:\s*x\s*(?P<count>\d+))?\s*$", re.I)
_TAG_RE = re.compile(r"\btag\s*=\s*(?P<tag>\d+)\b", re.I)
_PREREQ_RE = re.compile(
    r"\b(?P<action>[A-Z0-9_]+)\s+requires\s+(?P<depends>[A-Z0-9_]+)\s+first\b",
    re.I,
)
_AFTER_RE = re.compile(
    r"\b(?P<action>[A-Z0-9_]+)\s+should\s+come\s+after\s+(?P<depends>[A-Z0-9_]+)\b",
    re.I,
)


@dataclass
class RuleResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedResult:
    cot: str
    answer_text: str
    answer: Any
    call: dict[str, Any]


@dataclass
class ProcessedSample:
    index: int
    kept: bool
    decision: str
    generated: bool = False
    rule_passed: bool = False
    sample: dict[str, Any] | None = None
    reject: dict[str, Any] | None = None
    audit: dict[str, Any] | None = None


def _safe_model_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text).strip("_") or "model"


def _configured_model_name(model_key: str, config_path: str | None) -> str:
    pool = (load_agent_pool(config_path=config_path).get("llm_agents_pool") or {}).get(model_key) or {}
    return str(pool.get("model_name") or pool.get("model") or "")


def _unwrap_code_fence(text: str) -> str:
    text = (text or "").strip()
    match = _FENCE_RE.fullmatch(text)
    if match:
        return match.group(1).strip()
    return text


def _json_candidate(text: str, prefer_array: bool = False) -> str:
    text = _unwrap_code_fence(text)
    label = _FINAL_LABEL_RE.search(text)
    if label:
        text = text[label.end() :].strip()
    text = _unwrap_code_fence(text)
    if prefer_array:
        match = _JSON_ARRAY_RE.search(text)
        if match:
            return match.group(0)
    match = _JSON_OBJECT_RE.search(text)
    if match:
        return match.group(0)
    match = _JSON_ARRAY_RE.search(text)
    if match:
        return match.group(0)
    return text


def _loads_jsonish(text: str, prefer_array: bool = False) -> Any:
    candidate = _json_candidate(text, prefer_array=prefer_array)
    return json.loads(candidate)


def _assistant_answer_text(value: str) -> str:
    match = _THINK_RE.search(value or "")
    if match:
        return (value[: match.start()] + value[match.end() :]).strip()
    return (value or "").strip()


def _sample_parts(sample: dict[str, Any]) -> tuple[str, str, str]:
    conversations = sample.get("conversations") or []
    if len(conversations) < 2:
        raise ValueError("sample missing conversations")
    system = str(sample.get("system") or "")
    user = str(conversations[0].get("value") or "")
    answer_text = _assistant_answer_text(str(conversations[1].get("value") or ""))
    return system, user, answer_text


def _parse_answer(task: TaskName, text: str) -> Any:
    if task == "executor":
        parsed = _loads_jsonish(text, prefer_array=True)
        if isinstance(parsed, int):
            return [parsed]
        return parsed
    return _loads_jsonish(text)


def _normal_answer_text(answer: Any) -> str:
    if isinstance(answer, str):
        return answer
    return json.dumps(answer, ensure_ascii=False)


def _messages_for_generation(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": f"{GENERATION_INSTRUCTION}\n\n{system}".strip()},
        {"role": "user", "content": user},
    ]


def _extract_generated(task: TaskName, call: dict[str, Any]) -> GeneratedResult:
    raw_content = call.get("content") or call.get("raw_content") or ""
    cot = (call.get("reasoning") or "").strip()
    answer_text = str(raw_content or "").strip()
    if not cot:
        match = _THINK_RE.search(str(call.get("raw_content") or raw_content))
        if match:
            cot = match.group("reasoning").strip()
    if not answer_text and call.get("raw_content"):
        raw = str(call.get("raw_content") or "")
        match = _THINK_RE.search(raw)
        if match:
            answer_text = (raw[: match.start()] + raw[match.end() :]).strip()
        else:
            answer_text = raw.strip()
    if not cot:
        raise ValueError("generation returned empty CoT")
    answer = _parse_answer(task, answer_text)
    return GeneratedResult(cot=cot, answer_text=answer_text, answer=answer, call=call)


def _canonical_names() -> set[str]:
    units, upgrades = canonical_terran_names()
    return set(units).union(upgrades)


def _items(answer: Any) -> list[dict[str, Any]]:
    if not isinstance(answer, dict) or not isinstance(answer.get("items"), list):
        raise ValueError("answer must be an object with items list")
    result: list[dict[str, Any]] = []
    for item in answer["items"]:
        if not isinstance(item, dict):
            raise ValueError("item must be an object")
        name = str(item.get("name") or "")
        count = int(item.get("count"))
        if not name or count <= 0:
            raise ValueError("item must have positive count and non-empty name")
        result.append({"name": name, "count": count})
    return result


def _ordered_actions(answer: Any) -> list[str]:
    if not isinstance(answer, dict) or not isinstance(answer.get("ordered_actions"), list):
        raise ValueError("answer must be an object with ordered_actions list")
    actions = [str(action) for action in answer["ordered_actions"] if str(action)]
    if len(actions) != len(answer["ordered_actions"]):
        raise ValueError("ordered_actions contains empty actions")
    return actions


def _executor_tags(answer: Any) -> list[int]:
    if not isinstance(answer, list):
        raise ValueError("executor answer must be a list")
    tags = [int(tag) for tag in answer]
    if len(tags) != 1:
        raise ValueError("executor answer must contain exactly one tag")
    return tags


def _section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"\[{re.escape(heading)}\]\s*(?P<body>.*?)(?=\n\[[^\]]+\]|\Z)",
        re.I | re.S,
    )
    match = pattern.search(text)
    return match.group("body").strip() if match else ""


def _parse_actions_to_order(user: str) -> list[str]:
    body = _section(user, "Actions to order")
    actions: list[str] = []
    for line in body.splitlines():
        match = _ACTION_COUNT_RE.match(line.strip())
        if not match:
            continue
        count = int(match.group("count") or 1)
        actions.extend([match.group("action").upper()] * count)
    return actions


def _candidate_tags(user: str) -> set[int]:
    return {int(match.group("tag")) for match in _TAG_RE.finditer(user)}


def _prereq_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for pattern in (_PREREQ_RE, _AFTER_RE):
        for match in pattern.finditer(text):
            pairs.append((match.group("action").upper(), match.group("depends").upper()))
    return list(dict.fromkeys(pairs))


def rule_check_naming(gold: Any, generated: Any, cot: str) -> RuleResult:
    reasons: list[str] = []
    names = _canonical_names()
    try:
        gold_items = _items(gold)
        gen_items = _items(generated)
    except Exception as exc:
        return RuleResult(False, [f"parse_failed: {exc}"])
    gold_names = {item["name"] for item in gold_items}
    gen_names = {item["name"] for item in gen_items}
    missing = sorted(gold_names - gen_names)
    if missing:
        reasons.append(f"generated answer misses gold item types: {missing}")
    unknown = sorted(name for name in gen_names if name not in names)
    if unknown:
        reasons.append(f"generated answer has non-canonical names: {unknown}")
    total = sum(int(item["count"]) for item in gen_items)
    if total < 10 or total > 15:
        reasons.append(f"generated total count must be 10-15, got {total}")
    mentioned_unknown = sorted(name for name in names if name in cot and name not in gold_names.union(gen_names))
    if len(mentioned_unknown) > 10:
        reasons.append("CoT mentions many canonical names outside gold/generated answers")
    return RuleResult(
        not reasons,
        reasons,
        {
            "gold_names": sorted(gold_names),
            "generated_names": sorted(gen_names),
            "generated_total_count": total,
        },
    )


def rule_check_ordering(user: str, gold: Any, generated: Any, cot: str) -> RuleResult:
    reasons: list[str] = []
    try:
        gold_actions = _ordered_actions(gold)
        gen_actions = _ordered_actions(generated)
    except Exception as exc:
        return RuleResult(False, [f"parse_failed: {exc}"])
    input_actions = _parse_actions_to_order(user)
    reference_actions = input_actions or gold_actions
    if len(gen_actions) != len(gold_actions):
        reasons.append(f"generated action count {len(gen_actions)} != gold count {len(gold_actions)}")
    if Counter(gen_actions) != Counter(gold_actions):
        reasons.append("generated action multiset differs from gold")
    if Counter(reference_actions) != Counter(gold_actions):
        reasons.append("input action multiset differs from gold; source sample may be invalid")
    positions = {action: [] for action in set(gen_actions)}
    for i, action in enumerate(gen_actions):
        positions.setdefault(action, []).append(i)
    for action, depends in _prereq_pairs(user):
        if action not in positions or depends not in positions:
            continue
        if min(positions[action]) < min(positions[depends]):
            reasons.append(f"prereq order violated: {depends} must come before {action}")
    extra_mentions = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", cot)) - set(reference_actions))
    if len(extra_mentions) > 20:
        reasons.append("CoT mentions many action-like tokens outside the task")
    return RuleResult(
        not reasons,
        reasons,
        {
            "gold_count": len(gold_actions),
            "generated_count": len(gen_actions),
            "prereq_pairs": _prereq_pairs(user),
        },
    )


def rule_check_executor(user: str, gold: Any, generated: Any, cot: str) -> RuleResult:
    reasons: list[str] = []
    try:
        gold_tags = _executor_tags(gold)
        gen_tags = _executor_tags(generated)
    except Exception as exc:
        return RuleResult(False, [f"parse_failed: {exc}"])
    candidates = _candidate_tags(user)
    if not candidates:
        reasons.append("no candidate tags found in prompt")
    if gen_tags[0] not in candidates:
        reasons.append(f"generated tag {gen_tags[0]} is not in candidates")
    cot_tags = sorted({int(match.group("tag")) for match in _TAG_RE.finditer(cot)})
    if cot_tags and gen_tags[0] not in cot_tags:
        reasons.append(f"CoT mentions tag choices {cot_tags} but generated answer is {gen_tags[0]}")
    return RuleResult(
        not reasons,
        reasons,
        {"gold_tag": gold_tags[0], "generated_tag": gen_tags[0], "candidate_tags": sorted(candidates)},
    )


def rule_check(task: TaskName, user: str, gold: Any, generated: Any, cot: str) -> RuleResult:
    if task == "naming":
        return rule_check_naming(gold, generated, cot)
    if task == "ordering":
        return rule_check_ordering(user, gold, generated, cot)
    return rule_check_executor(user, gold, generated, cot)


def _teacher_messages(
    task: TaskName,
    system: str,
    user: str,
    gold_answer: Any,
    generated_answer: Any,
    cot: str,
    rule: RuleResult,
) -> list[dict[str, str]]:
    payload = {
        "task": task,
        "original_system": system,
        "original_user_prompt": user,
        "gold_answer": gold_answer,
        "generated_cot": cot,
        "generated_answer": generated_answer,
        "rule_check": {"passed": rule.passed, "reasons": rule.reasons, "metrics": rule.metrics},
    }
    return [
        {"role": "system", "content": TEACHER_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _parse_teacher_decision(text: str) -> dict[str, Any]:
    data = _loads_jsonish(text)
    decision = data.get("decision")
    if decision not in {"drop", "use_gold_answer", "use_generated_answer"}:
        raise ValueError(f"invalid teacher decision: {decision!r}")
    return {
        "decision": decision,
        "score": float(data.get("score", 0.0) or 0.0),
        "reason": str(data.get("reason") or ""),
    }


def _build_kept_sample(original: dict[str, Any], task: TaskName, cot: str, final_answer: Any) -> dict[str, Any]:
    sample = json.loads(json.dumps(original, ensure_ascii=False))
    sample["conversations"][1]["value"] = assistant_value(final_answer, "thinking", cot)
    return sample


def _reject(index: int, task: TaskName, stage: str, reason: str, extra: dict[str, Any] | None = None) -> ProcessedSample:
    return ProcessedSample(
        index=index,
        kept=False,
        decision="drop",
        generated=bool((extra or {}).get("generated")),
        rule_passed=bool((extra or {}).get("rule_passed")),
        reject={"index": index, "task": task, "stage": stage, "reason": reason, **(extra or {})},
    )


def process_one(
    index: int,
    task: TaskName,
    sample: dict[str, Any],
    *,
    gen_model_key: str,
    teacher_model_key: str,
    config_path: str | None,
    max_retries: int,
    gen_temperature: float | None,
    teacher_temperature: float | None,
) -> ProcessedSample:
    try:
        system, user, gold_answer_text = _sample_parts(sample)
        gold_answer = _parse_answer(task, gold_answer_text)
    except Exception as exc:
        return _reject(index, task, "gold_parse", str(exc))

    last_error = ""
    generated: GeneratedResult | None = None
    rule = RuleResult(False)
    for attempt in range(max_retries + 1):
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
            generated = _extract_generated(task, call)
        except Exception as exc:
            last_error = str(exc)
            continue
        rule = rule_check(task, user, gold_answer, generated.answer, generated.cot)
        if rule.passed:
            break
        last_error = "; ".join(rule.reasons)
        generated = None

    if generated is None:
        return _reject(index, task, "rule_or_generation", last_error, {"generated": bool(rule.metrics), "rule": rule.__dict__})

    teacher_call = call_openai_detailed(
        _teacher_messages(task, system, user, gold_answer, generated.answer, generated.cot, rule),
        model_key=teacher_model_key,
        config_path=config_path,
        temperature=teacher_temperature,
    )
    if teacher_call.get("error"):
        return _reject(
            index,
            task,
            "teacher_call",
            str(teacher_call.get("error")),
            {"generated": True, "rule_passed": True},
        )
    try:
        teacher = _parse_teacher_decision(teacher_call.get("content") or teacher_call.get("raw_content") or "")
    except Exception as exc:
        return _reject(
            index,
            task,
            "teacher_parse",
            str(exc),
            {
                "generated": True,
                "rule_passed": True,
                "teacher_content": teacher_call.get("content") or teacher_call.get("raw_content") or "",
            },
        )

    decision = teacher["decision"]
    if decision == "drop":
        return _reject(
            index,
            task,
            "teacher_drop",
            teacher.get("reason", ""),
            {"generated": True, "rule_passed": True, "teacher": teacher},
        )
    final_answer = gold_answer if decision == "use_gold_answer" else generated.answer
    kept = _build_kept_sample(sample, task, generated.cot, final_answer)
    audit = {
        "index": index,
        "task": task,
        "decision": decision,
        "rule": {"passed": rule.passed, "reasons": rule.reasons, "metrics": rule.metrics},
        "teacher": teacher,
        "generation": {
            "model_key": generated.call.get("model_key"),
            "model": generated.call.get("model"),
            "reasoning_source": generated.call.get("reasoning_source"),
            "reasoning_extract_mode": generated.call.get("reasoning_extract_mode"),
        },
        "teacher_model": {
            "model_key": teacher_call.get("model_key"),
            "model": teacher_call.get("model"),
            "reasoning_source": teacher_call.get("reasoning_source"),
            "reasoning_extract_mode": teacher_call.get("reasoning_extract_mode"),
        },
    }
    return ProcessedSample(
        index=index,
        kept=True,
        decision=decision,
        generated=True,
        rule_passed=True,
        sample=kept,
        audit=audit,
    )


def _input_file_for(input_path: Path, task: TaskName) -> Path:
    if input_path.is_file():
        return input_path
    return input_path / TASK_FILENAMES[task]


def _output_file_for(output_dir: Path, task: TaskName, gen_model: str, teacher_model: str) -> Path:
    return (
        output_dir
        / task
        / f"sc2_{task}_qwen3_thinking_cot_{_safe_model_name(gen_model)}_checked_by_{_safe_model_name(teacher_model)}_sft.json"
    )


def process_task(
    task: TaskName,
    *,
    input_path: Path,
    output_dir: Path,
    gen_model_key: str,
    teacher_model_key: str,
    config_path: str | None,
    max_workers: int,
    max_retries: int,
    limit: int | None,
    gen_temperature: float | None,
    teacher_temperature: float | None,
    dry_run: bool,
) -> dict[str, Any]:
    source_file = _input_file_for(input_path, task)
    samples = read_json(source_file)
    if not isinstance(samples, list):
        raise ValueError(f"{source_file} must contain a JSON list")
    if limit is not None:
        samples = samples[:limit]

    report: dict[str, Any] = {
        "task": task,
        "source_file": str(source_file.resolve()),
        "total": len(samples),
        "generated": 0,
        "rule_pass": 0,
        "rule_drop": 0,
        "teacher_use_gold": 0,
        "teacher_use_generated": 0,
        "teacher_drop": 0,
        "kept": 0,
        "dry_run": dry_run,
    }
    if dry_run:
        for i, sample in enumerate(samples):
            system, user, gold_answer_text = _sample_parts(sample)
            _ = system, user
            _parse_answer(task, gold_answer_text)
            report["kept"] += 1
        return report

    results: list[ProcessedSample] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_one,
                i,
                task,
                sample,
                gen_model_key=gen_model_key,
                teacher_model_key=teacher_model_key,
                config_path=config_path,
                max_retries=max_retries,
                gen_temperature=gen_temperature,
                teacher_temperature=teacher_temperature,
            ): i
            for i, sample in enumerate(samples)
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: item.index)
    kept_samples = [item.sample for item in results if item.kept and item.sample is not None]
    rejects = [item.reject for item in results if item.reject is not None]
    audits = [item.audit for item in results if item.audit is not None]
    for item in results:
        if item.generated:
            report["generated"] += 1
        if item.rule_passed:
            report["rule_pass"] += 1
        if item.kept:
            report["kept"] += 1
            if item.decision == "use_gold_answer":
                report["teacher_use_gold"] += 1
            elif item.decision == "use_generated_answer":
                report["teacher_use_generated"] += 1
        elif item.reject:
            stage = item.reject.get("stage")
            if stage in {"rule_or_generation", "gold_parse"}:
                report["rule_drop"] += 1
            elif stage == "teacher_drop":
                report["teacher_drop"] += 1

    output_file = _output_file_for(output_dir, task, gen_model_key, teacher_model_key)
    write_json(output_file, kept_samples)
    write_jsonl(output_dir / task / "cot_rejected_samples.jsonl", [row for row in rejects if row])
    write_jsonl(output_dir / task / "cot_audit.jsonl", [row for row in audits if row])
    report["output_file"] = str(output_file.resolve())
    report["rejected_file"] = str((output_dir / task / "cot_rejected_samples.jsonl").resolve())
    report["audit_file"] = str((output_dir / task / "cot_audit.jsonl").resolve())
    return report


def _parse_tasks(values: list[str]) -> list[TaskName]:
    if not values or "all" in values:
        return ["naming", "ordering", "executor"]
    tasks: list[TaskName] = []
    for value in values:
        if value not in TASK_FILENAMES:
            raise ValueError(f"unknown task: {value}")
        tasks.append(value)  # type: ignore[arg-type]
    return list(dict.fromkeys(tasks))


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject generated CoT into existing SC2 thinking SFT files.")
    parser.add_argument("--input", required=True, help="Existing sft_agent_aligned directory or one thinking JSON file.")
    parser.add_argument("--output", required=True, help="Output directory for CoT-enhanced SFT files.")
    parser.add_argument("--tasks", nargs="+", default=["all"], help="Tasks: all, naming, ordering, executor.")
    parser.add_argument("--gen-model-key", required=True, help="Generation model key from API_config/config.json.")
    parser.add_argument("--teacher-model-key", required=True, help="Teacher/judge model key from API_config/config.json.")
    parser.add_argument("--config-path", default=None, help="Optional API config path.")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None, help="Optional per-task sample limit for smoke runs.")
    parser.add_argument("--gen-temperature", type=float, default=None)
    parser.add_argument("--teacher-temperature", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true", help="Only parse source files and gold answers; do not call LLMs.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    tasks = _parse_tasks(args.tasks)
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "generation_model_key": args.gen_model_key,
        "generation_model": _configured_model_name(args.gen_model_key, args.config_path),
        "teacher_model_key": args.teacher_model_key,
        "teacher_model": _configured_model_name(args.teacher_model_key, args.config_path),
        "config_path": args.config_path,
        "tasks": {},
    }
    for task in tasks:
        report["tasks"][task] = process_task(
            task,
            input_path=input_path,
            output_dir=output_dir,
            gen_model_key=args.gen_model_key,
            teacher_model_key=args.teacher_model_key,
            config_path=args.config_path,
            max_workers=max(1, args.max_workers),
            max_retries=max(0, args.max_retries),
            limit=args.limit,
            gen_temperature=args.gen_temperature,
            teacher_temperature=args.teacher_temperature,
            dry_run=args.dry_run,
        )
    write_json(output_dir / "cot_injection_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
