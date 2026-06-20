# Terran BO 轨迹数据集（commitfix_v5）

本目录为 2026-06-16 使用**落地确认记录逻辑**采集的正式数据集。

## 采集概况

| 项目 | 值 |
|------|-----|
| 地图 | KairosJunctionLE |
| Bot 数量 | 10 |
| 每 bot 对局 | 3 种族 × 5 难度 = 15 场 |
| 总对局数 | **150** |
| 胜场 | 138 |
| 败场 | 12 |
| 记录逻辑 | pending → commit（动作落地后写入） |
| 采集脚本 | `tools/collect_terran_bo.py` |
| 实现说明 | 见 `docs/ability_recorder_commit_and_addon.md` |

### Bot 与目录对应

| 子目录 | bot key | 源 dummy |
|--------|---------|----------|
| `banshees/` | `banshee` | `dummies/terran/banshees.py` |
| `battle_cruisers/` | `bc` | `battle_cruisers.py` |
| `bio/` | `bio` | `bio.py` |
| `cyclones/` | `cyclone` | `cyclones.py` |
| `marine_rush/` | `marine` | `marine_rush.py` |
| `one_base_turtle/` | `terranturtle` | `one_base_turtle.py` |
| `rusty/` | `oldrusty` | `rusty.py` |
| `safe_tvt_raven/` | `saferaven` | `safe_tvt_raven.py` |
| `terran_silver_bio/` | `silverbio` | `terran_silver_bio.py` |
| `two_base_tanks/` | `tank` | `two_base_tanks.py` |

对手格式：`ai.<种族>.<难度>`，难度为 `medium` / `mediumhard` / `hard` / `harder` / `veryhard`。

---

## 目录结构

```
2026-06-16_terran_bo_commitfix_v5/
├── README.md                 # 本文件
├── summary.json              # 全部 150 场汇总
├── banshees/
│   ├── sequences/            # BO 轨迹 JSON（每局一个）
│   ├── logs/                 # Sharpy 单局日志
│   ├── replays/              # SC2 录像 (.SC2Replay)
│   └── results.json          # 该 bot 15 场胜负明细
├── battle_cruisers/
├── ...
└── two_base_tanks/
```

根目录同级还有运行日志：`../2026-06-16_terran_bo_commitfix_v5_run.log`

---

## 文件说明

### `summary.json`

全局汇总，顶层字段：

| 字段 | 含义 |
|------|------|
| `recorded_at` | 汇总写入时间（ISO 8601） |
| `total_games` | 总对局数（150） |
| `wins` / `losses` | 胜/负场数 |
| `results` | 每场对局的元数据数组 |

`results[]` 每条包含：

| 字段 | 含义 |
|------|------|
| `bot_key` / `bot_folder` | bot 标识与输出子目录名 |
| `opponent` | 对手 ID，如 `ai.zerg.veryhard` |
| `enemy_race` / `difficulty` | 对手种族与难度 |
| `map` | 地图名 |
| `status` | `ok` 或 `error` |
| `result` | `Victory` / `Defeat` 等 |
| `victory` | 是否胜利（布尔） |
| `sequence_file` | 轨迹 JSON 绝对路径 |
| `log_path` / `replay_path` | 日志与录像路径 |

### `*/results.json`

单个 bot 的 15 场明细，结构与 `summary.results` 中对应条目相同，并含 `total_games` / `wins` / `losses`。

### `*/sequences/*.json`

**核心数据**：一局游戏的 macro ability 序列。文件名格式：

```
{bot_key}-ai.{enemy_race}.{difficulty}_{map}_{timestamp}_{random}.json
```

---

## 轨迹 JSON 结构

每局 JSON 顶层四个部分：`meta`、`sequence`、`other_abilities`、`order_list`。

### `meta` — 对局元信息

```json
{
  "bot_name": "Rusty Screams",
  "opponent_id": "banshee-ai.zerg.mediumhard",
  "map": "KairosJunctionLE",
  "my_race": "Terran",
  "enemy_race": "Zerg",
  "result": "Victory",
  "game_duration": 612.5,
  "sequence_count": 176,
  "order_list_count": 176,
  "other_abilities_count": 11,
  "recorded_at": "2026-06-16T21:05:19.861090"
}
```

| 字段 | 含义 |
|------|------|
| `bot_name` | 游戏内显示名 |
| `opponent_id` | 对手完整 ID |
| `game_duration` | 对局时长（秒） |
| `sequence_count` | 宏观动作步数 |
| `order_list_count` | 与 `sequence_count` 相同 |
| `other_abilities_count` | 非宏观能力种类数 |

### `sequence` — 逐步宏观动作

按**落地时间**排序的数组，每一步一条记录：

```json
{
  "seq": 0,
  "game_time": 12.05,
  "ability": "TERRANBUILD_SUPPLYDEPOT",
  "semantic_target": {
    "type": "Build",
    "produces_name": "SupplyDepot"
  },
  "obs": { ... },
  "local_obs": { ... },
  "place": { "x": 111.0, "y": 42.0 }
}
```

