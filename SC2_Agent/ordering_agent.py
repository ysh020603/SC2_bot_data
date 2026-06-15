"""Ordering Agent (阶段4) Prompt 构建与解析。

对阶段3 映射出来的 Action 标准名进行排序。排序前由 data_tools 预计算：
* 前置缺失 + 科技链路关系提示（``check_action_prereqs`` / ``tech_chain_relations``）
* 执行者冲突提示（``detect_action_conflicts``）
* 每个 action 的资源/时间成本（``action_cost``，时间为游戏帧）

排序时 **忽略 SupplyDepot**（补给由 supply_planner 自动注入）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.ordering_agent")


def build_ordering_messages(
    race: str,
    actions: List[str],
    obs_text: str,
    prereq_hints: str,
    conflict_hints: str,
    cost_hints: str,
) -> List[Dict[str, str]]:
    """构建阶段4 排序 Agent Prompt。

    :param race:           当前种族名（固定 terran）。
    :param actions:        待排序的 Action 标准名列表（不含 SupplyDepot）。
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
* IGNORE supply depots entirely (supply is inserted automatically afterwards). If
  any supply-depot action appears in the input, DROP it.
* Keep every other input action exactly once. Do not invent new actions.

[Prerequisite & tech-chain hints]
{prereq_hints or '(none)'}

[Producer-conflict hints]
{conflict_hints or '(none)'}

[Per-action cost/time]
{cost_hints or '(none)'}

Output ONLY one JSON object, no prose, no markdown fences:
{{"ordered_actions":["TERRANBUILD_BARRACKS","BUILD_TECHLAB_BARRACKS","BARRACKSTRAIN_MARINE"]}}"""

    actions_json = json.dumps(actions, ensure_ascii=False, indent=2)
    user_msg = f"[Actions to order]\n{actions_json}\n\n[Current Observation]\n{obs_text}"

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
