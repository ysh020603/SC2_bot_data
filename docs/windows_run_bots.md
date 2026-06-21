# Windows Bot 运行指南

本文说明如何在 Windows 下运行本仓库中的 bot。运行前请先完成 `docs/windows_environment_setup.md`。

示例默认：

```text
仓库目录: C:\code\SC2_bot_data
Python: C:\Users\Descfly\.conda\envs\SC2_0615\python.exe
地图: KairosJunctionLE
```

## 1. 运行前准备

打开 PowerShell：

```powershell
cd C:\code\SC2_bot_data

$env:SC2PATH = 'C:\Program Files (x86)\StarCraft II'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'
```

可选：确认没有残留对局进程：

```powershell
Get-Process python,SC2_x64 -ErrorAction SilentlyContinue |
  Select-Object ProcessName,Id,CPU,StartTime
```

## 2. 查看可用 bot、地图和 AI 参数

```powershell
& $py run_custom.py --help
```

所有填入命令行的参数都必须使用英文 key，不能使用中文显示名。尤其是地图名要使用英文地图 ID：

```text
KairosJunctionLE
```

不要写游戏界面里的中文名：

```text
凯罗斯中转站-天梯版
```

bot、race、difficulty、build 也一样必须使用英文，例如 `saferaven`、`terran`、`veryhard`、`random`。

常用 Terran dummy bot：

| bot key | 文件 | 说明 |
| --- | --- | --- |
| `saferaven` | `dummies/terran/safe_tvt_raven.py` | TvT Raven 开局 |
| `bio` | `dummies/terran/bio.py` | 生化 |
| `marine` | `dummies/terran/marine_rush.py` | Marine rush |
| `tank` | `dummies/terran/two_base_tanks.py` | 双矿坦克 |
| `cyclone` | `dummies/terran/cyclones.py` | Cyclone |
| `bc` | `dummies/terran/battle_cruisers.py` | Battlecruiser |
| `banshee` | `dummies/terran/banshees.py` | Banshee |
| `silverbio` | `dummies/terran/terran_silver_bio.py` | Silver bio |
| `terranturtle` | `dummies/terran/one_base_turtle.py` | 一矿防守 |
| `oldrusty` | `dummies/terran/rusty.py` | Rusty |

内置 AI 格式：

```text
ai.<race>.<difficulty>.<build>
```

常用值：

- race: `terran`, `protoss`, `zerg`, `random`
- difficulty: `veryeasy`, `easy`, `medium`, `mediumhard`, `hard`, `harder`, `veryhard`
- build: `random`, `macro`, `rush`, `timing`, `power`, `air`

如果省略 build，例如 `ai.terran.veryhard`，会使用随机构筑。

## 3. 跑一局 bot vs 内置 AI

示例：新版 `saferaven` 对战 Terran hard：

```powershell
& $py run_custom.py `
  -m KairosJunctionLE `
  -p1 saferaven `
  -p2 ai.terran.hard `
  --port 26500
```

示例：挑战 veryhard：

```powershell
& $py run_custom.py `
  -m KairosJunctionLE `
  -p1 saferaven `
  -p2 ai.terran.veryhard `
  --port 26520
```

要求 bot 必须获胜，否则抛出异常：

```powershell
& $py run_custom.py `
  -m KairosJunctionLE `
  -p1 saferaven `
  -p2 ai.terran.veryhard `
  --port 26540 `
  --requirewin 1
```

## 4. 运行结果在哪里

`run_custom.py` 默认把 replay 和日志写到仓库根目录下的 `games/`：

```powershell
Get-ChildItem .\games -File |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 Name,Length,LastWriteTime
```

如果 `config.ini` 中：

```ini
write_ability_sequence = yes
ability_sequence_dir = ability_sequences
```

则 ability sequence 会写入：

```text
C:\code\SC2_bot_data\ability_sequences
```

检查最新序列：

```powershell
Get-ChildItem .\ability_sequences -Recurse -Filter *.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 FullName,LastWriteTime
```

## 5. 批量采集 Terran BO 序列

批量采集脚本：

```text
tools\collect_terran_bo.py
```

只跑 `saferaven`，对战所有脚本内置目标组合：

```powershell
& $py tools\collect_terran_bo.py `
  --output bo_collection_runs\win_saferaven_test `
  --map KairosJunctionLE `
  --bots saferaven `
  --workers 1 `
  --port-offset 26600
```

