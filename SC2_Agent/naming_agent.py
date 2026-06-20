"""Naming Agent (阶段2) Prompt 构建与解析。

把策略 step + 当前 obs 转换成 ``data_base_add_graph.json`` 的人族标准 Unit/Upgrade
名 + 数量。只允许使用提供的 canonical 名单；升级类数量恒为 1。
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
    obs_text: str = "",
    strategy_summary: str = "",
) -> List[Dict[str, str]]:
    """构建阶段2 命名 Agent Prompt。

    :param race:                当前种族名（固定 terran）。
    :param plan_text:           策略文件中的单个 step 文本（整段话）。
    :param terran_unit_names:   人族 canonical Unit 名单。
    :param terran_upgrade_names:人族 canonical Upgrade 名单。
    :param strategy_summary:    策略 ``# Summary`` 全文，作为宏观指导注入 system prompt。
    """
    race_cap = race.capitalize()
    units_text = ", ".join(terran_unit_names)
    upgrades_text = ", ".join(terran_upgrade_names)
    summary_text = strategy_summary.strip() or "(none)"

    system_msg = f"""You read one natural-language {race_cap} strategy step plus the current game
observation. Extract the concrete structure / unit / upgrade INCREMENTS that
should be issued now, as canonical entity names with counts, using ONLY names
from the Canonical Name List below.

[Strategy Summary]
{summary_text}

The Strategy Summary describes the overall game plan (composition, timings,
late-game direction). Use it as macro guidance to interpret the current step,
but the [Strategy Step] in the user message remains the authoritative source
of what to issue this cycle.

Rules:
* Read the step and the observation together. The step is the strategic
  requirement; the observation shows what already exists or is in progress.
  Enumerate the missing increments needed to satisfy this step now.
* The step may mix precise quantities (e.g. "build 3 Barracks") with vague
  language (e.g. "a few", "some", "enough", "more", "ramp up", "mass",
  "small batch"). In both cases output a reasonable concrete count for each
  increment, grounded in the observation (existing entities, in-progress
  production, supply, economy) and in what makes sense for a single scheduler
  cycle. If the step mentions the same entity more than once, sum the counts.
* Do NOT skip a requested entity just because the step says it happens "after",
  "when", "once", or "if" another prerequisite is ready. Output the requested
  entity anyway; the downstream scheduler will wait for prerequisites.
* Output only exact names from the Canonical Units / Upgrades lists below;
  if you cannot confidently map a request to one of those names, omit it.
* Upgrades / researches ALWAYS have count 1.
* For add-ons, use the host-specific canonical name, e.g. BarracksTechLab,
  not generic TechLab.

[Name Hints: Jargon and Upgrade Categories]
These hints help interpret strategy language. They do not expand the legal
output names. Every output name must still exactly match one name in the
Canonical Units or Canonical Upgrades lists below.

Common jargon:
- rax -> Barracks
- ebay -> EngineeringBay
- cc -> CommandCenter
- depot -> SupplyDepot
- mule economy -> OrbitalCommand
- blue flame -> HighCapacityBarrels
- stim -> Stimpack
- combat shield -> ShieldWall
- concussive shells -> PunisherGrenades
- yamato -> BattlecruiserEnableSpecializations
- advanced ballistics -> LiberatorAGRangeUpgrade
- building armor -> TerranBuildingArmor
- bio -> usually Marine, Marauder, Medivac, plus infantry upgrades when
  explicitly requested.
- mech -> usually Factory units and vehicle upgrades when explicitly requested.
- sky Terran -> usually Starport units and ship upgrades when explicitly
  requested.

Do not output a whole composition from a general term alone. Use general terms
only to interpret concrete requests in the Strategy Step.

Upgrade categories:
- Infantry upgrades: Stimpack, ShieldWall, PunisherGrenades,
  TerranInfantryWeaponsLevel1/2/3, TerranInfantryArmorsLevel1/2/3. These
  improve Marine/Marauder bio timing, durability, and damage.
- Vehicle/mech upgrades: TerranVehicleWeaponsLevel1/2/3,
  TerranVehicleArmorsLevel1/2/3, SmartServos, DrillClaws, HighCapacityBarrels,
  Cyclone upgrades. These support Factory-based mech armies and unit-specific
  power spikes.
- Air/ship upgrades: TerranShipWeaponsLevel1/2/3,
  TerranShipArmorsLevel1/2/3, BansheeCloak, BansheeSpeed,
  LiberatorAGRangeUpgrade, BattlecruiserEnableSpecializations. These improve
  Starport units, air control, harassment, and late-game air tech.
- Shared vehicle/ship upgrades: TerranVehicleAndShipWeaponsLevel1/2/3,
  TerranVehicleAndShipArmorsLevel1/2/3. These are broad Armory upgrades for
  mixed mech and air armies.
- Building/defensive upgrades: HiSecAutoTracking, TerranBuildingArmor,
  NeosteelFrame. These improve static defense, building durability, or Terran
  structure utility.
- Specialist tech upgrades: PersonalCloaking, RavenCorvidReactor,
  RavenEnhancedMunitions, RavenRecalibratedExplosives, Medivac upgrades. These
  unlock or improve spellcaster, support, and utility behavior.

[Canonical {race_cap} Units]
{units_text}

[Canonical {race_cap} Upgrades]
{upgrades_text}

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
