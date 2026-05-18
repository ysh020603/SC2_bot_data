# StarCraft II LLM Multi-Agent Framework

本项目是一个基于大语言模型 (LLM) 驱动的《星际争霸 II》自动化 AI 框架。底层封装了基于规则的 SC2 运行平台（`sharpy`），并在其之上构建了一个 **三层多智能体 (Multi-Agent) 架构**（`SC2_Agent`），将自然语言指令逐步降维、拆解，最终转化为游戏内的宏观与微操指令。

最新一轮迭代在原有的三层智能体之上叠加了三个跨层模块：

1. **T=0 动态策略生成与持久化** — 让 LLM 在开局生成全新策略，自动写盘并注册到策略库；
2. **双段式决策流 (Two-Stage Pipeline)** — Top/Mid Agent 每次决策先做 *Skill 筛选*，再做 *Decision Making*；
3. **消融实验开关 + 策略路由控制** — CLI / 环境变量一键控制 Skill 注入粒度，并支持强制策略旁路。

---

## 🧠 SC2_Agent 三层架构

`SC2_Agent` 目录是本项目的大模型决策核心，分为 **Top / Mid / Down** 三层，自上而下链式协作。

| 层 | 角色 | 触发节奏 | 输出 |
|---|---|---|---|
| **Top Agent** | 全局指挥官 | t=0 + 每 60s | t=0：策略名/详情；60s：`{phase, focus}` |
| **Mid Agent** | 运营执行官 | 每 12s（默认 `MID_AGENT_POLL_INTERVAL`） | `{"tasks": ["自然语言任务", ...]}` |
| **Down Agent** | 微操翻译官 | Mid 每输出一条任务即触发一次 | `{"action": "<key>", "to_count": <int>}` |

### Top Agent（全局指挥官）

* **t=0 — 交互式策略选择**：将 `SKILL/<race>/` 下经过注册表过滤的所有策略摘要展示给 LLM，允许它选择以下三种动作之一，最多 `TOP_AGENT_INITIAL_MAX_TURNS=5` 轮：
  * `SELECT`：直接选定某个已登记策略；
  * `VIEW`：请求查看一个或多个策略的完整 `Top_agent_0.md` 详情后再决定；
  * `GENERATE`：触发**模块一**的动态生成流程（详见下文）。
* **每 60s — 双段式阶段评估**（详见**模块二**）：先调一次 LLM 从 Skill 候选池中筛选关键约束，再调一次 LLM 输出 `{"phase": "early|mid|late", "focus": "..."}`。

### Mid Agent（运营执行官）

* **决策周期**：每 12s 跑一次双段式 Pipeline。
* **输入**：上一轮自然语言任务 + 当前 obs + Top Agent 输出的 `phase/focus` + Phase 1 选出的 Skill 约束块。
* **输出**：自然语言宏观任务列表。**列表顺序 = 资源分配优先级**。

### Down Agent（动作翻译官）

* **角色**：纯翻译层，无状态。
* **输入**：Mid 输出的**单条**自然语言任务 + obs + 合法动作空间（`SKILL/<race>/Action.py::get_action_space()`）。
* **输出**：严格 JSON `{"action": "<key>", "to_count": <int>}`；不合法动作直接被丢弃。

---

## 🎯 模块一：T=0 动态策略生成与持久化

### 1.1 GENERATE 流水线

当 t=0 交互流中 LLM 返回 `{"action": "GENERATE", ...}` 时，Bot 不再仅把生成的描述作为运行时占位，而是会执行一次**完整的策略落盘**：

1. **相似策略检索**（`SC2_Agent.top_agent.find_similar_strategies`）
   * 用 *玩家指令 + LLM 第一次草稿 + 开局 obs* 拼接作为 query。
   * 在 `_available_strategies` 中按 **Jaccard 相似度** 取 `STRATEGY_GENERATION_TOPK=3` 条作为写作参考。
2. **第二次 LLM 调用**（`build_strategy_generation_messages`）
   * 强制输出 JSON `{"Strategy_Name": "<snake_case>", "Strategy_Description": "<多段落正文>"}`。
   * `parse_generated_strategy` 自动规范化命名：仅保留 `[a-z0-9_]`、重名时追加 `_v2/_v3...`。
3. **文件系统持久化**（`UniversalLLMBot._materialise_strategy_folder`）
   * 创建 `SKILL/<race>/<Strategy_Name>/`
   * 写入 `Top_agent_0.md`（英文标题：`# Summary` / `# Details`）
   * **硬编码复制** `SKILL/<race>/generic/base_tactics.py` → `<Strategy_Name>/base_tactics.py`
   * 同时创建空的 `Top_agent_60.md` / `mid_agent.md` 占位
