from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sft_pipeline.common.io import write_json


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description="Config-light wrapper around tools/collect_terran_bo.py.")
    parser.add_argument("--output", default=None)
    parser.add_argument("--map", default=None)
    parser.add_argument("--bots", nargs="*", default=None)
    parser.add_argument("--races", nargs="*", default=None)
    parser.add_argument("--difficulties", nargs="*", default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--port-offset", type=int, default=25000)
    args = parser.parse_args()

    output = Path(args.output or ROOT / "bo_collection_runs" / datetime.now().strftime("%Y-%m-%d_%H_%M_%S_sft"))
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "collect_terran_bo.py"),
        "--output",
        str(output),
        "--workers",
        str(args.workers),
        "--port-offset",
        str(args.port_offset),
    ]
    if args.map:
        cmd.extend(["--map", args.map])
    if args.bots:
        cmd.extend(["--bots", *args.bots])
    if args.races:
        cmd.extend(["--races", *args.races])
    if args.difficulties:
        cmd.extend(["--difficulties", *args.difficulties])

    output.mkdir(parents=True, exist_ok=True)
    write_json(
        output / "run_manifest.json",
        {
            "created_at": datetime.now().isoformat(),
            "command": cmd,
            "map": args.map,
            "bots": args.bots,
            "races": args.races,
            "difficulties": args.difficulties,
            "workers": args.workers,
            "port_offset": args.port_offset,
            "obs_schema_version": "v1-train-executor-context",
        },
    )
    raise SystemExit(subprocess.call(cmd, cwd=ROOT))


if __name__ == "__main__":
    main()
