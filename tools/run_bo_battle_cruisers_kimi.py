"""BO list battle_cruisers experiment with Kimi-k2.5 executor (no thinking)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_vs_ai


def short_match_id(**kwargs):
    return f"{kwargs['timestamp']}_bc_kimi_veryhard"


def main() -> None:
    os.environ.setdefault("SC2_GAME_TIME_LIMIT", "1200")
    run_vs_ai.build_match_id = short_match_id
    run_vs_ai.play_vs_ai(
        bo_list="battle_cruisers",
        executor_model="Kimi-k2.5",
        enemy_difficulty="veryhard",
        batch_name="bo_battle_cruisers_kimi_veryhard",
        skip_version_update=True,
    )


if __name__ == "__main__":
    main()