4. **自动注册** 到 `SKILL/<race>/registry.json`（详见 §1.2）

完成后，新策略立刻生效：
* `_load_dynamic_tactics()` 会 `importlib.import_module("SKILL.<race>.<name>.base_tactics")` 加载战术；
* `_resolve_phase_guidance()` 会按新策略名路由 Skill 库。

### 1.2 策略注册表 `SKILL/<race>/registry.json`

```json
{
  "_comment": "Whitelist...",
  "registered_strategies": ["marine_rush", "battle_cruisers", "two_base_tanks"]
}
```

* `_discover_strategies()` 优先读注册表：**仅** 列出 `registered_strategies` 中的策略，物理目录存在但未登记的会被忽略。
* 注册表缺失/损坏时自动回退到旧行为（按文件系统扫描）。
* GENERATE 新策略由 `_register_strategy(name)` 自动追加（幂等去重）。
* `_comment` 字段仅供人阅读，不会进入任何 Prompt 也不会写入日志。

### 1.3 `--force-strategy` 旁路

调试或控制变量实验时可以用 `--force-strategy <name>` **完全绕过 t=0 LLM**：
* `on_start()` 检测到非空 `force_strategy` 后直接调 `_apply_forced_strategy(name)`；
* 不发起任何 LLM 调用；
* `selected_strategy / strategy_description` 直接从 `SKILL/<race>/<name>/Top_agent_0.md` 读取并锁定；
* 同时写入一条 `trigger_reason="top_agent_initial_t0_forced"` 的 JSON 记录，便于事后区分。

---

## 🔄 模块二：双段式决策流（Two-Stage Pipeline）

### 2.1 Skill 库结构

每个策略目录下的 `Top_agent_60.md` / `mid_agent.md` 都是一份**多条 Skill 的清单**，以一级标题分隔：

```markdown
# skill_title_a
This skill describes ... 多行 description，允许 ## 之类的子标题作为正文。

# skill_title_b
Another skill description ...
```

`SC2_Agent.skill_loader.parse_skill_md` 按 `^# ` 切分为 `[{"title": "...", "description": "..."}, ...]`。

每个 Agent 层（top/mid）都有 **两个候选池**：

| 池 | 来源 |
|---|---|
| **Generic** | `SKILL/<race>/generic/{Top_agent_60.md, mid_agent.md}` |
| **Specific** | `SKILL/<race>/<selected_strategy>/{Top_agent_60.md, mid_agent.md}` |

### 2.2 Phase 1 — Skill Selection

`UniversalLLMBot._run_skill_selection(layer, obs_text)` 执行的完整流程：

1. 检查该层是否启用 Skill 路由（看 `_skill_enabled_for(layer)`，受模块三开关控制）；
2. 加载 Generic + Specific 池（受 `--disable-specific-skills-layers` 控制是否包含 Specific）；
3. 调 `build_skill_selection_messages(...)` 构造 Phase 1 Prompt，**包含完整 obs / 策略名 / 策略描述 / commander 给出的 phase/focus / 玩家指令 / Skill 清单**；
4. LLM 输出 `{"selected": ["title_a", "title_b", ...]}`；
5. `parse_skill_selection` 大小写不敏感地映射回合法 title、去重、截断到 `SKILL_SELECTION_MAX=5`；
6. `render_selected_skills_block` 把选中 title 拼回 description 并加上引导头 `[Current Strategic Constraints]`。

**输出是三元组** `(selected_titles, rendered_block, trace_dict)`，`trace_dict` 包含完整 Prompt、原始响应、耗时、错误等审计字段，供 JSON 记录使用。

### 2.3 Phase 2 — Decision Making

Top Agent 60s：调 `build_phase_assessment_messages(..., selected_skills_block=rendered_block)`，让 LLM 在 *与 Phase 1 完全相同的 obs* 下生成 `{"phase", "focus"}`。

Mid Agent：调 `build_planning_messages(..., selected_skills_block=rendered_block)`，让 LLM 生成最终的 `{"tasks": [...]}`。

**关键不变量**：Phase 1 与 Phase 2 共享同一份 `obs_text`（在 Top 轮询 `_run_top_agent_poll_blocking` 与 Mid 流水线 `_run_mid_agent_pipeline_blocking` 中都只采样一次），保证 LLM 看到的世界状态完全一致。

### 2.4 各层信息传递

