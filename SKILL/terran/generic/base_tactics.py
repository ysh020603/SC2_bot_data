"""Generic Terran fallback tactics.

当 LLM 在 t=0 触发 ``GENERATE`` 动态生成全新策略时，``UniversalLLMBot`` 会硬编码地
把该文件拷贝到 ``SKILL/terran/<新策略名>/base_tactics.py``。

实现目标：提供一套与种族强相关、但与具体战术意图无关的兜底 ``BuildOrder``（并行执行），
保证 Sharpy 的资源调度（侦察、修理、扩矿、防守、推进、终结）始终可用，不会因为
缺失 base_tactics 导致游戏卡死或基础逻辑缺位。
"""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, Time, UnitReady
from sharpy.plans.tactics import *  # noqa: F401,F403
from sharpy.plans.tactics.terran import *  # noqa: F401,F403
from sharpy.plans.acts import *  # noqa: F401,F403
from sharpy.plans.acts.terran import *  # noqa: F401,F403


class GenericTerranTactics(BuildOrder):
    """种族通用兜底战术（并行执行）：与具体策略名无关，可被新生成策略直接复用。"""

    def __init__(self, attack_value: int = 60):
        super().__init__(
            [
                # AutoDepot(),
                Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                PlanZoneDefense(),
                Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(None, CallMule(50), skip=Time(5 * 60)),
                Step(None, CallMule(100), skip_until=Time(5 * 60)),
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ManTheBunkers(),
                Repair(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                PlanZoneAttack(attack_value),
                PlanFinishEnemy(),
            ]
        )
