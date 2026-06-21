# Agent Bot Test And Trajectory Review Guide

This guide is for agents that need to test Terran dummy bots on Windows and inspect the collected action trajectories.

Important: all command parameters must use English IDs. Do not use Chinese display names from the SC2 client.

Use:

```text
KairosJunctionLE
saferaven
threerax
ai.terran.veryhard
```

Do not use values like:

```text
Chinese localized map display names from the SC2 client
```

## 1. Windows Shell Setup

Run from PowerShell:

```powershell
cd C:\code\SC2_bot_data

$env:SC2PATH = 'C:\Program Files (x86)\StarCraft II'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'
```

The PowerShell profile warning about disabled script execution can usually be ignored if the command exits with code 0.

## 2. Static Checks Before Running SC2

Compile the bot files:

```powershell
& $py -m py_compile `
  dummies\terran\three_rax_stim.py `
  dummies\terran\safe_211_mine.py `
  dummies\terran\bio_mine_macro.py `
  dummies\terran\raven_liberator_tank.py `
  dummies\terran\tank_thor_mech.py `
  bot_loader\bot_definitions.py `
  tools\collect_terran_bo.py
```

Instantiate the plans without launching SC2:

```powershell
@'
import asyncio
from dummies.terran.three_rax_stim import LadderBot as Three
from dummies.terran.safe_211_mine import LadderBot as Safe
from dummies.terran.bio_mine_macro import LadderBot as BioMine
from dummies.terran.raven_liberator_tank import LadderBot as RavLibTank
from dummies.terran.tank_thor_mech import LadderBot as MechThor

async def main():
    for cls in [Three, Safe, BioMine, RavLibTank, MechThor]:
        bot = cls()
        plan = await bot.create_plan()
        print(cls.__module__, bot.name, type(plan).__name__)

asyncio.run(main())
'@ | & $py -
```

Check that collector keys are registered:

```powershell
@'
from tools.collect_terran_bo import TERRAN_BOTS

wanted = {'threerax', 'safe211', 'biomine', 'ravlibtank', 'mechthor'}
present = {key for key, _ in TERRAN_BOTS}
print('present:', sorted(wanted & present))
print('missing:', sorted(wanted - present))
'@ | & $py -
```

## 3. Single-Game Smoke Test

Start with one bot against `hard`:

```powershell
& $py run_custom.py `
  -m KairosJunctionLE `
  -p1 threerax `
  -p2 ai.terran.hard `
  --port 27000
```

Then try `veryhard`:

```powershell
& $py run_custom.py `
  -m KairosJunctionLE `
  -p1 threerax `
  -p2 ai.terran.veryhard `
  --port 27020
```

Use a new port for each run if a previous SC2 process did not exit cleanly.

## 4. Batch Collection For New Bots

Run only the five new Terran bots:

```powershell
& $py tools\collect_terran_bo.py `
  --output bo_collection_runs\test_new_5_terran_bots `
  --map KairosJunctionLE `
  --bots threerax safe211 biomine ravlibtank mechthor `
  --workers 1 `
  --port-offset 27100
```

On Windows, use `--workers 1` first. Increase it only after confirming SC2 can launch and close cleanly.

Expected output layout:

```text
bo_collection_runs\test_new_5_terran_bots\
  summary.json
  three_rax_stim\
    results.json
    sequences\
    logs\
    replays\
```

## 5. Result Review

Summarize wins and errors:

```powershell
@'
import json
from pathlib import Path

root = Path(r'bo_collection_runs\test_new_5_terran_bots')
summary = json.loads((root / 'summary.json').read_text(encoding='utf-8'))

print('total_games:', summary['total_games'])
print('wins:', summary['wins'])
print('losses:', summary['losses'])

for item in summary['results']:
    status = 'WIN' if item.get('victory') else 'LOSS'
    if item.get('status') != 'ok':
        status = 'ERR'
    print(status, item.get('bot_key'), item.get('opponent'), item.get('result') or item.get('error'))
'@ | & $py -
```

If a bot loses on `hard`, inspect the replay and log before spending time on `veryhard`.

## 6. Trajectory Fields

Each sequence JSON normally has:

```text
meta
order_list
sequence
other_abilities
```

Use `order_list` for macro action order checks. Use `sequence` when you need timestamps, observations, local state, or structured economy/combat snapshots. Use `other_abilities` for tactical abilities such as attacks, scans, MULEs, Raven spells, or siege mode.

