"""Top Agent (全局指挥官) Prompt 构建与解析。

职责：
* **t=0 初始化策略** — 多轮交互式决策（SELECT / VIEW / GENERATE）。
* **每 60 秒轮询**   — 根据 obs 评估当前阶段 (早/中/晚期)，输出焦点指导，
  可选拼接 ``Top_agent_60.md`` 作为 ``[Phase Guidance]`` 区块。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.top_agent")


# ======================================================================
# 常量
# ======================================================================

#: LLM 自行生成新策略时使用的占位策略名（用于路由到 ``SKILL/{race}/generic/``）。
CUSTOM_STRATEGY_NAME = "Custom_Generated"


# ======================================================================
# t=0 — 多轮交互式策略选择
# ======================================================================


def build_initial_strategy_messages(
    race: str,
    instruct: str,
    strategy_summaries: Dict[str, str],
) -> List[Dict[str, str]]:
    """构建 **t=0 第一轮** 的 LLM 消息列表。

    与旧版本最大不同：仅提供策略 **摘要** 列表，并要求 LLM 以 JSON Action 形式
    回答 ``SELECT`` / ``VIEW`` / ``GENERATE`` 三种动作之一。

    :param race:               当前种族名 (terran / zerg / protoss)。
    :param instruct:           玩家自然语言战术指令。
    :param strategy_summaries:  ``{策略名: 1~3 句摘要}`` 字典。
    :return: OpenAI messages 列表（system + user）。
    """
    summaries_text = "\n".join(
        f'  - "{name}": {summary.strip()}' for name, summary in strategy_summaries.items()
    )
    if not summaries_text:
        summaries_text = "  (No pre-defined strategies available for this race.)"

    system_msg = f"""You are a top-level StarCraft II strategist for the {race.capitalize()} race.
You are conducting an INTERACTIVE strategy selection. You will see a short summary of each
available strategy first. You may ask to read the FULL detail of one or more strategies
before deciding. You may also invent a brand-new strategy if none of the listed ones fit.

You MUST output ONLY a single JSON object, and choose exactly ONE of these three actions:

1. SELECT a listed strategy (the summary is convincing enough):
   {{"action": "SELECT", "strategy": "<strategy_name>"}}

2. VIEW the full detail of one or more listed strategies before deciding:
   {{"action": "VIEW", "strategies": ["<strategy_name>", "..."]}}
   - After your VIEW, the system will paste back the full detail and ask you again.
   - You may VIEW multiple times if you remain undecided.

