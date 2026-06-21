"""
SC2 Action -> Product Mapper

Loads data_base_add_graph.json and maps ability names to the unit / structure /
upgrade they produce, using the `action_result` relation.

Usage:
    # As a module
    from action_mapper import ActionMapper
    m = ActionMapper()
    print(m.get_product("BARRACKSTRAIN_MARINE"))  # -> "Marine"

    # From CLI
    python action_mapper.py BARRACKSTRAIN_MARINE TRAIN_CYCLONE
    python action_mapper.py --all-bo      # all 40 BO actions
    python action_mapper.py --all-other   # all 26 other-list actions
    python action_mapper.py --table       # full table
"""

import json
import os
import sys
from collections import OrderedDict


class ActionMapper:
    """Maps SC2 ability names to the thing they produce."""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "data_base_add_graph.json",
            )
        self.db_path = os.path.normpath(db_path)
        self._ability_to_product = {}
        self._ability_to_required = {}
        self._product_to_ability = {}
        self._product_info = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        with open(self.db_path, "r", encoding="utf-8") as f:
            db = json.load(f)

        for ability in db.get("Ability", []):
            name = ability["name"]
            for rel in ability.get("relations", []):
                if rel["relation"] == "action_result":
                    product = rel["object_name"]
                    self._ability_to_product[name] = product
                    self._product_to_ability[product] = name
                elif rel["relation"] == "ability_requires_unit":
                    self._ability_to_required[name] = rel["object_name"]

        for unit in db.get("Unit", []):
            n = unit["name"]
            self._product_info[n] = {
                "type": "Unit",
                "race": unit.get("race", ""),
                "minerals": unit.get("minerals", 0),
                "gas": unit.get("gas", 0),
                "time": unit.get("time", 0),
                "supply": unit.get("supply", 0),
                "is_structure": unit.get("is_structure", False),
                "is_worker": unit.get("is_worker", False),
                "is_addon": unit.get("is_addon", False),
            }
        for upg in db.get("Upgrade", []):
            n = upg["name"]
            cost = upg.get("cost", {})
            self._product_info[n] = {
                "type": "Upgrade",
                "race": upg.get("race", ""),
                "minerals": cost.get("minerals", 0),
                "gas": cost.get("gas", 0),
                "time": cost.get("time", 0),
                "supply": 0,
                "is_structure": False,
                "is_worker": False,
                "is_addon": False,
            }

        self._loaded = True

    def get_product(self, ability_name):
        self._load()
        if ability_name in self._ability_to_product:
            return self._ability_to_product[ability_name]
        parts = ability_name.rsplit("_", 1)
        if len(parts) == 2 and parts[1].upper() == parts[1]:
            return parts[1]
        return "(no product)"

    def get_required_structure(self, ability_name):
        self._load()
        return self._ability_to_required.get(ability_name, "")

    def get_product_info(self, ability_name):
        self._load()
        product = self.get_product(ability_name)
        if product == "(no product)":
            return None
        return self._product_info.get(product)

    def has_product(self, ability_name):
        self._load()
        return ability_name in self._ability_to_product

    def get_ability_for_product(self, product_name):
        self._load()
        return self._product_to_ability.get(product_name, "")

    def report(self, actions):
        self._load()
        lines = []
        max_ab = max(len(a) for a in actions) if actions else 20
        header = f"{'ACTION':<{max_ab}}  {'PRODUCT':<28} {'TYPE':<8} {'COST':>14} {'REQ':<20}"
        lines.append(header)
        lines.append("-" * len(header))
        for ab in actions:
            product = self.get_product(ab)
            info = self._product_info.get(product, {})
            ptype = info.get("type", "-")
            mins = info.get("minerals", 0)
            gas = info.get("gas", 0)
            cost = f"{mins}/{gas}" if (mins or gas) else "-"
            req = self.get_required_structure(ab) or "-"
            lines.append(
                f"{ab:<{max_ab}}  {product:<28} {ptype:<8} {cost:>14} {req:<20}"
            )
        return "\n".join(lines)


