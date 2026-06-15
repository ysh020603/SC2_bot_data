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
Planning horizon & sizing (IMPORTANT):
* Plan a RELATIVELY LONG-RANGE build covering the whole next ~{horizon} seconds,
  not just one or two immediate actions. Describe a coherent ~{horizon}s game plan.
* The plan must still be realistically achievable within {horizon} seconds given
  your CURRENT production capacity and economy in the observation — size the
  amounts to what your town halls / production buildings can actually output in
  {horizon}s, and to what your income can pay for. A {horizon}s window is long
  enough for several workers, a few army units, and 2-4 new structures / tech
  steps, sequenced sensibly.
* Cover the important dimensions for this window: economy (workers / expansion),
  production buildings & add-ons, tech / upgrades, and army composition — all in
  service of the Selected Strategy."""

_OUTPUT_FORMAT = """\
Output format:
1. First write ONE short reasoning sentence outside JSON.
2. Then output ONE JSON object with this exact schema:
{"mode":"append"|"replace","plan":"<one natural-language paragraph>"}

Rules for the "plan" field:
* It is ONE single natural-language PARAGRAPH (a full prose description), NOT a
  list and NOT bullet points. Write it as flowing sentences.
* Describe the WHOLE ~horizon plan: which structures to add, which units to
  train and roughly how many, which add-ons, and which upgrades/research — and
  the rough order / dependencies (use words like "first", "then", "after",
  "while"). State amounts as increments to ADD (e.g. "add three more SCVs",
  "build two Barracks"), never cumulative targets.
* Do NOT plan supply depots (they are inserted automatically).
* Keep it to one paragraph; do not output markdown fences, lists, JSON arrays, or
  action keys inside the "plan" string."""


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
Every cycle you describe, as ONE natural-language paragraph, the ABSOLUTE
INCREMENT (delta) of structures / units / upgrades to ADD over roughly the next
{horizon} seconds (this is your planning window / horizon), strictly following
the Selected Strategy below.

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
    """解析阶段1 输出 ``{"mode": ..., "plan": "<一段自然语言>"}``；非法返回 ``None``。

    兼容旧格式 ``{"increments": [...]}``：若没有 ``plan`` 字段但有 ``increments``
    列表，则把列表拼成一段文本。
    """
    if not text:
        return None
    data = _extract_json_object(text)
    if data is None:
        logger.warning("Increment Agent output is not valid JSON: %r", text[:200])
        return None

    mode = data.get("mode")
    if mode not in ("append", "replace"):
        mode = "replace"  # safe default

    plan = data.get("plan")
    if isinstance(plan, str) and plan.strip():
        return {"mode": mode, "plan": plan.strip()}

    # 向后兼容：旧的 increments 列表 → 合成一段文本
    raw_increments = data.get("increments")
    if isinstance(raw_increments, list):
        parts = [s.strip() for s in raw_increments if isinstance(s, str) and s.strip()]
        if parts:
            return {"mode": mode, "plan": "; ".join(parts) + "."}
    return None
