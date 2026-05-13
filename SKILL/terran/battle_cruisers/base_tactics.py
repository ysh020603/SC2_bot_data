import random
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId

# 确保引入了所有的动作类 (解决 MineOpenBlockedBase 找不到的问题)
from sharpy.plans.acts import ActBase
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.sequential_list import SequentialList
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, Time, RequireCustom
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


class JumpIn(ActBase):
    """大和战列舰折跃至敌方矿区后方的动作逻辑"""
    def __init__(self):
        super().__init__()
        self.done = False

    async def execute(self) -> bool:
        if self.done:
            return True
        bcs = self.cache.own(UnitTypeId.BATTLECRUISER)
        if bcs.amount > 1:
            self.done = True
            for bc in bcs:
                # 记录折跃技能已使用
                self.knowledge.cooldown_manager.used_ability(bc.tag, AbilityId.EFFECT_TACTICALJUMP)
                # 向敌方主基地矿后中心点折跃
                bc(AbilityId.EFFECT_TACTICALJUMP, self.zone_manager.enemy_main_zone.behind_mineral_position_center)
        return True


class BattleCruisersTactics(SequentialList):
    def __init__(self, attack_value: int = None, jump_index: int = 1):
        """
        大和战术列表
        :param attack_value: 触发攻击的单位价值阈值。如果未提供，将随机生成(50~80)的合理值。
        :param jump_index: 是否执行折跃的索引，默认为 1 (执行折跃)，0 为不折跃。
        """
        # 合理处理随机数据：外部不传参时，赋予原先合理的随机默认值
        if attack_value is None:
            attack_value = random.randint(50, 80)

        scout = Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
        
        super().__init__(
            [
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                PlanZoneDefense(),
                scout,
                # 5分钟前的矿螺调度
                Step(None, CallMule(50), skip=Time(5 * 60)),
                # 5分钟后的矿螺调度
                Step(None, CallMule(100), skip_until=Time(5 * 60)),
                # 5分钟后开始扫描敌军
                Step(None, ScanEnemy(), skip_until=Time(5 * 60)),
                DistributeWorkers(4),
                # 游戏开始后5步开始优化采矿
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ManTheBunkers(),
                Repair(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                # 根据配置决定是否进行先手折跃骚扰
                Step(None, JumpIn(), skip=RequireCustom(lambda k: jump_index == 0)),
                PlanZoneAttack(attack_value),
                PlanFinishEnemy(),
            ]
        )