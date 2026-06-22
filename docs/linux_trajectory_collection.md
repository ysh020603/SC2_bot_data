# Linux 轨迹采集指南

本文记录在 Linux 服务器上批量采集 Terran BO/action 轨迹的实践经验。采集流程与 [collect_terran_bo.md](collect_terran_bo.md) 一致，但环境变量、并发策略和 tmux 后台运行方式针对 Linux 服务器做了补充说明。

更完整的 SFT 流水线见 [sft_pipeline_usage.md](sft_pipeline_usage.md)。

## 1. 环境约定

当前 Linux 服务器上的约定路径：

```text
仓库目录:   /data2/SC2_2606/sharpy-sc2
conda 环境: sharpy-sc2
SC2 安装:   /data2/SC2/StarCraftII/
```

conda 环境名与仓库目录同名（`sharpy-sc2`），可直接在该环境中运行采集脚本。

### 1.1 激活环境并设置变量

每次手动运行前，先设置以下变量：

```bash
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate sharpy-sc2

export SC2PATH=/data2/SC2/StarCraftII/
export PYTHONPATH=/data2/SC2_2606/sharpy-sc2/python-sc2:$PYTHONPATH
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

cd /data2/SC2_2606/sharpy-sc2
```

说明：

- `SC2PATH` 必须指向 StarCraft II 安装根目录（含 `Maps/`、`Versions/`）。
- `PYTHONPATH` 需要包含仓库内的 `python-sc2` 子目录，否则 `import sc2` 可能失败。
- `PYTHONUTF8=1` 避免日志和 sequence JSON 在中文环境下出现编码问题。

### 1.2 检查地图是否已安装

采集参数中的地图名必须是 SC2 引擎英文 map id。运行前确认地图存在：

```bash
python -c "
from bot_loader.game_starter import GameStarter
maps = GameStarter.installed_maps()
for m in ['KairosJunctionLE', 'AutomatonLE', 'AbyssalReefLE']:
    print(m, 'OK' if m in maps else 'MISSING')
"
```

若显示 `MISSING`，需将对应 `.SC2Map` 放入 `$SC2PATH/Maps/` 后再采集。

## 2. 采集入口

标准入口是 pipeline 包装脚本，会写入 `run_manifest.json`：

```bash
python -m sft_pipeline.collect.run_collect \
  --output bo_collection_runs/<run_id> \
  --map KairosJunctionLE \
  --bots bio marine_rush two_base_tanks \
  --races zerg protoss terran \
  --difficulties medium mediumhard hard harder veryhard \
  --workers 20 \
  --port-offset 25000
```

底层脚本是 `tools/collect_terran_bo.py`，两者参数一致。推荐始终通过 `run_collect` 启动，便于记录 manifest。

### 2.1 对手 AI 说明

`collect_terran_bo.py` 中对手格式为 `ai.<race>.<difficulty>`，未指定 build 参数时默认使用 **RandomBuild**（随机建造风格）。日志中可看到类似输出：

```text
Players: Bot Bio(Terran), Computer Medium(Zerg, RandomBuild)
```

如需其他 AI 风格，需改 `tools/collect_terran_bo.py` 中 `player2_id` 的构造方式（例如 `ai.zerg.hard.macro`）。标准 SFT 采集流程使用默认 RandomBuild 即可。

### 2.2 bot 名称

`--bots` 可传 bot key 或输出目录名（folder name），二者等价。常用 Terran 策略对应关系：

| bot key | 输出目录名 |
| --- | --- |
| `bio` | `bio` |
| `saferaven` | `safe_tvt_raven` |
| `threerax` | `three_rax_stim` |
| `tank` | `two_base_tanks` |
| `mechthor` | `tank_thor_mech` |
| `bc` | `battle_cruisers` |
| `marine` | `marine_rush` |
| `oldrusty` | `rusty` |
| `banshee` | `banshees` |
| `ravlibtank` | `raven_liberator_tank` |

