from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from sft_pipeline.build_sft.templates import assistant_value
from sft_pipeline.common.agent_reference import canonical_terran_names
from sft_pipeline.common.io import append_jsonl, read_json, read_jsonl, reset_jsonl, write_json

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
    reject_detail: dict[str, Any] | None = None
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
    if total < 5 or total > 15:
        reasons.append(f"generated total count must be 5-15, got {total}")
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


def _answer_type_set(task: TaskName, answer: Any) -> frozenset[str]:
    if task == "naming":
        return frozenset(item["name"] for item in _items(answer))
    raise ValueError(f"class targeting is only supported for naming, got {task}")


@dataclass
class ClassTargetGate:
    target_min: int
    existing_counts: dict[frozenset[str], int]
    pending_counts: dict[frozenset[str], int]
    kept_counts: dict[frozenset[str], int] = field(default_factory=lambda: defaultdict(int))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def target_for(self, type_set: frozenset[str]) -> int:
        pending = self.pending_counts.get(type_set, 0)
        if pending < self.target_min:
            return pending
        return self.target_min

    def total_kept(self, type_set: frozenset[str]) -> int:
        return self.existing_counts.get(type_set, 0) + self.kept_counts.get(type_set, 0)

    def should_process(self, type_set: frozenset[str]) -> bool:
        with self._lock:
            return self.total_kept(type_set) < self.target_for(type_set)

    def record_kept(self, type_set: frozenset[str]) -> None:
        with self._lock:
            self.kept_counts[type_set] += 1

    def priority(self, type_set: frozenset[str]) -> tuple[int, int]:
        need = self.target_for(type_set) - self.total_kept(type_set)
        return (-need, len(type_set))


def _load_class_existing_counts(path: Path | None) -> dict[frozenset[str], int]:
    if path is None or not path.exists():
        return {}
    data = read_json(path)
    rows = data.get("classes") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a classes list")
    counts: dict[frozenset[str], int] = {}
    for row in rows:
        counts[frozenset(str(name) for name in row["types"])] = int(row.get("cot_prompts", 0))
    return counts


def _build_class_target_gate(
    task: TaskName,
    samples: list[dict[str, Any]],
    *,
    target_min: int,
    existing_counts: dict[frozenset[str], int],
) -> ClassTargetGate:
    pending_counts: dict[frozenset[str], int] = defaultdict(int)
    for sample in samples:
        _, _, gold_answer_text = _sample_parts(sample)
        gold_answer = _parse_answer(task, gold_answer_text)
        pending_counts[_answer_type_set(task, gold_answer)] += 1
    return ClassTargetGate(
        target_min=target_min,
        existing_counts=dict(existing_counts),
        pending_counts=dict(pending_counts),
    )


def _restore_class_gate_from_audit(
    audit_file: Path,
    task: TaskName,
    gate: ClassTargetGate,
) -> None:
    if not audit_file.exists():
        return
    for row in read_jsonl(audit_file):
        gold_answer = row.get("gold_answer")
        if gold_answer is None:
            continue
        gate.record_kept(_answer_type_set(task, gold_answer))


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


def _model_call_meta(call: dict[str, Any] | None) -> dict[str, Any] | None:
    if not call:
        return None
    return {
        "model_key": call.get("model_key"),
        "model": call.get("model"),
        "reasoning_source": call.get("reasoning_source"),
        "reasoning_extract_mode": call.get("reasoning_extract_mode"),
    }


def _build_reject_detail(
    index: int,
    task: TaskName,
    stage: str,
    reason: str,
    *,
    gold_answer: Any = None,
    generated: GeneratedResult | None = None,
    rule: RuleResult | None = None,
    teacher: dict[str, Any] | None = None,
    teacher_call: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "index": index,
        "task": task,
        "stage": stage,
        "reason": reason,
        "gold_answer": gold_answer,
        "generated_cot": generated.cot if generated else None,
        "generated_answer": generated.answer if generated else None,
        "generated_answer_text": generated.answer_text if generated else None,
        "rule": {"passed": rule.passed, "reasons": rule.reasons, "metrics": rule.metrics} if rule else None,
        "teacher": teacher,
        "generation": _model_call_meta(generated.call if generated else None),
        "teacher_model": _model_call_meta(teacher_call),
    }
    if extra:
        detail.update(extra)
    return detail


