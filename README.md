# StarCraft II LLM Multi-Agent Framework

本项目是一个基于大语言模型 (LLM) 驱动的《星际争霸 II》自动化 AI 框架。项目底层封装了基于规则的 SC2 运行平台（如 `sharpy`），并在其之上构建了一个 **三层多智能体 (Multi-Agent) 架构**（`SC2_Agent`），将人类的自然语言指令逐步降维、拆解，最终转化为游戏中可执行的严格宏观与微操指令。

---

## 🧠 SC2_Agent 架构与决策机制

`SC2_Agent` 目录是本项目的大模型决策核心，分为 **Top Agent**, **Mid Agent** 和 **Down Agent**。它们呈自上而下的链式结构，分别负责战略宏观指导、阶段性运营规划以及具体动作的参数化翻译。

### 1. Top Agent (全局指挥官)

**职责**：负责整局游戏的宏观战略把控和阶段性焦点转移。不涉及具体造兵细节，只做大方向决策。

* **决策机制**：
* **开局 (t=0)**：根据人类玩家输入的自然语言 `instruct`（例如：“打一波以大和为主的攻击”），以及预先加载的各族可用战术库（策略名与描述），评估并选择本局的唯一最优战术。
* **定时轮询 (每 60 秒)**：结合当前游戏内的全局观测信息（Observation），评估当前游戏处于哪个阶段（early/mid/late），并给出一个简短的“焦点指导”（Focus），指明当前阶段最紧急的核心目标。


* **Prompt 包含内容**：
* **System Prompt**：定义角色为顶级战略家。包含所有可用策略的列表（开局时），或选定策略的详细描述（轮询时）。规定了仅允许输出严格的 JSON 格式数据。
* **User Prompt**：包含人类玩家的原始指令，以及实时的 `[Current Observation]`（当前资源、人口、时间等观测文本）。
* **输出 Schema**：开局输出 `{"strategy": "..."}`；后续输出 `{"phase": "mid", "focus": "..."}`。



### 2. Mid Agent (运营执行官)

**职责**：负责维持并更新一段周期内的（通常每隔十几秒一次）自然语言任务列表。

* **决策机制**：接收 Top Agent 传达的战略思想和阶段焦点，对比上一轮的任务列表和当前的观测信息。决定移除已完成的任务、修改现有任务的数量目标，或插入新的阶段性任务。**任务列表的顺序代表绝对的资源分配优先级**（排在前面的任务优先获取矿物/天然气）。
* **Prompt 包含内容**：
* **System Prompt**：定义其作为宏观计划管理者的角色。注入底层平台的“执行模型”规则（任务按顺序抢占资源、底层为声明式执行等）。明确注入 Top Agent 的战略描述、当前 Phase 和 Focus。
* **User Prompt**：包含实时的 `[Current Observation]` 以及上一轮生成的 JSON 格式 `[Previous Natural-Language Tasks]`。
* **输出 Schema**：必须先输出一段思维链（reasoning）分析，然后输出 `{"tasks": ["造2个兵营", "生产机枪兵直到20个", ...]}`。



### 3. Down Agent (动作翻译官)

**职责**：纯粹的翻译层，将 Mid Agent 吐出的**单条**自然语言任务，精准映射为底层代码能理解的 API Action Key 和目标数量。

* **决策机制**：作为无状态的转换器，严格受限于给定的 `[Legal Action Space]`（合法动作空间）。如果自然语言任务无法对应到合法动作，将被视为无效并丢弃。
* **Prompt 包含内容**：
* **System Prompt**：约束其为一个精准的翻译机器，提供当前底层平台支持的所有合法操作字典（如 `{"BUILD_MARINE": "Train Marine units", ...}`）。
* **User Prompt**：包含实时的 `[Current Observation]` 和单条 `[Task Description]`（如：“将机枪兵补充到 20 个”）。
* **输出 Schema**：只允许输出严格的 JSON `{"action": "<action_key>", "to_count": <int>}`。



### 🤝 与底层平台的对接机制

1. **指令降维**：人类指令 -> `Top Agent` 确定大方向 -> `Mid Agent` 拆解为有顺序的自然语言任务队列 -> `Down Agent` 逐条翻译为 JSON Action。
2. **声明式执行 (Declarative Execution)**：底层平台（基于 `sharpy` 改造）收到 `Down Agent` 的 JSON 指令后，采用“目标驱动”的方式执行。例如收到 `{"action": "TRAIN_MARINE", "to_count": 20}`，底层会自动判断当前机枪兵数量，只要不足 20 且资源允许，底层队列就会不断请求生产，而不需要 LLM 频繁微操。
3. **优先级阻塞**：`Mid Agent` 排在列表最前方的任务拥有最高的资源分配权。如果前置核心建筑（如首个补给站）未完成，它会吞噬资源，保证卡脖子科技最先落地，然后才执行列表后方的任务。

