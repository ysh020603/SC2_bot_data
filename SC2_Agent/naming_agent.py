"""Naming Agent (阶段2) Prompt 构建与解析。

把策略 step + 当前 obs 转换成 ``data_base_add_graph.json`` 的人族标准 Unit/Upgrade
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
    plan_text: str,
    terran_unit_names: List[str],
    terran_upgrade_names: List[str],
    alias_pairs: Dict[str, str],
    obs_text: str = "",
) -> List[Dict[str, str]]:
    """构建阶段2 命名 Agent Prompt。

    :param race:                当前种族名（固定 terran）。
    :param plan_text:           策略文件中的单个 step 文本（整段话）。
    :param terran_unit_names:   人族 canonical Unit 名单。
    :param terran_upgrade_names:人族 canonical Upgrade 名单。
    :param alias_pairs:         别名 -> canonical 名 映射（仅作提示）。
    """
    race_cap = race.capitalize()
    units_text = ", ".join(terran_unit_names)
    upgrades_text = ", ".join(terran_upgrade_names)
    alias_text = "\n".join(f"  - {k} -> {v}" for k, v in alias_pairs.items()) or "  (none)"

    system_msg = f"""You read one natural-language {race_cap} strategy step plus
the current game observation. Extract the concrete structure / unit / upgrade
INCREMENTS that should be issued now, as canonical entity names with counts,
using ONLY names from the Canonical Name List below.

Rules:
* Read the whole step and the observation together. The step is the strategic
  requirement; the observation tells you what already exists or is already in
  progress.
* Enumerate the missing increments needed to satisfy this step now.
* Do NOT skip a requested entity just because the step says it happens "after",
  "when", "once", or "if" another prerequisite is ready. Output the requested
  entity anyway; the downstream scheduler will wait for prerequisites.
* Output ONLY entities that {race_cap} can build / train / research.
* Use the EXACT canonical spelling from the lists below.
* Map common aliases to their canonical name (see Alias map).
* Upgrades / researches ALWAYS have count 1.
* Structures, add-ons and units: count = how many to ADD now (sum up if the step
  mentions the same entity more than once).
* For production/build goals, do NOT output alternate state names. Use
  SupplyDepot instead of SupplyDepotLowered, Barracks/Factory/Starport instead
  of their Flying forms, CommandCenter/OrbitalCommand instead of Flying forms,
  and SiegeTank instead of SiegeTankSieged.
* For add-ons, output the host-specific canonical add-on name:
  BarracksTechLab, FactoryTechLab, StarportTechLab, BarracksReactor,
  FactoryReactor, or StarportReactor. Do not output generic TechLab/Reactor.
* If the step states a transition like "increase Marines from 6 to 12", output
  the increment count (6 Marines), unless the observation shows some of those
  Marines are already completed or in active queues.
* If the step says to morph a Command Center into Orbital Command, output
  OrbitalCommand with count 1 unless the observation already has enough
  Orbital Commands or one is actively morphing.
* If the step is open-ended or repeatable, such as "continue Marine production"
  or "toward the 200 target", do NOT output the full remaining target. Output a
  finite batch suitable for the current scheduler cycle, usually one production
  round from the available production buildings described by the step and obs.
* If the step says "keep N" or "keep 0", do not output that entity unless the
  observation shows it is below a required positive count and the step explicitly
  requires adding it.
* Drop anything you cannot confidently map to a canonical name.

[Canonical {race_cap} Units]
{units_text}

[Canonical {race_cap} Upgrades]
{upgrades_text}

[Alias map]
{alias_text}

Output ONLY one JSON object, no prose, no markdown fences:
{{"items":[{{"name":"Barracks","count":1}},{{"name":"BarracksTechLab","count":1}},{{"name":"Marine","count":4}}]}}"""

    user_msg = f"[Current Observation]\n{obs_text or '(empty)'}\n\n[Strategy Step]\n{plan_text}"

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