def _reject_summary(detail: dict[str, Any], *, generated: bool = False, rule_passed: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "index": detail["index"],
        "task": detail["task"],
        "stage": detail["stage"],
        "reason": detail["reason"],
        "generated": generated,
        "rule_passed": rule_passed,
    }
    if detail.get("rule") is not None:
        summary["rule"] = detail["rule"]
    if detail.get("teacher") is not None:
        summary["teacher"] = detail["teacher"]
    return summary


def _reject_from_detail(
    detail: dict[str, Any],
    *,
    generated: bool = False,
    rule_passed: bool = False,
) -> ProcessedSample:
    return ProcessedSample(
        index=int(detail["index"]),
        kept=False,
        decision="drop",
        generated=generated,
        rule_passed=rule_passed,
        reject=_reject_summary(detail, generated=generated, rule_passed=rule_passed),
        reject_detail=detail,
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
    no_teacher_drop: bool = False,
    skip_teacher: bool = False,
    class_gate: ClassTargetGate | None = None,
    max_generation_attempts: int = 1,
) -> ProcessedSample:
    try:
        system, user, gold_answer_text = _sample_parts(sample)
        gold_answer = _parse_answer(task, gold_answer_text)
    except Exception as exc:
        detail = _build_reject_detail(index, task, "gold_parse", str(exc))
        return _reject_from_detail(detail)

    type_set: frozenset[str] | None = None
    if class_gate is not None:
        type_set = _answer_type_set(task, gold_answer)
        if not class_gate.should_process(type_set):
            detail = _build_reject_detail(
                index,
                task,
                "class_target_met",
                "class already reached CoT target",
                gold_answer=gold_answer,
                extra={
                    "type_set": sorted(type_set),
                    "target": class_gate.target_for(type_set),
                    "total_kept": class_gate.total_kept(type_set),
                },
            )
            return _reject_from_detail(detail)

    last_error = ""
    generated: GeneratedResult | None = None
    rule = RuleResult(False)
    last_rule_candidate: GeneratedResult | None = None
    attempts = max(1, max_generation_attempts)
    for attempt in range(attempts):
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
            candidate = _extract_generated(task, call)
        except Exception as exc:
            last_error = str(exc)
            continue
        rule = rule_check(task, user, gold_answer, candidate.answer, candidate.cot)
        if not rule.passed:
            last_rule_candidate = candidate
            last_error = "; ".join(rule.reasons)
            continue
        generated = candidate
        break

    if generated is None and last_rule_candidate is not None:
        detail = _build_reject_detail(
            index,
            task,
            "rule_check",
            last_error,
            gold_answer=gold_answer,
            generated=last_rule_candidate,
            rule=rule,
            extra={"generation_attempts": attempts},
        )
        return _reject_from_detail(detail, generated=True, rule_passed=False)

    if generated is None:
        detail = _build_reject_detail(
            index,
            task,
            "generation",
            last_error,
            gold_answer=gold_answer,
            rule=rule if rule.reasons else None,
        )
        return _reject_from_detail(detail, generated=False, rule_passed=False)

    if skip_teacher:
        final_answer = generated.answer
        kept = _build_kept_sample(sample, task, generated.cot, final_answer)
        if class_gate is not None and type_set is not None:
            class_gate.record_kept(type_set)
        audit = {
            "index": index,
            "task": task,
            "decision": "skip_teacher_use_generated",
            "gold_answer": gold_answer,
            "generated_cot": generated.cot,
            "generated_answer": generated.answer,
            "final_answer": final_answer,
            "rule": {"passed": rule.passed, "reasons": rule.reasons, "metrics": rule.metrics},
            "teacher": None,
            "generation": _model_call_meta(generated.call),
            "teacher_model": None,
        }
        if type_set is not None and class_gate is not None:
            audit["type_set"] = sorted(type_set)
            audit["class_total_kept"] = class_gate.total_kept(type_set)
        return ProcessedSample(
            index=index,
            kept=True,
            decision="skip_teacher_use_generated",
            generated=True,
            rule_passed=True,
            sample=kept,
            audit=audit,
        )

    teacher_call = call_openai_detailed(
        _teacher_messages(task, system, user, gold_answer, generated.answer, generated.cot, rule),
        model_key=teacher_model_key,
        config_path=config_path,
        temperature=teacher_temperature,
    )
    if teacher_call.get("error"):
        detail = _build_reject_detail(
            index,
            task,
            "teacher_call",
            str(teacher_call.get("error")),
            gold_answer=gold_answer,
            generated=generated,
            rule=rule,
            teacher_call=teacher_call,
        )
        return _reject_from_detail(detail, generated=True, rule_passed=True)
    try:
        teacher = _parse_teacher_decision(teacher_call.get("content") or teacher_call.get("raw_content") or "")
    except Exception as exc:
        detail = _build_reject_detail(
            index,
            task,
            "teacher_parse",
            str(exc),
            gold_answer=gold_answer,
            generated=generated,
            rule=rule,
            teacher_call=teacher_call,
            extra={
                "teacher_content": teacher_call.get("content") or teacher_call.get("raw_content") or "",
            },
        )
        return _reject_from_detail(detail, generated=True, rule_passed=True)

    decision = teacher["decision"]
    if decision == "drop":
        if no_teacher_drop:
            teacher = {
                **teacher,
                "original_decision": "drop",
                "overridden_to": "use_gold_answer",
            }
            decision = "use_gold_answer"
        else:
            detail = _build_reject_detail(
                index,
                task,
                "teacher_drop",
                teacher.get("reason", ""),
                gold_answer=gold_answer,
                generated=generated,
                rule=rule,
                teacher=teacher,
                teacher_call=teacher_call,
            )
            return _reject_from_detail(detail, generated=True, rule_passed=True)
    final_answer = gold_answer if decision == "use_gold_answer" else generated.answer
    kept = _build_kept_sample(sample, task, generated.cot, final_answer)
    if class_gate is not None and type_set is not None:
        class_gate.record_kept(type_set)
    audit = {
        "index": index,
        "task": task,
        "decision": decision,
        "gold_answer": gold_answer,
        "generated_cot": generated.cot,
        "generated_answer": generated.answer,
        "final_answer": final_answer,
        "rule": {"passed": rule.passed, "reasons": rule.reasons, "metrics": rule.metrics},
        "teacher": teacher,
        "generation": _model_call_meta(generated.call),
        "teacher_model": _model_call_meta(teacher_call),
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


@dataclass
class TaskOutputPaths:
    task_dir: Path
    output_file: Path
    rejected_file: Path
    rejected_detail_file: Path
    audit_file: Path


def _task_output_paths(
    output_dir: Path,
    task: TaskName,
    gen_model_key: str,
    teacher_model_key: str,
) -> TaskOutputPaths:
    task_dir = output_dir / task
    return TaskOutputPaths(
        task_dir=task_dir,
        output_file=_output_file_for(output_dir, task, gen_model_key, teacher_model_key),
        rejected_file=task_dir / "cot_rejected_samples.jsonl",
        rejected_detail_file=task_dir / "cot_rejected_detail.jsonl",
        audit_file=task_dir / "cot_audit.jsonl",
    )


def _prepare_task_output(paths: TaskOutputPaths) -> None:
    paths.task_dir.mkdir(parents=True, exist_ok=True)
    reset_jsonl(paths.rejected_file)
    reset_jsonl(paths.rejected_detail_file)
    reset_jsonl(paths.audit_file)


def _load_processed_indices(paths: TaskOutputPaths) -> set[int]:
    indices: set[int] = set()
    for path in (paths.audit_file, paths.rejected_file):
        if not path.exists():
            continue
        for row in read_jsonl(path):
            if row.get("index") is not None:
                indices.add(int(row["index"]))
    return indices


def _rebuild_kept_samples_from_audit(
    audit_file: Path,
    samples: list[dict[str, Any]],
    task: TaskName,
) -> list[dict[str, Any]]:
    if not audit_file.exists():
        return []
    audits = sorted(read_jsonl(audit_file), key=lambda row: int(row["index"]))
    kept: list[dict[str, Any]] = []
    for row in audits:
        idx = int(row["index"])
        cot = str(row.get("generated_cot") or "")
        final_answer = row.get("final_answer")
        if final_answer is None or idx < 0 or idx >= len(samples):
            continue
        kept.append(_build_kept_sample(samples[idx], task, cot, final_answer))
    return kept


_PROGRESS_COUNT_KEYS = (
    "processed",
    "generated",
    "rule_pass",
    "rule_drop",
    "teacher_use_gold",
    "teacher_use_generated",
    "teacher_drop",
    "kept",
)


class CotProgressTracker:
    def __init__(self, progress_file: Path) -> None:
        self.progress_file = progress_file
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}

    def init_run(self, report: dict[str, Any], tasks: list[TaskName]) -> None:
        with self._lock:
            self._state = {
                **report,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "tasks": {
                    task: {
                        "status": "pending",
                        "total": 0,
                        "processed": 0,
                        "generated": 0,
                        "rule_pass": 0,
                        "rule_drop": 0,
                        "teacher_use_gold": 0,
                        "teacher_use_generated": 0,
                        "teacher_drop": 0,
                        "kept": 0,
                    }
                    for task in tasks
                },
            }
            self._flush_locked()

    def load_existing(self, progress_file: Path, report: dict[str, Any], tasks: list[TaskName]) -> None:
        with self._lock:
            self._state = read_json(progress_file)
            self._state.update(
                {
                    "generation_model_key": report.get("generation_model_key"),
                    "generation_model": report.get("generation_model"),
                    "teacher_model_key": report.get("teacher_model_key"),
                    "teacher_model": report.get("teacher_model"),
                    "config_path": report.get("config_path"),
                }
            )
            task_state = self._state.setdefault("tasks", {})
            for task in tasks:
                task_state.setdefault(
                    task,
                    {
                        "status": "pending",
                        "total": 0,
                        "processed": 0,
                        "generated": 0,
                        "rule_pass": 0,
                        "rule_drop": 0,
                        "teacher_use_gold": 0,
                        "teacher_use_generated": 0,
                        "teacher_drop": 0,
                        "kept": 0,
                    },
                )
            self._flush_locked()

    def begin_task(
        self,
        task: TaskName,
        total: int,
        task_report: dict[str, Any],
        *,
        resume: bool = False,
    ) -> None:
        with self._lock:
            entry = self._state["tasks"][task]
            preserved = {key: entry[key] for key in _PROGRESS_COUNT_KEYS if key in entry} if resume else {}
            entry.update(task_report)
            entry["status"] = "running"
            entry["total"] = total
            if resume:
                entry.update(preserved)
            else:
                for key in _PROGRESS_COUNT_KEYS:
                    entry[key] = 0
            self._flush_locked()

    def task_counts(self, task: TaskName) -> dict[str, Any]:
        with self._lock:
            return dict(self._state.get("tasks", {}).get(task, {}))

    def record_result(self, task: TaskName, item: ProcessedSample) -> None:
        with self._lock:
            entry = self._state["tasks"][task]
            entry["processed"] += 1
            if item.generated:
                entry["generated"] += 1
            if item.rule_passed:
                entry["rule_pass"] += 1
            if item.kept:
                entry["kept"] += 1
                if item.decision in {"use_gold_answer", "skip_teacher_use_gold"}:
                    entry["teacher_use_gold"] += 1
                elif item.decision in {"use_generated_answer", "skip_teacher_use_generated"}:
                    entry["teacher_use_generated"] += 1
            elif item.reject:
                stage = item.reject.get("stage")
                if stage in {"rule_or_generation", "rule_check", "gold_parse"}:
                    entry["rule_drop"] += 1
                elif stage == "teacher_drop":
                    entry["teacher_drop"] += 1
                elif stage == "class_target_met":
                    entry.setdefault("skipped_class_target", 0)
                    entry["skipped_class_target"] += 1
            self._flush_locked()

    def finish_task(self, task: TaskName, task_report: dict[str, Any]) -> None:
        with self._lock:
            entry = self._state["tasks"][task]
            entry.update(task_report)
            entry["status"] = "done"
            self._flush_locked()

    def _flush_locked(self) -> None:
        self._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.progress_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.progress_file)


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
    progress: CotProgressTracker | None = None,
    no_resume: bool = False,
    no_teacher_drop: bool = False,
    skip_teacher: bool = False,
    class_target_min: int | None = None,
    class_existing_json: Path | None = None,
    max_generation_attempts: int = 1,
) -> dict[str, Any]:
    source_file = _input_file_for(input_path, task)
    samples = read_json(source_file)
    if not isinstance(samples, list):
        raise ValueError(f"{source_file} must contain a JSON list")
    if limit is not None:
        samples = samples[:limit]

    paths = _task_output_paths(output_dir, task, gen_model_key, teacher_model_key)
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
        "output_file": str(paths.output_file.resolve()),
        "rejected_file": str(paths.rejected_file.resolve()),
        "rejected_detail_file": str(paths.rejected_detail_file.resolve()),
        "audit_file": str(paths.audit_file.resolve()),
        "no_teacher_drop": no_teacher_drop,
        "skip_teacher": skip_teacher,
        "class_target_min": class_target_min,
        "class_existing_json": str(class_existing_json.resolve()) if class_existing_json else None,
        "max_generation_attempts": max_generation_attempts,
        "skipped_class_target": 0,
    }
    class_gate: ClassTargetGate | None = None
    if class_target_min is not None:
        if task != "naming":
            raise ValueError("--class-target-min is only supported for naming")
        class_gate = _build_class_target_gate(
            task,
            samples,
            target_min=class_target_min,
            existing_counts=_load_class_existing_counts(class_existing_json),
        )
    if dry_run:
        for i, sample in enumerate(samples):
            system, user, gold_answer_text = _sample_parts(sample)
            _ = system, user
            _parse_answer(task, gold_answer_text)
            report["kept"] += 1
        return report

    processed_indices = set() if no_resume else _load_processed_indices(paths)
    resuming = bool(processed_indices) and not no_resume
    if resuming:
        paths.task_dir.mkdir(parents=True, exist_ok=True)
    else:
        _prepare_task_output(paths)
    report["resumed"] = resuming
    report["skipped_existing"] = len(processed_indices)
    report["pending"] = len(samples) - len(processed_indices)
    if progress is not None:
        progress.begin_task(task, len(samples), report, resume=resuming)

    if class_gate is not None and resuming:
        _restore_class_gate_from_audit(paths.audit_file, task, class_gate)

    results: list[ProcessedSample] = []
    write_lock = threading.Lock()
    pending_jobs = [(i, sample) for i, sample in enumerate(samples) if i not in processed_indices]
    if class_gate is not None:
        pending_jobs.sort(
            key=lambda job: class_gate.priority(
                _answer_type_set(task, _parse_answer(task, _sample_parts(job[1])[2]))
            )
        )

    def _persist_result(item: ProcessedSample) -> None:
        with write_lock:
            if item.audit is not None:
                append_jsonl(paths.audit_file, item.audit)
            if item.reject is not None:
                append_jsonl(paths.rejected_file, item.reject)
            if item.reject_detail is not None:
                append_jsonl(paths.rejected_detail_file, item.reject_detail)
        if progress is not None:
            progress.record_result(task, item)

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
                no_teacher_drop=no_teacher_drop,
                skip_teacher=skip_teacher,
                class_gate=class_gate,
                max_generation_attempts=max_generation_attempts,
            ): i
            for i, sample in pending_jobs
        }
        for future in as_completed(futures):
            item = future.result()
            results.append(item)
            _persist_result(item)

    results.sort(key=lambda item: item.index)
    if progress is not None:
        counts = progress.task_counts(task)
        for key in _PROGRESS_COUNT_KEYS:
            if key in counts:
                report[key] = counts[key]

    write_json(paths.output_file, _rebuild_kept_samples_from_audit(paths.audit_file, samples, task))
    if progress is not None:
        progress.finish_task(task, report)
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
    parser.add_argument("--max-retries", type=int, default=3, help="Retries for API/parse failures only; rule-check failures are not retried.")
    parser.add_argument("--limit", type=int, default=None, help="Optional per-task sample limit for smoke runs.")
    parser.add_argument("--gen-temperature", type=float, default=None)
    parser.add_argument("--teacher-temperature", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true", help="Only parse source files and gold answers; do not call LLMs.")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing cot_audit/cot_rejected jsonl and restart the task outputs from scratch.",
    )
    parser.add_argument(
        "--no-teacher-drop",
        action="store_true",
        help="If teacher returns drop, keep the sample with gold answer instead of rejecting.",
    )
    parser.add_argument(
        "--skip-teacher",
        action="store_true",
        help="Skip teacher model; keep samples that pass hard rules with gold answer.",
    )
    parser.add_argument(
        "--class-target-min",
        type=int,
        default=None,
        help="Naming only: stop annotating a class once it reaches this many CoT samples.",
    )
    parser.add_argument(
        "--class-existing-json",
        type=Path,
        default=None,
        help="JSON with classes[].types and classes[].cot_prompts for existing CoT coverage.",
    )
    parser.add_argument(
        "--max-generation-attempts",
        type=int,
        default=1,
        help="Total generation+rule attempts per sample (retries on rule failure).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = _parse_tasks(args.tasks)
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "generation_model_key": args.gen_model_key,
        "generation_model": _configured_model_name(args.gen_model_key, args.config_path),
        "teacher_model_key": args.teacher_model_key,
        "teacher_model": _configured_model_name(args.teacher_model_key, args.config_path),
        "config_path": args.config_path,
        "no_teacher_drop": args.no_teacher_drop,
        "skip_teacher": args.skip_teacher,
        "class_target_min": args.class_target_min,
        "max_generation_attempts": max(1, args.max_generation_attempts),
        "tasks": {},
    }
    progress = None if args.dry_run else CotProgressTracker(output_dir / "cot_progress.json")
    progress_file = output_dir / "cot_progress.json"
    if progress is not None:
        if not args.no_resume and progress_file.exists():
            progress.load_existing(progress_file, report, tasks)
        else:
            progress.init_run(report, tasks)
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
            progress=progress,
            no_resume=args.no_resume,
            no_teacher_drop=args.no_teacher_drop,
            skip_teacher=args.skip_teacher,
            class_target_min=args.class_target_min,
            class_existing_json=args.class_existing_json,
            max_generation_attempts=max(1, args.max_generation_attempts),
        )
    report["progress_file"] = str((output_dir / "cot_progress.json").resolve())
    write_json(output_dir / "cot_injection_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
