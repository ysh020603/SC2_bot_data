# Current Standard: v8 Macro-Control Fuzzy

For future action list -> NL step labeling, use v8 by default:

```bash
python Tools/bo_to_doc_v8.py --model-key deepseek-v4-flash --max-workers 2
```

v8 is based on v7 No Ordinals and keeps the v7 summary/final-step contract:

- Normal steps still use SC2 Terran slang, natural coach-style wording, and merge repeated actions within each step.
- Quantity rules stay unchanged: current-step cardinal quantities such as `3 rax` or `2 tanks` and fuzzy quantities such as `a few Marines` or `several SCVs` are allowed.
- Counted ordinal wording is removed: do not use `first/second/third/fourth`, `the first one`, `third base`, `second Starport`, etc.
- SC2 opening labels such as `depot-first`, `rax-first`, and `CC-first` are allowed because they describe opening style, not cumulative ordinal state.
- Summary and final step also follow the no-ordinal rule; the final step remains the v6-style concise strategic style characterization.
- Each normal step receives action-derived macro-control cues for downstream obs-aware reasoning:
  - Worker saturation: phrase SCV production so the executor can use obs worker current/ideal plus the current economy task.
  - Supply buffer: phrase depot planning so the executor can use obs supply used/cap plus the current SCV, Marine, and production demand.
  - Gas capacity: phrase Refinery/gas as economy capacity or flexibility for upcoming tech without binding it to exactly one unit, building, or upgrade.
- Normal steps should not add enemy/scout/pressure/threat, active-queue, idle-production, or generic tech-readiness analysis. The point is macro execution under obs, not enemy-state branching.

Recommended output directory:

```text
Tools/bo_docs_situation_aware/
```

Full example test completed with `deepseek-v4-flash` non-thinking on all 10 BOs in `2026-06-16_terran_bo_commitfix_v5/`; output:

```text
Tools/bo_docs_situation_aware_deepseek_flash_all/

---
# SC2 Terran BO Collection → Build Order → Natural Language Documentation

## 1. 项目概述

从 StarCraft II 人族 AI Bot 的对战 replay 中提取的原始动作序列 (`order_list`)，通过 LLM (DeepSeek V4 Flash) 转换为人类教练风格的自然语言 Build Order 文档。

**核心输入**：`2026-06-16_terran_bo_commitfix_v5/` 下 10 个 BO 策略目录，每个目录包含多场 AI 对战的 replay 解析数据。

**核心输出**：
- `Tools/bo_docs/` — Original 模式：混合精确/模糊数量的自然语言描述（见第 3-4 节）
- `Tools/bo_docs_precise/` — **Precision 模式**：全精确数值、步间独立的描述（见第 9 节）
- `Tools/bo_docs_slang/` — **Slang 模式**：SC2 社区黑话风格、无跨步上下文的自然语言描述（见第 10 节）
- `Tools/bo_docs_balanced/` — **Balanced Phase Summary 模式**：3 句式 Early/Mid/Late 均衡 Summary + 独立策略外推 Final Step（见第 12 节）
- `Tools/bo_docs_concise/` — **Concise Style Summary 模式**：基于 v5，Final Step 精炼为简短战略风格概括（见第 13 节）
- `Tools/bo_docs_enhanced/` — **Enhanced Summary 模式**：基于 Slang 风格，升级 Summary 为结构化战术摘要 + 策略外推引导（见第 11 节）

| 文件 | 说明 |
|------|------|
| `prompt_template_v6.py` | **Concise Style Summary 模式** 的 System Prompt 和 User Prompt 模板。基于 v5，Final Step 改为简短战略风格概括。 |
| `bo_to_doc_v6.py` | **主程序 (Concise Style Summary v6)**。基于 v5，Final Step 从详细决策指南改为简略风格概括，输出到 `bo_docs_concise/`。 |
| `bo_docs_concise/` | Concise Style Summary 模式下产出目录，包含 10 个 `*.md` 文件 + `step_index.json`，Final Step 为 1-2 句战略风格概括。 |

## 2. 文件清单

### 2.1 工具文件 (`Tools/`)

| 文件 | 用途 |
|------|------|
| `action_products.py` | **Action → 产物映射工具**。运行时读取 `data_base_add_graph.json` 的 `action_result` 关系，将 SC2 原始 Action 名解析为产物名。支持 DB 主查 + 模式匹配 fallback + 显式 fallback 三层解析。|
| `action_mapper.py` | Action 映射类（功能同上，面向对象版本，额外提供矿产/气花费、产物类型、依赖建筑等元信息）。 |
| `prompt_template.py` | LLM 调用的 System Prompt 和 User Prompt 模板。包含步描述的全部风格规则。|
| `bo_to_doc.py` | **主程序 (Original v1)**。遍历 BO 目录 →随机选取胜利轨迹 →标注 Action → 切分 Step → 每 Step 调 LLM（带跨步上下文）→组装 Markdown + step_index.json。|
| `bo_docs/` | Original 模式下产出目录，包含 10 个 `*.md` 文件 + `step_index.json`。|
| `prompt_template_v2.py` | **Precision 模式** 的 System Prompt 和 User Prompt 模板。全部精确数值、步间完全独立。|
| `bo_to_doc_v2.py` | **主程序 (Precision v2)**。与 v1 相同的数据提取切分流程，但 Step 内按产物聚合计数，无跨步上下文传递。|
| `bo_docs_precise/` | Precision 模式下产出目录，包含 10 个 `*.md` 文件 + `step_index.json`。|
| `prompt_template_v3.py` | **Slang 模式** 的 System Prompt 和 User Prompt 模板。基于 v1，加入 SC2 社区黑话术语表，移除跨步上下文。|
| `bo_to_doc_v3.py` | **主程序 (Slang v3)**。基于 v1，删除 BuildContext 跨步上下文，选取最高难度胜利对局，输出使用黑话风格。|
| `prompt_template_v4.py` | **Enhanced Summary 模式** 的 System Prompt 和 User Prompt 模板。基于 v3，升级 Summary 为 4 句结构化战术摘要，末尾固定策略外推引导句。|
| `bo_to_doc_v4.py` | **主程序 (Enhanced Summary v4)**。基于 v3，修改 Summary prompt 调用的 v4 模板，默认输出到 `bo_docs_enhanced/`。|
| `bo_docs_slang/` | Slang 模式下产出目录，包含 10 个 `*.md` 文件 + `step_index.json`。|

| `prompt_template_v5.py` | **Balanced Phase Summary 模式** 的 System Prompt 和 User Prompt 模板。基于 v4，Summary 改为 3 句式 Early/Mid/Late 均衡覆盖，策略外推句独立 Final Step prompt。|
| `bo_to_doc_v5.py` | **主程序 (Balanced Phase Summary v5)**。基于 v4，在 Summary 生成后额外调用一次 LLM 生成 `[Step N+1]` 策略外推 Step，插入 Details 末尾。|
| `bo_docs_balanced/` | Balanced Phase Summary 模式下产出目录，包含 10 个 `*.md` 文件 + `step_index.json`，每个 Summary 为 3 句式均衡摘要，Details 末尾为独立 `[Step N]` 后续决策指南。|
| `bo_docs_enhanced/` | Enhanced Summary 模式下产出目录，包含 10 个 `*.md` 文件 + `step_index.json`，每个 Summary 为 4 句结构化战术摘要。|

### 2.2 API 调用 (`api_call/`)

| 文件 | 用途 |
|------|------|
| `api_call.py` | OpenAI 兼容协议的 LLM 调用封装。支持 `API_config/config.json` 和 `llm_agents_pool` 的多模型配置，自动处理 thinking 模式开关。|
| `config/provider_config.json` | 各 Provider 配置（含 `deepseek-v4-flash` 的 api_key、base_url、temperature 等）。|

### 2.3 API 配置 (`API_config/`)

| 文件 | 用途 |
|------|------|
| `config.json` | `llm_agents_pool` 格式的模型配置，供 `api_call.py` 读取。当前配置 `deepseek-v4-flash`。|

### 2.4 数据库

| 文件 | 用途 |
|------|------|
| `data_base_add_graph.json` | SC2 完整单位/技能/升级关系图数据库。包含 `Ability`、`Unit`、`Upgrade` 三张表，通过 `relations` 字段记录 `action_result`、`ability_requires_unit` 等关联。|

### 2.5 输入数据 (`2026-06-16_terran_bo_commitfix_v5/`)

```
2026-06-16_terran_bo_commitfix_v5/
├── banshees/
│   ├── results.json          # 15 场对战记录，含 sequence_file 引用
│   ├── sequences/            # 每场的解析数据 JSON (含 order_list)
│   ├── logs/                 # 对战日志
│   └── replays/              # SC2Replay 文件
├── battle_cruisers/          # (同上结构)
├── bio/
├── cyclones/
├── marine_rush/
├── one_base_turtle/
├── rusty/
├── safe_tvt_raven/
├── terran_silver_bio/
├── two_base_tanks/
├── summary.json              # 全部 150 场的汇总
└── README.md
```

---

## 3. Pipeline 流程 (Original v1)

```
┌─────────────────────────────────────────────────────────────────────┐
│ Step 1  数据提取                                                   │
│ bo_to_doc.py 遍历 10 个 BO 目录的 results.json                     │
│ → shuffle victories 找到第一个含 order_list 的 sequence JSON    │
│ • 每个 BO 随机选 1 条胜利轨迹                                    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────────┐
│ Step 2  Action 标注                                                │
│ action_mapper.py 读取 data_base_add_graph.json                     │
│ • 通过 action_result 关系获取产物名                               │
│ • 产出: [训练/建造/升级] 产物名 (矿/气花费, 依赖建筑)               │
│                                                                    │
│ 示例:                                                              │
│   COMMANDCENTERTRAIN_SCV →[训练] SCV (50矿)                       │
│   TERRANBUILD_BARRACKS   →[建造] Barracks (150矿, 依赖:SupplyDepot│
│   SIEGEMODE_SIEGEMODE    →[变形] SiegeTankSieged                  │
│   ATTACK                 →(Attack)  [无产物, 显式 fallback]       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────────┐
│ Step 3  Step 切分                                                  │
│ 贪心算法: 窗口 10-15 actions                                       │
│ • 在战略边界点 (Factory/Starport/二矿/关键科技) 优先切割           │
│ • Sticky actions (TechLab/Reactor) 不跟主建筑拆开                  │
│ • 记录每个 step →[start_idx, end_idx]                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────────┐
│ Step 4  每个 Step LLM 生成                                           │
│ 对每个 step 发送一次 API 调用:                                     │
│ • System Prompt = prompt_template.SYSTEM_PROMPT (11条风格规则)     │
│ • User Prompt = 上一步LLM输出 + 本步标注Action列表 + Step编号      │
│ • 轨迹内串行(step N+1 等待 step N 完成)                           │
│ • 轨迹间并行(ThreadPoolExecutor, max_workers=5)                   │
│                                                                    │
│ LLM 提供 DeepSeek V4 Flash (via api_call.py)                    │
│ is_reasoning: true (thinking 模式开启)                             │
│ temperature: 0.3, max_tokens: 4096                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────────┐
│ Step 5  Summary 生成                                               │
│ 所有step 完成→单独一次 LLM 调用                               │
│ →输入: 全部 step 描述 + bot_name + map_name                       │
│ →输出: 一段英文字段落概括整体策略                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────────┐
│ Step 6  产出                                                       │
│ → bo_docs/{bot_name}.md    # Summary + Details                     │
│ → bo_docs/step_index.json  # 每个 BO →step 切分索引              │
└─────────────────────────────────────────────────────────────────────┘
```

### 并发模型

```
ThreadPoolExecutor(max_workers=5)
─
├── Worker 1: banshees         Step 1 →Step 2 →... →Step N  (串行)
├── Worker 2: battle_cruisers  Step 1 →Step 2 →... →Step N  (串行)
├── Worker 3: bio              Step 1 →Step 2 →... →Step N  (串行)
├── Worker 4: cyclones         Step 1 →Step 2 →... →Step N  (串行)
└── Worker 5: marine_rush      Step 1 →Step 2 →... →Step N  (串行)
                                →完成后接
                                one_base_turtle, rusty, ...
```

---

## 4. Step 描述规范 — Original v1 (LLM Prompt 规则)

### 4.1 输出格式

每个 step 只输出一句
```
[Step N] Your natural language description here.
```

### 4.2 风格规则 (11 条

**规则 1 — 自然语言，非机械翻译**
> 不要逐条列出 Action 名。像人类教练描述一个战术阶段。

```
BAD:  "Train 4 SCVs, build 1 Supply Depot, build 1 Barracks."
GOOD: "Keep SCV production going while setting up the first Barracks and Refinery, 
       then train a few Marines for defense before moving into Factory tech."
```

**规则 2 — 关键建筑用序数词**
> first/second/third Barracks, first/second Starport, second/third Command Center　

**规则 3 — 步内合并重复 Action**
> 同一步中出现 4 的`BARRACKSTRAIN_MARINE` →"train 4 Marines" →"reinforce with several Marines"→

**规则 4 →两种数量描述风格，交替使用*
> (a) **精确数字** →重要建筑和核心单→ "build 2 Barracks", "train 1 Battlecruiser"→
> (b) **模糊* →普通单→SCV: "a few Marines", "several SCVs", "a wave of Marines", "a couple of Hellions", "a handful of Banshees"→
> 重要建筑 (Command Center, Starport, Factory, Fusion Core) 和标志性单→(Battlecruiser, Siege Tank, Banshee) 倾向精确数字→

**规则 5 →每步数量自包含，不跨步累→*
> 禁止 "Marines reach 8 total"→bringing the worker count to 46"→SCVs from 12 to 16"→

**规则 6 — 阶段意识**
> 说明 WHY：防御、科技转型、扩张时机、地图控制　

**规则 7 — 战略性语言**
> `TERRANBUILD_COMMANDCENTER` →"take an expansion" 而非 "build Command Center"→

**规则 8 →只有最后一步可用持续生产表*
> 最后一步可以写 "continue flooding Marines / keep producing Siege Tanks"。前面步骤必须描述具体动作　

**规则 9 →不编造输入中没有的单→建筑/升级*

**规则 10 →简洁流畅，每步 2-4 句*

**规则 11 →不打破原→Action 顺序*

### 4.3 Context 传退

每步接收上一步的 LLM 自然语言输出作为上下文，**仅用于维持战略连贯*（知道当前处于哪个阶段）→*不从中复制具体数字或追踪累计数量**→

### 4.4 数量词的 LLM 行为（实测映射）

LLM 根据规则 4 自动选择精确/模糊风格，无硬编码映射

| 输入数量 | 典型输出 |
|---------|---------|
| 1 (重要单位) | 直接名称: "the Battlecruiser" |
| 2 | "a couple of" / "a pair of" / "two" |
| 3 | "a few" / "a handful of" |
| 4-6 | "several" |
| 7-8 | "a wave of" / "a large batch of" / 偶尔直接甀"7 Marines" |
| 10+ | "a massive wave of" / "over a dozen" |

---

## 5. Action 产物解析机制

`action_products.py` 采用三层解析:

```
优先1: data_base_add_graph.json →action_result 关系 (主源)
  →BUILD/TRAIN/RESEARCH/MORPH/UPGRADE →Action
  →示例: TERRANBUILD_BARRACKS →action_result: Barracks

优先纀2: 模式匹配 (fallback)
  →MORPH_XXX →拆分 target + mode
  →EFFECT_XXX →提取效果
  →CALLDOWNMULE_XXX →MULE
  与

优先3: 显式 fallback 字典 (6 →
  →ATTACK →(Attack)
  →MOVE_MOVE →(Move)
  →SMART/STOP/LAND/LIFT
```

`action_mapper.py` 额外提供:
- 产物类型 (Unit / Upgrade)
- 矿产/气花贀
- 需求建筀(`ability_requires_unit`)
- 是否为建筀(`is_structure`)
- 反向查询 (`product →ability`)

---

## 6. 使用方法

### 6.1 Original v1 模式

```bash
# 单 BO 测试
python Tools/bo_to_doc.py --bot banshees --max-workers 1

# 全部 10 个 BO (5 并发)
python Tools/bo_to_doc.py --max-workers 5
```

### 6.2 Precision v2 模式

```bash
# 单 BO 测试
python Tools/bo_to_doc_v2.py --bot banshees --max-workers 1

# 全部 10 个 BO (5 并发)
python Tools/bo_to_doc_v2.py --max-workers 5
```

### 6.3 Slang v3 模式

```bash
# 单 BO 测试
python Tools/bo_to_doc_v3.py --bot banshees --max-workers 1

# 全部 10 个 BO (5 并发)
python Tools/bo_to_doc_v3.py --max-workers 5
```

### 6.4 Action 产物查询

```bash
# 命令行
python Tools/action_products.py TERRANBUILD_BARRACKS SIEGEMODE_SIEGEMODE ATTACK

# 代码调用
from action_products import action_to_product
product = action_to_product("BARRACKSTRAIN_MARINE")  # →"Marine"
```

### 6.5 Balanced Phase Summary v5 模式

```bash
# 单 BO 测试
python Tools/bo_to_doc_v5.py --bot banshees --max-workers 1

# 全部 10 个 BO (5 并发)
python Tools/bo_to_doc_v5.py --max-workers 5
```

### 6.6 Concise Style Summary v6 模式

```bash
# 与 BO →
python Tools/bo_to_doc_v6.py --bot banshees --max-workers 1

# ȫ→ 10 与 BO5 →
python Tools/bo_to_doc_v6.py --max-workers 5
```

### 6.7 Enhanced Summary v4 模式

```bash
# 单 BO 测试
python Tools/bo_to_doc_v4.py --bot banshees --max-workers 1

# 全部 10 个 BO (5 并发)
python Tools/bo_to_doc_v4.py --max-workers 5
```

### 6.8 自定义数据目录彀

```bash
python Tools/bo_to_doc.py --data-dir "path/to/another/run" --output-dir "path/to/output"
```

---

## 7. 输出文件结构

### `{bot_name}.md`

```markdown
# Summary
(一段自然语言策略概述)

# Details
[Step 1] (自然语言描述)
[Step 2] (自然语言描述)
...
```

### `step_index.json`

```json
{
  "total_trajectories": 10,
  "success": 10,
  "failed": 0,
  "bo_data": {
    "banshees": {
      "bot_name": "...",
      "map": "KairosJunctionLE",
      "sequence_file": "...",
      "total_actions": 176,
      "total_steps": 15,
      "steps": [
        {"step": 1, "range": [0, 9], "action_count": 10, "llm_call_done": true},
        ...
      ]
    },
    ...
  }
}
```

---



---


## 8. 依赖

```
openai          # OpenAI SDK (api_call.py 使用)
data_base_add_graph.json  # SC2 关系数据库
DeepSeek API    # deepseek-v4-flash, api.deepseek.com/v1
```

## 9. Precision 模式 (v2) 与Original v1 的差异

Precision 模式的设计目标：**所有数量精确化 + 步间完全独立**，适用于需要精准数值、不希望 LLM 跨步"串联"场景。

### 9.1 核心差异

| 维度 | Original v1 | Precision v2 |
|------|-----------|-------------|
| 数量风格 | 精确+模糊交替（规→。| **全部精确数字*，由脚本统计注入 prompt |
| 跨步上下。| 上一次 LLM 输出传给下一步（BuildContext。| **完全移除**，每步只看自己的聚合 action 。|
| Prompt 输入 | 逐条列出 action 。| **按产物聚合*：`[Train] Marine: ×4` |
| 持续生产 | 仅最后一步可用（规则8＀| 任何时候都可以，但数量必须精准 |
| 序数。| 基于累计状态("third Barracks") | **仅同 step 内* ("first and second Barracks")，否则只"a Barracks" |
| 输出目录 | `bo_docs/` | `bo_docs_precise/` |

### 9.2 Step 内Action 聚合

脚本在每步内统计每种产物的数量，生成聚合摘要喂给 LLM。

```
[Build] SupplyDepot: ×2
[Train] SCV: ×6
[Train] Marine: ×4
[Build] Refinery: ×1
[Build] Factory: ×1
[Morph] OrbitalCommand: ×1
```

分类规则：
- `UPGRADETO*` 动作 的`[Morph]`
- `is_structure=True` 的`[Build]`
- `is_addon=True` 的`[Addon]`
- `ptype=Unit` 的`[Train]`
- `ptype=Upgrade` 的`[Research]`

### 9.3 风格规则 (9 条

同 v1 →11 条规则相比，v2 做了以下调整

- **删除**规则 4（精→模糊交替）、规则 8（仅最后一步可用持续生产）
- **新增**规则 2（全部精确数值）、规则 3（步间完全独立）
- **修改**规则 4（序数词仅限于step 内）

完整规则。[prompt_template_v2.py](bo_2_nlstep\Tools\prompt_template_v2.py) 的`SYSTEM_PROMPT`→

### 9.4 输出示例

```markdown
[Step 1] Produce 6 SCVs while constructing the first and second Supply Depots,
a Barracks, and a Refinery to set up early production capacity and gas income.

[Step 2] Produce 6 SCVs and 4 Marines while building a Supply Depot, a Refinery,
and a Factory. Simultaneously, take an expansion with a new Command Center.
```

对比 v1 的同一阶段：
```markdown
[Step 1] Keep SCV production going while setting up the first Barracks and Refinery,
then train a few Marines for defense before moving into Factory tech.
```


---

## 10. Slang 模式 (v3) 与Original v1 的差异

Slang 模式的设计目标：**无跨步上下文 + SC2 社区黑话/缩写**，适用于想要读起来像资深玩家之间交流的 Build Order 文档　

### 10.1 核心差异

| 维度 | Original v1 | Slang v3 |
|------|-----------|----------|
| 跨步上下。| 上一次 LLM 输出传给下一步（BuildContext。| **完全移除**，每步只看自己的 action 。|
| 语言风格 | 标准英文，全称为主| **SC2 社区黑话/缩写**（depot, rax, CC, OC, BC, tank, stim 等） |
| 数量风格 | 精确+模糊交替（同 v1。| **同 v1**（保留精确模糊交替。|
| 对局选择 | 随机选一条胜利轨。| **选最高难度的胜利对局**（veryhard > harder > hard > mediumhard > medium。|
| Prompt 输入 | 逐条列出 action 名（同 v1。| **同 v1**（逐条标注 Action。|
| 输出目录 | `bo_docs/` | `bo_docs_slang/` |

### 10.2 黑话术语表

• System Prompt 中内嵌完整的 Terran 社区黑话映射表，覆盖

- **建筑**: depot, rax, CC, orbital/OC, PF/planetary, factory/fact, starport/port, ebay, armory, turret 筀
- **单位**: worker/SCV, marine, marauder, tank, BC/BCs, lib, medivac, banshee, raven, ghost, hellion, thor, viking 筀
- **升级**: stim, shield/combat shield, blue flame, banshee cloak, yamato, +1 attack, mech +1 筀
- **宏观动作**: throw down a depot, add rax, make an orbital / morph an OC, take an expo / take the natural / take the third

规则要求 LLM 自然使用黑话，不强行每个词都替换，保持可读性。

### 10.3 风格规则 (11 条

同 v1 →11 条规则相比，v3 做了以下调整

- **删除**规则 8（仅最后一步可用持续生产）→无跨步上下文→最后一次概念失去意义
- **删除** Context Awareness 段落
- **新增**规则 8（使用SC2 社区黑话
- 其余规则。v1 一次

### 10.4 输出示例

```markdown
[Step 4] Keep SCV production humming from the Command Center while the rax
cranks out a couple of Marines for map presence. From the factory, roll out
a Siege Tank for defensive positioning, and from the starport, produce a
Banshee for harassment or scouting. Don't forget to add a depot to keep
supply open.

[Step 9] Keep pumping Marines out of the rax while your factory produces a
Siege Tank and your starport adds a Banshee for harassment. Throw down two
more depots to avoid supply blocks, then continue reinforcing the army with
four additional Marines.
```

对比 v1 的同一阶段：
```markdown
[Step 4] Pump out SCVs from both Orbital Commands while training a couple
of Marines for defense. Build a Siege Tank from the Factory and a Banshee
from the Starport to apply early pressure, and add another Supply Depot to
prevent supply block.
```

完整术语表见 [prompt_template_v3.py](bo_2_nlstep/Tools/prompt_template_v3.py) 的`SYSTEM_PROMPT`→

## 11. Enhanced Summary 模式 (v4) 与Slang v3 的差异

Enhanced Summary 模式的设计目标：**在保留Slang v3 全部 Step 描述风格不变的前提下，将 Summary 升级为结构化战术摘要**，包含核心兵种组合、战术思想提炼，以及供后续决策参考的外推引导句

### 11.1 核心差异

| 维度 | Slang v3 | Enhanced Summary v4 |
|------|----------|---------------------|
| Step 描述 | SC2 黑话风格，无跨步上下。| **完全不变**（复同 v3 →SYSTEM_PROMPT。|
| Summary 结构 | 一段式自然语言概括 | **4 句结构化摘要** |
| 战术思想提炼 | 顺带提及在组成描述中 | **独立首句**：核心兵种组合+ 战术哲学 |
| 中后期方。| 最后一句顺带提取| **独立第三句*：生产方向+ 关键升级/配置 |
| 策略外推引导 | 。| **固定末句**→Use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds." |
| 对局选择 | 最高难度胜。| **同 v3** |
| 输出目录 | `bo_docs_slang/` | `bo_docs_enhanced/` |

### 11.2 Summary 4 句结构

每篇 `.md` →Summary →LLM 严格按以→4 句输出：

**Sentence 1 —Core Composition & Tactical Concept**
主力兵种组合 + 核心战术哲学（如 sustained pressure、timing push、macro greed、harass-heavy 等）　

**Sentence 2 —Early Game & Tech Path**
开局节奏、扩张时机、科技路线（如 Rax →Factory →Starport 的顺序）→

**Sentence 3 —Mid-Game Production Direction**
中后期生产方向、关键升级和 add-on 配置。

**Sentence 4 —Closing Guidance（固定句式）**
`Use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds.`

### 11.3 设计意图

Summary 的升级服务于一个核心场景：当所有Step 执行完毕后，玩家需要基于对整体战术思想的理解来外推下一步决策。传统的「一段式概括」只提供了回顾性总结，Enhanced Summary 通过→

- **首句点明战术哲学** →玩家明确这场对局的核心理→
- **→3 句描述生产方向* →玩家知道应该朝什么路线继续发送
- **→4 句固定引→* →明确提示"以此为基础，根据侦查调整决→

从而让 Summary 从「回顾」变成「展望」的起点。

### 11.4 输出示例

→banshees BO 为例

> This build revolves around a marine-tank-banshee core, leaning on early banshee harass to soften the opponent before rolling into a powerful timing push with sieged tanks and marines. You open with a standard reaper-less expand, dropping your natural CC early, grabbing double gas, and teching through rax into factory and starport—slapping tech labs on both to unlock tanks and banshees. Mid-game you add a couple more rax, keep pumping marines, tanks, and banshees, grab Combat Shield, and take a third while staying aggressive with constant harass and pressure. Use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds.

完整模板：[prompt_template_v4.py](bo_2_nlstep/Tools/prompt_template_v4.py) 的`SUMMARY_SYSTEM_PROMPT`→


## 12. Balanced Phase Summary 模式 (v5) 与Enhanced Summary v4 的差异

Balanced Phase Summary 模式的设计目标：**在保留Slang v3/v4 全部 Step 描述风格不变的前提下，将 Summary 改为 3 句式 Early/Mid/Late 均衡结构，并将策略外推句Summary 中剥离，作为最后一个独立的 Step N 插入 Details 末尾**→

### 12.1 核心差异

| 维度 | Enhanced Summary v4 | Balanced Phase Summary v5 |
|------|---------------------|---------------------------|
| Step 描述 | SC2 黑话风格，无跨步上下。| **完全相同**（复同 v4 →SYSTEM_PROMPT。|
| Summary 结构 | 4 句式（Composition + Early + Mid + 固定外推句） | **3 句式（Early + Mid + Late）*，禁止外推句 |
| 策略外推 | Summary →4 句固定句。| **独立 Step**：`[Step N+1]` 插入 Details 末尾 |
| 与后期覆盖 | 侧重中期，早期合并到第 2 。| **均衡**：每句严格对应一个阶。|
| 对局选择 | 最高难度胜。| **同 v4** |
| 输出目录 | `bo_docs_enhanced/` | `bo_docs_balanced/` |

### 12.2 Summary 3 句式结构

**Sentence 1 →Early Game**（开局 + 扩张 + 科技起点→
→用什么开局、何时扩张、科技路线起点（Rax →Factory →Starport 顺序）、add-on 选择

**Sentence 2 →Mid Game**（中期生→+ 关键升级 + 兵力成型
→生产建筑规模、关键升级时机（Stim/Combat Shield/Cloak）、兵种组合成型、中期节│

**Sentence 3 →Late Game**（后期方向+ 终局形态）
→最终兵力结构、经济扩张到几矿、终局计划（持续压制/ 科技转型 / 满人timing│

### 12.3 Final Step 结构

在所有常规Step 生成完毕 句式 Summary 生成完毕之后，额外调用一次 LLM 生成 `[Step N]`（N = 最后一个常规step 编号 + 1），内容定位后续决策指南"。

1. **Reinforcement continuity**：延续的核心兵种生产和macro 循环
2. **Scout-based adaptations**→-3 →如果侦察→X，则调整 Y"的决策框架
3. **Key transition cue**：一个自然延伸的后期过渡或科技转型方向
4. **Closing guidance**：固定结尾句 "Use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds."

### 12.4 设计意图

Summary 同 v4 同 v5 升级服务于一个核心场景：当所有Step 执行完毕后，玩家需要基于对整体战术思想的理解来外推下一步决策。v4 将策略外推藏在Summary 最后一句话里，v5 将其独立为一次Step→
- Summary 专注回顾"——按时间线均衡总结后期
- 最后一次Step 专注展望"——基于已生成Summary 和所有Step，给出actionable 的后续决策指引

从而让 Summary 同 v4 →回顾+一句展望变成纯粹的阶段均衡总结，Final Step 则承担起独立决策起点"角色

### 12.5 输出示例

→banshees BO 为例

**Summary (v5 →3 句式均衡)**→
> Opened with a standard 1 Rax expand into Factory and Starport, taking a fast natural and slapping tech labs on both to rush out a Siege Tank and Banshee for early aggression. Mid-game you ramp up production with a second Rax and extra Starport, churning out Marines, Tanks, and Banshees while grabbing Combat Shield and maintaining constant depot spacing to avoid supply blocks. Late game you plop down a third CC and a pair of extra Rax, aiming for a nasty Marine-Tank-Banshee timing that leverages sustained harass and overwhelming macro to close out the game.

**Final Step (v5 →独立Step 16)**→
> [Step 16] Keep pumping Marines, Tanks, and Banshees from your 3 barracks, factory, and 2 starports while constantly depoting. If you scout a heavy roach or hydra push, add more tanks and spread them well; if your opponent masses mutalisks or phoenix, cut Banshees for Vikings from your starports; and against high-tech ground like Colossi or Infestors, work in a Ghost Academy for EMP or snipe. As you secure a fourth base, transition into adding a second factory for more tanks or start upgrading to a Battlecruiser fleet for unstoppable late-game aggression. Use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds.


---

## 13. Concise Style Summary 模式 (v6) 与Balanced Phase Summary v5 的差异
Concise Style Summary 模式的设计目标：**在保留Slang v3/v4/v5 全部 Step 描述风格不变 + 3 句式 Balanced Summary 不变的前提下，将 Final Step →详细的后续决策指引精烬简短的战略风格概括"**。只点出这个 Build Order 的战略基因，把后续决策权还给玩家
### 13.1 核心差异

| 维度 | Balanced v5 | Concise Style Summary v6 |
|------|------------|--------------------------|
| Step 描述 | SC2 黑话风格，无跨步上下。| **完全相同**（复同 v5 →SYSTEM_PROMPT。|
| Summary 结构 | 3 句式 Early / Mid / Late | **完全相同** |
| Final Step 角色 | 详细的后续决策指。| **简略战略风格概括* |
| Final Step 内容 | 持续生产循环 + 2-3 条侦察应+ 转型方向 + 固定收尾。| 1-2 句风格概括+ 固定收尾。|
| Final Step 禁止。| 。| 禁止给出具体生产建议、禁止if X then Y"适应分支、禁止提及基地数/升级时间/科技转型目标 |
| 对局选择 | 最高难度胜。| **同 v5** |
| 输出目录 | `bo_docs_balanced/` | `bo_docs_concise/` |

### 13.2 Final Step 结构

v5 的Final Step 包含 4 个要求：
1. Reinforcement continuity（持续生产指南）
2. Scout-based adaptations→-3 条具体如果……则…与3. Key transition cue（后期转型方向）
4. Closing guidance（固定收尾句）
v6 **砍掉3 →*，只保留
1. **Strategy style summary**＀-2 句）
   - 用简短的 slang 概括这个 BO 的战略特征风格基因
   - 聚焦于build →DNA：节奏感、姿态（aggressive/defensive/macro/tech-heavy/harassment-based）、整体战略定位   - 不列具体兵种数量、不列生产建筑、不给出后续生产指令

2. **固定收尾句*
   `Use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds.`

### 13.3 设计意图

Summary 同 v5 →v6 升级服务于一个核心场景：当所有Step 执行完毕、Summary 回顾完整→build 之后，玩家需要的是一次*方向的提取*而非详细的行动计划
v5 →Final Step 承担了过多的"教练"角色——具体告诉玩家接下来出什么、怎么转型、侦察到什么怎么办。但这实际上越界了：一旦进入未知局面，这些具体建议可能反而是误导
v6 的选择：- Final Step 只说"这是什么类型的打法"——让玩家自己建立后续决策的框架- 不假装知道游戏会怎么发展——把"根据侦察自行判断"作为核心原则
- Summary 保持 3 句式阶段回顾，Final Step 变成简洁的"基调确认"

### 13.4 使用方式

```bash
# 单 BO 测试
python Tools/bo_to_doc_v6.py --bot banshees --max-workers 1

# 全部 10 个 BO→ 并发python Tools/bo_to_doc_v6.py --max-workers 5
```

### 13.5 输出示例对比

→banshees BO 为例
**v5 Final Step（太详细，太长）**→> [Step 16] Keep producing marines, siege tanks, and banshees from your 5 raxes, factory, and 2 starports while constantly adding SCVs and depots to sustain a maxed army; if you scout heavy air like mutas or colossi, swap some banshees for vikings or thors, and if you see a fast third base from the opponent, drop a few more tanks to lock down their expansions; as you secure a fourth base and approach 200 supply, consider adding a fusion core for battlecruisers to break entrenched positions or upgrade to +3/+3 for a decisive push; use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds.

**v6 Final Step（精炼，克制*→> [Step 16] This build embodies a marine-tank-banshee harassment style that looks to snowball through multi-pronged pressure into a decisive timing push. Use this gameplan as your strategic baseline — adapt your decisions based on what you scout and how the game unfolds.

---

## 14. 运行记录

### 2026-06-19 — v6 全量运行

| 项目 | 值 |
|------|-----|
| 模式 | Concise Style Summary v6 |
| Bot 数量 | 10 |
| 成功率 | **10/10** |
| 输出目录 | `Tools/bo_docs_concise/` |

各 BO 概况：

| Bot | Actions | Steps (含 Final) | 对局难度 |
|-----|---------|------------------|----------|
| banshees | 176 | 15 + 1 | Zerg mediumhard |
| battle_cruisers | 168 | 15 + 1 | Zerg mediumhard |
| bio | 187 | 17 + 1 | Zerg veryhard |
| cyclones | 204 | 19 + 1 | Zerg mediumhard |
| marine_rush | 93 | 9 + 1 | Zerg veryhard |
| one_base_turtle | 104 | 10 + 1 | Zerg veryhard |
| rusty | 238 | 22 + 1 | Zerg veryhard |
| safe_tvt_raven | 236 | 21 + 1 | Zerg mediumhard |
| terran_silver_bio | 223 | 21 + 1 | Zerg veryhard |
| two_base_tanks | 195 | 18 + 1 | Zerg veryhard |

所有 Final Step 均为 1-2 句战略风格概括 + 固定收尾句，不包含具体生产建议、侦察应对分支或科技转型目标。