3. GENERATE a brand-new custom strategy (only after you have already VIEWed the most
   relevant candidates and concluded none of them satisfy the player's instruction):
   {{"action": "GENERATE", "strategy": "<concise multi-paragraph custom strategy text>"}}

Rules:
* The ``strategy_name`` in SELECT / VIEW MUST be a key from the listed summaries below.
* In GENERATE, the ``strategy`` field MUST be a coherent multi-line StarCraft II macro
  build, structured similarly to the existing strategy details (opening, tech, units,
  expansion, end-goal).
* Do NOT output text outside the JSON object.
* Do NOT wrap the JSON in markdown fences.

[Available Strategy Summaries]
{summaries_text}"""

    user_msg = f"[Player Instruction]\n{instruct or '(no specific instruction)'}"

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def build_view_followup_user_message(view_details: Dict[str, str]) -> Dict[str, str]:
    """构建当 LLM 请求 ``VIEW`` 后，由 Python 端追加回去的用户消息。

    :param view_details: ``{策略名: Top_agent_0.md 全文}``。
    :return: 一条 ``role="user"`` 的消息（追加到 messages 历史里）。
    """
    if not view_details:
        body = "(No detail available for the requested strategies. Please decide using the summaries.)"
    else:
        parts: List[str] = []
        for name, detail in view_details.items():
            parts.append(
                f"=== Full detail of strategy '{name}' ===\n{detail.strip()}\n=== End of '{name}' ==="
            )
        body = "\n\n".join(parts)

    content = (
        "[Requested Strategy Details]\n"
        f"{body}\n\n"
        "Now please make your decision again. Output ONE JSON object only, choosing "
        "SELECT / VIEW / GENERATE as before."
    )
    return {"role": "user", "content": content}


def parse_initial_action(text: str) -> Optional[Dict[str, Any]]:
    """解析 t=0 LLM 的动作 JSON。

    :return: 形如 ``{"action": "SELECT"/"VIEW"/"GENERATE", ...}`` 的标准化字典，
             解析失败返回 ``None``。
    """
    if not text:
        return None
    cleaned = _strip_fences(text)
    data = _safe_json_load(cleaned)
    if data is None:
        return None

    raw_action = data.get("action")
    if not isinstance(raw_action, str):
        return None
    action = raw_action.strip().upper()

    if action == "SELECT":
        name = data.get("strategy")
        if not isinstance(name, str) or not name.strip():
            return None
        return {"action": "SELECT", "strategy": name.strip()}

    if action == "VIEW":
        raw_list = data.get("strategies")
        names: List[str] = []
        if isinstance(raw_list, list):
            for item in raw_list:
                if isinstance(item, str) and item.strip():
                    names.append(item.strip())
        elif isinstance(raw_list, str) and raw_list.strip():
            names.append(raw_list.strip())
        else:
            single = data.get("strategy")
            if isinstance(single, str) and single.strip():
                names.append(single.strip())
        if not names:
            return None
        return {"action": "VIEW", "strategies": names}

    if action == "GENERATE":
        content = data.get("strategy") or data.get("content") or data.get("strategy_content")
        if not isinstance(content, str) or not content.strip():
            return None
        return {"action": "GENERATE", "strategy": content.strip()}

    return None


# 保留旧 API 名称以便外部依旧能 import（薄包装）。
def parse_strategy_selection(
    text: str,
    valid_names: List[str],
) -> Optional[str]:
    """旧 API 兼容：仅解析 ``SELECT``，且校验是否在合法列表中。"""
    action = parse_initial_action(text)
    if not action or action["action"] != "SELECT":
        return None
    name = action["strategy"]
    if name in valid_names:
        return name
    lower_map = {n.lower(): n for n in valid_names}
    return lower_map.get(name.lower())


# ======================================================================
# 每 60 秒 — 阶段评估与焦点指导
# ======================================================================


def build_phase_assessment_messages(
    race: str,
    obs_text: str,
    instruct: str,
    strategy_name: str,
    strategy_description: str,
    *,
    enable_phase_guidance: bool = False,
    phase_guidance_text: str = "",
) -> List[Dict[str, str]]:
    """构建 60 秒轮询的阶段评估 LLM 消息列表。

    :param race:                  当前种族。
    :param obs_text:              当前观测文本。
    :param instruct:              玩家指令。
    :param strategy_name:         t=0 选定的策略名。
    :param strategy_description:  策略的完整描述（Top_agent_0.md 或 GENERATE 内容）。
    :param enable_phase_guidance: 是否启用 ``[Phase Guidance]`` 区块的拼接。
    :param phase_guidance_text:   ``Top_agent_60.md`` 文件内容（专属或 generic）。
    """
    blocks: List[str] = [
        f"You are monitoring an ongoing StarCraft II game as the {race.capitalize()} race.",
        "",
        f"Selected strategy: **{strategy_name}**",
        "Strategy overview:",
        strategy_description,
        "",
        "Based on the current observation, determine:",
        "1. The current game phase: \"early\", \"mid\", or \"late\".",
        "2. A concise paragraph describing what the player should focus on RIGHT NOW.",
        "   * All your planning must revolve solely around macro operations (building structures and training units). You do not need to plan for scouting or other micro/tactical maneuvers.",
    ]

    if enable_phase_guidance and phase_guidance_text and phase_guidance_text.strip():
        blocks.extend([
            "",
            "[Phase Guidance]",
            phase_guidance_text.strip(),
        ])

    blocks.extend([
        "",
        "Output ONLY a JSON object with this exact schema:",
        "  {\"phase\": \"<early|mid|late>\", \"focus\": \"<concise focus description>\"}",
        "",
        "Do not output anything outside the JSON object.",
    ])

    system_msg = "\n".join(blocks)

    user_parts = [f"[Current Observation]\n{obs_text}"]
    if instruct:
        user_parts.append(f"[Player Instruction]\n{instruct}")

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_phase_assessment(text: str) -> Optional[Dict[str, str]]:
    """解析阶段评估输出 ``{"phase": "...", "focus": "..."}``。"""
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
# Top_agent_0.md 摘要/详细内容 解析
# ======================================================================

# 同时兼容 ``# 摘要`` / ``# Summary`` / ``# Abstract``
_SUMMARY_HEADER_RE = re.compile(r"^\s*#\s*(?:摘要|Summary|Abstract)\s*$", re.MULTILINE | re.IGNORECASE)
# 同时兼容 ``# 详细内容`` / ``# Detail`` / ``# Details``
_DETAIL_HEADER_RE = re.compile(r"^\s*#\s*(?:详细内容|Detail|Details|Full|Content)\s*$", re.MULTILINE | re.IGNORECASE)


def parse_top_agent_0_md(text: str) -> Dict[str, str]:
    """将 ``Top_agent_0.md`` 的原文拆为 ``{"summary", "detail"}``。

    解析规则（按优先级递降）：
    1. 同时找到 ``# 摘要`` 与 ``# 详细内容`` 两个一级标题，按标题位置切分。
    2. 只找到其中一个标题：另一字段按 fallback 处理（detail 缺省 = 原文，summary 缺省
       = 取 detail 的首段去掉 markdown 标题）。
    3. 没有任何匹配：summary = detail = 原文（首段裁剪后作为 summary）。
    """
    if not text:
        return {"summary": "", "detail": ""}
    raw = text.strip()

    summary_match = _SUMMARY_HEADER_RE.search(raw)
    detail_match = _DETAIL_HEADER_RE.search(raw)

    summary = ""
    detail = ""

    if summary_match and detail_match:
        if summary_match.start() < detail_match.start():
            summary = raw[summary_match.end():detail_match.start()].strip()
            detail = raw[detail_match.end():].strip()
        else:
            detail = raw[detail_match.end():summary_match.start()].strip()
            summary = raw[summary_match.end():].strip()
    elif summary_match:
        summary = raw[summary_match.end():].strip()
        detail = raw  # 没有 detail 标题时退而取整文
    elif detail_match:
        detail = raw[detail_match.end():].strip()
        summary = _fallback_summary_from_detail(detail)
    else:
        detail = raw
        summary = _fallback_summary_from_detail(detail)

    return {"summary": summary, "detail": detail}


def _fallback_summary_from_detail(detail: str) -> str:
    """从详细内容里抽出一段（首个非空段或首句）作为兜底摘要。"""
    if not detail:
        return ""
    for paragraph in detail.split("\n\n"):
        line = paragraph.strip()
        if not line:
            continue
        # 去掉首行的 markdown 标题/列表前缀
        line = re.sub(r"^[#>*\-\+\s]+", "", line).strip()
        if line:
            return line[:500]
    return detail.strip()[:500]


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
