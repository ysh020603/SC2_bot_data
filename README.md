# StarCraft II LLM Multi-Agent Framework

> 📌 **当前版本（2026-06）已升级为「LLM 增量驱动宏观决策 + 命令式执行调度」架构**（5 个 LLM 阶段
> + DATA_TOOLS 知识库 + `ExecutionScheduler`）。完整说明请看 **[`docs/系统文档.md`](docs/系统文档.md)**；
> 环境搭建请看 **[`docs/环境配置教程.md`](docs/环境配置教程.md)**。
> 下文保留的 Top/Mid/Down 声明式说明为 **legacy**（仍在仓库中、但默认不再走该执行路径）。

本项目是一个基于大语言模型 (LLM) 驱动的《星际争霸 II》自动化 AI 框架。底层封装了基于规则的 SC2 运行平台（`sharpy`），并在其之上构建了 **三层多智能体 (Multi-Agent) 架构**（`SC2_Agent` + `UniversalLLMBot`），将自然语言指令逐步降维、拆解，最终转化为游戏内的宏观运营与动作指令。

当前版本的核心设计：

1. **Top Agent（t=0 策略选择）** — 开局从策略库中 SELECT / VIEW / GENERATE，确定本局宏观打法；
2. **Mid Agent（周期性规划）** — 每 30 秒根据观测 + 策略全文输出自然语言任务列表；
3. **Down Agent（动作翻译）** — 将每条任务翻译为带 **优先级锁资源** 的目标导向 JSON 动作；
4. **双轨执行层** — LLM 动态运营（`ActLLMOngoingTasks`）与策略静态战术（`base_tactics.py`）并行运行。

---

## 项目结构

```
sharpy-sc2/
├── SC2_Agent/              # 三层 Agent 的 Prompt 构建与解析
│   ├── top_agent.py        # t=0 策略选择 / GENERATE 持久化
│   ├── mid_agent.py        # 宏观任务规划
│   └── down_agent.py       # 自然语言 → 动作 JSON
├── dummies/generic/
│   └── universal_llm_bot.py  # 核心 Bot：UniversalLLMBot
├── SKILL/                  # 战术知识库（当前已实现 terran）
│   └── terran/
│       ├── Action.py       # 合法动作空间 + priority 支持
│       ├── registry.json   # 策略白名单
│       ├── generic/        # 兜底战术（GENERATE 拷贝源）
│       └── <strategy>/     # 各策略目录
├── API_config/config.json  # LLM 模型池配置
├── API_Tools/llm_caller.py # OpenAI 兼容 API 调用封装
├── bot_loader/             # Bot 注册与对战启动
├── run_vs_ai.py            # 单局对战入口（含 DEFAULT_* 运行配置）
├── run_vs_ai_batch.sh      # 批量并发引擎
├── start_experiments.sh    # 实验参数预设 + 启动批量
├── parse_sc2_logs.py       # 批次胜率 / 策略维度统计
└── game_records/           # 对局录像、日志、JSON 记录
```

更详细的 Sharpy Bot 继承关系见 [`readme_bot.md`](readme_bot.md)。

---

## SC2_Agent 三层架构

`SC2_Agent` 是 LLM 决策核心；`UniversalLLMBot`（`dummies/generic/universal_llm_bot.py`）负责调度三层 Agent 并与 Sharpy 执行层对接。

| 层 | 角色 | 触发节奏 | 输出 |
|---|---|---|---|
| **Top Agent** | 全局指挥官 | 仅 t=0（开局一次） | 策略名 + `Top_agent_0.md` 全文 |
| **Mid Agent** | 运营规划师 | 每 **30s**（`MID_AGENT_POLL_INTERVAL`） | `{"tasks": ["自然语言任务", ...]}` |
| **Down Agent** | 动作翻译官 | Mid 每输出一条任务即触发一次 | `{"action": "<key>", "to_count": <int>, "priority": <bool>}` |

### Top Agent（t=0 策略选择）

开局将 `SKILL/<race>/` 下经注册表过滤的策略摘要展示给 LLM，支持最多 `TOP_AGENT_INITIAL_MAX_TURNS=5` 轮交互：

