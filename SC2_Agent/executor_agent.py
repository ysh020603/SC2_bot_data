"""Executor Agent (执行期) Prompt 构建与解析。

仅用于 train / addon / morph 类动作的「选执行单位」：规则层先用
``get_available_abilities`` 筛出能执行该 ability 的候选 + 状态，并预计算候选与
未执行/等待 action 的冲突提示；本 Agent 让 LLM 从候选里挑一个 tag。

仿 SC2_scout_RL 的 ``MicroAgent.select_executor``，但加入了「不要一味追求短期效率
而占掉后续冲突动作执行者」的约束（典型：没挂 add-on 的 Barracks 别拿去造兵，留给
建 Tech Lab/Reactor 的动作）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.executor_agent")


def build_executor_messages(
    ability_name: str,
    candidate_units_text: str,
    cost_hint: str,
    pending_actions_summary: str,
    waiting_actions_summary: str,
    executor_conflict_hints: str,
) -> List[Dict[str, str]]:
    """构建执行者 Agent Prompt。

    :param ability_name:            要执行的 ability/action 标准名。
    :param candidate_units_text:    候选执行者文本（含 tag/类型/状态）。
    :param cost_hint:               该 action 的资源/时间成本提示。
    :param pending_actions_summary: 当前序列中未执行 action 摘要。
    :param waiting_actions_summary: 资源/科技等待中的 action 摘要。
    :param executor_conflict_hints: 候选与未执行/等待 action 的冲突提示。
    """
    system_msg = f"""You are a StarCraft II Terran executor selector. Pick the
SINGLE BEST unit/structure to execute the given ability, choosing strictly from
the Candidate Executors list.

Selection rules:
* The candidates are already filtered to those that CAN execute this ability now.
* Prefer an IDLE producer over a busy one.
* DO NOT blindly chase short-term efficiency. Avoid picking a producer that an
  upcoming pending/waiting action will conflict with, even if it looks free now.
  Concrete example: do NOT pick a Barracks that has NO add-on to train a Marine if
  a pending action needs that Barracks to build a Tech Lab / Reactor next - once it
  starts the Marine it cannot start the add-on and the later action stalls. Prefer a
  Barracks that already has the matching add-on (or a Reactor that can queue 2) for
  training, and leave a bare Barracks free for the add-on action.
* Consider add-on status (a Reactor lets a Barracks queue 2 units; a producer that
  is building an add-on is unavailable).
* Return EXACTLY ONE tag.

[Ability to execute] {ability_name}
[Action cost/time] {cost_hint or '(unknown)'}
[Pending actions not yet executed] {pending_actions_summary or '(none)'}
[Actions currently waiting] {waiting_actions_summary or '(none)'}
[Possible conflicts in pending actions]
{executor_conflict_hints or '(none)'}

Output ONLY a JSON list with exactly one tag, no prose, no markdown fences:
[12345]"""

    user_msg = f"[Candidate Executors]\n{candidate_units_text}"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def parse_executor_response(
    text: str,
    legal_tags: Optional[set] = None,
    tag_map: Optional[Dict[int, int]] = None,
) -> Optional[int]:
    """解析执行者 Agent 输出的 ``[tag]``，返回单个 tag（int）。

    :param legal_tags: 若提供，则校验 tag 必须在候选集合内，否则返回 ``None``。
    :param tag_map:    可选的 ``{prompt_tag: real_tag}`` 映射，用于短 tag 还原。
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    tag: Optional[int] = None
    try:
        data = json.loads(cleaned)
        if isinstance(data, list) and data:
            tag = int(data[0])
        elif isinstance(data, (int, float)):
            tag = int(data)
    except Exception:
        match = re.search(r"-?\d+", cleaned)
        if match:
            try:
                tag = int(match.group(0))
            except ValueError:
                tag = None

    if tag is None:
        return None
    if legal_tags is not None and tag not in legal_tags:
        logger.warning("Executor Agent returned illegal tag %r (not a candidate).", tag)
        return None
    if tag_map is not None:
        if tag not in tag_map:
            logger.warning("Executor Agent returned unmapped prompt tag %r.", tag)
            return None
        return tag_map[tag]
    return tag
