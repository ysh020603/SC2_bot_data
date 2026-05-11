from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.sequential_list import SequentialList
from sharpy.plans.build_step import Step
from sharpy.plans.require import Time, UnitExists
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *
from sharpy.plans.acts import *
from sharpy.plans.acts.terran import *

class TerranBaseTactics(SequentialList):
    def __init__(self, bot):
        super().__init__([
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
            Step(None, bot.attack), 
            PlanFinishEnemy(),
        ])