# Terran BO 轨迹批量采集

批量运行 `dummies/terran/` 下的 10 个 dummy bot，对战内置 AI（三族 × 五档难度），采集 macro ability sequence（BO 轨迹）并记录胜负。

## 采集机制

- 由 `AbilityRecorderManager`（`sharpy/managers/extensions/ability_recorder.py`）在对局中记录宏操作序列
- 每局结束写入 JSON，包含 `meta`（bot、对手、地图、胜负、时长）和 `sequence`（逐步 ability + obs）
- 需在 `config.ini` 中开启 `write_ability_sequence = yes`（脚本运行时会按 bot 子目录覆盖输出路径）

## 对战矩阵

| 项目 | 内容 |
|------|------|
| Bot 数量 | 10 |
| 对手种族 | protoss / zerg / terran |
| AI 难度 | medium / mediumhard / hard / harder / veryhard |
| 每 bot 对局数 | 3 × 5 = **15 场** |
| 总对局数 | 10 × 15 = **150 场** |
| 并行策略 | **每个 bot 内 15 场并行**，bot 之间串行 |
| 单局时限 | 30 分钟 |

### Bot 列表（bot_loader key → 输出目录名）

| bot key | 源文件 | 输出子目录 |
|---------|--------|------------|
| `banshee` | `banshees.py` | `banshees/` |
| `bc` | `battle_cruisers.py` | `battle_cruisers/` |
| `bio` | `bio.py` | `bio/` |
| `cyclone` | `cyclones.py` | `cyclones/` |
| `marine` | `marine_rush.py` | `marine_rush/` |
| `terranturtle` | `one_base_turtle.py` | `one_base_turtle/` |
| `oldrusty` | `rusty.py` | `rusty/` |
| `saferaven` | `safe_tvt_raven.py` | `safe_tvt_raven/` |
| `silverbio` | `terran_silver_bio.py` | `terran_silver_bio/` |
| `tank` | `two_base_tanks.py` | `two_base_tanks/` |

对手格式：`ai.<种族>.<难度>`，例如 `ai.zerg.veryhard`。

## 环境要求

```bash
conda activate SC2_0615
cd /data2/SC2_2606/sharpy-sc2
```

需已安装 StarCraft II 及 ladder 地图（如 `KairosJunctionLE`）。

## 推荐：tmux 后台运行

```bash
cd /data2/SC2_2606/sharpy-sc2
chmod +x tools/run_terran_bo_collect.sh

# 默认：地图 KairosJunctionLE，会话名 sc2_terran_bo_collect
./tools/run_terran_bo_collect.sh

# 自定义输出目录和会话名
OUTPUT=bo_collection_runs/my_run_001 \
TMUX_SESSION=sc2_terran_bo_collect \
MAP=KairosJunctionLE \
./tools/run_terran_bo_collect.sh
```

### tmux 常用操作

```bash
tmux attach -t sc2_terran_bo_collect   # 进入会话
# 脱离（不杀进程）：Ctrl+b 然后按 d
tmux ls                                 # 查看所有会话
tmux kill-session -t sc2_terran_bo_collect  # 终止会话（会中断采集）
```

### 手动 tmux 启动（等价命令）

```bash
tmux new-session -d -s sc2_terran_bo_collect -c /data2/SC2_2606/sharpy-sc2 \
  'source ~/miniconda3/etc/profile.d/conda.sh && conda activate SC2_0615 && \
   python tools/collect_terran_bo.py \
     --output bo_collection_runs/2026-06-16_terran_bo \
     --map KairosJunctionLE \
     --workers 15 \
     2>&1 | tee bo_collection_runs/2026-06-16_terran_bo_run.log'
```

## 直接运行 Python 脚本

```bash
conda activate SC2_0615
cd /data2/SC2_2606/sharpy-sc2

python tools/collect_terran_bo.py \
  --output bo_collection_runs/2026-06-16_terran_bo \
  --map KairosJunctionLE
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--output` | `bo_collection_runs/<时间戳>` | 输出根目录 |
| `--map` | 随机 melee 图 | 固定地图名 |
| `--bots` | 全部 10 个 | 只跑指定 bot（key 或目录名） |
| `--workers` | 15 | 每个 bot 的并行对局数 |
| `--port-offset` | 25000 | SC2 起始端口 |

示例：只跑 `bio` 和 `marine`：

```bash
python tools/collect_terran_bo.py --bots bio marine --output bo_collection_runs/test_bio_marine
```

## 输出目录结构

```
bo_collection_runs/2026-06-16_terran_bo/
├── summary.json              # 全部 150 场汇总（胜负、路径）
├── banshees/
│   ├── sequences/            # BO 轨迹 JSON（AbilityRecorder 输出）
│   ├── logs/                 # 单局 Sharpy log
│   ├── replays/              # SC2Replay 录像
│   └── results.json          # 该 bot 15 场胜负明细
├── battle_cruisers/
├── ...
└── two_base_tanks/
```

### 轨迹 JSON 示例（`meta` 字段）

```json
{
  "meta": {
    "bot_name": "Marine Rush",
    "opponent_id": "marine-ai.terran.veryhard",
    "map": "KairosJunctionLE",
    "my_race": "Terran",
    "enemy_race": "Terran",
    "result": "Victory",
    "game_duration": 539.29,
    "sequence_count": 453
  },
  "sequence": [ ... ]
}
```

### 查看进度

```bash
# 若用 run 脚本或 tee，日志在 <output>_run.log
tail -f bo_collection_runs/2026-06-16_terran_bo_run.log

# 已完成序列数量
find bo_collection_runs/2026-06-16_terran_bo -path '*/sequences/*.json' | wc -l

# 某 bot 胜负
cat bo_collection_runs/2026-06-16_terran_bo/banshees/results.json | python -m json.tool
```

## 相关文件

| 文件 | 说明 |
|------|------|
| `tools/collect_terran_bo.py` | 批量采集主脚本 |
| `tools/run_terran_bo_collect.sh` | tmux 一键启动包装 |
| `sharpy/managers/extensions/ability_recorder.py` | 轨迹记录器 |
| `config.ini` | `write_ability_sequence`、`ability_sequence_dir` |
| `bot_loader/bot_definitions.py` | bot key 与 dummy 注册 |

## 当前运行实例（2026-06-16）

| 项目 | 值 |
|------|-----|
| 输出目录 | `bo_collection_runs/2026-06-16_terran_bo/` |
| 日志 | `bo_collection_runs/2026-06-16_terran_bo_run.log` |
| 地图 | `KairosJunctionLE` |
| tmux 监控会话 | `sc2_terran_bo_collect`（`tail -f` 日志，主进程为 nohup 启动） |

查看：

```bash
tmux attach -t sc2_terran_bo_collect
tail -f bo_collection_runs/2026-06-16_terran_bo_run.log
```