| 动作 | 含义 |
|---|---|
| `SELECT` | 直接选定某个已登记策略 |
| `VIEW` | 请求查看一个或多个策略的完整 `Top_agent_0.md` 后再决定 |
| `GENERATE` | 触发新策略生成并持久化到磁盘（见下文） |

选定策略后，`strategy_description`（`Top_agent_0.md` 的 `# Details` 段）会作为 **Mid Agent 全程的宏观指导**，不再做额外的 60 秒阶段评估轮询。

### Mid Agent（宏观规划）

* **决策周期**：每 30 秒执行一次（可在 `UniversalLLMBot.MID_AGENT_POLL_INTERVAL` 调整）。
* **输入**：当前 obs + 上一轮自然语言任务 + t=0 选定的策略全文（`strategy_description`）。
* **输出**：自然语言宏观任务列表。**列表顺序 = 资源分配优先级**（靠前的任务优先占用矿/气/工人）。
* **Priority 约定**：若某任务需要锁资源等待高价值单位/建筑，在任务字符串末尾追加 `(Priority)`，Down Agent 会将其翻译为 `priority: true`（详见下文）。

Mid Agent 只负责宏观运营（造建筑、训单位），不规划侦察或微操；执行位置由下层 Sharpy Act 自行决定。

#### 游戏观测（obs）与 `BuildDetector`

Mid / Down Agent 每轮决策前，由 `LLMObservationRecorder` 生成英文观测文本（`observation_at_decision_time`）。其中 **`[Threat Flags]`** 段依赖 Sharpy 扩展管理器 **`BuildDetector`**（对手 rush / 宏观开局识别）。

`UniversalLLMBot` 在 `configure_managers()` 中显式加载 `BuildDetector()`，与 `KnowledgeBot` 默认栈合并后：

| 能力 | 说明 |
|---|---|
| `[Threat Flags]` | 无威胁时为 `none.`；检测到 rush 时形如 `enemy rush detected (ProxyRax)`；非标准宏观为 `enemy macro build = Mmm` 等 |
| `DataManager` | 可将对手 `rush_build` / `macro_build` 写入对局数据（需 `write_data=yes`） |
| 结构化快照 | `observation_structured.memory_flags` 含 `is_rushing`、`rush_build`、`macro_build` |

**注意**：`BuildDetector` 根据**已侦察到的敌方建筑与单位**推断开局；前期无侦察时 `[Threat Flags]` 仍可能为 `none.`，与 Manager 是否加载无关。更完整的观测字段说明见 [`note/llm_observation_recorder.md`](note/llm_observation_recorder.md)。

### Down Agent（动作翻译）

* **角色**：无状态纯翻译层。
* **输入**：Mid 输出的**单条**自然语言任务 + obs + 合法动作空间（`SKILL/<race>/Action.py::get_action_space()`）。
* **输出**：严格 JSON `{"action": "<key>", "to_count": <int>, "priority": <bool>}`；不合法动作直接被丢弃。
* **`to_count`**：场上（含建造中）的**绝对目标数量**，非增量。
* **`priority`**：仅当任务含 `(Priority)` 且该 action 支持 priority 时为 `true`。

---

## 决策与执行流程

```
[Player Instruct]
       │
       ▼
┌── Top Agent (t=0) ──────────────────────────────┐
│  SELECT / VIEW / GENERATE                        │
│  → selected_strategy + strategy_description      │
└──────────────────────┬──────────────────────────┘
                       │
       ┌───────────────┴───────────────┐
       │  每 30s                        │
       ▼                                │
┌── Mid Agent ─────────────────────────┤
│  obs + strategy_desc + prev_tasks    │
│  → {"tasks": [...]}  (有序优先级)    │
└──────────────┬───────────────────────┘
               │ 逐条
               ▼
┌── Down Agent ────────────────────────┐
│  task + obs + action_space             │
│  → {"action", "to_count", "priority"}  │
└──────────────┬─────────────────────────┘
               ▼
       active_tasks
               │
               ▼
   ActLLMOngoingTasks（每帧 await act.execute()）
               ║  并行
               ║
   base_tactics.py（策略静态战术：防守/进攻/收尾）
```

