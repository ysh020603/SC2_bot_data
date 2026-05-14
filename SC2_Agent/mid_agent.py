"""Mid Agent (运营执行官) Prompt 构建与解析。

职责：保持原有 Stage 1 调用频率，根据 obs + Top Agent 注入的策略/阶段上下文，
输出自然语言宏观任务列表。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.mid_agent")


_EXECUTION_MODEL = """\
Execution model:
* Your output list will be translated and executed concurrently by the lower layer.
* A blocked task does NOT block later tasks.
* The order of the list dictates absolute resource priority: earlier tasks claim \
minerals, gas, and workers first. Therefore, you MUST place urgent, important, and \
short-term tasks at the very front of the list. Less urgent, long-term goals must be \
placed at the back.
* Tasks that act as tech-tree bottlenecks (e.g., the opening Supply Depot or the first \
Barracks) MUST be prioritized at the absolute front. To guarantee their immediate \
execution, you can even issue them as a single isolated task for that cycle.
* The lower layer is declarative: if you ask for "Train Marines to 20", it will keep \
training until the absolute target count is reached."""

_OUTPUT_FORMAT = """\
Output format:
1. First write one concise reasoning paragraph outside JSON.
2. Then output one JSON object with this exact schema:
{"tasks":["natural-language task 1","natural-language task 2"]}

Do not output markdown, comments, or action keys. The JSON object itself must contain \
only the tasks field."""


def build_planning_messages(
    race: str,
    obs_text: str,
    previous_tasks: List[str],
    strategy_description: str,
    phase: str,
    focus: str,
) -> List[Dict[str, str]]:
    """构建 Mid Agent 规划 Prompt。

    :param race:                  当前种族名。
    :param obs_text:              当前观测文本。
    :param previous_tasks:        上一轮自然语言任务列表。
    :param strategy_description:  从 prompt.md 读取的策略正文（或空串）。
    :param phase:                 Top Agent 判定的当前阶段 (early/mid/late)。
    :param focus:                 Top Agent 判定的当前焦点描述。
    :return:                      OpenAI messages 格式列表。
    """
    race_cap = race.capitalize()

    top_context_lines: List[str] = []
    if phase:
        top_context_lines.append(f"Current game phase (assessed by commander): {phase}")
    if focus:
        top_context_lines.append(f"Current focus directive: {focus}")
    top_context_block = "\n".join(top_context_lines) or "(No commander directive yet.)"

    strategy_block = strategy_description.strip() if strategy_description else (
        f"(No pre-defined strategy loaded. Use general {race_cap} best practices.)"
    )

    system_msg = f'''You are a senior StarCraft II strategist controlling a {race_cap} bot.
This is a macro Planning Task. You are the global plan manager for the next 20 seconds.

{_EXECUTION_MODEL}

Your job each cycle:
* Compare the current observation with the previous natural-language task list.
* Remove tasks that are already complete or no longer appropriate.
* Update tasks whose target count should increase or decrease.
* Add new tasks needed for the current stage.
* Describe each task clearly: Each natural-language task MUST contain only ONE single plan or action. Do not combine multiple actions in one sentence. While there is no limit on the total number of tasks, you must strictly maintain the priority order. Key bottleneck actions can still be issued as a single isolated task for that cycle to guarantee focus.
* All your planning must revolve solely around macro operations (building structures and training units). You do not need to plan for scouting or other micro/tactical maneuvers.

[Commander Directive]
{top_context_block}

[Strategy]
{strategy_block}

{_OUTPUT_FORMAT}'''

    previous_tasks_json = json.dumps(previous_tasks, ensure_ascii=False, indent=2)

    user_msg = (
        f"[Current Observation]\n{obs_text}\n\n"
        f"[Previous Natural-Language Tasks]\n{previous_tasks_json}"
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def parse_planning_response(text: str) -> Optional[List[str]]:
    """解析 Mid Agent 的 ``{"tasks": [...]}`` 输出；格式错误返回 ``None``。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        data = json.loads(cleaned)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            logger.warning("Mid Agent output is not valid JSON: %r", text[:200])
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            logger.warning("Mid Agent output is not valid JSON: %r", text[:200])
            return None

    if not isinstance(data, dict):
        return None
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return None
    return [t.strip() for t in raw_tasks if isinstance(t, str) and t.strip()]
