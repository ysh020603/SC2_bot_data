"""Increment Agent (阶段1) Prompt 构建与解析。

每个决策周期（默认 30s，或上一条 action 序列执行完时），结合 obs 与 t=0 选定的
策略描述，输出未来一段时间要 **新增（绝对增量）** 的建筑/单位/升级（自然语言），
并决定是 ``append`` 到正在执行的序列，还是 ``replace`` 重头规划。

注意：这里输出的是「增量（delta）」而非「累计目标数量」。例如 "Add 4 Marines"，
而不是 "Train Marines to 20"。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.increment_agent")


_EXECUTION_MODEL = """\
Execution model:
* Your increments are mapped to concrete actions, ordered, and executed by a
  command-style scheduler. Earlier items in your list get absolute resource and
  executor priority, so put the most urgent / bottleneck items first.
* Tech-tree bottlenecks (the first Barracks, a required add-on, a required tech
  building) MUST be placed before the items that depend on them.
* Supply Depots are inserted automatically by the system. Do NOT plan supply
  depots yourself.
* Scouting, attacking, worker distribution, repairs and base-defense buildings
  are handled by an always-on background layer. Do NOT plan them.
* If the previous action list is still running, you choose:
    - "append": add these increments on top of what is still pending.
    - "replace": discard the unfinished plan and start fresh from this list."""

_SIZING_GUIDANCE = """\
Sizing guidance (IMPORTANT):
* The increments must be realistically achievable within ONE planning window of
  about {horizon} seconds, given your CURRENT production capacity and economy in
  the observation. Do NOT dump a huge batch at once.
* Rule of thumb for one {horizon}s window:
    - Workers (SCV): roughly 1 per active town hall for this window (a town hall
      makes ~1 SCV every ~12s, but you also spend minerals elsewhere). Asking for
      6+ SCVs in a single window is almost always too much.
    - Army units: only as many as your current production buildings can actually
      produce in {horizon}s (about 1-2 per relevant production building).
    - Structures: usually 1-3 new buildings per window.
* Prefer a small, focused increment that the economy can sustain, then expand it
  next cycle. Over-planning wastes the window and starves higher-priority items."""

_OUTPUT_FORMAT = """\
Output format:
1. First write ONE concise reasoning paragraph outside JSON.
2. Then output ONE JSON object with this exact schema:
{"mode":"append"|"replace","increments":["Add 1 Barracks","Add a Tech Lab on the Barracks","Add 4 Marines"]}

Rules for increments:
* Each item is ONE increment about ONE unit / structure / upgrade type only.
* Express increments as deltas to ADD ("Add N X"), never cumulative targets.
* Upgrades/researches are written as a single item (e.g. "Research Stimpack").
* List order = execution precedence (earliest first); you may use words like
  "then"/"before" for clarity.
* Do not output markdown fences, comments, or action keys inside the JSON."""


def build_increment_messages(
    race: str,
    obs_text: str,
    pending_actions_summary: str,
    strategy_description: str,
    interval_seconds: float = 30.0,
) -> List[Dict[str, str]]:
    """构建阶段1 增量 Agent 的 Prompt。

    :param race:                 当前种族名（固定 terran）。
    :param obs_text:             当前观测文本。
    :param pending_actions_summary: 当前仍未执行完的 action 序列摘要文本。
    :param strategy_description: 从 ``Top_agent_0.md`` 读取的策略正文。
    :param interval_seconds:     决策周期（秒）。
    """
    race_cap = race.capitalize()

    strategy_block = strategy_description.strip() if strategy_description else (
        f"(No pre-defined strategy loaded. Use general {race_cap} best practices.)"
    )

    horizon = int(interval_seconds)
    system_msg = f"""You are a senior StarCraft II {race_cap} macro commander.
Every cycle you output the ABSOLUTE INCREMENT (delta) of structures / units /
upgrades to ADD over roughly the next {horizon} seconds (this is your planning
window / horizon), strictly following the Selected Strategy below.

{_EXECUTION_MODEL}

{_SIZING_GUIDANCE.format(horizon=horizon)}

[Strategy]
{strategy_block}

{_OUTPUT_FORMAT}"""

    user_msg = (
        f"[Current Observation]\n{obs_text}\n\n"
        f"[Currently unfinished action list]\n{pending_actions_summary or '(empty)'}"
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


def parse_increment_response(text: str) -> Optional[Dict[str, Any]]:
    """解析阶段1 输出 ``{"mode": ..., "increments": [...]}``；非法返回 ``None``。"""
    if not text:
        return None
    data = _extract_json_object(text)
    if data is None:
        logger.warning("Increment Agent output is not valid JSON: %r", text[:200])
        return None

    mode = data.get("mode")
    if mode not in ("append", "replace"):
        mode = "replace"  # safe default
    raw_increments = data.get("increments")
    if not isinstance(raw_increments, list):
        return None
    increments = [s.strip() for s in raw_increments if isinstance(s, str) and s.strip()]
    return {"mode": mode, "increments": increments}
