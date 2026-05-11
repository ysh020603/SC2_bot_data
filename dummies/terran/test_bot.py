import random

from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.interfaces import IZoneManager
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans.require import *
from sharpy.plans import BuildOrder, Step

from sharpy.knowledges import Knowledge, KnowledgeBot
from sharpy.utils import select_build_index

# 1. 引入定义好的外部战术模块
from SKILL.terran.marine_rush.base_tactics import TerranBaseTactics

# 2. 动态战术包装器：用于支持中途无缝切换战术
class DynamicTacticsWrapper(ActBase):
    def __init__(self, initial_tactics):
        super().__init__()
        self.current_tactics = initial_tactics

    async def start(self, knowledge: Knowledge):
        await super().start(knowledge)
        await self.current_tactics.start(knowledge)

    async def execute(self) -> bool:
        # 执行当前挂载的战术
        return await self.current_tactics.execute()
        
    async def switch_tactics(self, new_tactics):
        """核心功能：在游戏中途切换战术"""
        self.current_tactics = new_tactics
        # 新战术需要初始化 Knowledge 上下文
        await self.current_tactics.start(self.knowledge)


class TestBot(KnowledgeBot):
    tactic_index: int
    zone_manager: IZoneManager

    def __init__(self, build_name: str = "default"):
        super().__init__("Test Bot")
        self.build_name = build_name

    async def on_start(self):
        await super().on_start()
        self.zone_manager = self.knowledge.get_required_manager(IZoneManager)

    async def pre_step_execute(self):
        if self.tactic_index != 1 and self.time < 5 * 60:
            self.knowledge.gather_point = self.zone_manager.expansion_zones[-2].gather_point
            
        # 【演示】如何在中途动态切换战术：
        # 假设游戏进行到 10 分钟，你想切换成一个防御战术或其他引入的战术：
        # if self.time > 10 * 60 and not getattr(self, "switched", False):
        #     self.switched = True
        #     new_tactics = SomeOtherTactics(self)
        #     await self.dynamic_tactics.switch_tactics(new_tactics)
        #     self.knowledge.print("战术已中途切换！")

    async def create_plan(self) -> BuildOrder:
        if self.build_name == "default":
            self.tactic_index = select_build_index(self.knowledge, "build.marine", 0, 2)
        else:
            self.tactic_index = int(self.build_name)

        if self.tactic_index == 0:
            self.knowledge.print("Proxy 2 rax bunker rush", "Build")
            attack_marines = 3
            zone = self.zone_manager.expansion_zones[-random.randint(3, 5)]
            natural = self.zone_manager.expansion_zones[-2]
            chunk = [
                Step(Supply(12), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
                BuildPosition(UnitTypeId.BARRACKS, zone.center_location, exact=False, only_once=True),
                BuildPosition(
                    UnitTypeId.BARRACKS,
                    zone.center_location.towards(self.zone_manager.enemy_expansion_zones[0].ramp.bottom_center, 5),
                    exact=False,
                    only_once=True,
                ),
                BuildPosition(
                    UnitTypeId.BARRACKS,
                    zone.center_location.towards(self.game_info.map_center, 5),
                    exact=False,
                    only_once=True,
                ),
                Step(None, GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
                Step(
                    UnitReady(UnitTypeId.MARINE, 1),
                    BuildPosition(
                        UnitTypeId.BUNKER,
                        natural.center_location.towards(self.game_info.map_center, 4),
                        exact=False,
                        only_once=True,
                    ),
                ),
                Step(Minerals(225), GridBuilding(UnitTypeId.BARRACKS, 6)),
            ]
        elif self.tactic_index == 1:
            self.knowledge.print("20 marine all in", "Build")
            attack_marines = 20
            chunk = [
                Step(Supply(14), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 1), GridBuilding(UnitTypeId.BARRACKS, 1)),
                Step(None, GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
                GridBuilding(UnitTypeId.BARRACKS, 6),
            ]
        else:
            self.knowledge.print("10 marine proxy rax", "Build")
            attack_marines = 10
            zone = self.zone_manager.expansion_zones[-random.randint(3, 5)]
            chunk = [
                Step(Supply(14), GridBuilding(UnitTypeId.SUPPLYDEPOT, 1)),
                Step(UnitReady(UnitTypeId.SUPPLYDEPOT, 1), GridBuilding(UnitTypeId.BARRACKS, 1)),
                Step(None, GridBuilding(UnitTypeId.SUPPLYDEPOT, 2)),
                BuildPosition(UnitTypeId.BARRACKS, zone.center_location, exact=False, only_once=True),
                BuildPosition(
                    UnitTypeId.BARRACKS,
                    zone.center_location.towards(self.zone_manager.expansion_zones[-1].ramp.bottom_center, 5),
                    exact=False,
                    only_once=True,
                ),
                Step(Minerals(225), GridBuilding(UnitTypeId.BARRACKS, 6)),
            ]

        empty = BuildOrder([])

        # 3. 实例化外部战术并挂载到包装器上
        base_tactics = TerranBaseTactics(attack_marines)
        self.dynamic_tactics = DynamicTacticsWrapper(base_tactics)

        return BuildOrder(
            empty.depots,
            Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
            [Step(None, ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 20))],
            chunk,
            ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS, 200),
            self.dynamic_tactics,  # 这里不再使用死代码，而是使用我们的动态战术执行器
        )


class LadderBot(TestBot):
    @property
    def my_race(self):
        return Race.Terran