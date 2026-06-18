"""Full game experiment: two_base_tanks strategy (Top_agent_0.md)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_vs_ai


def short_match_id(**kw):
    return f"{kw['timestamp']}_tbt_full"


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
    batch_name="two_base_tanks_full",
    output_base_dir=r"C:\code\SC2_Agent_OLD\game_records",
    skip_version_update=True,
    force_strategy="two_base_tanks",
)