`create_plan()` 返回 `BuildOrder([ActLLMOngoingTasks, base_tactics])`，两条执行轨并行运行。

---

## Priority 资源锁机制

部分动作支持 `priority=True`，触发 Sharpy 的资源预留（锁矿/锁气），防止低优先级任务在资源不足时"偷走"高价值目标的积累。

**Mid → Down 协作方式**：

* Mid 在任务字符串末尾写 `(Priority)`，例如 `"Train Battlecruiser to 3 (Priority)"`；
* Down 解析为 `"priority": true`；
* Bot 在写入 `active_tasks` 前会调用 `action_supports_priority(key)` 校验，不支持则自动降级为 `false`。

**支持 priority 的动作类型**（人族 `Action.py`）：

* 所有 `type=unit` 的训练动作；
* `expand`（扩张）；
* 部分 GridBuilding 类建筑（Supply Depot、Barracks、Factory、Starport 等）。

动作空间中支持 priority 的 key 会在 description 末尾标注 `[Supports Priority]`，供 Down Agent 参考。

---

## SKILL 战术知识库

当前已实现 **人族 (`terran`)** 策略库；Bot 通过 `race_name` 动态加载 `SKILL.{race}.Action` 与对应策略目录，其他种族需自行补充 `Action.py` 与策略文件夹。

### 目录结构

```
SKILL/terran/
├── registry.json              ← 策略白名单
├── Action.py                  ← 合法动作空间（Down Agent 用）
├── generic/
│   ├── base_tactics.py        ← 兜底战术 + GENERATE 拷贝源
│   └── base_des.md            ← 人类可读的战术说明（不参与运行时加载）
├── marine_rush/
│   ├── Top_agent_0.md         ← # Summary / # Details（Top/Mid 共用）
│   ├── base_tactics.py        ← 静态战术（防守/进攻/收尾）
│   └── base_des.md            ← 可选，人类参考
├── battle_cruisers/
├── two_base_tanks/
├── two_base_tanks_marine_rush_2 … _8   ← GENERATE 或手工迭代版本
└── …
```

### `Top_agent_0.md` 格式

由 `parse_top_agent_0_md` 解析，兼容中英文标题：

| 字段 | 标题（任选其一） | 用途 |
|---|---|---|
| `summary` | `# Summary` / `# 摘要` | t=0 策略列表中的短摘要 |
| `detail` | `# Details` / `# 详细内容` | Mid Agent 全程宏观指导正文 |

### 策略注册表 `registry.json`

```json
{
  "_comment": "Whitelist...",
  "registered_strategies": [
    "marine_rush", "battle_cruisers", "two_base_tanks",
    "safe_tvt_raven", "cyclones", "rusty", "bio", "banshees",
    "one_base_turtle", "two_base_tanks_marine_rush_2", "…"
  ]
}
```

* `_discover_strategies()` 优先读注册表：**仅**列出 `registered_strategies` 中的策略；
* 物理目录存在但未登记的会被忽略；
* GENERATE 新策略由 `_register_strategy(name)` 自动追加（幂等去重）。

### 静态战术 `base_tactics.py`

每个策略目录下的 `base_tactics.py` 定义 **与 LLM 并行的后台战术**（如区域防守、阈值进攻、收尾）。Bot 在 `create_plan()` 时通过 `importlib` 动态加载：

```
SKILL.{race}.{selected_strategy}.base_tactics
```

模块中第一个继承 `BuildOrder` 或 `SequentialList` 的战术类会被实例化。GENERATE 新策略时，会自动从 `generic/base_tactics.py` 拷贝一份作为起点。

---

## T=0 动态策略生成与持久化

当 t=0 交互中 LLM 返回 `{"action": "GENERATE", ...}` 时，Bot 执行完整落盘流程：