```
[Player Instruct]                ┐
                                 │
   ┌─── Top Agent (t=0) ─────────┼─→ selected_strategy + strategy_description
   │   (SELECT/VIEW/GENERATE)    │
   │                             ▼
   │   ┌──── 每 60s ────┐    ┌─ obs(snap once)
   │   │ Phase 1: pick  │←───┤
   │   │  top60 Skills  │    │
   │   └──────┬─────────┘    │
   │          ▼              │
   │   ┌──── Phase 2 ────┐   │
   │   │ {phase, focus}  │   │
   │   └──────┬──────────┘   │
   │          │              │
   ▼          ▼              ▼
   ┌─── Mid Agent (每 12s) ──────────────────────┐
   │  strategy_desc / phase / focus / prev_tasks │
   │  ┌── Phase 1: pick mid Skills ──┐           │
   │  │   (same obs as Phase 2)      │           │
   │  └──────────┬───────────────────┘           │
   │             ▼                               │
   │  ┌── Phase 2: build_planning_messages ──┐   │
   │  │ → {"tasks": [...]}                   │   │
   │  └──────────┬───────────────────────────┘   │
   └─────────────│───────────────────────────────┘
                 ▼
   ┌─── Down Agent (逐条翻译) ──┐
   │ task + obs + action_space  │
   │ → {"action", "to_count"}   │
   └───────────┬────────────────┘
               ▼
       active_tasks  →  ActLLMOngoingTasks (每帧 await act.execute())
```

---

## 🧪 模块三：消融实验 / 策略路由开关

为支持 Ablation Study，新增了 **4 个 CLI 参数 + 环境变量**：

| CLI | 环境变量 | 取值 | 含义 |
|---|---|---|---|
| `--disable-all-skills` | `DISABLE_ALL_SKILLS` | `1` / `0` | 完全跳过 Phase 1；Phase 2 不注入 Skill。退化为基线。 |
| `--enable-skill-layers` | `ENABLE_SKILL_LAYERS` | `all` / `top_only` / `mid_only` / `none` | 哪一层启用两段式 Skill 路由。 |
| `--disable-specific-skills-layers` | `DISABLE_SPECIFIC_SKILLS_LAYERS` | `all` / `top` / `mid` / `none` | 哪一层只用 Generic、不用 Specific。 |
| `--force-strategy` | `FORCE_STRATEGY` | 策略文件夹名 / 空 | 强制锁定策略，绕过 t=0 LLM。 |

**优先级**：`disable_all_skills > enable_skill_layers`。任意层都先判 `disable_all_skills`，再判 `enable_skill_layers`。

**和老开关的关系**：当某一层 Skill 路由被关掉时（`disable_all_skills=1` 或 `enable_skill_layers` 把该层排除），若旧字段 `USE_TOP_60_PROMPT / USE_MID_PROMPT` 仍为 1，则**回退到整文注入** `Top_agent_60.md` / `mid_agent.md`（旧 Phase Guidance / Execution Guidance 机制）。这样可以单独剥离"双段式 vs 单段整文" / "Skill 注入 vs 无注入"两个变量。

**透传链路**：

```
start_experiments.sh
  └── export DISABLE_ALL_SKILLS / ENABLE_SKILL_LAYERS / ...
       └── run_vs_ai_batch.sh (read env, build skill_flags[])
            └── python run_vs_ai.py --disable-all-skills --enable-skill-layers ...
                 └── bot_loader/game_starter.py argparse + setup_bot()
                      └── my_bot.disable_all_skills = ...  (attribute injection)
                           └── UniversalLLMBot.__init__ / _skill_enabled_for() / ...
```

---

## 🗂️ SKILL 战术知识库结构

```
SKILL/
└── terran/
    ├── registry.json              ← 策略白名单 (§1.2)
    ├── Action.py                  ← 合法动作空间 (Down Agent 用)
    ├── generic/
    │   ├── base_tactics.py        ← 兜底战术 + GENERATE 拷贝源
    │   ├── Top_agent_60.md        ← Generic top60 Skill 库
    │   └── mid_agent.md           ← Generic mid Skill 库
    ├── marine_rush/
    │   ├── base_tactics.py
    │   ├── Top_agent_0.md         ← # Summary / # Details
    │   ├── Top_agent_60.md        ← Specific top60 Skill 库
    │   └── mid_agent.md           ← Specific mid Skill 库
    ├── battle_cruisers/...
    └── two_base_tanks/...
```

**MD 解析规则**：

