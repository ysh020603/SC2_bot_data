"""Experiment: marine_rush with SUPPLY_MANAGED=False (LLM places supply depots)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_vs_ai
from dummies.generic.universal_llm_bot import UniversalLLMBot

UniversalLLMBot.SUPPLY_MANAGED = False

RUN_INDEX = int(os.environ.get("RUN_INDEX", "1"))


def short_match_id(**kw):
    return f"{kw['timestamp']}_mr_no_managed_r{RUN_INDEX}"


run_vs_ai.build_match_id = short_match_id

run_vs_ai.play_vs_ai(
    map_name="KairosJunctionLE",
    enemy_race="terran",
    enemy_difficulty="hard",
    enemy_build="air",
    mid_model="DeepSeek-V4-flash",
    down_model="DeepSeek-V4-flash",
    naming_model="DeepSeek-V4-flash",
    ordering_model="DeepSeek-V4-flash",
    executor_model="DeepSeek-V4-flash",
    batch_name="marine_rush_no_supply_managed",
    output_base_dir=r"C:\code\SC2_Agent_OLD\game_records",
    skip_version_update=True,
    force_strategy="marine_rush",
)
