"""人族动作空间 (Action Space).

定义 LLM 唯一被允许产出的动作 key（dict.keys() of ``_ACTION_REGISTRY``），并把每个
key 翻译成具体的 Sharpy ``ActBase`` 实例 + 一个"任务是否完成"的判定函数。

设计意图与 ``llm_bot.py`` 的状态机协同：

* LLM 输出的格式严格限制为 ``{"action": <key>, "to_count": n}``，**目标导向（Goal-Oriented）**。
* 上层 Bot 维护 ``self.active_tasks: List[Dict]``，每个任务持有一个 lazy 实例化的 Sharpy
  Act；动态运营层每帧 ``await act.execute()`` 让 Sharpy 自动调度资源。
* 但 Sharpy 各 Act 的 "is_done" 语义不一致：``ActUnit`` 在数量达标才返回 True、``Tech``
  在研究**启动**就返回 True 等。所以这里为每个 entry 额外提供 ``is_done_func``，让上层
  用统一的"实际数量 >= to_count"语义来移除 active_tasks。

新增 API：

* ``get_action_space() -> Dict[str, str]`` —— 原有；返回 ``key: description``。
* ``get_action(key, *args, **kwargs)`` —— 原有；按 key 生成 Sharpy Act 实例。
* ``get_action_type(key) -> str`` —— 新增；标记 ``unit`` / ``building`` / ``tech`` 等粗粒度
  类型，方便上层做统一计数。
* ``is_task_done(key, to_count, ai) -> bool`` —— 新增；脱离 Act 实例本身直接基于 ai
  状态判断，避免对 Act 内部细节的耦合。
"""

from typing import Callable, Dict

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


def _count_units_owned(ai, unit_type: UnitTypeId) -> int:
    """统计某 unit_type 的现存数量（已就绪 + 在产/在建，工人取 supply_workers 兜底）。"""
    direct = ai.units(unit_type).amount + ai.structures(unit_type).amount

    if unit_type == UnitTypeId.SCV:
        direct = max(direct, int(ai.supply_workers))

    pending = 0
    try:
        pending = int(ai.already_pending(unit_type))
    except Exception:
        pending = 0

    return direct + pending


def _count_structures_owned(ai, unit_type: UnitTypeId) -> int:
    """统计建筑数量（已建好 + 正在建）。"""
    in_progress_or_done = ai.structures(unit_type).amount
    pending = 0
    try:
        pending = int(ai.already_pending(unit_type))
    except Exception:
        pending = 0
    return max(in_progress_or_done, pending)


def _is_upgrade_done(ai, upgrade_type: UpgradeId) -> bool:
    """升级是否完成（不计算研究中）。"""
    try:
        return upgrade_type in ai.state.upgrades
    except Exception:
        return False


_ACTION_REGISTRY: Dict[str, Dict] = {
    # ==========================
    # 1. 训练单位 (Train Units)
    # ==========================
    "train_scv": {
        "description": "Train SCV from Command Center",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_units_owned(ai, UnitTypeId.SCV) >= to_count,
    },
    "train_marine": {
        "description": "Train Marine from Barracks",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_units_owned(ai, UnitTypeId.MARINE) >= to_count,
    },
    "train_siege_tank": {
        "description": "Train Siege Tank from Factory",
        "type": "unit",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.SIEGETANK, UnitTypeId.FACTORY, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_units_owned(ai, UnitTypeId.SIEGETANK) >= to_count,
    },

    # ==========================
    # 2. 建造建筑 (Build Structures)
    # ==========================
    "build_supply_depot": {
        "description": "Build Supply Depot",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.SUPPLYDEPOT, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.SUPPLYDEPOT) >= to_count,
    },
    "build_barracks": {
        "description": "Build Barracks",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.BARRACKS, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.BARRACKS) >= to_count,
    },
    "build_factory": {
        "description": "Build Factory",
        "type": "building",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.FACTORY, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.FACTORY) >= to_count,
    },
    "build_gas": {
        "description": "Build Refinery for gas",
        "type": "building",
        "action_func": lambda *args, **kw: BuildGas(*args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.REFINERY) >= to_count,
    },
    "expand": {
        "description": "Expand to new base",
        "type": "building",
        "action_func": lambda *args, **kw: Expand(*args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.COMMANDCENTER) >= to_count,
    },

    # ==========================
    # 3. 附属建筑 (Build Addons)
    # ==========================
    "build_barracks_techlab": {
        "description": "Build TechLab on Barracks",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.BARRACKSTECHLAB) >= to_count,
    },
    "build_barracks_reactor": {
        "description": "Build Reactor on Barracks",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.BARRACKSREACTOR) >= to_count,
    },
    "build_factory_techlab": {
        "description": "Build TechLab on Factory",
        "type": "building",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, *args, **kw),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.FACTORYTECHLAB) >= to_count,
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
        "is_done_func": lambda ai, to_count: _is_upgrade_done(ai, UpgradeId.SHIELDWALL),
    },
    "morph_orbital_command": {
        "description": "Morph Command Center into Orbital Command",
        "type": "building",
        "action_func": lambda *args: MorphOrbitals(*args),
        "is_done_func": lambda ai, to_count: _count_structures_owned(ai, UnitTypeId.ORBITALCOMMAND) >= to_count,
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


def is_task_done(action_key: str, to_count: int, ai) -> bool:
    """判定一个 (action_key, to_count) 任务是否已经达成 to_count 目标。

    采用纯 ai 状态查询（不依赖 Act 实例的内部状态），让 LLM Bot 在任意时刻都能
    一致性地判断任务是否该出列。
    """
    entry = _ACTION_REGISTRY.get(action_key)
    if entry is None:
        return False

    is_done_func: Callable = entry.get("is_done_func")
    if is_done_func is None:
        return False

    try:
        return bool(is_done_func(ai, to_count))
    except Exception:
        return False


__all__ = [
    "get_action_space",
    "get_action",
    "get_action_type",
    "is_task_done",
]
