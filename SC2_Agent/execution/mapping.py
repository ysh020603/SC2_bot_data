"""Map DB canonical action / entity names to python-sc2 enums and sharpy Acts.

The five-stage pipeline speaks in ``data_base_add_graph.json`` canonical names
(e.g. ``BARRACKSTRAIN_MARINE``, ``TERRANBUILD_BARRACKS``, ``ShieldWall``). The
execution layer needs the corresponding ``AbilityId`` / ``UnitTypeId`` /
``UpgradeId`` enums plus, for build/research actions, a ready-to-run sharpy Act.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from SC2_Agent.data_tools import cost_for_action

#: Categories used by the scheduler to choose an execution path.
CAT_BUILD = "build"
CAT_RESEARCH = "research"
CAT_TRAIN = "train"
CAT_ADDON = "addon"
CAT_MORPH = "morph"


def _norm(name: str) -> str:
    return name.replace("_", "").replace(" ", "").lower()


@lru_cache(maxsize=1)
def _unit_norm_index() -> dict:
    # Use __members__ (name -> member) instead of iterating the enum directly:
    # burnysc2's IntEnum iteration can yield bare ints for some entries.
    return {_norm(name): member for name, member in UnitTypeId.__members__.items()}


@lru_cache(maxsize=1)
def _upgrade_norm_index() -> dict:
    return {_norm(name): member for name, member in UpgradeId.__members__.items()}


def ability_for(action_name: str) -> Optional[AbilityId]:
    try:
        return AbilityId[action_name]
    except KeyError:
        return _ability_norm_index().get(_norm(action_name))


@lru_cache(maxsize=1)
def _ability_norm_index() -> dict:
    return {_norm(name): member for name, member in AbilityId.__members__.items()}


def unit_type_for(entity_name: str) -> Optional[UnitTypeId]:
    if not entity_name:
        return None
    return _unit_norm_index().get(_norm(entity_name))


def upgrade_for(entity_name: str) -> Optional[UpgradeId]:
    if not entity_name:
        return None
    return _upgrade_norm_index().get(_norm(entity_name))


def category_for(action_name: str) -> str:
    """Classify a canonical action name into one of the five categories."""
    upper = action_name.upper()
    if upper.startswith("BUILD_TECHLAB") or upper.startswith("BUILD_REACTOR"):
        return CAT_ADDON

    info = cost_for_action(action_name)
    target_kind = (info.get("target_kind") or "")
    if target_kind == "Train":
        return CAT_TRAIN
    if target_kind in ("Morph", "MorphPlace"):
        return CAT_MORPH
    if target_kind == "Research":
        return CAT_RESEARCH
    if target_kind in ("Build", "BuildOnUnit", "BuildInstant"):
        return CAT_BUILD

    # Fallbacks based on name shape.
    if "RESEARCH" in upper:
        return CAT_RESEARCH
    if "TRAIN" in upper:
        return CAT_TRAIN
    if upper.startswith("UPGRADETO") or upper.startswith("MORPH"):
        return CAT_MORPH
    return CAT_BUILD


def make_build_act(action_name: str, target_result: Optional[str], to_count: int):
    """Instantiate the sharpy Act that builds a structure for ``action_name``.

    Returns ``None`` if the structure type cannot be resolved.
    """
    # Lazy imports to avoid heavy sharpy import at module import time.
    from sharpy.plans.acts import BuildGas, Expand, GridBuilding

    upper = action_name.upper()
    if "REFINERY" in upper or "REFINERY" in (target_result or "").upper():
        return BuildGas(to_count)
    if upper == "TERRANBUILD_COMMANDCENTER" or (target_result or "") == "CommandCenter":
        return Expand(to_count, priority=True, consider_worker_production=False)

    unit_type = unit_type_for(target_result or "")
    if unit_type is None:
        return None
    return GridBuilding(unit_type, to_count)


def make_research_act(target_result: Optional[str]):
    """Instantiate a sharpy ``Tech`` Act for an upgrade result name."""
    from sharpy.plans.acts import Tech

    upgrade = upgrade_for(target_result or "")
    if upgrade is None:
        return None
    return Tech(upgrade)


def make_addon_act(action_name: str, to_count: int):
    """Instantiate a sharpy ``BuildAddon`` Act for a ``BUILD_TECHLAB_*`` /
    ``BUILD_REACTOR_*`` action.

    ``BuildAddon`` checks for free space to the building's right via
    ``find_placement`` BEFORE issuing the addon, so a building never lifts off
    to chase addon space (the raw ability issue used previously would make the
    structure fly, e.g. a Factory floating away when a Tech Lab is ordered with
    no room — which then breaks tech-chain prerequisites that need a *landed*
    producer). Returns ``None`` if the names cannot be resolved.
    """
    from sharpy.plans.acts.terran import BuildAddon

    parts = action_name.upper().split("_")  # e.g. BUILD_TECHLAB_FACTORY
    if len(parts) < 3:
        return None
    kind, base = parts[1], parts[2]  # TECHLAB|REACTOR , BARRACKS|FACTORY|STARPORT
    from_type = unit_type_for(base)
    addon_type = unit_type_for(base + kind)  # e.g. FactoryTechLab -> FACTORYTECHLAB
    if from_type is None or addon_type is None:
        return None
    return BuildAddon(addon_type, from_type, int(to_count))