完整列表见 `tools/collect_terran_bo.py` 中的 `TERRAN_BOTS`。

### 2.3 难度参数

难度值必须写成一个单词，与 SC2 API 一致：

```text
medium  mediumhard  hard  harder  veryhard
```

注意：`medium hard`（两个词）会被解析成两个独立难度，而不是 `mediumhard`。启动后检查日志中的 `Difficulty filter` 行，确认过滤结果符合预期。

## 3. 并发与端口

| 参数 | 说明 |
| --- | --- |
| `--workers` | 同一 bot 下同时运行的最大对局数 |
| `--port-offset` | SC2 起始端口，默认 `25000`，每个对局递增 `8` |

Linux 服务器上实测 `--workers 20` 可稳定运行（10 个 bot × 3 种族 × 5 难度 = 每图 150 局）。建议：

1. 首次在新机器上先用 `--workers 4` 跑一小批，确认 SC2 能正常启停。
2. 逐步提高到 `10`、`20`，观察 CPU/内存和端口占用。
3. 若出现 SC2 进程残留或端口冲突，降低 `--workers` 或增大 `--port-offset`。

采集脚本按 bot 顺序串行、每个 bot 内部并行：先跑完 `banshees` 的 15 局，再跑 `battle_cruisers`，依此类推。

## 4. tmux 后台运行

批量采集单图约 150 局、三图共 450 局，耗时 1～2 小时，**必须用 tmux**（或 screen）挂后台，避免 SSH 断开导致任务中断。

### 4.1 单地图采集（通用脚本）

仓库提供 `tools/run_terran_bo_collect.sh`：

```bash
CONDA_ENV=sharpy-sc2 \
MAP=KairosJunctionLE \
WORKERS=20 \
OUTPUT=bo_collection_runs/my_run \
EXTRA_ARGS="--bots bio marine --races zerg protoss --difficulties hard harder" \
bash tools/run_terran_bo_collect.sh
```

脚本会：

- 检查 tmux 会话和已有 `collect_terran_bo.py` 进程，避免重复启动
- 在 tmux 会话 `sc2_terran_bo_collect`（可通过 `TMUX_SESSION` 覆盖）中启动采集
- 将 stdout/stderr 写入 `<output>_run.log`

常用 tmux 操作：

```bash
tmux attach -t sc2_terran_bo_collect   # 进入会话
# 脱离（不中断任务）: Ctrl+b 然后 d
tmux ls                                  # 列出所有会话
tmux kill-session -t sc2_terran_bo_collect  # 终止会话
```

### 4.2 多地图批量采集（10 bot × 3 图）

仓库提供 `tools/run_terran_10bots_3maps_collect.sh`，按地图顺序依次采集：

```bash
RUN_ID=2026-06-22_terran_10bots_3maps \
WORKERS=20 \
TMUX_SESSION=sc2_terran_bo_collect_20260622 \
bash tools/run_terran_10bots_3maps_collect.sh
```

默认配置：

- 10 个 Terran 策略
- 3 张地图：`KairosJunctionLE`、`AutomatonLE`、`AbyssalReefLE`
- 3 种族 × 5 难度
- 每图 150 局，共 450 局

输出结构：

```text
bo_collection_runs/<run_id>/
  master_run.log                 # 三图总日志
  KairosJunctionLE/
    run_manifest.json
    summary.json
    <bot_folder>/
      sequences/*.json
      replays/*.SC2Replay
      logs/*.log
      results.json
  AutomatonLE/
  AbyssalReefLE/
```

可通过环境变量覆盖 `RUN_ID`、`OUTPUT_ROOT`、`WORKERS`、`CONDA_ENV`、`TMUX_SESSION`。

## 5. 进度监控

### 5.1 查看已采集 sequence 数量