只跑多个 bot：

```powershell
& $py tools\collect_terran_bo.py `
  --output bo_collection_runs\win_bio_marine_test `
  --map KairosJunctionLE `
  --bots bio marine `
  --workers 1 `
  --port-offset 26700
```

输出结构通常是：

```text
bo_collection_runs/<run_name>/
  summary.json
  safe_tvt_raven/
    sequences/
    logs/
    replays/
    results.json
```

查看最新采集结果：

```powershell
Get-ChildItem .\bo_collection_runs\win_saferaven_test -Recurse -Filter *.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 FullName,LastWriteTime
```

## 6. 后台运行一局

长局可以用 `Start-Process` 放到后台，并把 stdout/stderr 写入文件：

```powershell
$outDir = Join-Path (Get-Location) 'game_records\manual_runs'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$stdout = Join-Path $outDir 'saferaven_veryhard_stdout.log'
$stderr = Join-Path $outDir 'saferaven_veryhard_stderr.log'

$p = Start-Process -FilePath $py `
  -ArgumentList @(
    'run_custom.py',
    '-m', 'KairosJunctionLE',
    '-p1', 'saferaven',
    '-p2', 'ai.terran.veryhard',
    '--port', '26800'
  ) `
  -WorkingDirectory (Get-Location) `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -WindowStyle Hidden `
  -PassThru

"PID=$($p.Id)"
```

查看日志：

```powershell
Get-Content $stderr -Tail 80
Get-Content $stdout -Tail 80
```

## 7. 常用验证命令

编译 bot：

```powershell
& $py -m py_compile dummies\terran\safe_tvt_raven.py
```

检查 sequence 中是否出现起飞/降落动作：

```powershell
$seq = Get-ChildItem .\bo_collection_runs -Recurse -Filter *.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

@'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
order = data.get("order_list") or data.get("sequence") or []
other = data.get("other_abilities") or []

def name(item):
    if isinstance(item, str):
        return item
    return item.get("ability_name") or item.get("ability") or item.get("action") or item.get("name")

bad_order = [name(x) for x in order if name(x) and ("LIFT" in name(x) or "LAND" in name(x))]
bad_other = [name(x) for x in other if name(x) and ("LIFT" in name(x) or "LAND" in name(x))]

print(path)
print("order:", sorted(set(bad_order)))
print("other:", sorted(set(bad_other)))
'@ | & $py - $seq.FullName
```

## 8. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `run_custom.py` 找不到地图 | 先用 `run_custom.py --help` 看 `Installed maps`，确认 `KairosJunctionLE` 已安装 |
| SC2 启动失败 | 检查 `$env:SC2PATH` 和 SC2 安装目录 |
| 端口冲突 | 换一个 `--port` 或 `--port-offset`，例如 `26900` |
| 终端中文乱码 | 设置 `$env:PYTHONUTF8='1'` 和 `$env:PYTHONIOENCODING='utf-8'` |
| 对局挂住或资源占用高 | 检查并结束残留 `SC2_x64.exe` 或 `python.exe` |
| 批量采集同时启动太多 SC2 | 降低 `--workers`，Windows 上建议先从 `--workers 1` 开始 |

## 9. 推荐工作流

单局调 bot：

```powershell
& $py -m py_compile dummies\terran\safe_tvt_raven.py
& $py run_custom.py -m KairosJunctionLE -p1 saferaven -p2 ai.terran.hard --port 26500
& $py run_custom.py -m KairosJunctionLE -p1 saferaven -p2 ai.terran.veryhard --port 26520
```

采集数据：

```powershell
& $py tools\collect_terran_bo.py `
  --output bo_collection_runs\win_collect_001 `
  --map KairosJunctionLE `
  --bots saferaven `
  --workers 1 `
  --port-offset 26600
```

结束后检查：

```powershell
Get-ChildItem .\bo_collection_runs\win_collect_001 -Recurse -Filter *.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 FullName

Get-Process python,SC2_x64 -ErrorAction SilentlyContinue |
  Select-Object ProcessName,Id,CPU,StartTime
```
