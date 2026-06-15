"""Naming Agent (阶段2) Prompt 构建与解析。

把阶段1 的自然语言增量，转换成 ``data_base_add_graph.json`` 的人族标准 Unit/Upgrade
名 + 数量。只允许使用提供的 canonical 名单；常见别名（Combat Shield -> ShieldWall 等）
需映射到标准名；升级类数量恒为 1。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SC2_Agent.naming_agent")


def build_naming_messages(
    race: str,
    increments: List[str],
    terran_unit_names: List[str],
    terran_upgrade_names: List[str],
    alias_pairs: Dict[str, str],
) -> List[Dict[str, str]]:
    """构建阶段2 命名 Agent Prompt。

    :param race:                当前种族名（固定 terran）。
    :param increments:          阶段1 输出的自然语言增量列表。
    :param terran_unit_names:   人族 canonical Unit 名单。
    :param terran_upgrade_names:人族 canonical Upgrade 名单。
    :param alias_pairs:         别名 -> canonical 名 映射（仅作提示）。
    """
    race_cap = race.capitalize()
    units_text = ", ".join(terran_unit_names)
    upgrades_text = ", ".join(terran_upgrade_names)
    alias_text = "\n".join(f"  - {k} -> {v}" for k, v in alias_pairs.items()) or "  (none)"

    system_msg = f"""You convert natural-language {race_cap} build increments into
canonical entity names with counts, using ONLY names from the Canonical Name List
below.

Rules:
* Output ONLY entities that {race_cap} can build / train / research.
* Use the EXACT canonical spelling from the lists below.
* Map common aliases to their canonical name (see Alias map).
* Upgrades / researches ALWAYS have count 1.
* Structures and add-ons: count = how many to ADD this cycle.
* Units: count = how many to ADD this cycle.
* Drop any increment you cannot confidently map to a canonical name.
* Do NOT include Supply Depots (handled automatically by the system).

[Canonical {race_cap} Units]
{units_text}

[Canonical {race_cap} Upgrades]
{upgrades_text}

[Alias map]
{alias_text}

Output ONLY one JSON object, no prose, no markdown fences:
{{"items":[{{"name":"Barracks","count":1}},{{"name":"BarracksTechLab","count":1}},{{"name":"Marine","count":4}}]}}"""

    increments_json = json.dumps(increments, ensure_ascii=False, indent=2)
    user_msg = f"[Increments]\n{increments_json}"

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


def parse_naming_response(text: str) -> Optional[List[Dict[str, Any]]]:
    """解析阶段2 输出 ``{"items": [{"name","count"}, ...]}``。

    :return: ``[{"name": str, "count": int}, ...]``，或 ``None``。
    """
    if not text:
        return None
    data = _extract_json_object(text)
    if data is None:
        logger.warning("Naming Agent output is not valid JSON: %r", text[:200])
        return None
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return None

    items: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            count = int(raw.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        if count <= 0:
            count = 1
        items.append({"name": name.strip(), "count": count})
    return items
