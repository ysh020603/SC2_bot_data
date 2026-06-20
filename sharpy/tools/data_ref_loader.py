import json
import os
from functools import lru_cache
from typing import Dict, Optional

from sc2.dicts.generic_redirect_abilities import GENERIC_REDIRECT_ABILITIES


DEFAULT_DATA_REF_PATH = os.path.join("data_ref", "data_base_add_graph.json")

SEMANTIC_TARGET_TYPES = ("Build", "BuildOnUnit", "BuildInstant", "Train", "Research", "Morph")

ALLOWED_BASE_MORPH_ABILITIES = frozenset(
    {
        "UPGRADETOORBITAL_ORBITALCOMMAND",
        "UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS",
    }
)


def _build_generic_representatives() -> Dict[str, str]:
    """Map generic ability names to one concrete ability for semantic lookup."""
    representatives: Dict[str, str] = {}
    for specific_id, generic_id in GENERIC_REDIRECT_ABILITIES.items():
        generic_name = generic_id.name
        if generic_name not in representatives:
            representatives[generic_name] = specific_id.name
    return representatives


GENERIC_REPRESENTATIVES = _build_generic_representatives()

ADDON_ABILITY_HOSTS = {
    "BUILD_TECHLAB": {
        "BARRACKS": "BUILD_TECHLAB_BARRACKS",
        "BARRACKSFLYING": "BUILD_TECHLAB_BARRACKS",
        "FACTORY": "BUILD_TECHLAB_FACTORY",
        "FACTORYFLYING": "BUILD_TECHLAB_FACTORY",
        "STARPORT": "BUILD_TECHLAB_STARPORT",
        "STARPORTFLYING": "BUILD_TECHLAB_STARPORT",
    },
    "BUILD_REACTOR": {
        "BARRACKS": "BUILD_REACTOR_BARRACKS",
        "BARRACKSFLYING": "BUILD_REACTOR_BARRACKS",
        "FACTORY": "BUILD_REACTOR_FACTORY",
        "FACTORYFLYING": "BUILD_REACTOR_FACTORY",
        "STARPORT": "BUILD_REACTOR_STARPORT",
        "STARPORTFLYING": "BUILD_REACTOR_STARPORT",
    },
}


class DataRefLoader:
    """Loads ability definitions from data_ref and resolves ability name lookups."""

    def __init__(self, path: str = DEFAULT_DATA_REF_PATH) -> None:
        self.path = path
        self._abilities_by_name: Dict[str, dict] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return

        if not os.path.isfile(self.path):
            raise FileNotFoundError(f"data_ref file not found: {self.path}")

        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        for ability in data.get("Ability", []):
            name = ability.get("name")
            if name:
                self._abilities_by_name[name] = ability

        self._loaded = True

    def _semantic_from_entry(self, entry: dict) -> Optional[dict]:
        target = entry.get("target")
        if not isinstance(target, dict):
            return None
        for key in SEMANTIC_TARGET_TYPES:
            if key in target:
                return {"type": key, **target[key]}
        return None

    def resolve(self, ability_name: str) -> Optional[dict]:
        self.load()
        ability = self._abilities_by_name.get(ability_name)
        if ability is None:
            return None

        remap = ability.get("remaps_to_ability_name")
        if remap:
            return self._abilities_by_name.get(remap, ability)
        return ability

    def get_entry(self, ability_name: str) -> Optional[dict]:
        self.load()
        return self._abilities_by_name.get(ability_name)

    def has_ability(self, ability_name: str) -> bool:
        self.load()
        return ability_name in self._abilities_by_name

    def get_semantic_target(self, ability_name: str) -> Optional[dict]:
        entry = self.resolve(ability_name)
        if entry is not None:
            semantic = self._semantic_from_entry(entry)
            if semantic is not None:
                return semantic

        representative = GENERIC_REPRESENTATIVES.get(ability_name)
        if representative and representative != ability_name:
            return self.get_semantic_target(representative)

        if ability_name.startswith("RESEARCH_"):
            return {"type": "Research", "upgrade_name": ability_name[len("RESEARCH_") :]}

        return None

    def is_semantic_macro_action(self, ability_name: str) -> bool:
        return self.should_record_in_sequence(ability_name)

    def should_record_in_sequence(self, ability_name: str) -> bool:
        semantic = self.get_semantic_target(ability_name)
        if semantic is None:
            return False
        if semantic["type"] == "Morph":
            return ability_name in ALLOWED_BASE_MORPH_ABILITIES
        return True

    def resolve_recorded_ability_name(self, ability_name: str, target: Optional[object] = None) -> str:
        if target is not None and hasattr(target, "type_id"):
            host_name = target.type_id.name
            host_map = ADDON_ABILITY_HOSTS.get(ability_name)
            if host_map and host_name in host_map:
                return host_map[host_name]
        return ability_name


@lru_cache(maxsize=1)
def get_data_ref_loader(path: str = DEFAULT_DATA_REF_PATH) -> DataRefLoader:
    loader = DataRefLoader(path)
    loader.load()
    return loader