| 文件 | 解析器 | 期望结构 |
|---|---|---|
| `Top_agent_0.md` | `parse_top_agent_0_md` | `# Summary` + `# Details`（或中文 `# 摘要` / `# 详细内容`） |
| `Top_agent_60.md` / `mid_agent.md` | `parse_skill_md` | 多个 `# <title>` 段，每段一条 Skill |

> 已有 3 个预设策略的 `Top_agent_0.md` 已统一为英文标题；`parse_top_agent_0_md` 仍同时兼容中英两种写法。

---

## ⚙️ LLM 配置指南

大模型的调用配置完全由 `API_config/config.json` 掌控。框架支持为 Top/Mid/Down 三层各自指定一个 `model_key`，建议组合：

* **`top_model` / `mid_model`**：带 Reasoning/Thinking 能力的大模型（如 `deepseek-v4-pro-reasoning`、`kimi-k2.5_base`），单次调用偏慢但稳定。
* **`down_model`**：速度极快的 Flash/Lite 级模型，温度 `0.0` 并开启强制 JSON 返回。

**智能 Vendor 分发**：`API_Tools/llm_caller.py` 会根据 `model_key` 自动向云端 API 注入开启深度思考的特定字段。

---

## 🚀 测试与运行

测试生成的录像、日志、JSON 默认存放在 `./game_records/`。

### 1. 单局测试 `run_vs_ai.py`

```bash
# 基础运行（默认 Terran 对战 Hard 难度 Terran）
python run_vs_ai.py

# 自定义指令 + 双段式 Skill 全启
python run_vs_ai.py \
  --real-time \
  --bot-instruct "速生科技，打一波隐刀rush" \
  --bot-race protoss \
  --enemy-race zerg \
  --enemy-difficulty veryhard \
  --map-name KairosJunctionLE

# Ablation 示例：仅 Top Agent 启用 Skill；Top 强制只用 Generic；锁定 marine_rush
python run_vs_ai.py \
  --bot-instruct "all-in marine rush" \
  --enable-skill-layers top_only \
  --disable-specific-skills-layers top \
  --force-strategy marine_rush
```

**全部 CLI 参数**：

| 参数 | 作用 |
|---|---|
| `--bot-instruct` | 自然语言战术指令 |
| `--bot-race` / `--enemy-race` | 双方种族 |
| `--enemy-difficulty` / `--enemy-build` | 内置 AI 难度 + 风格 |
| `--top-model` / `--mid-model` / `--down-model` | 三层 LLM 的 `model_key` |
| `--use-top-60-prompt` | **旧机制**：仅在 Skill 路由关闭时生效，整文注入 `Top_agent_60.md` |
| `--use-mid-prompt` | **旧机制**：同上，整文注入 `mid_agent.md` |
| `--disable-all-skills` | 关闭所有 Phase 1 |
| `--enable-skill-layers` | `all` / `top_only` / `mid_only` / `none` |
| `--disable-specific-skills-layers` | `all` / `top` / `mid` / `none` |
| `--force-strategy` | 锁定 t=0 策略，绕过 LLM |
| `--batch-name` / `--run-index` / `--output-base-dir` | 批量调度用 |

### 2. 批量并发测试 `run_vs_ai_batch.sh`

通过 `start_experiments.sh` 注入环境变量后启动；推荐使用 `tmux` 模式：

```bash
# start_experiments.sh 中关键变量（节选）
export BOT_INSTRUCT="打一波，以大和战列巡洋舰为主的攻击"
export TOP_MODEL="Kimi-k2.5_base"
export USE_TOP_60_PROMPT="1"
export USE_MID_PROMPT="1"
export DISABLE_ALL_SKILLS="0"
export ENABLE_SKILL_LAYERS="all"
export DISABLE_SPECIFIC_SKILLS_LAYERS="none"
export FORCE_STRATEGY=""        # 留空 = 走 LLM 选择/生成

# 然后启动批量
bash start_experiments.sh       # 默认 10 局 / 5 并发 / tmux
# 或直接调用底层引擎
bash run_vs_ai_batch.sh <总局数> <并发数> [fg|tmux]
```

> tmux 模式启动后会打印 `tmux attach -t sc2_batch_<pid>`，附加进去可看到每个 worker 的实时日志。

---

## 📊 实验结果与分析

### 单局产出

每局结束后 `game_records/<batch_name>/<match_id>/` 目录下会写出：

| 文件 | 内容 |
|---|---|
| `<match_id>.log` | Sharpy + bot 的完整运行日志，含 `[UniversalLLMBot][LLM-INFER]` 行 |
| `<match_id>.SC2Replay` | 录像文件 |
| `<match_id>.json` | **LLM 交互完整记录**（关键产物） |

