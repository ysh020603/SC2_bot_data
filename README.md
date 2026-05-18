# StarCraft II LLM Multi-Agent Framework

本项目是一个基于大语言模型 (LLM) 驱动的《星际争霸 II》自动化 AI 框架。项目底层封装了基于规则的 SC2 运行平台（如 `sharpy`），并在其之上构建了一个 **三层多智能体 (Multi-Agent) 架构**（`SC2_Agent`），将人类的自然语言指令逐步降维、拆解，最终转化为游戏中可执行的严格宏观与微操指令。

---

## 🧠 SC2_Agent 架构与决策机制

`SC2_Agent` 目录是本项目的大模型决策核心，分为 **Top Agent**, **Mid Agent** 和 **Down Agent**。它们呈自上而下的链式结构，分别负责战略宏观指导、阶段性运营规划以及具体动作的参数化翻译。

### 1. Top Agent (全局指挥官)

**职责**：负责整局游戏的宏观战略把控和阶段性焦点转移。不涉及具体造兵细节，只做大方向决策。

* **决策机制**：
* **开局 (t=0) - 交互式决策流 (Agentic Workflow)**：开局阶段不再是单次生成，而是升级为多轮对话引擎。系统首先向大模型提供人类的初始指令、当前种族，以及 `SKILL` 目录下所有可用战术的**摘要列表**。LLM 可以在多轮对话中评估并输出以下三种动作：
* **`VIEW`**：请求查看某个策略的详细内容。系统会读取对应战术的 `Top_agent_0.md` 完整内容并返回，同时保留其思考过程的历史记忆，供其再次评估。
* **`SELECT`**：直接选择匹配度最高的已有策略。
* **`GENERATE`**：当已查看的策略均不满足人类指令时，基于已有信息动态生成一套全新的定制化策略。


* **定时轮询 (每 60 秒)**：结合当前游戏内的全局观测信息（Observation），评估当前游戏处于哪个阶段，并给出一个简短的“焦点指导”（Focus）。如果系统开启了扩展 Prompt 开关，会自动拉取该策略对应的 `Top_agent_60.md` 知识库文件，将其作为额外的阶段性指导 (Phase Guidance) 动态注入。


* **Prompt 包含内容**：
* **System Prompt**：定义角色为顶级战略家。
* **User Prompt**：包含人类玩家的原始指令、实时的 `[Current Observation]`。
* **输出 Schema**：开局输出动作指令 (`VIEW`/`SELECT`/`GENERATE`)；轮询时输出 `{"phase": "mid", "focus": "..."}`。



### 2. Mid Agent (运营执行官)

**职责**：负责维持并更新一段周期内的（通常每隔十几秒一次）自然语言任务列表。

* **决策机制**：接收 Top Agent 传达的战略思想和阶段焦点，对比上一轮的任务列表和当前的观测信息。决定移除已完成的任务、修改现有任务的数量目标，或插入新的阶段性任务。**任务列表的顺序代表绝对的资源分配优先级**。
* **Prompt 包含内容**：
* **System Prompt**：定义其作为宏观计划管理者的角色。如果系统开启了扩展 Prompt 开关，会自动拉取对应策略的 `mid_agent.md` 文件，作为执行指导 (Execution Guidance) 动态注入。
* **User Prompt**：包含实时的 `[Current Observation]` 以及上一轮生成的 `[Previous Natural-Language Tasks]`。
* **输出 Schema**：必须先输出一段思维链（reasoning）分析，然后输出 `{"tasks": ["造2个兵营", "生产机枪兵直到20个", ...]}`。



### 3. Down Agent (动作翻译官)

**职责**：纯粹的翻译层，将 Mid Agent 吐出的**单条**自然语言任务，精准映射为底层代码能理解的 API Action Key 和目标数量。作为无状态的转换器，严格受限于给定的合法动作空间，无法对应合法动作的任务将被丢弃。

---

## 🗂️ SKILL 战术知识库结构

项目中的具体战术知识与 Prompt 指导文件统一维护在 `SKILL/<race>/` 目录下。无论是预设战术还是 LLM 生成的全新战术，都有严密的读取路由逻辑：

