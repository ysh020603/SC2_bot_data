"""Rule-based golden ranking for Terran executor (producer selection) prompts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

AddonKind = Literal["none", "techlab", "reactor", "other"]

ABILITY_RE = re.compile(r"\[Ability to execute\]\s*(\S+)")
CONFLICTS_RE = re.compile(
    r"\[Possible conflicts in pending actions\]\s*\n(.*?)(?:\n\nOutput|\Z)",
    re.S,
)
CANDIDATE_RE = re.compile(
    r"^\s*-\s*tag=(\d+)\s+(\S+)\s+\[(.+)\]\s*$",
    re.M,
)
BUSY_PROGRESS_RE = re.compile(r"busy:.*?\((\d+)%\)", re.I)

ADDON_HOST_TYPES = frozenset({"BARRACKS", "FACTORY", "STARPORT"})
UPGRADED_BASE_TYPES = frozenset({"ORBITALCOMMAND", "PLANETARYFORTRESS"})
BASE_TYPES = frozenset({"COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS"})


@dataclass
class CandidateExecutor:
    tag: int
    unit_type: str
    status_text: str
    is_idle: bool
    busy_progress: float | None
    addon: AddonKind
    base_tier: int

    @property
    def addon_tier(self) -> int:
        return {"reactor": 3, "techlab": 2, "other": 1, "none": 0}[self.addon]

    @property
    def is_bare_producer(self) -> bool:
        return self.unit_type in ADDON_HOST_TYPES and self.addon == "none"

    @property
    def is_unupgraded_base(self) -> bool:
        return self.unit_type == "COMMANDCENTER"


@dataclass
class ExecutorPromptContext:
    ability: str
    conflict_actions: list[str]
    candidates: list[CandidateExecutor]


@dataclass
class CandidateRanking:
    tag: int
    unit_type: str
    eligible: bool
    filtered_reason: str
    ready_score: float
    addon_tier: int
    base_tier: int
    rank_key: tuple[Any, ...]
    sort_key: tuple[Any, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rank_key"] = list(self.rank_key)
        payload["sort_key"] = list(self.sort_key)
        return payload


@dataclass
class GoldenRankResult:
    ability: str
    conflict_actions: list[str]
    reservation_active: bool
    fallback_no_eligible: bool
    golden_tags: list[int]
    rankings: list[CandidateRanking] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ability": self.ability,
            "conflict_actions": self.conflict_actions,
            "reservation_active": self.reservation_active,
            "fallback_no_eligible": self.fallback_no_eligible,
            "golden_tags": self.golden_tags,
            "rankings": [r.to_dict() for r in self.rankings],
        }


def is_addon_action(action: str) -> bool:
    return action.startswith("BUILD_TECHLAB_") or action.startswith("BUILD_REACTOR_")


def is_upgrade_action(action: str) -> bool:
    return action.startswith("UPGRADETO")


def is_reservation_action(action: str) -> bool:
    return is_addon_action(action) or is_upgrade_action(action)


def is_addon_or_upgrade_ability(ability: str) -> bool:
    return is_addon_action(ability) or is_upgrade_action(ability)


def host_type_for_reservation(action: str) -> str | None:
    if is_addon_action(action):
        parts = action.split("_")
        if len(parts) >= 3:
            return parts[-1]
        return None
    if is_upgrade_action(action):
        return "COMMANDCENTER"
    return None


def parse_ability(system: str) -> str:
    match = ABILITY_RE.search(system or "")
    return match.group(1) if match else ""


def parse_conflict_actions(system: str) -> list[str]:
    match = CONFLICTS_RE.search(system or "")
    if not match:
        return []
    actions: list[str] = []
    for line in match.group(1).split("\n"):
        line = line.strip().lstrip("-").strip()
        if line and line != "(none)":
            actions.append(line)
    return actions


def _parse_addon(status: str) -> AddonKind:
    lowered = status.lower()
    if "has reactor" in lowered:
        return "reactor"
    if "has techlab" in lowered:
        return "techlab"
    if "has add-on" in lowered:
        return "other"
    if "no add-on" in lowered:
        return "none"
    return "none"


def _parse_base_tier(unit_type: str) -> int:
    if unit_type in UPGRADED_BASE_TYPES:
        return 2
    if unit_type == "COMMANDCENTER":
        return 1
    return 0


def parse_candidate(tag: int, unit_type: str, status_text: str) -> CandidateExecutor:
    status = status_text.strip()
    is_idle = status.startswith("idle")
    progress_match = BUSY_PROGRESS_RE.search(status)
    busy_progress = int(progress_match.group(1)) / 100.0 if progress_match else None
    return CandidateExecutor(
        tag=tag,
        unit_type=unit_type,
        status_text=status,
        is_idle=is_idle,
        busy_progress=busy_progress,
        addon=_parse_addon(status),
        base_tier=_parse_base_tier(unit_type),
    )


def parse_candidates(user: str) -> list[CandidateExecutor]:
    candidates: list[CandidateExecutor] = []
    for match in CANDIDATE_RE.finditer(user or ""):
        candidates.append(parse_candidate(int(match.group(1)), match.group(2), match.group(3)))
    return candidates


def parse_executor_prompt(system: str, user: str) -> ExecutorPromptContext:
    return ExecutorPromptContext(
        ability=parse_ability(system),
        conflict_actions=parse_conflict_actions(system),
        candidates=parse_candidates(user),
    )


def _reserved_host_types(conflict_actions: list[str]) -> set[str]:
    hosts: set[str] = set()
    for action in conflict_actions:
        if not is_reservation_action(action):
            continue
        host = host_type_for_reservation(action)
        if host:
            hosts.add(host)
    return hosts


def _filter_reason(
    candidate: CandidateExecutor,
    *,
    reserved_hosts: set[str],
    reservation_active: bool,
    ability_is_addon_or_upgrade: bool,
) -> str:
    if not reservation_active or ability_is_addon_or_upgrade:
        return ""
    if candidate.unit_type in reserved_hosts and candidate.is_bare_producer:
        return f"reserve bare {candidate.unit_type} for pending add-on"
    if "COMMANDCENTER" in reserved_hosts and candidate.is_unupgraded_base:
        return "reserve unupgraded COMMANDCENTER for pending base upgrade"
    return ""


def _ready_score(candidate: CandidateExecutor) -> float:
    if candidate.is_idle:
        ready = 1000.0
        if candidate.addon == "reactor":
            ready += 1.0
        return ready
    if candidate.busy_progress is not None:
        ready = candidate.busy_progress * 1000.0
        if candidate.addon == "reactor" and candidate.busy_progress < 0.5:
            ready = max(ready, 500.0)
        return ready
    return 0.0


def _rank_key(candidate: CandidateExecutor, *, eligible: bool, ready_score: float) -> tuple[Any, ...]:
    return (
        eligible,
        ready_score,
        candidate.addon_tier,
        candidate.base_tier,
    )


def _sort_key(candidate: CandidateExecutor, *, eligible: bool, ready_score: float) -> tuple[Any, ...]:
    return (
        *_rank_key(candidate, eligible=eligible, ready_score=ready_score),
        -candidate.tag,
    )


def rank_executor_candidates(ctx: ExecutorPromptContext) -> GoldenRankResult:
    reserved_hosts = _reserved_host_types(ctx.conflict_actions)
    reservation_active = bool(reserved_hosts)
    ability_is_addon_or_upgrade = is_addon_or_upgrade_ability(ctx.ability)

    rankings: list[CandidateRanking] = []
    for candidate in ctx.candidates:
        filtered_reason = _filter_reason(
            candidate,
            reserved_hosts=reserved_hosts,
            reservation_active=reservation_active,
            ability_is_addon_or_upgrade=ability_is_addon_or_upgrade,
        )
        eligible = not filtered_reason
        ready = _ready_score(candidate)
        rkey = _rank_key(candidate, eligible=eligible, ready_score=ready)
        skey = _sort_key(candidate, eligible=eligible, ready_score=ready)
        rankings.append(
            CandidateRanking(
                tag=candidate.tag,
                unit_type=candidate.unit_type,
                eligible=eligible,
                filtered_reason=filtered_reason,
                ready_score=ready,
                addon_tier=candidate.addon_tier,
                base_tier=candidate.base_tier,
                rank_key=rkey,
                sort_key=skey,
            )
        )

    eligible_rankings = [r for r in rankings if r.eligible]
    fallback = False
    if not eligible_rankings:
        eligible_rankings = rankings
        fallback = True

    best_rank_key = max(r.rank_key for r in eligible_rankings)
    golden_tags = [r.tag for r in eligible_rankings if r.rank_key == best_rank_key]

    return GoldenRankResult(
        ability=ctx.ability,
        conflict_actions=list(ctx.conflict_actions),
        reservation_active=reservation_active,
        fallback_no_eligible=fallback,
        golden_tags=golden_tags,
        rankings=rankings,
    )


def rank_executor_prompt(system: str, user: str) -> GoldenRankResult:
    return rank_executor_candidates(parse_executor_prompt(system, user))


def parse_llm_answer_tag(answer: str) -> int | None:
    if not answer:
        return None
    cleaned = answer.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    match = re.search(r"-?\d+", cleaned)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None
