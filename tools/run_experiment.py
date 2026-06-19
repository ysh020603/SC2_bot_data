"""Generic SC2 Agent experiment launcher.

Examples:
    python tools/run_experiment.py --strategy marine_rush --batch-name smoke
    python tools/run_experiment.py --strategy battle_cruisers --game-time-limit 1200
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_GAME_TIME_LIMIT = 20 * 60  # seconds; default test match length

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_vs_ai
from dummies.generic.universal_llm_bot import UniversalLLMBot


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one UniversalLLMBot match with explicit experiment parameters.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--strategy", required=True, help="Strategy folder under SKILL/<race>/")
    parser.add_argument("--batch-name", default="", help="Folder under game_records/")
    parser.add_argument("--match-prefix", default="", help="Optional short match-id suffix prefix")
    parser.add_argument("--map-name", default="KairosJunctionLE")
    parser.add_argument("--bot-race", default="terran")
    parser.add_argument("--enemy-race", default="terran")
    parser.add_argument("--enemy-difficulty", default="medium")
    parser.add_argument("--enemy-build", default="random")
    parser.add_argument("--naming-model", default=run_vs_ai.DEFAULT_NAMING_MODEL)
    parser.add_argument("--ordering-model", default=run_vs_ai.DEFAULT_ORDERING_MODEL)
    parser.add_argument("--executor-model", default=run_vs_ai.DEFAULT_EXECUTOR_MODEL)
    parser.add_argument("--supply-managed", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--real-time", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--game-time-limit",
        type=int,
        default=DEFAULT_GAME_TIME_LIMIT,
        help="SC2_GAME_TIME_LIMIT in seconds",
    )
    parser.add_argument("--run-index", type=int, default=None)
    parser.add_argument("--skip-version-update", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(argv)


def _install_short_match_id(prefix: str) -> None:
    if not prefix:
        return

    def short_match_id(**kwargs):
        run_index = kwargs.get("run_index")
        suffix = f"_run{run_index}" if run_index is not None else ""
        return f"{kwargs['timestamp']}_{prefix}{suffix}"

    run_vs_ai.build_match_id = short_match_id


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(argv)

    os.environ["SC2_GAME_TIME_LIMIT"] = str(args.game_time_limit)

    UniversalLLMBot.SUPPLY_MANAGED = bool(args.supply_managed)
    _install_short_match_id(args.match_prefix)

    run_vs_ai.play_vs_ai(
        map_name=args.map_name,
        real_time=args.real_time,
        enemy_race=args.enemy_race,
        enemy_difficulty=args.enemy_difficulty,
        enemy_build=args.enemy_build,
        bot_race=args.bot_race,
        naming_model=args.naming_model,
        ordering_model=args.ordering_model,
        executor_model=args.executor_model,
        batch_name=args.batch_name or None,
        run_index=args.run_index,
        output_base_dir=str(ROOT / "game_records"),
        skip_version_update=args.skip_version_update,
        force_strategy=args.strategy,
    )


if __name__ == "__main__":
    main()