```bash
RUN=bo_collection_runs/2026-06-22_terran_10bots_3maps

# 总数
find "$RUN" -path '*/sequences/*.json' | wc -l

# 按地图 / 策略
for d in "$RUN"/*/*/; do
  n=$(find "$d/sequences" -name '*.json' 2>/dev/null | wc -l)
  [ "$n" -gt 0 ] && echo "$(basename $(dirname $d))/$(basename $d): $n"
done
```

### 5.2 查看当前 bot 与胜负

```bash
grep -E "^(=== Bot |Done\.|\[WIN\]|\[LOSS\]|\[ERR\])" "$RUN/master_run.log" | tail -20
```

### 5.3 确认任务是否仍在运行

```bash
tmux has-session -t sc2_terran_bo_collect_20260622 && echo "tmux OK"
pgrep -af "collect_terran_bo.py" | head -5
grep "ALL MAPS DONE" "$RUN/master_run.log"   # 三图全部完成标志
```

### 5.4 实时刷新（可选）

```bash
watch -n 30 'find bo_collection_runs/<run_id> -path "*/sequences/*.json" | wc -l'
```

## 6. 输出与下一阶段

采集完成后，每张地图目录下会生成 `summary.json`，记录胜负统计。标准 SFT 流程后续步骤：

1. **Obs QA**（采集后建议立即跑）：

```bash
python -m sft_pipeline.collect.validate_obs \
  --run bo_collection_runs/<run_id>/KairosJunctionLE \
  --output sft_pipeline_outputs/<run_id>/obs_qa.json
```

2. **v6 Step 标注**（单独阶段，不在采集脚本中执行）：

```bash
python -m sft_pipeline.label_steps.build_v6_steps \
  --data-dir bo_collection_runs/<run_id>/KairosJunctionLE \
  --output sft_pipeline_outputs/<run_id>/v6_steps \
  --model-key kimi-k2.5 \
  --no-thinking \
  --workers 4
```

多地图采集时，可对每个地图子目录分别跑 QA 和 step 标注，或合并后再处理。

## 7. 常见问题

### tmux 会话已存在

```text
tmux 会话已存在: sc2_terran_bo_collect
```

说明同名会话还在。先 `tmux attach` 确认是否仍在采集；若需重启：

```bash
tmux kill-session -t <session_name>
pkill -f "collect_terran_bo.py"   # 必要时清理残留 worker
```

### 已有 collect 进程在运行

启动脚本会检测 `pgrep -f "tools/collect_terran_bo.py"`。不要同时跑两个采集任务，否则端口和 SC2 进程会冲突。

### SC2 进程残留

对局异常退出后，可能残留 `SC2_x64` 进程：

```bash
pgrep -a SC2
# 确认无重要任务后
pkill SC2
```

### 难度或 bot 配错

任务启动后第一时间检查 `run_manifest.json` 和日志开头的 `Difficulty filter`、`Total games` 行。若配置有误，尽早停止并重启，避免浪费算力。错误配置的不完整数据可移到备份目录，例如：

```bash
mv bo_collection_runs/wrong_run bo_collection_runs/wrong_run_incomplete_backup
```

### 地图名用了中文

`--map` 必须传英文 map id（如 `KairosJunctionLE`），不要传客户端中文名。中文名只会出现在 `meta.map_localized`，不能用于文件名或训练元数据。

## 8. 与 Windows 文档的关系

| 主题 | Linux 本文 | Windows |
| --- | --- | --- |
| 环境安装 | 第 1 节 | [windows_environment_setup.md](windows_environment_setup.md) |
| 单局调试 | — | [windows_run_bots.md](windows_run_bots.md) |
| 采集参数与输出格式 | [collect_terran_bo.md](collect_terran_bo.md) | 同左 |
| SFT 全流程 | [sft_pipeline_usage.md](sft_pipeline_usage.md) | 同左 |

Linux 与 Windows 的采集命令参数相同，主要差异在于 `SC2PATH`、conda 环境名、以及 Linux 上推荐使用 tmux 挂长任务。
