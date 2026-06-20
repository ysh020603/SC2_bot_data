"""Kimi think naming + Kimi non-think ordering/executor smoke run."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_vs_ai


def short_match_id(**kwargs):
    return f"{kwargs['timestamp']}_kimi_think_name"


def main() -> None:
    run_vs_ai.build_match_id = short_match_id
    run_vs_ai.play_vs_ai(
        naming_model="Kimi-k2.5_think",
        ordering_model="Kimi-k2.5",
        executor_model="Kimi-k2.5",
        force_strategy="marine_rush",
        batch_name="kimi_think_name_nothink_order",
        skip_version_update=True,
    )


if __name__ == "__main__":
    main()
