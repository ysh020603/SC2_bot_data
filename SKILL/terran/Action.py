from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *

# 定义内部的动作映射表
# 结构为: "action_key": {"description": "动作描述", "action_func": 生成对应Sharpy Action的函数}
# 描述中去除了所有数量词，仅保留纯粹的动作语义
# *args 和 **kwargs 允许在生成Action时传入具体数量或参数（如空着则使用Sharpy自带的默认值）
_ACTION_REGISTRY = {
    # ==========================
    # 1. 训练单位 (Train Units)
    # ==========================
    "train_scv": {
        "description": "Train SCV from Command Center",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, *args, **kw)
    },
    "train_marine": {
        "description": "Train Marine from Barracks",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, *args, **kw)
    },
    "train_siege_tank": {
        "description": "Train Siege Tank from Factory",
        "action_func": lambda *args, **kw: ActUnit(UnitTypeId.SIEGETANK, UnitTypeId.FACTORY, *args, **kw)
    },

    # ==========================
    # 2. 建造建筑 (Build Structures)
    # ==========================
    "build_supply_depot": {
        "description": "Build Supply Depot",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.SUPPLYDEPOT, *args, **kw)
    },
    "build_barracks": {
        "description": "Build Barracks",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.BARRACKS, *args, **kw)
    },
    "build_factory": {
        "description": "Build Factory",
        "action_func": lambda *args, **kw: GridBuilding(UnitTypeId.FACTORY, *args, **kw)
    },
    "build_gas": {
        "description": "Build Refinery for gas",
        "action_func": lambda *args, **kw: BuildGas(*args, **kw)
    },
    "expand": {
        "description": "Expand to new base",
        "action_func": lambda *args, **kw: Expand(*args, **kw)
    },

    # ==========================
    # 3. 附属建筑 (Build Addons)
    # ==========================
    "build_barracks_techlab": {
        "description": "Build TechLab on Barracks",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS, *args, **kw)
    },
    "build_barracks_reactor": {
        "description": "Build Reactor on Barracks",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS, *args, **kw)
    },
    "build_factory_techlab": {
        "description": "Build TechLab on Factory",
        "action_func": lambda *args, **kw: BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY, *args, **kw)
    },

    # ==========================
    # 4. 科技与升级 (Tech & Upgrades)
    # ==========================
    "research_shieldwall": {
        "description": "Research Combat Shield (Shield Wall) for Marines",
        "action_func": lambda *args, **kw: Tech(UpgradeId.SHIELDWALL, *args, **kw)
    },
    # "morph_orbital_command": {
    #     "description": "Morph Command Center into Orbital Command",
    #     "action_func": lambda *args, **kw: MorphOrbitals(*args, **kw)
    # },

    # ==========================
    # 5. 战术与控制 (Tactics & Control)
    # ==========================
    # "worker_scout": {
    #     "description": "Send worker to scout",
    #     "action_func": lambda *args, **kw: WorkerScout(*args, **kw)
    # },
    # "zone_attack": {
    #     "description": "Initiate zone attack (pushing towards enemy)",
    #     "action_func": lambda *args, **kw: PlanZoneAttack(*args, **kw)
    # },
    # "zone_defense": {
    #     "description": "Defend home zones",
    #     "action_func": lambda *args, **kw: PlanZoneDefense(*args, **kw)
    # },
    # "scan_enemy": {
    #     "description": "Use Orbital Command to scan enemy",
    #     "action_func": lambda *args, **kw: ScanEnemy(*args, **kw)
    # },
    # "call_mule": {
    #     "description": "Drop MULE for extra income",
    #     "action_func": lambda *args, **kw: CallMule(*args, **kw)
    # },
    # "lower_depots": {
    #     "description": "Lower supply depots to allow unit passing",
    #     "action_func": lambda *args, **kw: LowerDepots(*args, **kw)
    # },
    # "distribute_workers": {
    #     "description": "Balance workers across bases and gas",
    #     "action_func": lambda *args, **kw: DistributeWorkers(*args, **kw)
    # },
    # "speed_mining": {
    #     "description": "Enable speed mining logic for SCVs",
    #     "action_func": lambda *args, **kw: SpeedMining(*args, **kw)
    # },
    # "repair": {
    #     "description": "Repair damaged buildings or mech units",
    #     "action_func": lambda *args, **kw: Repair(*args, **kw)
    # },
    # "mine_open_blocked_base": {
    #     "description": "Mine out rocks blocking bases",
    #     "action_func": lambda *args, **kw: MineOpenBlockedBase(*args, **kw)
    # },
    # "zone_gather_terran": {
    #     "description": "Gather army at rally point",
    #     "action_func": lambda *args, **kw: PlanZoneGatherTerran(*args, **kw)
    # },
    # "finish_enemy": {
    #     "description": "Hunt down remaining enemy buildings",
    #     "action_func": lambda *args, **kw: PlanFinishEnemy(*args, **kw)
    # }
}


def get_action_space() -> dict:
    """
    功能一：返回动作空间，里面是 action_key : description
    用于展示给上层决策模块或大语言模型，让它们知道当前可以执行哪些动作。
    
    Returns:
        dict: 例如 {"train_marine": "Train Marine from Barracks", ...}
    """
    return {key: value["description"] for key, value in _ACTION_REGISTRY.items()}


def get_action(action_key: str, *args, **kwargs):
    """
    功能二：输入一个 action key，返回对应的 Sharpy 函数实例对象。
    后面的参数可以通过 *args 传进来（比如想明确造几个、第几个基地）。
    如果空着不传参数，就会默认调用基础的动作。
    
    Args:
        action_key (str): 在 ACTION_REGISTRY 中定义的 key
        *args: 传递给底层 sharpy Act 的位置参数 (如数量)
        **kwargs: 传递给底层 sharpy Act 的命名参数
        
    Returns:
        实例化后的 Sharpy Act 类，例如 ActUnit, GridBuilding 等。
    """
    if action_key not in _ACTION_REGISTRY:
        raise ValueError(f"Action key '{action_key}' not found in action space.")
    
    return _ACTION_REGISTRY[action_key]["action_func"](*args, **kwargs)