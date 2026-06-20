"""Kimi non-thinking naming/ordering/executor with battle_cruisers strategy."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_vs_ai


def short_match_id(**kwargs):
    return f"{kwargs['timestamp']}_kimi_nothink_bc"


def main() -> None:
    run_vs_ai.build_match_id = short_match_id
    run_vs_ai.play_vs_ai(
        naming_model="Kimi-k2.5",
        ordering_model="Kimi-k2.5",
        executor_model="Kimi-k2.5",
        force_strategy="battle_cruisers",
        batch_name="kimi_nothink_bc",
        skip_version_update=True,
    )


if __name__ == "__main__":
    main()
