"""Ordering Agent (阶段4) Prompt 构建与解析。

对阶段3 映射出来的 Action 标准名进行排序。排序前由 data_tools 预计算：
* 前置缺失 + 科技链路关系提示（``check_action_prereqs`` / ``tech_chain_relations``）
* 执行者冲突提示（``detect_action_conflicts``）
* 每个 action 的资源/时间成本（``action_cost``，时间为游戏帧）

SupplyDepot（``TERRANBUILD_SUPPLYDEPOT``）作为普通动作参与排序；
Stage5 根据 ``SUPPLY_MANAGED`` 决定是否用算法覆盖 LLM 的 depot 位置。
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.ordering_agent")


def _format_action_counts(actions: List[str]) -> str:
    counts = Counter(actions)
    seen = set()
    lines: List[str] = []
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        count = counts[action]
        lines.append(f"{action} x {count}" if count > 1 else action)
    return "\n".join(lines)


def build_ordering_messages(
    race: str,
    actions: List[str],
    obs_text: str,
    prereq_hints: str,
    conflict_hints: str,
    cost_hints: str,
    strategy_step_text: str = "",
) -> List[Dict[str, str]]:
    """构建阶段4 排序 Agent Prompt。

    :param race:           当前种族名（固定 terran）。
    :param actions:        待排序的 Action 标准名列表（**已按数量展开的扁平列表**）。
                           同一个 action 可能出现多次，表示要生产/建造多个；
                           排序只决定先后，不增删、不改变出现次数。
                           可能包含 ``TERRANBUILD_SUPPLYDEPOT``。
    :param obs_text:       当前观测文本。
    :param prereq_hints:   前置缺失 + 科技链路关系提示文本。
    :param conflict_hints: 执行者冲突提示文本。
    :param cost_hints:     每个 action 的资源/时间成本提示文本。
    """
    race_cap = race.capitalize()

    system_msg = f"""You order a list of {race_cap} ACTIONS for the most efficient
execution this cycle. You are given prerequisite, tech-chain, producer-conflict
and cost hints.

Rules:
* Put tech-bottleneck / prerequisite actions BEFORE the actions that depend on
  them (see the prerequisite & tech-chain hints).
* If two actions share the SAME producer (conflict hint), they cannot run in
  parallel; sequence them sensibly so neither starves.
* Cheaper / unlocking / short actions generally go earlier; expensive long-term
  goals later.
* Use the Strategy Step to understand strategic priority and intended timing,
  but the action list remains authoritative. Do not add or remove actions
  because of the Strategy Step.
* The input is a compact counted list: "ACTION x N" means that action must
  appear N times in the final ordered action sequence. A line without "x N"
  means exactly one occurrence.
* Keep EXACTLY the same multiset (the same number of occurrences of each action)
  and only reorder them.
* The JSON output must be an expanded ordered list with repeated action strings;
  do not use "x N" counts in the JSON.
* Supply depots (TERRANBUILD_SUPPLYDEPOT) are ordinary actions; order them just
  like any other build action, placing them before the training actions that need
  the supply headroom.
* Do not invent new actions and do not add or remove occurrences.

[Prerequisite & tech-chain hints]
{prereq_hints or '(none)'}

[Producer-conflict hints]
{conflict_hints or '(none)'}

[Per-action cost/time]
{cost_hints or '(none)'}

Output ONLY one JSON object, no prose, no markdown fences. The output list must
contain exactly the same items as the input (same multiset), just reordered:
{{"ordered_actions":["TERRANBUILD_BARRACKS","BARRACKSTRAIN_MARINE","BARRACKSTRAIN_MARINE"]}}"""

    actions_text = _format_action_counts(actions)
    user_msg = (
        f"[Actions to order]\n"
        f"{actions_text}\n\n[Strategy Step]\n"
        f"{strategy_step_text or '(none)'}\n\n[Current Observation]\n{obs_text}"
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def parse_ordering_response(
    text: str,
    legal_actions: Optional[set] = None,
) -> Optional[List[str]]:
    """解析阶段4 输出 ``{"ordered_actions": [...]}``。

    :param legal_actions: 若提供，则过滤掉不在集合内的 action（防 LLM 编造）。
    :return: 排序后的 action 标准名列表，或 ``None``。
    """
    if not text:
        return None
    data = _extract_json_object(text)
    if data is None:
        logger.warning("Ordering Agent output is not valid JSON: %r", text[:200])
        return None
    raw = data.get("ordered_actions")
    if not isinstance(raw, list):
        return None
    ordered = [a.strip() for a in raw if isinstance(a, str) and a.strip()]
    if legal_actions is not None:
        ordered = [a for a in ordered if a in legal_actions]
    return ordered
