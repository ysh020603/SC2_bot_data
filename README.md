# SC2 Bot Action 采集

基于 [sharpy-sc2](https://github.com/DrInfy/sharpy-sc2) 框架，在本仓库中实现了 **Bot 宏操作（macro ability）序列的自动采集** 能力。对局过程中记录 bot 实际落地的建造、训练、研究、变形等动作，并配对全局/局部观测，输出结构化 JSON，供后续策略学习、知识图谱对齐或 LLM Agent 训练使用。

原 sharpy-sc2 项目说明见 [`docs/sharpy_original_readme.md`](docs/sharpy_original_readme.md)。

## 核心能力

| 模块 | 路径 | 说明 |
|------|------|------|
| **Ability 记录器** | `sharpy/managers/extensions/ability_recorder.py` | 在 bot 下发命令后，等待动作被 SC2 接受再写入序列；支持 TechLab/Reactor 按宿主建筑命名 |
| **批量采集脚本** | `tools/collect_terran_bo.py` | 批量运行 Terran dummy bot，对战内置 AI，采集 BO 轨迹并汇总胜负 |
| **一键启动** | `tools/run_terran_bo_collect.sh` | tmux 后台启动批量采集 |
| **数据参考库** | `data_ref/data_base_add_graph.json` | 能力/实体标准命名与技术图，用于解析与对齐 action |
| **观测系统摘录** | `obs_system/` | LLM 观测生成与 Executor 执行相关代码（独立阅读/移植用） |

记录器通过 `KnowledgeBot` 挂载，在 `SkeletonBot.do()` 中拦截每次宏操作：

```
bot.do(action) → AbilityRecorderManager.record() → pending → 落地 commit → sequence JSON
```

## 采集数据格式

每局对局结束写入一个 JSON 文件，主要字段：

- **`meta`**：bot 名称、对手、地图、种族、胜负、时长、序列步数等
- **`sequence`**：逐步 ability 名称 + 全局/局部观测快照
- **`other_abilities`**：未纳入宏序列的操作（如 ATTACK、MOVE 等）集合

详细字段说明与落地检测机制见 [`docs/ability_recorder_commit_and_addon.md`](docs/ability_recorder_commit_and_addon.md)。

## 快速开始

### 环境

- Python 3.8+（64-bit）
- 已安装 StarCraft II 及 ladder 地图（如 `KairosJunctionLE`）
- 依赖：`pip install -r requirements.txt`

### 配置

在 `config.ini` 的 `[general]` 段中：

```ini
write_ability_sequence = yes
ability_sequence_dir = ability_sequences
data_ref_path = data_ref/data_base_add_graph.json
```

### 运行批量采集（Terran BO）

```bash
cd /data2/SC2_2606/sharpy-sc2

python tools/collect_terran_bo.py \
  --output bo_collection_runs/my_run \
  --map KairosJunctionLE
```

或使用 tmux 后台运行：

```bash
chmod +x tools/run_terran_bo_collect.sh
./tools/run_terran_bo_collect.sh
```

完整参数、对战矩阵与输出目录结构见 [`docs/collect_terran_bo.md`](docs/collect_terran_bo.md)。

## 目录概览

```
.
├── sharpy/                    # Sharpy 框架（含 ability_recorder 扩展）
├── dummies/                   # 练习用 dummy bot（采集对象）
├── tools/                     # 采集脚本与启动脚本
├── data_ref/                  # 能力/实体知识图谱数据
├── bo_collection_runs/        # 批量采集输出（轨迹、日志、录像）
├── obs_system/                # 观测与执行子系统摘录
└── docs/                      # 详细文档
    ├── collect_terran_bo.md
    ├── ability_recorder_commit_and_addon.md
    └── sharpy_original_readme.md
```

## 文档

- [Terran BO 轨迹批量采集](docs/collect_terran_bo.md) — 采集流程、参数、输出结构
- [Ability 落地记录与附属建筑命名](docs/ability_recorder_commit_and_addon.md) — 记录器设计与调试要点
- [OBS 系统说明](obs_system/README.md) — 观测生成与 Executor 执行摘录

## 致谢

本项目基于 [sharpy-sc2](https://github.com/DrInfy/sharpy-sc2)（[python-sc2](https://github.com/BurnySc2/python-sc2) 之上的 SC2 AI 开发框架）扩展开发。
