# LLM Observation Recorder 接入记录

> 日期：2026-05-10
> 目的：在 sharpy-sc2 框架内新增一个 `LLMObservationRecorder` 模块，按 20 秒步长抓取游戏态势，输出「结构化字典 + 英文文本」双态记录，比赛结束时一次性持久化为 JSON，与 `.SC2Replay` 文件同前缀同目录对齐保存。

---

## 一、整体架构

把记录器实现为一个独立的 `Manager`（继承 `ManagerBase`），挂入 `KnowledgeBot` 的标准管理器列表，自动随 `knowledge.update()` 一起被框架调度。

```
┌──────────────────────────────────────────────────────────────────────────┐
│ KnowledgeBot.on_step (每帧)                                               │
│   └─> Knowledge.update(iteration)                                        │
│         └─> for manager in managers: await manager.update()              │
│               ├─ MemoryManager / EnemyUnitsManager / GameAnalyzer ...    │
│               └─ LLMObservationRecorder.update()                         │
│                    ├─ 时间戳判定 (>=20s 才走流水线)                       │
│                    ├─ _build_snapshot() 调度各 _extract_* 方法            │
│                    ├─ _generate_english_text_obs(snapshot)               │
│                    └─ append 到 self.record_history                       │
│                                                                          │
│ KnowledgeBot.on_end(game_result)                                         │
│   └─> Knowledge.on_end                                                   │
│         └─> LLMObservationRecorder.on_end                                │
│               └─ 一次性 dump 为 JSON                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

设计原则：

1. **触发解耦**：以 `last_recorded_time` + `interval_seconds` 控制采样频率，不依赖物理帧。
2. **提取解耦**：每个数据域一个 `_extract_*` 方法，只返回 `Dict`；不直接生成文本。
3. **格式解耦**：`_generate_english_text_obs(snapshot)` 仅读 dict 拼模板；以后改提示词不动提取层。
4. **I/O 解耦**：内存缓存 + `on_end` 一次性写入，避免比赛中频繁刷盘卡帧。

---

## 二、新增 / 修改文件清单

| 类型 | 路径 | 说明 |
| --- | --- | --- |
| 新增 | `sharpy/managers/extensions/llm_observation_recorder.py` | 核心记录器实现 |
| 修改 | `sharpy/managers/extensions/__init__.py` | 导出 `LLMObservationRecorder` |
| 修改 | `sharpy/knowledges/knowledge_bot.py` | 实例化 + 注册到管理器列表 |
| 修改 | `bot_loader/game_starter.py` | 把 `save_replay_as` 路径同步给记录器 |

---

## 三、`LLMObservationRecorder` 关键实现

文件：`sharpy/managers/extensions/llm_observation_recorder.py`

### 3.1 触发与生命周期

```python
class LLMObservationRecorder(ManagerBase):
    def __init__(self, interval_seconds=20.0, output_folder="games", enabled=True):
        super().__init__()
        self.interval_seconds = interval_seconds
        self.output_folder = output_folder
        self.enabled = enabled
        self.last_recorded_time = -interval_seconds   # 让 t≈0 也能触发首次采样
        self.record_history: list[dict] = []
        self.replay_save_path: Optional[str] = None    # 由 GameStarter 注入
        self.output_path: Optional[str] = None         # 调用方可强制覆写

    async def update(self):
        if not self.enabled:
            return
        if self.ai.time - self.last_recorded_time < self.interval_seconds:
            return
        try:
            snapshot = self._build_snapshot()
            text_obs = self._generate_english_text_obs(snapshot)
            self.record_history.append({
                "game_time_seconds": round(self.ai.time, 2),
                "structured_state": snapshot,
                "text_observation": text_obs,
            })
        except Exception as exc:
            self.print(f"failed to capture snapshot: {exc}", ...)
        finally:
            self.last_recorded_time = self.ai.time