1. **相似策略检索**（`find_similar_strategies`）：用玩家指令 + LLM 草稿 + 开局 obs 拼接 query，按 Jaccard 相似度取 `STRATEGY_GENERATION_TOPK=3` 条参考；
2. **第二次 LLM 调用**（`build_strategy_generation_messages`）：强制输出 `{"Strategy_Name": "<snake_case>", "Strategy_Description": "..."}`；
3. **文件系统持久化**（`_materialise_strategy_folder`）：
   * 创建 `SKILL/<race>/<Strategy_Name>/`
   * 写入 `Top_agent_0.md`（`# Summary` + `# Details`）
   * 拷贝 `generic/base_tactics.py` → 新策略目录
4. **自动注册**到 `registry.json`

命名自动规范化（仅 `[a-z0-9_]`），重名时追加 `_v2/_v3...`。

---

## `--force-strategy` 策略锁定

调试或控制变量实验时，用 `--force-strategy <name>` **完全绕过 t=0 LLM**：

* `on_start()` 检测到非空 `force_strategy` 后直接调 `_apply_forced_strategy(name)`；
* 不发起任何 LLM 调用；
* `selected_strategy / strategy_description` 直接从 `SKILL/<race>/<name>/Top_agent_0.md` 读取；
* 写入 `trigger_reason="top_agent_initial_t0_forced"` 的 JSON 记录。

```bash
python run_vs_ai.py --force-strategy battle_cruisers
python run_vs_ai.py --force-strategy none   # 取消强制，走 LLM 选择
```

`run_vs_ai.py` 文件顶部的 `DEFAULT_FORCE_STRATEGY` 常量也可预设默认锁定策略。

---

## LLM 配置

大模型调用由 `API_config/config.json` 的 `llm_agents_pool` 统一管理。Top / Mid / Down 三层各自指定一个 `model_key`：

```json
{
  "llm_agents_pool": {
    "DeepSeek-V4-flash": { "api_url": "...", "model_name": "deepseek-v4-flash", "is_reasoning": false },
    "DeepSeek-V4-flash-reasoning": { "...", "is_reasoning": true },
    "Kimi-k2.5": { "..." }
  }
}
```

**建议组合**：

* **`top_model` / `mid_model`**：带 Reasoning 能力的大模型（如 `DeepSeek-V4-flash-reasoning`），规划更稳定；
* **`down_model`**：速度快的 Flash 级模型，温度低、强制 JSON 返回。

`API_Tools/llm_caller.py` 会根据 `is_reasoning` 字段向厂商 API 注入 thinking 开关，并自动剥离 `<think>` 等推理段。

---

## 运行与测试

对局录像、日志、JSON 默认存放在 `./game_records/`。

### 1. 单局测试 `run_vs_ai.py`

**推荐方式**：直接编辑 `run_vs_ai.py` 中 **「运行配置」** 区的 `DEFAULT_*` 常量，然后：

```bash
python run_vs_ai.py
```

常用 CLI 参数（显式传参会覆盖文件默认值）：

| 参数 | 作用 |
|---|---|
| `--bot-instruct` | 自然语言战术指令 |
| `--bot-race` / `--enemy-race` | 双方种族 |
| `--enemy-difficulty` / `--enemy-build` | 内置 AI 难度 + 风格（macro / rush / timing / air 等） |
| `--top-model` / `--mid-model` / `--down-model` | 三层 LLM 的 `model_key` |
| `--force-strategy` | 锁定 t=0 策略，绕过 LLM；传 `none` 取消 |
| `--real-time` | 实时模式，便于人类观战 |
| `--batch-name` / `--run-index` / `--output-base-dir` | 批量调度用 |

```bash
# 自定义指令 + 对手
python run_vs_ai.py \
  --bot-instruct "速二矿，以坦克+枪兵推进" \
  --bot-race terran \
  --enemy-race zerg \
  --enemy-difficulty harder \
  --enemy-build rush \
  --force-strategy two_base_tanks_marine_rush_5
```

### 2. 批量并发 `start_experiments.sh` + `run_vs_ai_batch.sh`

编辑 `start_experiments.sh` 中的环境变量后启动：

