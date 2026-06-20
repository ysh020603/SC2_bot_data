"""One-off BO list marine_rush smoke with Kimi-k2.5 executor."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_vs_ai


def short_match_id(**kwargs):
    return f"{kwargs['timestamp']}_mr_kimi_harder"


def main() -> None:
    os.environ.setdefault("SC2_GAME_TIME_LIMIT", "1200")
    run_vs_ai.build_match_id = short_match_id
    run_vs_ai.play_vs_ai(
        bo_list="marine_rush",
        executor_model="Kimi-k2.5",
        enemy_difficulty="harder",
        batch_name="bo_marine_rush_kimi_harder",
        skip_version_update=True,
    )


if __name__ == "__main__":
    main()