BO_ACTIONS = [
    "BARRACKSTECHLABRESEARCH_STIMPACK", "BARRACKSTRAIN_MARAUDER",
    "BARRACKSTRAIN_MARINE", "BARRACKSTRAIN_REAPER",
    "BUILD_REACTOR_BARRACKS", "BUILD_REACTOR_FACTORY",
    "BUILD_REACTOR_STARPORT", "BUILD_TECHLAB_BARRACKS",
    "BUILD_TECHLAB_FACTORY", "BUILD_TECHLAB_STARPORT",
    "COMMANDCENTERTRAIN_SCV", "FACTORYTRAIN_HELLION",
    "FACTORYTRAIN_SIEGETANK", "RESEARCH_COMBATSHIELD",
    "RESEARCH_CONCUSSIVESHELLS", "RESEARCH_CYCLONELOCKONDAMAGE",
    "RESEARCH_INFERNALPREIGNITER", "RESEARCH_TERRANINFANTRYARMOR",
    "RESEARCH_TERRANINFANTRYWEAPONS", "RESEARCH_TERRANVEHICLEWEAPONS",
    "STARPORTTRAIN_BANSHEE", "STARPORTTRAIN_BATTLECRUISER",
    "STARPORTTRAIN_LIBERATOR", "STARPORTTRAIN_MEDIVAC",
    "STARPORTTRAIN_RAVEN", "STARPORTTRAIN_VIKINGFIGHTER",
    "TERRANBUILD_ARMORY", "TERRANBUILD_BARRACKS",
    "TERRANBUILD_BUNKER", "TERRANBUILD_COMMANDCENTER",
    "TERRANBUILD_ENGINEERINGBAY", "TERRANBUILD_FACTORY",
    "TERRANBUILD_FUSIONCORE", "TERRANBUILD_MISSILETURRET",
    "TERRANBUILD_REFINERY", "TERRANBUILD_STARPORT",
    "TERRANBUILD_SUPPLYDEPOT", "TRAIN_CYCLONE",
    "UPGRADETOORBITAL_ORBITALCOMMAND",
    "UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS",
]

OTHER_ACTIONS = [
    "ATTACK", "CALLDOWNMULE_CALLDOWNMULE", "CANCEL_BUILDINPROGRESS",
    "EFFECT_ANTIARMORMISSILE", "EFFECT_INTERFERENCEMATRIX",
    "EFFECT_REPAIR", "EFFECT_STIM_MARAUDER", "EFFECT_STIM_MARINE",
    "EFFECT_TACTICALJUMP", "HARVEST_GATHER", "KD8CHARGE_KD8CHARGE",
    "LAND", "LIFT", "MORPH_LIBERATORAAMODE", "MORPH_LIBERATORAGMODE",
    "MORPH_SUPPLYDEPOT_LOWER", "MORPH_SUPPLYDEPOT_RAISE",
    "MORPH_VIKINGASSAULTMODE", "MORPH_VIKINGFIGHTERMODE",
    "MOVE_MOVE", "RALLY_BUILDING", "SCANNERSWEEP_SCAN",
    "SIEGEMODE_SIEGEMODE", "SMART", "STOP", "UNSIEGE_UNSIEGE",
]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SC2 Action -> Product Mapper")
    parser.add_argument("actions", nargs="*", help="One or more ability names")
    parser.add_argument("--all-bo", action="store_true", help="Report all 40 BO actions")
    parser.add_argument("--all-other", action="store_true", help="Report all 26 other-list actions")
    parser.add_argument("--table", action="store_true", help="Full table (BO + other)")
    args = parser.parse_args()

    mapper = ActionMapper()

    actions_to_report = []
    if args.all_bo or args.table:
        actions_to_report.extend(BO_ACTIONS)
    if args.all_other or args.table:
        actions_to_report.extend(OTHER_ACTIONS)
    if args.actions:
        actions_to_report = args.actions

    if not actions_to_report:
        parser.print_help()
        return

    print()
    print(mapper.report(actions_to_report))
    print()


if __name__ == "__main__":
    main()
