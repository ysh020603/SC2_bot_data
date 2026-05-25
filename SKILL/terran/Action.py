"""人族动作空间 (Action Space).

定义 LLM 唯一被允许产出的动作 key（dict.keys() of ``_ACTION_REGISTRY``），并把每个
key 翻译成具体的 Sharpy ``ActBase`` 实例。

设计意图与 ``llm_bot.py`` 的状态机协同：

* LLM 输出的格式严格限制为 ``{"action": <key>, "to_count": n}``，**目标导向（Goal-Oriented）**。
* 上层 Bot 维护动作历史序列，每个动作持有一个 lazy 实例化的 Sharpy Act；动态运营层
  每帧 ``await act.execute()`` 让 Sharpy 自动调度资源。
* 动作历史不会再根据完成条件出队；是否继续产生实际指令交给对应 Sharpy Act 的内部逻辑。

新增 API：

* ``get_action_space() -> Dict[str, str]`` —— 原有；返回 ``key: description``。
* ``get_action(key, *args, **kwargs)`` —— 原有；按 key 生成 Sharpy Act 实例。
* ``get_action_type(key) -> str`` —— 标记 ``unit`` / ``building`` / ``tech`` 等粗粒度类型。
"""

from typing import Dict

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


_ACTION_REGISTRY: Dict[str, Dict] = {
    # ==========================
    # 1. 训练单位 (Train Units)
    # ==========================
    "train_scv": {
        "description": "Train SCV from Command Center",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, *args, **kw),
    },
    "train_marine": {
        "description": "Train Marine from Barracks",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, *args, **kw),
    },
    "train_marauder": {
        "description": "Train Marauder from Barracks (requires TechLab)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.MARAUDER, UnitTypeId.BARRACKS, *args, **kw),
    },
    "train_reaper": {
        "description": "Train Reaper from Barracks",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.REAPER, UnitTypeId.BARRACKS, *args, **kw),
    },
    "train_ghost": {
        "description": "Train Ghost from Barracks (requires TechLab and Ghost Academy)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.GHOST, UnitTypeId.BARRACKS, *args, **kw),
    },
    "build_nuke": {
        "description": "Build Nuke at Ghost Academy",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.NUKE, UnitTypeId.GHOSTACADEMY, *args, **kw),
    },
    "train_hellion": {
        "description": "Train Hellion from Factory",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.HELLION, UnitTypeId.FACTORY, *args, **kw),
    },
    "train_hellbat": {
        "description": "Train Hellbat from Factory (requires Armory)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.HELLIONTANK, UnitTypeId.FACTORY, *args, **kw),
    },
    "train_widow_mine": {
        "description": "Train Widow Mine from Factory",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.WIDOWMINE, UnitTypeId.FACTORY, *args, **kw),
    },
    "train_cyclone": {
        "description": "Train Cyclone from Factory",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.CYCLONE, UnitTypeId.FACTORY, *args, **kw),
    },
    "train_siege_tank": {
        "description": "Train Siege Tank from Factory (requires TechLab)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.SIEGETANK, UnitTypeId.FACTORY, *args, **kw),
    },
    "train_thor": {
        "description": "Train Thor from Factory (requires TechLab and Armory)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.THOR, UnitTypeId.FACTORY, *args, **kw),
    },
    "train_viking": {
        "description": "Train Viking from Starport",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.VIKINGFIGHTER, UnitTypeId.STARPORT, *args, **kw),
    },
    "train_medivac": {
        "description": "Train Medivac from Starport",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.MEDIVAC, UnitTypeId.STARPORT, *args, **kw),
    },
    "train_liberator": {
        "description": "Train Liberator from Starport",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.LIBERATOR, UnitTypeId.STARPORT, *args, **kw),
    },
    "train_raven": {
        "description": "Train Raven from Starport (requires TechLab)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.RAVEN, UnitTypeId.STARPORT, *args, **kw),
    },
    "train_banshee": {
        "description": "Train Banshee from Starport (requires TechLab)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.BANSHEE, UnitTypeId.STARPORT, *args, **kw),
    },
    "train_battlecruiser": {
        "description": "Train Battlecruiser from Starport (requires TechLab and Fusion Core)",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.BATTLECRUISER, UnitTypeId.STARPORT, *args, **kw),
    },

    # ==========================
    # 2. 建造建筑 (Build Structures)
    # ==========================
    "build_supply_depot": {
        "description": "Build Supply Depot",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.SUPPLYDEPOT, *args, **kw),
    },
    "build_barracks": {
        "description": "Build Barracks",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.BARRACKS, *args, **kw),
    },
    "build_factory": {
        "description": "Build Factory",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.FACTORY, *args, **kw),
    },
    "build_starport": {
        "description": "Build Starport",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.STARPORT, *args, **kw),
    },
    "build_gas": {
        "description": "Build Refinery for gas",
        "type": "building",
        "action_func": lambda *args, **kw: BuildGas(*args, **kw),
    },
    "expand": {
        "description": "Expand to new base (Command Center)",
        "type": "building",
        "action_func": lambda *args, **kw: Expand(*args, **kw),
    },
    "build_engineering_bay": {
        "description": "Build Engineering Bay for infantry upgrades and turrets",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.ENGINEERINGBAY, *args, **kw),
    },
    "build_armory": {
        "description": "Build Armory for vehicle/ship upgrades and Thors",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.ARMORY, *args, **kw),
    },
    "build_ghost_academy": {
        "description": "Build Ghost Academy for Ghosts and nukes",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.GHOSTACADEMY, *args, **kw),
    },
    "build_fusion_core": {
        "description": "Build Fusion Core for Battlecruisers and advanced upgrades",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.FUSIONCORE, *args, **kw),
    },
    "build_bunker": {
        "description": "Build Defensive Bunker",
        "type": "building",
        # DefensiveBuilding(unit, position, to_base_index, to_count)；get_action 只传 to_count
        "action_func": lambda *args, **kw: DefensiveBuilding(
            UnitTypeId.BUNKER, DefensePosition.Entrance, None, args[0] if args else 1
        ),
    },
    "build_missile_turret": {
        "description": "Build Missile Turret for anti-air and detection",
        "type": "building",
        "action_func": lambda *args, **kw: DefensiveBuilding(
            UnitTypeId.MISSILETURRET, DefensePosition.Entrance, None, args[0] if args else 1
        ),
    },
    "build_sensor_tower": {
        "description": "Build Sensor Tower",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.SENSORTOWER, *args, **kw),
    },

    # ==========================
    # 3. 附属建筑 (Build Addons)
    # ==========================
    "build_barracks_techlab": {
        "description": "Build TechLab on Barracks",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, *args, **kw),
    },
    "build_barracks_reactor": {
        "description": "Build Reactor on Barracks",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, *args, **kw),
    },
    "build_factory_techlab": {
        "description": "Build TechLab on Factory",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, *args, **kw),
    },
    "build_factory_reactor": {
        "description": "Build Reactor on Factory",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY, *args, **kw),
    },
    "build_starport_techlab": {
        "description": "Build TechLab on Starport",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT, *args, **kw),
    },
    "build_starport_reactor": {
        "description": "Build Reactor on Starport",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT, *args, **kw),
    },

    # ==========================
    # 4. 科技与升级 (Tech & Upgrades)
    # ==========================
    "research_shieldwall": {
        "description": "Research Combat Shield (Shield Wall) for Marines",
        "type": "tech",
        # Tech() 只接受 (UpgradeId, from_building=None)；这里吞掉来自 get_action() 的 to_count
        # 等 positional/keyword 占位参数，避免把 to_count 当成 from_building 传入。
        "action_func": lambda *args, **kw: Tech(UpgradeId.SHIELDWALL),
    },
    "research_stimpack": {
        "description": "Research Stimpack for Marines and Marauders",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.STIMPACK),
    },
    "research_concussive_shells": {
        "description": "Research Concussive Shells for Marauders",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.PUNISHERGRENADES),
    },
    "research_personal_cloaking": {
        "description": "Research Personal Cloaking for Ghosts",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.PERSONALCLOAKING),
    },
    "research_infernal_preigniter": {
        "description": "Research Infernal Pre-igniter for Hellions/Hellbats",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.HIGHCAPACITYBARRELS),
    },
    "research_drilling_claws": {
        "description": "Research Drilling Claws for Widow Mines",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.DRILLCLAWS),
    },
    "research_magfield_accelerator": {
        "description": "Research Mag-Field Accelerator for Cyclones",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.MAGFIELDLAUNCHERS),
    },
    "research_smart_servos": {
        "description": "Research Smart Servos for transforming units (Thor, Hellbat, Viking)",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.SMARTSERVOS),
    },
    "research_banshee_cloak": {
        "description": "Research Cloaking Field for Banshees",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.BANSHEECLOAK),
    },
    "research_banshee_speed": {
        "description": "Research Hyperflight Rotors for Banshees",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.BANSHEESPEED),
    },
    "research_raven_corvid_reactor": {
        "description": "Research Corvid Reactor for Ravens",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.RAVENCORVIDREACTOR),
    },
    "research_liberator_range": {
        "description": "Research Advanced Ballistics for Liberators",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.LIBERATORAGRANGEUPGRADE),
    },
    "research_yamato_cannon": {
        "description": "Research Yamato Cannon for Battlecruisers (requires Starport TechLab and Fusion Core)",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.BATTLECRUISERENABLESPECIALIZATIONS),
    },
    "research_hisec_auto_tracking": {
        "description": "Research Hi-Sec Auto Tracking (Turret Range)",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.HISECAUTOTRACKING),
    },
    "research_neosteel_armor": {
        "description": "Research Neosteel Armor (Building Armor)",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANBUILDINGARMOR),
    },
    "research_infantry_weapons_1": {
        "description": "Upgrade Infantry Weapons Level 1",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1),
    },
    "research_infantry_weapons_2": {
        "description": "Upgrade Infantry Weapons Level 2",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL2),
    },
    "research_infantry_weapons_3": {
        "description": "Upgrade Infantry Weapons Level 3",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANINFANTRYWEAPONSLEVEL3),
    },
    "research_infantry_armor_1": {
        "description": "Upgrade Infantry Armor Level 1",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL1),
    },
    "research_infantry_armor_2": {
        "description": "Upgrade Infantry Armor Level 2",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL2),
    },
    "research_infantry_armor_3": {
        "description": "Upgrade Infantry Armor Level 3",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANINFANTRYARMORSLEVEL3),
    },
    "research_vehicle_weapons_1": {
        "description": "Upgrade Vehicle Weapons Level 1",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL1),
    },
    "research_vehicle_weapons_2": {
        "description": "Upgrade Vehicle Weapons Level 2",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL2),
    },
    "research_vehicle_weapons_3": {
        "description": "Upgrade Vehicle Weapons Level 3",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANVEHICLEWEAPONSLEVEL3),
    },
    "research_ship_weapons_1": {
        "description": "Upgrade Ship Weapons Level 1",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL1),
    },
    "research_ship_weapons_2": {
        "description": "Upgrade Ship Weapons Level 2",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL2),
    },
    "research_ship_weapons_3": {
        "description": "Upgrade Ship Weapons Level 3",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANSHIPWEAPONSLEVEL3),
    },
    "research_vehicle_and_ship_armor_1": {
        "description": "Upgrade Vehicle and Ship Armor Level 1",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1),
    },
    "research_vehicle_and_ship_armor_2": {
        "description": "Upgrade Vehicle and Ship Armor Level 2",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2),
    },
    "research_vehicle_and_ship_armor_3": {
        "description": "Upgrade Vehicle and Ship Armor Level 3",
        "type": "tech",
        "action_func": lambda *args, **kw: Tech(UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL3),
    },
    "morph_orbital_command": {
        "description": "Morph Command Center into Orbital Command",
        "type": "building",
        "action_func": lambda *args: MorphOrbitals(*args),
    },
    "morph_planetary_fortress": {
        "description": "Morph Command Center into Planetary Fortress (requires Engineering Bay)",
        "type": "building",
        "action_func": lambda *args: MorphPlanetary(*args),
    },
}


def get_action_space() -> Dict[str, str]:
    """返回 ``{action_key: description}`` 字典，作为提供给 LLM 的合法动作清单。

    LLM 的输出必须严格属于这些 key；任何 key 之外的动作都被视为非法 JSON，调用
    侧将拒绝并触发重试（或回退到 ``none``）。
    """
    return {key: value["description"] for key, value in _ACTION_REGISTRY.items()}


def get_action(action_key: str, *args, **kwargs):
    """实例化 action_key 对应的 Sharpy Act 对象，转交动态运营层调度。"""
    if action_key not in _ACTION_REGISTRY:
        raise ValueError(f"Action key '{action_key}' not found in action space.")
    return _ACTION_REGISTRY[action_key]["action_func"](*args, **kwargs)


def get_action_type(action_key: str) -> str:
    """返回粗粒度动作类型 ``unit`` / ``building`` / ``tech``，未知则返回 ``unknown``。"""
    if action_key not in _ACTION_REGISTRY:
        return "unknown"
    return _ACTION_REGISTRY[action_key].get("type", "unknown")


__all__ = [
    "get_action_space",
    "get_action",
    "get_action_type",
]
