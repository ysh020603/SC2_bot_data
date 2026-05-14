"""Down Agent (微操执行官) Prompt 构建与解析。

职责：将 Mid Agent 输出的单条自然语言任务翻译成严格 JSON 动作指令
``{"action": "<key>", "to_count": <int>}``。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("SC2_Agent.down_agent")


def build_translation_messages(
    race: str,
    task_description: str,
    obs_text: str,
    action_space: Dict[str, str],
) -> list[Dict[str, str]]:
    """构建 Down Agent 动作翻译 Prompt。

    :param race:             当前种族名。
    :param task_description: Mid Agent 输出的单条自然语言任务。
    :param obs_text:         当前观测文本。
    :param action_space:     ``{action_key: description}`` 字典。
    :return:                 OpenAI messages 格式列表。
    """
    race_cap = race.capitalize()

    action_space_lines = [
        f'  - "{key}": {desc}' for key, desc in action_space.items()
    ]
    action_space_text = "\n".join(action_space_lines) or "  (empty)"

    system_msg = (
        f"You translate ONE {race_cap} build-order task description into a single "
        "JSON object that strictly matches the action space below.\n"
        "Output ONLY the JSON object, no prose, no markdown fences.\n\n"
        "Schema:\n"
        '  {"action": "<action_key>", "to_count": <positive integer>}\n\n'
        "Constraints:\n"
        "  * <action_key> MUST be one of the legal keys listed below.\n"
        "  * <to_count> is the ABSOLUTE target count on the field "
        "(including under-construction).\n"
        "  * Translate only the single task provided by the planner.\n\n"
        f"[Legal Action Space]\n{action_space_text}"
    )

    user_msg = (
        f"[Task Description]\n{task_description}\n\n"
        f"[Current Observation]\n{obs_text}"
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def parse_translation_response(
    text: str,
    legal_keys: Set[str],
) -> Optional[Dict[str, Any]]:
    """解析 Down Agent 输出 ``{"action": ..., "to_count": ...}``。

    :return: 合法的动作字典，或 ``None``。
    """
    if not text:
        return None
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    parsed: Optional[Dict[str, Any]] = None
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            parsed = data
    except Exception:
        parsed = None

    if parsed is None:
        brace_match = re.search(r"\{[\s\S]*?\}", cleaned)
        if not brace_match:
            return None
        try:
            data = json.loads(brace_match.group(0))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        parsed = data

    action = parsed.get("action")
    to_count_raw = parsed.get("to_count")
    if not isinstance(action, str) or not action:
        return None
    try:
        to_count = int(to_count_raw)
    except (TypeError, ValueError):
        return None
    if to_count <= 0:
        return None

    if action not in legal_keys:
        logger.warning("Down Agent produced illegal action %r; dropping.", action)
        return None

    return {"action": action, "to_count": to_count}
