from __future__ import annotations

import sys

from sft_pipeline.build_sft.build_all import main


if __name__ == "__main__":
    if "--task" not in sys.argv:
        sys.argv.extend(["--task", "naming"])
    main()
