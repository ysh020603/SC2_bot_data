"""Temporary launcher for the battle_cruisers addon/placement test."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_vs_ai


def short_match_id(**kw):
    return f"{kw['timestamp']}_bc_addonfix"


run_vs_ai.build_match_id = short_match_id

run_vs_ai.play_vs_ai(
    map_name="KairosJunctionLE",
    enemy_race="terran",
    enemy_difficulty="medium",
    enemy_build="air",
    mid_model="DeepSeek-V4-flash",
    down_model="DeepSeek-V4-flash",
    naming_model="DeepSeek-V4-flash",
    ordering_model="DeepSeek-V4-flash",
    executor_model="DeepSeek-V4-flash",
    batch_name="battle_cruisers_test_addonfix",
    output_base_dir=r"C:\code\SC2_Agent_OLD\game_records",
    skip_version_update=True,
    force_strategy="battle_cruisers",
)
