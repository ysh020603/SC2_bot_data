"""Skill 库加载/解析/筛选辅助。

对应 *双段式决策流 (Two-Stage Pipeline)* 中的 **Step 2.2.1 - Skill 库加载与解析**。

文件约定（每个 ``.md`` 文件）::

    # Skill_Title_A
    skill description...
    可以多行 / 可以包含 ``##`` 等子标题（仅一级 ``# `` 标题用于切分）。

    # Skill_Title_B
    ...

解析后得到 ``[{"title": "Skill_Title_A", "description": "..."}, ...]``。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.skill_loader")


_H1_HEADER_RE = re.compile(r"^[ \t]*#[ \t]+(.+?)[ \t]*$", re.MULTILINE)


# ======================================================================
# Skill MD 解析
# ======================================================================


def parse_skill_md(text: str) -> List[Dict[str, str]]:
    """按一级标题（``# ``）切分 markdown，提取 ``{"title", "description"}`` 列表。

    解析规则：
    * 仅识别 ``^#\\s+...$`` 形式的一级标题（``##`` 以上视为正文内容）。
    * 若文件没有一级标题但非空，将整体视为一条匿名 Skill（title=``"_default"``）。
    * 空文本/空文件返回 ``[]``。
    * 自动去掉每条 description 前后空白；空 description 自动跳过。
    """
    if not text or not text.strip():
        return []
    raw = text.strip()

    matches = list(_H1_HEADER_RE.finditer(raw))
    if not matches:
        return [{"title": "_default", "description": raw.strip()}]

    skills: List[Dict[str, str]] = []
    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        description = raw[start:end].strip()
        if not title:
            continue
        if not description:
            continue
        skills.append({"title": title, "description": description})
    return skills


def load_skill_md_file(path: str) -> List[Dict[str, str]]:
    """读取并解析单个 markdown 文件；不存在/异常返回 ``[]``。"""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_skill_md(f.read())
    except Exception as exc:
        logger.warning("Failed to load skill MD %s: %s", path, exc)
        return []


def load_skill_library(
    *,
    race: str,
    layer: str,
    strategy_name: Optional[str],
    skill_root: str,
    enable_specific: bool = True,
) -> Dict[str, List[Dict[str, str]]]:
    """加载某一层（``top_60`` / ``mid``）的 Generic + Specific Skill 库。

    :param race:           种族名（小写）。
    :param layer:          ``"top_60"`` 或 ``"mid"``。
    :param strategy_name:  当前 t=0 选定的策略文件夹名；``None`` 或不存在时只加载 Generic。
    :param skill_root:     ``SKILL/`` 根目录绝对路径。
    :param enable_specific: 若为 ``False``，强制忽略 Specific（用于消融实验
                            ``--disable_specific_skills_layers``）。
    :return:               ``{"generic": [...], "specific": [...]}``。
    """
    filename = "Top_agent_60.md" if layer == "top_60" else "mid_agent.md"
    race_dir = os.path.join(skill_root, race)
    generic_path = os.path.join(race_dir, "generic", filename)

    generic = load_skill_md_file(generic_path)

    specific: List[Dict[str, str]] = []
    if enable_specific and strategy_name:
        specific_path = os.path.join(race_dir, strategy_name, filename)
        specific = load_skill_md_file(specific_path)

    return {"generic": generic, "specific": specific}


# ======================================================================
# Phase 1 — Skill 筛选 Prompt 构造 & 输出解析
# ======================================================================


def _format_skill_catalog(
    generic: List[Dict[str, str]],
    specific: List[Dict[str, str]],
) -> str:
    """把候选库渲染成可读清单（按 title 列出，附 description 预览）。"""
    sections: List[str] = []

    def render(label: str, skills: List[Dict[str, str]]) -> str:
        if not skills:
            return f"[{label}]\n  (empty)"
        lines = [f"[{label}]"]
        for s in skills:
            title = s.get("title", "").strip() or "(unnamed)"
            desc = s.get("description", "").strip()
            if len(desc) > 480:
                desc = desc[:480].rstrip() + " ..."
            lines.append(f"  - title: {title}")
            lines.append(f"    description: {desc}")
        return "\n".join(lines)

    sections.append(render("Generic Skills", generic))
    sections.append(render("Strategy-Specific Skills", specific))
    return "\n\n".join(sections)


def build_skill_selection_messages(
    *,
    race: str,
    layer: str,
    obs_text: str,
    instruct: str,
    strategy_name: str,
    strategy_description: str,
    phase: str,
    focus: str,
    generic_skills: List[Dict[str, str]],
    specific_skills: List[Dict[str, str]],
    max_selection: int = 5,
) -> List[Dict[str, str]]:
    """构造 *Phase 1 — Skill Selection* 的 LLM messages。

    要求 LLM 仅输出 JSON 形如 ``{"selected": ["title_a", "title_b"]}``，
    其中 title 必须来自给定候选清单。
    """
    layer_label = "Top Agent (60s phase assessment)" if layer == "top_60" else "Mid Agent (macro planning)"

    catalog = _format_skill_catalog(generic_skills, specific_skills)

    system_lines = [
        f"You are an assistant that pre-selects the most relevant tactical skills for the "
        f"{layer_label} of a StarCraft II {race.capitalize()} bot.",
        "",
        "Decision context:",
        f"* Currently selected strategy: {strategy_name or '(none)'}",
    ]
    if strategy_description:
        system_lines.extend([
            "* Strategy overview:",
            strategy_description.strip(),
        ])
    if phase:
        system_lines.append(f"* Commander-assessed game phase: {phase}")
    if focus:
        system_lines.append(f"* Commander focus directive: {focus}")
    if instruct:
        system_lines.append(f"* Player instruction: {instruct}")

    system_lines.extend([
        "",
        "Below is the candidate skill catalog. You MUST pick AT MOST "
        f"{max_selection} skills whose Title strings appear in this catalog.",
        "Prefer skills that are directly applicable to the current observation and ",
        "strategy. If none of the skills fit, return an empty list.",
        "",
        catalog,
        "",
        "Output ONLY a single JSON object with this exact schema:",
        '  {"selected": ["<title_1>", "<title_2>"]}',
        "Do not include any text outside the JSON object.",
        "Do not wrap the JSON in markdown fences.",
    ])

    user_lines = [f"[Current Observation]\n{obs_text}" if obs_text else "[Current Observation]\n(unavailable)"]
    if instruct:
        user_lines.append(f"[Player Instruction]\n{instruct}")

    return [
        {"role": "system", "content": "\n".join(system_lines)},
        {"role": "user", "content": "\n\n".join(user_lines)},
    ]


def parse_skill_selection(
    text: str,
    *,
    valid_titles: List[str],
    max_selection: int = 5,
) -> List[str]:
    """解析 Phase 1 LLM 输出，返回合法的 Title 列表（按出现顺序、去重、截断到上限）。"""
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    data: Optional[Dict[str, Any]] = None
    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, dict):
            data = loaded
        elif isinstance(loaded, list):
            data = {"selected": loaded}
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                loaded = json.loads(match.group(0))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = None

    if not isinstance(data, dict):
        return []

    raw_list = data.get("selected") or data.get("skills") or data.get("titles") or []
    if not isinstance(raw_list, list):
        return []

    lower_map: Dict[str, str] = {t.lower(): t for t in valid_titles}
    seen: set = set()
    result: List[str] = []
    for item in raw_list:
        if not isinstance(item, str):
            continue
        key = item.strip()
        if not key:
            continue
        resolved = lower_map.get(key.lower())
        if resolved is None:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
        if len(result) >= max_selection:
            break
    return result


# ======================================================================
# 拼接选中 Skill 描述（供 Phase 2 Prompt 使用）
# ======================================================================


def render_selected_skills_block(
    selected_titles: List[str],
    *,
    generic_skills: List[Dict[str, str]],
    specific_skills: List[Dict[str, str]],
    heading: str = "Current Strategic Constraints",
) -> str:
    """根据 selected_titles 从候选库中按出现顺序取出对应 description 并拼接成块。

    返回串包含一个引导标题（默认 ``Current Strategic Constraints``），便于在 Phase 2
    Prompt 中作为约束注入。若 ``selected_titles`` 为空则返回空串。
    """
    if not selected_titles:
        return ""
    index: Dict[str, str] = {}
    for s in list(generic_skills) + list(specific_skills):
        title = s.get("title", "").strip()
        if title and title not in index:
            index[title] = s.get("description", "").strip()

    blocks: List[str] = []
    for title in selected_titles:
        desc = index.get(title)
        if not desc:
            continue
        blocks.append(f"### {title}\n{desc}")
    if not blocks:
        return ""
    return f"[{heading}]\n" + "\n\n".join(blocks)