* **具体战术目录 (如 `SKILL/terran/battle_cruisers/`)**
* `Top_agent_0.md`：包含该战术的 `# 摘要`（用于 t=0 时的粗略筛选）与 `# 详细内容`。
* `Top_agent_60.md`：该战术专属的 t=60 轮询阶段战略提示。
* `mid_agent.md`：该战术专属的 Mid Agent 运营执行提示。


* **通用/兜底目录 (`SKILL/<race>/generic/`)**
* 当 Top Agent 在 t=0 使用 `GENERATE` 动作生成了自定义策略时，由于没有专属文件夹，系统会在游戏运行时自动路由到此 `generic` 目录，读取通用的 `Top_agent_60.md` 和 `mid_agent.md` 作为兜底指导。



---

## ⚙️ LLM 配置指南

大模型的调用配置完全由 `API_config/llm_settings.json` 掌控。为了在成本、速度和智商之间取得平衡，框架采用了 **双 Profile 机制**：

* **`stage1_reasoning`** (用于 Top Agent & Mid Agent)：推荐使用带有 Reasoning/Thinking 能力的大模型（如 `deepseek-v4-pro` 或 `glm-4-plus`）。
* **`stage2_translation`** (用于 Down Agent)：推荐使用速度极快的 Flash/Lite 级别模型（如 `deepseek-v4-flash`），温度设为 `0.0` 并开启强制 JSON 返回。

**智能 Vendor 分发机制**：底层 `llm_caller.py` 会根据填入的 `model` 名称自动向对应云端 API 注入开启深度思考的特定字段。

---

## 🚀 测试与运行

本项目提供了两个强大的启动脚本，用于针对星际争霸 2 内置 AI 展开对战测试。测试生成的录像与日志默认存放在 `./game_records` 目录下。

### 1. 单局测试 (`run_vs_ai.py`)

适合用来调试特定的 prompt 或指令。可以直连 SC2 客户端观察实况。

```bash
# 基础运行（默认使用 Terran，对战 Hard 难度的 Terran）
python run_vs_ai.py

# 自定义指令，并开启 Prompt 注入开关与人类实时观测
python run_vs_ai.py \
  --real-time \
  --bot-instruct "速生科技，打一波隐刀rush" \
  --bot-race protoss \
  --enemy-race zerg \
  --enemy-difficulty veryhard \
  --map-name Equilibrium513AIE \
  --use-top-60-prompt \
  --use-mid-prompt

```

**新增参数说明**：

* `--use-top-60-prompt`: 开启此开关后，游戏进行中将读取对应策略的 `Top_agent_60.md` 文件注入到 Top Agent 的提示词中。
* `--use-mid-prompt`: 开启此开关后，将读取 `mid_agent.md` 文件注入到 Mid Agent 的提示词中。

### 2. 批量并发测试 (`run_vs_ai_batch.sh`)

用于进行多局基准测试（Benchmark）。脚本支持使用 `tmux`（推荐）并发压测大模型并收集胜率统计。

你可以通过传递环境变量来控制对战配置和 Prompt 注入开关：

```bash
# 用法: bash run_vs_ai_batch.sh <总局数> <并发数> [fg|tmux]

# 示例：运行 20 局，4 个并发，开启知识库文档的动态注入
MAP_NAME="KairosJunctionLE" \
ENEMY_RACE="zerg" \
BOT_INSTRUCT="防守反击，暴兵爆蟑螂" \
USE_TOP_60_PROMPT="1" \
USE_MID_PROMPT="1" \
bash run_vs_ai_batch.sh 20 4 tmux

```

*提示：使用 tmux 模式启动后，脚本会打印一个 `tmux attach -t sc2_batch_...` 的命令，你可以复制执行进入终端查看各个并发 worker 的实时日志。*

---

## 📦 Python 环境安装

请确保你的 Python 版本为 **3.8 - 3.10**（过高的 Python 版本可能会导致 `s2clientprotocol` 兼容问题）。

```bash
# 建议使用虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows 用户使用: venv\Scripts\activate

# 安装 Python 依赖
pip install -r requirements.txt

```