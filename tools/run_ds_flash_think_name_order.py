"""DeepSeek flash think naming + ordering smoke run."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_vs_ai


def short_match_id(**kwargs):
    return f"{kwargs['timestamp']}_ds_flash_bc"


def main() -> None:
    run_vs_ai.build_match_id = short_match_id
    run_vs_ai.play_vs_ai(
        naming_model="DeepSeek-V4-flash_think",
        ordering_model="DeepSeek-V4-flash_think",
        executor_model="DeepSeek-V4-flash",
        force_strategy="battle_cruisers",
        batch_name="ds_flash_think_bc",
        skip_version_update=True,
    )


if __name__ == "__main__":
    main()