```

`update`/`post_update` 是 `ManagerBase` 的抽象方法，必须实现；`on_end` 由框架在游戏结束时回调。

### 3.2 模块化提取器（每个返回 Dict）

| 方法 | 主要依赖 | 输出字段 |
| --- | --- | --- |
| `_extract_economy_state` | `IncomeCalculator`, `self.ai` | `minerals`, `vespene`, `supply_used/cap/left/workers/army`, `minerals_per_min`, `vespene_per_min` |
| `_extract_own_army_state` | `UnitCacheManager.own_unit_cache` | `{ "<UnitTypeId.name>": count }` |
| `_extract_enemy_intelligence` | `EnemyUnitsManager` | 已侦察到的敌方单位/建筑聚合 |
| `_extract_map_control` | `ZoneManager.expansion_zones` | `own_bases`, `known_enemy_bases`, `neutral_expansions` |
| `_extract_combat_analysis` | `GameAnalyzer`, `LostUnitsManager` | `army_advantage` / `income_advantage` / `advantage_predicted` 三种枚举 + 双方累计 `lost_minerals/gas` + `our_army_power/enemy_army_power` |
| `_extract_memory_flags` | `BuildDetector`, `EnemyUnitsManager`, `MemoryManager` + 自定义启发式 | `is_rushing`, `rush_build`, `macro_build`, `enemy_cloak_threat`, `has_proxy_buildings`, `remembered_enemy_units` |

> 备注：`IncomeCalculator` 暴露的是「每秒矿/气速率」，提取器统一乘 60 转成 LLM 友好的 `per_min`。

### 3.3 双态格式化

`_build_snapshot()` 把所有提取器汇总成一个 master dict：

```python
{
    "time": 40.0,
    "time_formatted": "00:40",
    "economy": {...},
    "army": {...},
    "enemy": {...},
    "map_control": {...},
    "combat": {...},
    "memory_flags": {...},
}
```

`_generate_english_text_obs(snapshot)` 仅读取该 dict 拼接英文 prompt，例如：

```
Time: 00:40 (40.0s).
Economy: 800 minerals, 300 vespene; income 850 mins/min, 320 gas/min. Supply: 40/62 (workers 30, army 10).
Own forces: 15 PROBE, 5 ZEALOT.
Enemy intelligence: 12 ZERGLING, 1 SPAWNINGPOOL.
Map control: 2 own bases, 1 known enemy bases, 13 neutral expansions remaining.
Analysis: army advantage = SlightAdvantage, income advantage = Even, predicted = SmallAdvantage. Power: 8 vs 6. Losses: own 0 minerals/0 gas, enemy 100 minerals/0 gas.
Threat flags: enemy rush detected (Pool12).
```

### 3.4 持久化

`on_end(game_result)` 写入：

```json
{
  "metadata": {
    "map_name": "Kairos Junction LE",
    "my_race": "Terran",
    "enemy_race": "Terran",
    "matchup": "TvT",
    "opponent_id": "marine-ai.terran.hard.macro",
    "bot_name": "Marine Rush",
    "game_duration_seconds": 612.34,
    "game_duration_formatted": "10:12",
    "result": "Victory",
    "interval_seconds": 20.0,
    "record_count": 31
  },
  "records": [
    { "game_time_seconds": 20.0, "structured_state": {...}, "text_observation": "..." },
    { "game_time_seconds": 40.0, "structured_state": {...}, "text_observation": "..." },
    ...
  ]
}
```

### 3.5 输出路径三级优先级

`_resolve_output_path()`：

1. `recorder.output_path` —— 显式覆写（绝对优先）。
2. `recorder.replay_save_path` —— 把 `xxx.SC2Replay` 替换为 `xxx.json`。**这是默认走的路径**，由 `GameStarter` 自动注入，确保和 replay 同前缀同目录。
3. 若都未设置，自动生成 `games/Replay_YYYYMMDD_HHMMSS_PvT_MapName.json`。

---

## 四、框架接入点

### 4.1 `sharpy/managers/extensions/__init__.py`

新增导出：

```python
from .llm_observation_recorder import LLMObservationRecorder
```

### 4.2 `sharpy/knowledges/knowledge_bot.py`

构造函数中实例化：

```python
self.game_analyzer = GameAnalyzer()
self.data_manager = DataManager()
self.llm_observation_recorder = LLMObservationRecorder()
```

`on_start` 的 managers 列表中追加：

```python
managers = [
    self.memory_manager,
    ...
    self.game_analyzer,
    self.data_manager,
    self.llm_observation_recorder,
]
```

排在 `data_manager` 之后，方便它读取已就绪的统计/分析管理器。

### 4.3 `bot_loader/game_starter.py`

在调用 `runner.run_game(...)` 之前，把即将传给 `save_replay_as` 的路径，通过新加的静态方法 `_set_recorder_replay_path` 同步给两边 bot 上的记录器：

```python
replay_path = f"{folder}/{file_name}.SC2Replay"
GameStarter._set_recorder_replay_path(player1_bot, replay_path)
GameStarter._set_recorder_replay_path(player2_bot, replay_path)

runner.run_game(
    ...,
    save_replay_as=replay_path,
    ...
)
```

`_set_recorder_replay_path` 内部 `getattr(player.ai, "llm_observation_recorder", None)`，对人类玩家、ladder bot、内置 AI 等不持有该字段的玩家会自动短路。

---

## 五、运行验证

实测一局 `python run_vs_ai.py`（Marine Rush vs `ai.terran.hard.macro` @ KairosJunctionLE）后：

- 控制台日志输出：
  ```
  LLMObservationRecorder: LLM observations saved to games/ai.terran.hard.macro_KairosJunctionLE_2026-05-10 16_24_51_486559.json (N snapshots).
  ```
- `games/` 目录下同时出现：
  - `*.SC2Replay`
  - `*.json`（前缀完全一致）
  - `*.log`（sharpy 自带）
- JSON 内 `metadata.matchup` / `metadata.result` / 每条 record 的 `text_observation` 与 `structured_state` 都符合预期。

另外，本地用 stub 隔离 sharpy/sc2 后，单测 `_generate_english_text_obs` 与 `_resolve_output_path` 行为正常。

---

## 六、可拓展点

- 想换成其他时间步长：`bot.llm_observation_recorder.interval_seconds = 30`。
- 想暂时关掉：`bot.llm_observation_recorder.enabled = False`。
- 想写到指定目录：`bot.llm_observation_recorder.output_path = "/abs/path/foo.json"`。
- 后续若要 RL 状态向量：直接读 `record_history[i]["structured_state"]` 即可，无需改提取层。
- 想新增数据域：照葫芦画瓢加一个 `_extract_xxx() -> Dict`，并在 `_build_snapshot` 里挂上键即可，文本模板那边按需引用。

---

## 七、安全性 / 性能小结

- 所有提取器对依赖管理器都做了 `is None` 兜底，关键 try/except 仅写 warning 日志，单点失败不会拖垮记录器。
- 即使本帧 `_build_snapshot` 抛异常，`finally` 仍推进 `last_recorded_time`，不会反复刷屏。
- 比赛中零硬盘 I/O，仅 `on_end` 写一次，安全规避超时断开风险。
- 20 秒步长既保证 LLM 可消化的语义粒度，又不会反应迟钝。
