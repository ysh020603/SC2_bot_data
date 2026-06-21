"""
SC2 Action -> Product mapping tool.

Reads ``data_base_add_graph.json`` at runtime and resolves every action name
against the ``action_result`` relation in the Ability table.  Pattern-based
extraction and a small explicit-fallback map handle abilities that lack
``action_result`` (e.g. EFFECT_*, movement/control actions).

Usage::

    from action_products import action_to_product
    product = action_to_product("TERRANBUILD_SUPPLYDEPOT")  # -> "SupplyDepot"
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# DB loading (lazy, cached)
# ---------------------------------------------------------------------------
_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data_base_add_graph.json",
)
_DB_PATH = os.path.normpath(_DB_PATH)

_ability_to_product: Dict[str, str] = {}
_db_loaded = False


def _load_db() -> None:
    """Lazy-load data_base_add_graph.json and build ability->product dict."""
    global _ability_to_product, _db_loaded
    if _db_loaded:
        return
    try:
        with open(_DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        _db_loaded = True
        return

    for ability in db.get("Ability", []):
        name = ability["name"]
        for rel in ability.get("relations", []):
            if rel["relation"] == "action_result":
                _ability_to_product[name] = rel["object_name"]
    _db_loaded = True


# ---------------------------------------------------------------------------
# Pattern-based extraction (fallback for abilities w/o action_result)
# PATTERN order matters: put most-specific patterns first.
# ---------------------------------------------------------------------------

_MORPH_TARGETS = [
    "SUPPLYDEPOT", "COMMANDCENTER", "ORBITALCOMMAND",
    "LIBERATOR", "VIKING", "BANSHEE", "BATTLECRUISER",
    "SIEGETANK", "HELLION", "CYCLONE", "RAVEN",
    "BARRACKS", "FACTORY", "STARPORT",
]


def _title_case(name: str) -> str:
    """UPPERCASE_SNAKE -> Title Case."""
    parts = name.split("_")
    return " ".join(p.title() for p in parts)


def _extract_product(action: str) -> Optional[str]:
    """Try to extract a meaningful product from an action name via patterns.

    This is a fallback – the DB lookup is always tried first.
    """
    # MORPH_<Target><Mode>  or  MORPH_<Target>_<Mode>
    if action.startswith("MORPH_"):
        after = action[len("MORPH_"):]
        for target in sorted(_MORPH_TARGETS, key=len, reverse=True):
            if after.upper().startswith(target):
                rest = after[len(target):]
                mode = rest[1:] if rest.startswith("_") else rest
                if mode:
                    return f"{_title_case(target)} ({_title_case(mode)} mode)"
                return _title_case(target)
        m = re.match(r"^(.+)_(.+)$", after)
        if m:
            return f"{_title_case(m.group(1))} ({_title_case(m.group(2))} mode)"

    # EFFECT_<Name>
    if action.startswith("EFFECT_"):
        after = action[len("EFFECT_"):]
        return _title_case(after)

    # CALLDOWNMULE_CALLDOWNMULE  ->  MULE
    if action.startswith("CALLDOWNMULE_"):
        return "MULE"

    # SCANNERSWEEP_SCAN  ->  ScannerSweep
    if action.startswith("SCANNERSWEEP_"):
        return "ScannerSweep"

    # KD8CHARGE_KD8CHARGE  ->  KD8Charge
    if action.startswith("KD8CHARGE_"):
        return "KD8Charge"

    # HARVEST_GATHER  ->  Gather
    if action.startswith("HARVEST_"):
        after = action[len("HARVEST_"):]
        return _title_case(after)

    # RALLY_<Target>  ->  RallyPoint
    if action.startswith("RALLY_"):
        return "RallyPoint"

    # CANCEL_BUILDINPROGRESS  ->  (Cancel)
    if action.startswith("CANCEL_"):
        return "(Cancel)"

    # Generic: *BUILD_YYY  (catches TERRANBUILD_*, BUILD_REACTOR_*, BUILD_TECHLAB_*)
    m = re.match(r"^.+BUILD_(.+)$", action)
    if m:
        return _title_case(m.group(1))

    # Generic: *TRAIN_YYY  (catches BARRACKSTRAIN_*, COMMANDCENTERTRAIN_*, etc.)
    m = re.match(r"^.*TRAIN_(.+)$", action)
    if m:
        return _title_case(m.group(1))

    # UPGRADETO..._RESULT
    m = re.match(r"^UPGRADETO.+_(.+)$", action)
    if m:
        return _title_case(m.group(1))

    # Generic: *RESEARCH_YYY
    m = re.match(r"^.*RESEARCH_(.+)$", action)
    if m:
        return m.group(1)  # keep original, often an upgrade name

    return None


# ---------------------------------------------------------------------------
# Explicit fallback – TINY map for well-known actions that have NO
# action_result in the DB AND where the pattern extraction gives a poor result.
# ---------------------------------------------------------------------------
_EXPLICIT_FALLBACK: Dict[str, str] = {
    "ATTACK":                           "(Attack)",
    "MOVE_MOVE":                        "(Move)",
    "SMART":                            "(Smart)",
    "STOP":                             "(Stop)",
    "LAND":                             "(Land)",
    "LIFT":                             "(Lift)",
    "CANCEL_BUILDINPROGRESS":           "(Cancel)",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def action_to_product(action: str) -> str:
    """Return the product name for an SC2 action.

    Resolution order:
    1. ``action_result`` relation in data_base_add_graph.json
    2. Explicit fallback map (for movement/control actions)
    3. Pattern-based extraction (MORPH_*, EFFECT_*, etc.)
    4. ``(Unknown: action)``
    """
    if not action or not isinstance(action, str):
        return "(Unknown)"

    _load_db()

    # 1. DB (primary source of truth)
    if action in _ability_to_product:
        return _ability_to_product[action]

    # 2. Explicit fallback
    if action in _EXPLICIT_FALLBACK:
        return _EXPLICIT_FALLBACK[action]

    # 3. Pattern-based extraction
    result = _extract_product(action)
    if result is not None:
        return result

    # 4. Last resort
    return f"(Unknown: {action})"


def action_to_product_batch(actions):
    """Return ``{action: product}`` dict for a list of actions."""
    return {a: action_to_product(a) for a in actions}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
_TEST_CASES = [
    # ---- from DB action_result ----
    ("BARRACKSTRAIN_MARINE",                  "Marine"),
    ("BARRACKSTRAIN_MARAUDER",                "Marauder"),
    ("BARRACKSTRAIN_REAPER",                  "Reaper"),
    ("COMMANDCENTERTRAIN_SCV",                "SCV"),
    ("FACTORYTRAIN_HELLION",                  "Hellion"),
    ("FACTORYTRAIN_SIEGETANK",                "SiegeTank"),
    ("STARPORTTRAIN_BANSHEE",                 "Banshee"),
    ("STARPORTTRAIN_BATTLECRUISER",           "Battlecruiser"),
    ("STARPORTTRAIN_LIBERATOR",               "Liberator"),
    ("STARPORTTRAIN_MEDIVAC",                 "Medivac"),
    ("STARPORTTRAIN_RAVEN",                   "Raven"),
    ("STARPORTTRAIN_VIKINGFIGHTER",           "VikingFighter"),
    ("TRAIN_CYCLONE",                         "Cyclone"),
    ("TERRANBUILD_SUPPLYDEPOT",               "SupplyDepot"),
    ("TERRANBUILD_BARRACKS",                  "Barracks"),
    ("TERRANBUILD_REFINERY",                  "Refinery"),
    ("TERRANBUILD_FACTORY",                   "Factory"),
    ("TERRANBUILD_STARPORT",                  "Starport"),
    ("TERRANBUILD_COMMANDCENTER",             "CommandCenter"),
    ("TERRANBUILD_ARMORY",                    "Armory"),
    ("TERRANBUILD_BUNKER",                    "Bunker"),
    ("TERRANBUILD_ENGINEERINGBAY",            "EngineeringBay"),
    ("TERRANBUILD_FUSIONCORE",                "FusionCore"),
    ("TERRANBUILD_MISSILETURRET",             "MissileTurret"),
    ("BUILD_REACTOR_BARRACKS",                "BarracksReactor"),
    ("BUILD_REACTOR_FACTORY",                 "FactoryReactor"),
    ("BUILD_REACTOR_STARPORT",                "StarportReactor"),
    ("BUILD_TECHLAB_BARRACKS",                "BarracksTechLab"),
    ("BUILD_TECHLAB_FACTORY",                 "FactoryTechLab"),
    ("BUILD_TECHLAB_STARPORT",                "StarportTechLab"),
    ("UPGRADETOORBITAL_ORBITALCOMMAND",       "OrbitalCommand"),
    ("UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS", "PlanetaryFortress"),
    ("RESEARCH_COMBATSHIELD",                 "ShieldWall"),
    ("RESEARCH_CONCUSSIVESHELLS",             "PunisherGrenades"),
    ("RESEARCH_CYCLONELOCKONDAMAGE",          "CycloneLockOnDamageUpgrade"),
    ("RESEARCH_INFERNALPREIGNITER",           "HighCapacityBarrels"),
    ("BARRACKSTECHLABRESEARCH_STIMPACK",      "Stimpack"),
    ("SIEGEMODE_SIEGEMODE",                   "SiegeTankSieged"),
    ("UNSIEGE_UNSIEGE",                       "SiegeTank"),
    ("MORPH_SUPPLYDEPOT_LOWER",               "SupplyDepotLowered"),
    ("MORPH_SUPPLYDEPOT_RAISE",               "SupplyDepot"),
    ("MORPH_LIBERATORAAMODE",                 "Liberator"),
    ("MORPH_LIBERATORAGMODE",                 "LiberatorAG"),
    ("MORPH_VIKINGASSAULTMODE",               "VikingAssault"),
    ("MORPH_VIKINGFIGHTERMODE",               "VikingFighter"),

    # ---- explicit fallback ----
    ("ATTACK",                                "(Attack)"),
    ("MOVE_MOVE",                             "(Move)"),
    ("SMART",                                 "(Smart)"),
    ("STOP",                                  "(Stop)"),
    ("LAND",                                  "(Land)"),
    ("LIFT",                                  "(Lift)"),
    ("CANCEL_BUILDINPROGRESS",                "(Cancel)"),

    # ---- pattern-based fallback ----
    ("EFFECT_REPAIR",                         "Repair"),
    ("EFFECT_ANTIARMORMISSILE",               "Antiarmormissile"),
    ("EFFECT_INTERFERENCEMATRIX",             "Interferencematrix"),
    ("EFFECT_TACTICALJUMP",                   "Tacticaljump"),
    ("EFFECT_STIM_MARINE",                    "Stim Marine"),
    ("EFFECT_STIM_MARAUDER",                  "Stim Marauder"),
    ("CALLDOWNMULE_CALLDOWNMULE",             "MULE"),
    ("SCANNERSWEEP_SCAN",                     "ScannerSweep"),
    ("KD8CHARGE_KD8CHARGE",                   "KD8Charge"),
    ("HARVEST_GATHER",                        "Gather"),
    ("RALLY_BUILDING",                        "RallyPoint"),

    # ---- edge: RESEARCH without action_result falls to pattern ----
    ("RESEARCH_TERRANINFANTRYARMOR",          "TERRANINFANTRYARMOR"),
    ("RESEARCH_TERRANINFANTRYWEAPONS",        "TERRANINFANTRYWEAPONS"),
    ("RESEARCH_TERRANVEHICLEWEAPONS",         "TERRANVEHICLEWEAPONS"),

    # ---- edge: truly unknown ----
    ("NONEXISTENT_ACTION",                    "(Unknown: NONEXISTENT_ACTION)"),
]


def _run_tests():
    ok = 0
    fail = 0
    for action, expected in _TEST_CASES:
        got = action_to_product(action)
        if got == expected:
            ok += 1
        else:
            print(f"  FAIL: {action:50s}  expected: {expected:<35s}  got: {got}")
            fail += 1
    print(f"Tests: {ok} passed, {fail} failed")
    return fail == 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        _run_tests()
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        all_actions = sorted({a for a, _ in _TEST_CASES})
        extras = sys.argv[2:]
        all_actions.extend(extras)
        for a in sorted(set(all_actions)):
            print(f"{action_to_product(a):40s} <-- {a}")
        sys.exit(0)
    for a in sys.argv[1:]:
        print(f"{a}  -->  {action_to_product(a)}")
    if len(sys.argv) == 1:
        _run_tests()
