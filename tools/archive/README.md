# Archived Experiment Launchers

This folder keeps old one-off launchers for reference only.

They predate `tools/run_experiment.py` and may contain obsolete arguments such
as `mid_model` / `down_model` or hard-coded local paths. Do not use them for new
runs. Use the generic launcher instead:

```bash
python tools/run_experiment.py --strategy marine_rush --batch-name smoke
```
