"""Temporary launcher for safe_tvt_raven vs macro AI."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_vs_ai


def short_match_id(**kw):
    return f"{kw['timestamp']}_safe_tvt_macro"


run_vs_ai.build_match_id = short_match_id

run_vs_ai.play_vs_ai(
    map_name="KairosJunctionLE",
    enemy_race="terran",
    enemy_difficulty="harder",
    enemy_build="macro",
    mid_model="DeepSeek-V4-flash",
    down_model="DeepSeek-V4-flash",
    naming_model="DeepSeek-V4-flash",
    ordering_model="DeepSeek-V4-flash",
    executor_model="DeepSeek-V4-flash",
    batch_name="safe_tvt_raven_test",
    output_base_dir=r"C:\code\SC2_Agent_OLD\game_records",
    skip_version_update=True,
    force_strategy="safe_tvt_raven",
)
