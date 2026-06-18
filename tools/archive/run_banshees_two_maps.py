"""Temporary launcher: banshees on AutomatonLE + TritonLE, pro naming/ordering, no supply."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_vs_ai
from dummies.generic.universal_llm_bot import UniversalLLMBot

UniversalLLMBot.SUPPLY_MANAGED = False

_MAP_TAG = {
    "AutomatonLE": "auto",
    "TritonLE": "trit",
}


def short_match_id(**kw):
    tag = _MAP_TAG.get(kw.get("map_name", ""), "map")
    return f"{kw['timestamp']}_bs_{tag}_pro_nosupply"


run_vs_ai.build_match_id = short_match_id

_COMMON = dict(
    enemy_race="terran",
    enemy_difficulty="medium",
    enemy_build="air",
    mid_model="DeepSeek-V4-flash",
    down_model="DeepSeek-V4-flash",
    naming_model="DeepSeek-V4-pro",
    ordering_model="DeepSeek-V4-pro",
    executor_model="DeepSeek-V4-flash",
    batch_name="banshees_pro_name_order_nosupply_medium",
    output_base_dir=r"C:\code\SC2_Agent_OLD\game_records",
    skip_version_update=True,
    force_strategy="banshees",
)

for _map in ("AutomatonLE", "TritonLE"):
    run_vs_ai.play_vs_ai(map_name=_map, **_COMMON)