### `<match_id>.json` Schema

```json
{
  "metadata": {
    "result": "Victory|Defeat|Tie",
    "interval_seconds": 12.0,
    "llm_interaction_count": 38,
    "..." : "..."
  },
  "interactions": [
    { "trigger_reason": "top_agent_initial_t0", "...": "..." },
    { "trigger_reason": "top_agent_poll_60",  "...": "..." },
    { "trigger_reason": "poll",                "...": "..." },
    "..."
  ]
}
```

**`interactions[]` 中可能出现的 `trigger_reason`**：

| `trigger_reason` | 来自 | 包含字段（关键） |
|---|---|---|
| `top_agent_initial_t0` | t=0 LLM 选择 | `top_agent_initial.turns[]` (每轮 messages/raw/parsed)、`viewed_strategies`、`final_action` (`SELECT`/`VIEW`/`GENERATE`/`MAX_TURNS_EXCEEDED`/...)、`selected_strategy`、`strategy_description` |
| `top_agent_initial_t0_forced` | `--force-strategy` 旁路 | `top_agent_initial.forced_strategy`、`final_action="FORCED"` |
| `top_agent_poll_60` | 每 60s 阶段评估 | `top_agent_skill_selection`（Phase 1 完整 trace）、`top_agent_phase_assessment.{messages_sent, raw_response, parsed}`、`top_agent_phase`、`top_agent_focus` |
| `poll` | 每 12s Mid Pipeline | `mid_agent_skill_selection`、`mid_agent_input_previous_tasks`、`mid_agent_raw_response`、`mid_agent_output_new_tasks`、`down_agent_translations[]`、`active_tasks_after_refresh`、`observation_at_this_moment` |

### `*_skill_selection` 子结构（Phase 1 trace）

每次 Skill 筛选都会带一个完整 trace 字段（key 为 `top_agent_skill_selection` 或 `mid_agent_skill_selection`）：

```json
{
  "layer": "top" | "mid",
  "enabled": true,                  // 该层是否启用 Skill 路由
  "skipped_reason": "",             // "" / "empty_candidate_pool" / "disable_all_skills=..."
  "candidate_titles": ["...", "..."],
  "generic_count": 3,
  "specific_count": 5,
  "max_selection": 5,
  "messages_sent": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
  "raw_response": "{\"selected\": [\"...\"]}",
  "selected_titles": ["..."],
  "rendered_block": "[Current Strategic Constraints]\n### title\n...",
  "wall_elapsed_seconds": 1.235,
  "error": null
}
```

### 胜率聚合 `parse_sc2_logs.py`

提供了一个轻量的批次胜率统计脚本：

```bash
# 编辑 parse_sc2_logs.py 末尾的 my_paths 列表（指向 batch_xxx 目录）
python parse_sc2_logs.py
# 输出在每个 batch 目录下生成 match_statistics.json：
#   { "statistics": { "total_logs": ..., "victories": ..., "win_rate": "..." } }
```

> 这个脚本目前只解析 `.log` 末尾的 `Result for player 1 ... : Victory|Defeat|Tie` 行；想做 Skill 选择频次/Phase 分布等更细致的分析，可以遍历 `.json` 中的 `interactions[*].*_skill_selection.selected_titles`。

### 推荐的消融对比矩阵

| 实验组 | 关键开关 | 解释 |
|---|---|---|
| Baseline | `DISABLE_ALL_SKILLS=1` + `USE_TOP_60_PROMPT=0` + `USE_MID_PROMPT=0` | 纯 obs 决策，无任何 Skill / Guidance 注入 |
| Legacy full guidance | `DISABLE_ALL_SKILLS=1` + `USE_*_PROMPT=1` | 旧机制：整文注入 |
| Two-stage Top only | `ENABLE_SKILL_LAYERS=top_only` | 仅 Top 用 Skill，Mid 裸跑 |
| Two-stage Mid only | `ENABLE_SKILL_LAYERS=mid_only` | 仅 Mid 用 Skill |
| Generic only (Top) | `DISABLE_SPECIFIC_SKILLS_LAYERS=top` | Top 只见 Generic Skill，无 strategy-specific |
| Forced strategy | `FORCE_STRATEGY=marine_rush` | 控制变量：固定策略观察 Phase 1/2 效果 |
| Full | 全默认 | 完整双段式 + Generic+Specific 全开 |

---

## 📦 Python 环境安装

请确保 Python 版本为 **3.8 - 3.10**（过高的版本可能导致 `s2clientprotocol` 兼容问题）。

```bash
# 建议使用虚拟环境
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
```
