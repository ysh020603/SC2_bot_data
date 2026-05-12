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
    "train_siege_tank": {
        "description": "Train Siege Tank from Factory",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.SIEGETANK, UnitTypeId.FACTORY, *args, **kw),
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
    "build_gas": {
        "description": "Build Refinery for gas",
        "type": "building",
        "action_func": lambda *args, **kw: BuildGas(*args, **kw),
    },
    "expand": {
        "description": "Expand to new base",
        "type": "building",
        "action_func": lambda *args, **kw: Expand(*args, **kw),
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
    "morph_orbital_command": {
        "description": "Morph Command Center into Orbital Command",
        "type": "building",
        "action_func": lambda *args: MorphOrbitals(*args),
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