---

## ⚙️ LLM 配置指南

大模型的调用配置完全由 `API_config/llm_settings.json` 掌控。为了在成本、速度和智商之间取得平衡，框架采用了 **双 Profile 机制**：

* **`stage1_reasoning`** (用于 Top Agent & Mid Agent)
* **定位**：需要进行复杂战略推理、思维链分析。
* **推荐配置**：使用带有 Reasoning/Thinking 能力的大模型（如 `deepseek-v4-pro` 或 `glm-4-plus`）。温度 (`temperature`) 可适度调高（如 0.6）以激发发散性思维。


* **`stage2_translation`** (用于 Down Agent)
* **定位**：需要极快的响应速度和极度严格的 JSON 输出，不需要发散推理。
* **推荐配置**：使用速度极快的 Flash/Lite 级别模型（如 `deepseek-v4-flash`）。温度 (`temperature`) 设为 `0.0`，并开启强制 JSON 返回 (`"response_format": {"type": "json_object"}`)。



**智能 Vendor 分发机制 (`vendor_dispatch`)**：
配置文件内建了适配器规则，根据你填入的 `model` 名称（如匹配 `kimi`, `glm`, `deepseek`, `qwen` 等关键字），底层 `llm_caller.py` 会自动向对应的云端 API 注入正确的开启深度思考（Thinking/Reasoning）的特定字段，无需修改代码。

---

## 🚀 测试与运行

本项目提供了两个强大的启动脚本，用于针对星际争霸 2 内置 AI 展开对战测试。测试生成的录像与日志默认存放在 `./game_records` 目录下。

### 1. 单局测试 (`run_vs_ai.py`)

适合用来调试特定的 prompt 或指令。可以直连 SC2 客户端观察实况。

```bash
# 基础运行（默认使用 Terran，对战 Hard 难度的 Terran）
python run_vs_ai.py

# 自定义指令，并开启人类实时观测 (-rt/--real-time)
python run_vs_ai.py \
  --real-time \
  --bot-instruct "速生科技，打一波隐刀rush" \
  --bot-race protoss \
  --enemy-race zerg \
  --enemy-difficulty veryhard \
  --map-name Equilibrium513AIE

```

**常用参数说明**：

* `--bot-instruct`: 传给 Top Agent 的人类自然语言指令。
* `--top-model` / `--mid-model` / `--down-model`: 用于覆盖配置文件中的默认模型（方便 A/B 测试不同模型能力）。
* `--enemy-build`: 设置内置 AI 的风格（如 `macro`, `rush`, `timing` 等）。

### 2. 批量并发测试 (`run_vs_ai_batch.sh`)

用于进行多局基准测试（Benchmark）。脚本支持使用 `tmux`（推荐）或直接在后台 `fg` 起多个进程，并发压测大模型并收集胜率统计。

```bash
# 用法: bash run_vs_ai_batch.sh <总局数> <并发数> [fg|tmux]

# 示例：总共运行 10 局，同时开 2 个 SC2 实例跑，使用 tmux 托管会话
bash run_vs_ai_batch.sh 10 2 tmux

```

如果需要修改对战配置，可以通过传递环境变量来覆盖默认值：

```bash
MAP_NAME="KairosJunctionLE" ENEMY_RACE="zerg" BOT_INSTRUCT="防守反击，暴兵爆蟑螂" bash run_vs_ai_batch.sh 20 4 tmux

```

*提示：使用 tmux 模式启动后，脚本会打印一个 `tmux attach -t sc2_batch_...` 的命令，你可以复制执行进入终端查看各个并发 worker 的实时日志。*

---

## 📦 Python 环境安装

本项目依赖的包列在 `requirements.txt` 中。请确保你的 Python 版本为 **3.8 - 3.10**（过高的 Python 版本可能会导致 `s2clientprotocol` 兼容问题）。

```bash
# 建议使用虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows 用户使用: venv\Scripts\activate

# 安装 Python 依赖
pip install -r requirements.txt

```

**核心依赖说明**：

* `s2clientprotocol` / `python-sc2` 核心协议库：用于与星际争霸 II 客户端进行底层通讯。
* `requests` / `aiohttp`：用于和各个大语言模型的 API 服务通信。
* `loguru`：提供高可读性的并发异步日志记录。

*注意：要使脚本成功运行，您还需要在您的操作系统上安装《星际争霸 II》游戏客户端，并将对应的天梯地图（如 `.SC2Map` 文件）放置在游戏的 `Maps` 目录下（或项目的 `maps` 文件夹中）。*