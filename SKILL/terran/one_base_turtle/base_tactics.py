from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *
from sharpy.plans import BuildOrder
from sharpy.plans.sequential_list import SequentialList
from sharpy.plans.build_step import Step
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *


class OneBaseTurtleTactics(SequentialList):
    """单矿憋兵防守战术列表"""
    def __init__(self, attack_value: int = 4, required_marines: int = 18):
        super().__init__(
            [
                *BuildOrder([]).depots,
                Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                ManTheBunkers(),
                LowerDepots(),
                PlanZoneDefense(),
                CallMule(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                Repair(),
                ContinueBuilding(),
                PlanZoneGatherTerran(),
                # 等有足够的机枪兵保护坦克时才进攻
                Step(UnitExists(UnitTypeId.MARINE, required_marines, include_killed=True), PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ]
        )
