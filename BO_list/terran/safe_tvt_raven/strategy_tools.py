"""Resource-free tool package for the safe_tvt_raven strategy."""

from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans import BuildOrder, SequentialList
from sharpy.plans.acts import MineOpenBlockedBase
from sharpy.plans.build_step import Step
from sharpy.plans.require import TechReady
from sharpy.plans.tactics import (
    PlanFinishEnemy,
    PlanWorkerOnlyDefense,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, ContinueBuilding, LowerDepots


class SafeTvTRavenStrategyTools(BuildOrder):
    """TvT raven tools that do not spend minerals, gas, or supply."""

    def __init__(self, attack_value: int = 4):
        super().__init__(
            SequentialList([
                CallMule(50),
                LowerDepots(),
                MineOpenBlockedBase(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                ContinueBuilding(),
                PlanZoneGather(),
                PlanWorkerOnlyDefense(),
                PlanZoneDefense(),
                Step(TechReady(UpgradeId.STIMPACK), PlanZoneAttack(attack_value)),
                PlanFinishEnemy(),
            ])
        )