```bash
# start_experiments.sh 关键变量
export BOT_INSTRUCT="打一波，以大和战列巡洋舰为主的攻击"
export BOT_RACE="terran"
export ENEMY_RACE="zerg"
export ENEMY_DIFFICULTY="harder"
export ENEMY_BUILD="air"
export FORCE_STRATEGY="two_base_tanks_marine_rush_6"   # 留空 = 走 LLM 选择
export TOP_MODEL="DeepSeek-V4-flash"
export MID_MODEL="DeepSeek-V4-flash-reasoning"
export DOWN_MODEL="DeepSeek-V4-flash"

bash start_experiments.sh       # 默认 10 局 / 10 并发 / tmux
# 或直接调用底层引擎
bash run_vs_ai_batch.sh <总局数> <并发数> [fg|tmux]
```

tmux 模式启动后会打印 `tmux attach -t sc2_batch_<pid>`，附加进去可看到每个 worker 的实时日志。

---

## 实验结果与分析

### 单局产出

每局结束后 `game_records/<batch_name>/<match_id>/` 目录下会写出：

| 文件 | 内容 |
|---|---|
| `<match_id>.log` | Sharpy + Bot 完整运行日志，含 `[UniversalLLMBot][LLM-INFER]` 行 |
| `<match_id>.SC2Replay` | 录像文件 |
| `<match_id>.json` | **LLM 交互完整记录**（关键产物） |

### `<match_id>.json` 关键字段

```json
{
  "metadata": {
    "result": "Victory|Defeat|Tie",
    "interval_seconds": 30.0,
    "llm_interaction_count": 52
  },
  "interactions": [ "..." ]
}
```

**`interactions[]` 中可能出现的 `trigger_reason`**：

| `trigger_reason` | 来自 | 关键字段 |
|---|---|---|
| `top_agent_initial_t0` | t=0 LLM 选择 | `top_agent_initial.turns[]`、`final_action`（SELECT/VIEW/GENERATE/…）、`selected_strategy`、`strategy_description` |
| `top_agent_initial_t0_forced` | `--force-strategy` 旁路 | `forced_strategy`、`final_action="FORCED"` |
| `poll` | 每 30s Mid Pipeline | `mid_agent_input_previous_tasks`、`mid_agent_output_new_tasks`、`down_agent_translations[]`（含 `priority`）、`active_tasks_after_refresh`、`observation_at_this_moment` |

`down_agent_translations[]` 中每条记录包含 Mid 原始任务、Down 原始响应、解析后的 `{action, to_count, priority}`。

### 批次统计 `parse_sc2_logs.py`

按 **策略 → 对手种族 → 对手风格** 三维度聚合胜率：

```bash
# 编辑 parse_sc2_logs.py 末尾的 my_paths 列表（指向 batch 目录或 game_records 根目录）
python parse_sc2_logs.py
# 输出至 game_records/strategy_statistics.json
```

脚本从 `.log` 末尾解析 `Result for player 1 ... : Victory|Defeat|Tie`，并从日志 / 路径中提取 `--force_strategy`、对手种族与 AI 风格。

---

## Python 环境安装

环境安装以 [`docs/环境配置教程.md`](docs/环境配置教程.md) 为准。当前推荐统一使用
conda 环境 `SC2_0615` 和 Python 3.11；Linux 与 Windows 的依赖安装、`SC2PATH`
配置、pytest 自检和短时对局冒烟命令都维护在该文档中。

旧式 `requirements*.txt` 与 venv 安装方式已移除，避免和实测环境版本冲突。
需要本地安装 StarCraft II 客户端，并确认地图位于 SC2 的 `Maps/` 目录。
Sharpy 底层说明见 [`README_sharpy.md`](README_sharpy.md)。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [`readme_bot.md`](readme_bot.md) | Sharpy Bot 继承关系、dummies 目录说明 |
| [`readme_test.md`](readme_test.md) | 测试与对战详细说明 |
| [`sharpy模块与配置说明.md`](sharpy模块与配置说明.md) | Sharpy 模块与 config.ini 配置 |
| [`note/llm_observation_recorder.md`](note/llm_observation_recorder.md) | 观测文本生成规则 |
