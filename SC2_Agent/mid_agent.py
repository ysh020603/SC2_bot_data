"""Mid Agent (运营执行官) Prompt 构建与解析。

职责：保持原有 Stage 1 调用频率，根据 obs 与 t=0 选定的策略描述，
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
* The order of the list dictates absolute resource priority: earlier tasks claim minerals, gas, and workers first. Therefore, you MUST place urgent, important, and short-term tasks at the very front of the list. Less urgent, long-term goals must be placed at the back.
* Tasks that act as tech-tree bottlenecks (e.g., the opening Supply Depot or the first Barracks) MUST be prioritized at the absolute front. To guarantee their immediate execution, you can even issue them as a single isolated task for that cycle.
* The lower layer is declarative: if you ask for "Train Marines to 20", it will keep training until the absolute target count is reached.
* IMPORTANT: If a task cannot be executed because of lack of Supply (population), you MUST prioritize "Build Supply Depot" at the very front of your list to clear the bottleneck.
* IMPORTANT: If a task cannot be executed due to insufficient resources, the system will NOT reserve your money if the task is physically blocked (e.g., by supply or lack of production buildings). Therefore, if you genuinely need a high-cost unit (like a Battlecruiser), you MUST ensure the necessary infrastructure (Starport/Fusion Core) and sufficient supply room are built BEFORE the train task; otherwise, your resources will be leaked to cheaper, lower-priority units.

[Resource Allocation & Priority Rules]
When you need to save resources for expensive, high-tier actions (like Battlecruisers or Expansions) and want to prevent other automatic or minor tasks from stealing your Minerals/Gas, append "(Priority)" to your task string.
* Standard task: "Train Battlecruiser to 20" (builds when resources happen to be enough)
* Priority task: "Train Battlecruiser to 20 (Priority)" (locks resources and hoards them until affordable)
Use "(Priority)" sparingly and only for tasks marked with [Supports Priority] in the Action Space. Do not use it for Tech research or Refineries."""

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
) -> List[Dict[str, str]]:
    """构建 Mid Agent 规划 Prompt。

    :param race:                 当前种族名。
    :param obs_text:             当前观测文本。
    :param previous_tasks:       上一轮自然语言任务列表。
    :param strategy_description: 从 ``Top_agent_0.md`` 读取的策略正文（或空串）。
    """
    race_cap = race.capitalize()

    strategy_block = strategy_description.strip() if strategy_description else (
        f"(No pre-defined strategy loaded. Use general {race_cap} best practices.)"
    )

    system_msg = f'''You are a senior StarCraft II strategist controlling a {race_cap} bot.
This is a macro Planning Task. You are the global plan manager for the next 30 seconds.

{_EXECUTION_MODEL}

Your job each cycle:
* Follow the selected strategy below and align every task with its build order, tech path, and unit composition goals.
* Compare the current observation with the previous natural-language task list.
* Remove tasks that are already complete or no longer appropriate.
* Update tasks whose target count should increase or decrease.
* Add new tasks needed for the current stage.
* Describe each task clearly: Each natural-language task MUST contain only ONE single plan or action. Do not combine multiple different actions in one sentence. While there is no limit on the total number of tasks, you must strictly maintain the priority order. Key bottleneck actions can still be issued as a single isolated task for that cycle to guarantee focus.
* For concrete macro task planning, all requirements concerning the same unit type or structure type MUST be consolidated into one single task with one clear final target. Do not split the planning of the same unit or structure across multiple tasks in the same cycle.
* You ONLY need to describe the corresponding unit and its planned target quantity. Do NOT describe its specific purpose or usage. For example, simply write "Train Marines to 24". Do not add explanations like "for early defense and bio army scaling".
* You do NOT need to consider or describe the physical execution location of any task. The lower layer will decide where to build, where to train, where to rally units, and where to assign workers. Your task should only describe the macro objective, not the execution position.
* All your planning must revolve solely around macro operations (building structures and training units). You do not need to plan for scouting or other micro/tactical maneuvers.

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
    """解析 Mid Agent 的 ``{{"tasks": [...]}}`` 输出；格式错误返回 ``None``。"""
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