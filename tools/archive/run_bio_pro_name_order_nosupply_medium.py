"""Temporary launcher: bio vs Terran medium, pro naming/ordering, no supply managed."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_vs_ai
from dummies.generic.universal_llm_bot import UniversalLLMBot

UniversalLLMBot.SUPPLY_MANAGED = False


def short_match_id(**kw):
    return f"{kw['timestamp']}_bio_pro_name_order_nosupply"


run_vs_ai.build_match_id = short_match_id

run_vs_ai.play_vs_ai(
    map_name="KairosJunctionLE",
    enemy_race="terran",
    enemy_difficulty="medium",
    enemy_build="air",
    mid_model="DeepSeek-V4-flash",
    down_model="DeepSeek-V4-flash",
    naming_model="DeepSeek-V4-pro",
    ordering_model="DeepSeek-V4-pro",
    executor_model="DeepSeek-V4-flash",
    batch_name="bio_pro_name_order_nosupply_medium",
    output_base_dir=r"C:\code\SC2_Agent_OLD\game_records",
    skip_version_update=True,
    force_strategy="bio",
)