| 字段 | 含义 |
|------|------|
| `seq` | 从 0 递增的序号 |
| `game_time` | 动作落地时的游戏时间（秒） |
| `ability` | 能力名（与 `data_ref` 对齐；addon 带后缀如 `BUILD_TECHLAB_BARRACKS`） |
| `semantic_target` | 语义类型与产出，见下表 |
| `obs` | 落地时刻的全局观测（结构化 + 文本） |
| `local_obs` | 落地时刻的本地实体观测 |
| `place` | 可选，建造/挂载位置（`Build` / `BuildOnUnit` 时有） |

#### `semantic_target.type` 含义

| type | 含义 | 示例 ability |
|------|------|--------------|
| `Build` | SCV 在地面建造 | `TERRANBUILD_BARRACKS` |
| `BuildOnUnit` | 挂在已有建筑上 | `BUILD_REACTOR_FACTORY` |
| `BuildInstant` | 瞬间添加（TechLab） | `BUILD_TECHLAB_STARPORT` |
| `Train` | 训练单位 | `BARRACKSTRAIN_MARINE` |
| `Research` | 研究升级 | `RESEARCH_COMBATSHIELD` |
| `Morph` | 建筑变形 | `UPGRADETOORBITAL_ORBITALCOMMAND` |

#### `obs` — 全局观测

```json
{
  "structured": {
    "time": 12.05,
    "time_formatted": "00:12",
    "economy": { "minerals", "vespene", "supply_used", "supply_cap", ... },
    "own_forces": { "completed", "under_construction", "workers_en_route", "active_queues" },
    "enemy": { "composition", "last_observation_time", ... },
    "map_control": { "own_bases", "known_enemy_bases", "neutral_expansions" },
    "combat": { "advantage_predicted", "army_advantage", ... },
    "memory_flags": { "is_rushing", "rush_build", ... },
    "upgrades": []
  },
  "text": "[Time] 00:12 (12.1s).\n[Economy] ..."
}
```

- `structured`：由 `LLMObservationRecorder` 生成的结构化状态
- `text`：同一时刻的自然语言摘要，便于 LLM 阅读

#### `local_obs` — 本地实体观测

基于 `data_ref` 知识图谱的实体级视图：

```json
{
  "completed": ["CommandCenter", "SCV", "Barracks", ...],
  "in_progress": ["Starport", "FactoryTechLab"],
  "pending": []
}
```

| 字段 | 含义 |
|------|------|
| `completed` | 已完成、可参与后续合成的实体名 |
| `in_progress` | 正在建造/训练中的实体 |
| `pending` | 已下单但尚未出现在地图上的实体 |

#### `place` — 位置信息

- **地面建造**（`Build`）：`{ "x", "y" }` 地图坐标
- **挂在单位上**（`BuildOnUnit`）：`{ "unit_type", "tag", "x", "y" }`

### `order_list` — 动作名列表

仅含 `ability` 名称的扁平数组，与 `sequence` 逐步对应：

```json
"order_list": [
  "TERRANBUILD_SUPPLYDEPOT",
  "COMMANDCENTERTRAIN_SCV",
  "UPGRADETOORBITAL_ORBITALCOMMAND",
  "BUILD_TECHLAB_FACTORY",
  ...
]
```

便于快速查看 BO 顺序，无需解析完整 `obs`。

### `other_abilities` — 非宏观能力

本局出现过的**战斗/微操/移动**类能力名集合（去重、排序），**不含**逐步 obs。

常见条目：

| 能力名 | 游戏含义 |
|--------|----------|
| `ATTACK` | 攻击 |
| `MOVE_MOVE` | 移动 |
| `EFFECT_REPAIR` | SCV 修理 |
| `CALLDOWNMULE_CALLDOWNMULE` | 轨道命令中心叫 MULE |
| `EFFECT_TACTICALJUMP` | 战列舰战术跳跃 |
| `EFFECT_ANTIARMORMISSILE` | 渡鸦破甲导弹 |
| `EFFECT_INTERFERENCEMATRIX` | 渡鸦干扰矩阵 |
| `SIEGEMODE_SIEGEMODE` / `UNSIEGE_UNSIEGE` | 坦克架/收炮 |
| `MORPH_SUPPLYDEPOT_LOWER` | 补给站降下 |

这些**不在** `order_list` 中，不参与 BO 轨迹建模。

---

## 数据使用建议

1. **BO 顺序**：直接读 `order_list`，或 `sequence[i].ability`。
2. **状态-动作对**：用 `sequence[i].obs` + `sequence[i].ability`（+ 可选 `place`）。
3. **知识图谱对齐**：`ability` 名称与 `data_ref/data_base_add_graph.json` 中 `Ability.name` 一致。
4. **胜负标签**：`meta.result` 或 `summary.json` 中 `victory`。
5. **回放核对**：同目录 `replays/` 下有对应 `.SC2Replay`。

---

## 相关文档

- 采集流程：`docs/collect_terran_bo.md`
- 记录器实现细节：`docs/ability_recorder_commit_and_addon.md`
