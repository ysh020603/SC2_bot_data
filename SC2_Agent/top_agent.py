"""Top Agent (全局指挥官) Prompt 构建与解析。

职责：
* **t=0 初始化策略** — 根据 instruct + SKILL 目录可用策略描述，选定本局战术。
* **每 60 秒轮询**   — 根据 obs 评估当前阶段 (早/中/晚期)，输出当前需着重关注的核心点。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.top_agent")


# ======================================================================
# t=0 — 初始化策略选择
# ======================================================================


def build_initial_strategy_messages(
    race: str,
    instruct: str,
    strategies: Dict[str, str],
) -> List[Dict[str, str]]:
    """构建 t=0 策略选择的 LLM 消息列表。

    :param race:       当前种族名 (terran / zerg / protoss)。
    :param instruct:   玩家预设战术要求或偏好（自然语言）。
    :param strategies:  ``{策略名: 策略描述}`` 字典，来自 SKILL/{race}/ 目录遍历。
    :return:           OpenAI messages 格式列表。
    """
    strategies_text = "\n".join(
        f'  - "{name}": {desc}' for name, desc in strategies.items()
    )
    if not strategies_text:
        strategies_text = "  (No pre-defined strategies available for this race.)"

    system_msg = f'''You are a top-level StarCraft II strategist for the {race.capitalize()} race.
Your job is to evaluate the player's tactical instruction and choose the single best strategy from the available options.

If the player's instruction strongly implies a particular strategy, prefer that one.
If the instruction is vague or empty, pick the strategy you judge most versatile for a standard ladder game.

Output ONLY a JSON object with this exact schema:
  {{"strategy": "<strategy_name>"}}

The strategy_name MUST be one of the keys listed below. Do not output anything outside the JSON object.

[Available Strategies]
{strategies_text}'''

    user_msg = f"[Player Instruction]\n{instruct or '(no specific instruction)'}"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def parse_strategy_selection(
    text: str,
    valid_names: List[str],
) -> Optional[str]:
    """解析 t=0 LLM 输出中的 ``{"strategy": "..."}``。

    :return: 合法策略名，或 ``None``。
    """
    if not text:
        return None
    cleaned = _strip_fences(text)

    data = _safe_json_load(cleaned)
    if data is None:
        return None

    strategy = data.get("strategy")
    if isinstance(strategy, str) and strategy.strip():
        strategy = strategy.strip()
        if strategy in valid_names:
            return strategy
        lower_map = {n.lower(): n for n in valid_names}
        if strategy.lower() in lower_map:
            return lower_map[strategy.lower()]
    return None


# ======================================================================
# 每 60 秒 — 阶段评估与焦点指导
# ======================================================================


def build_phase_assessment_messages(
    race: str,
    obs_text: str,
    instruct: str,
    strategy_name: str,
    strategy_description: str,
) -> List[Dict[str, str]]:
    """构建 60 秒轮询的阶段评估 LLM 消息列表。

    :param race:                  当前种族。
    :param obs_text:              当前观测文本。
    :param instruct:              玩家指令。
    :param strategy_name:         t=0 选定的策略名。
    :param strategy_description:  策略的 prompt.md 描述。
    :return:                      OpenAI messages 格式列表。
    """
    system_msg = f'''You are monitoring an ongoing StarCraft II game as the {race.capitalize()} race.

Selected strategy: **{strategy_name}**
Strategy overview:
{strategy_description}

Based on the current observation, determine:
1. The current game phase: "early", "mid", or "late".
2. A concise paragraph describing what the player should focus on RIGHT NOW. 
   * All your planning must revolve solely around macro operations (building structures and training units). You do not need to plan for scouting or other micro/tactical maneuvers.

Output ONLY a JSON object with this exact schema:
  {{"phase": "<early|mid|late>", "focus": "<concise focus description>"}}

Do not output anything outside the JSON object.'''

    user_parts = [f"[Current Observation]\n{obs_text}"]
    if instruct:
        user_parts.append(f"[Player Instruction]\n{instruct}")

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_phase_assessment(text: str) -> Optional[Dict[str, str]]:
    """解析阶段评估输出 ``{"phase": "...", "focus": "..."}``。

    :return: 包含 ``phase`` 和 ``focus`` 的字典，或 ``None``。
    """
    if not text:
        return None
    cleaned = _strip_fences(text)
    data = _safe_json_load(cleaned)
    if data is None:
        return None

    phase = data.get("phase", "").strip().lower()
    focus = data.get("focus", "").strip()
    if phase not in ("early", "mid", "late"):
        phase = "early"
    return {"phase": phase, "focus": focus}


# ======================================================================
# 内部工具
# ======================================================================


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    return cleaned


def _safe_json_load(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None