## 7. Basic Trajectory Audit

Scan latest trajectory files for suspicious lift or land actions:

```powershell
@'
import json
from pathlib import Path

root = Path(r'bo_collection_runs\test_new_5_terran_bots')
bad_tokens = ('LIFT', 'LAND')

for path in root.rglob('sequences/*.json'):
    data = json.loads(path.read_text(encoding='utf-8'))
    order = data.get('order_list') or []
    other = data.get('other_abilities') or []
    bad_order = [a for a in order if any(t in a for t in bad_tokens)]
    bad_other = [a for a in other if any(t in a for t in bad_tokens)]
    if bad_order or bad_other:
        print(path)
        print('  order:', sorted(set(bad_order)))
        print('  other:', sorted(set(bad_other)))
'@ | & $py -
```

For bots that intentionally avoid addon swap, any `LIFT` or `LAND` action should be treated as a regression.

## 8. Addon And Tech Prerequisite Audit

Check that Raven production appears only after a Starport Tech Lab action in the same trajectory:

```powershell
@'
import json
from pathlib import Path

root = Path(r'bo_collection_runs\test_new_5_terran_bots')

for path in root.rglob('sequences/*.json'):
    data = json.loads(path.read_text(encoding='utf-8'))
    order = data.get('order_list') or []
    raven_at = [i for i, a in enumerate(order) if a == 'STARPORTTRAIN_RAVEN']
    techlab_at = [i for i, a in enumerate(order) if 'STARPORTTECHLAB' in a or a == 'BUILD_TECHLAB_STARPORT']
    if raven_at and (not techlab_at or min(raven_at) < min(techlab_at)):
        print('Raven prerequisite issue:', path)
        print('  first_raven:', min(raven_at), 'first_starport_techlab:', min(techlab_at) if techlab_at else None)
'@ | & $py -
```

This is a sequence-level check. A replay can still be legal if a Tech Lab came from an addon swap, but these dummy bots should prefer direct addon construction unless the strategy explicitly says otherwise.

## 9. Macro Shape Audit

Print high-level production counts:

```powershell
@'
import json
from collections import Counter
from pathlib import Path

root = Path(r'bo_collection_runs\test_new_5_terran_bots')

for path in root.rglob('sequences/*.json'):
    data = json.loads(path.read_text(encoding='utf-8'))
    order = data.get('order_list') or []
    counts = Counter(order)
    interesting = {
        key: value for key, value in counts.items()
        if any(token in key for token in (
            'BARRACKS', 'FACTORY', 'STARPORT', 'RAVEN', 'LIBERATOR',
            'MEDIVAC', 'WIDOWMINE', 'SIEGETANK', 'THOR', 'STIMPACK',
            'SHIELDWALL'
        ))
    }
    print('\n', path)
    for key, value in sorted(interesting.items()):
        print(f'  {key}: {value}')
'@ | & $py -
```

Compare the printed shape with the intended build:

```text
threerax: early 3 Barracks, Stim, Combat Shield, Medivacs.
safe211: Factory Reactor, Widow Mines, 2 Barracks, Starport Reactor, Medivacs.
biomine: 3CC, many Barracks, Factory Reactors, continuous Widow Mines.
ravlibtank: Factory Tech Labs, Starport Tech Lab, Raven, Liberator, Tanks.
mechthor: Armory, many Factories, Tank, Thor, Viking.
```

## 10. Log And Replay Triage

Find newest logs:

```powershell
Get-ChildItem .\bo_collection_runs\test_new_5_terran_bots -Recurse -Filter *.log |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 FullName,LastWriteTime
```

Look for exceptions or blocked build messages:

```powershell
Select-String -Path .\bo_collection_runs\test_new_5_terran_bots\**\logs\*.log `
  -Pattern 'Traceback','Exception','Error','no space','not found','timeout'
```

If the trajectory looks legal but the bot loses, inspect the replay before changing build order code. Many losses are tactical timing or army-control problems, not recorder problems.

## 11. Cleanup

After failed or interrupted runs:

```powershell
Get-Process python,SC2_x64 -ErrorAction SilentlyContinue |
  Select-Object ProcessName,Id,CPU,StartTime
```

Terminate only stale processes that belong to the failed test run.
