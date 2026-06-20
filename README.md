# SC2 Agent OLD

这是一个基于 Sharpy / python-sc2 的《星际争霸 II》LLM Bot 实验仓库。当前代码的主线已经从旧版 `Top/Mid/Down` 声明式三层 Agent，迁移为：

```text
固定策略 Top_agent_0.md
  -> Naming Agent
  -> DATA_TOOLS 映射
  -> Ordering Agent
  -> Supply Planner
  -> ExecutionScheduler
  -> Sharpy / SC2
```

一句话说：玩家先固定一个人族策略目录，系统读取该策略的阶段说明，把每个阶段拆成标准单位、建筑、科技动作，再用命令式调度器逐帧执行。

当前主线只适配 Terran。Protoss / Zerg 的 Sharpy dummy bot 仍在仓库里，但 LLM 增量流水线和 `SKILL` 策略库目前按人族维护。

## 当前状态

- 主入口：`run_vs_ai.py`
- 通用 LLM Bot：`dummies/generic/universal_llm_bot.py`
- 策略目录：`SKILL/terran/<strategy>/`
- 策略文件：`SKILL/terran/<strategy>/Top_agent_0.md`
- 策略工具轨：`SKILL/terran/<strategy>/strategy_tools.py`
- 模型配置：`API_config/config.json`
- 对局记录：`game_records/`

重要变化：

- `UniversalLLMBot` 现在必须指定固定策略，`--force-strategy none` 会报错。
- 旧的 t=0 交互式策略选择、Mid/Down Agent 声明式执行路径已删除。
- 当前关键 LLM 调用点是 `--naming-model`、`--ordering-model`、`--executor-model`。

## 仓库结构

```text
SC2_Agent/
  naming_agent.py              # Stage 2: 策略 step + obs -> 标准实体名和数量
  ordering_agent.py            # Stage 4: 对标准 action 排序
  executor_agent.py            # 为 train/addon/morph 选择执行单位
  data_tools/                  # 内置 SC2 数据库、别名、成本、前置、冲突、补给规划
  execution/
    command.py                 # PlannedAction 状态对象
    direct_build.py            # Terran 普通建筑直接建造执行器
    executor_select.py         # 候选执行单位筛选
    mapping.py                 # 标准 action 与 SC2 / Sharpy 枚举映射
    scheduler.py               # ExecutionScheduler 核心调度器

dummies/generic/
  universal_llm_bot.py         # 编排策略读取、五阶段流水线、调度器和记录落盘

SKILL/terran/
  registry.json                # 人族策略白名单/索引（force-strategy 模式）
  marine_rush/
  battle_cruisers/
  banshees/
  bio/
  cyclones/
  one_base_turtle/
  rusty/
  safe_tvt_raven/
  terran_silver_bio/
  two_base_tanks/

BO_list/terran/                # BO list 直接执行模式（bo-list 模式）
  registry.json                # 已注册 BO list 策略名单
  marine_rush/
    BO.json                    # 标准 action 名顺序列表（直接喂入 ExecutionScheduler）
    strategy_tools.py          # 不耗资源的后台战术（与 SKILL 同名文件等价）

API_config/
  config.example.json          # OpenAI 兼容模型配置模板
  config.json                  # 本地实际模型配置，不要提交真实 key

bot_loader/                    # Bot 注册、对局启动、内置 AI 参数解析
sharpy/                        # Sharpy 框架主体
docs/                          # 系统说明、环境配置、测试记录和经验总结
```

## 核心流程

### 1. 固定策略

运行时通过 `--force-strategy <name>` 指定策略目录，例如：

```bash
python run_vs_ai.py --force-strategy marine_rush
```

策略名对应：

```text
SKILL/terran/marine_rush/
  Top_agent_0.md
  strategy_tools.py
```

`Top_agent_0.md` 中的 `# Details` 会被解析为若干 `[Step N]`，`# Summary` 同时被保留。每次宏观流水线触发时，Bot 取当前 step 原文作为本轮宏观目标；当所有 `[Step N]` 都装入调度器之后，自动切换到 **Summary 模式**，后续每次决策都改用 `# Summary` 全文作为 plan_text，而不再重复最后一个 step。

### 2. 五阶段宏观流水线

触发入口在 `UniversalLLMBot.pre_step_execute()`。首次进入、动作序列执行完、或队列只剩 deferred 动作时，会 append 下一阶段动作。

五阶段如下：

| 阶段 | 作用 |
|---|---|
| Strategy Step Source | 读取当前策略 step 文本；所有 step 走完后切换为 `# Summary` |
| Naming Agent | 将自然语言目标转成 Terran 标准实体名和数量 |
| DATA_TOOLS | 将实体名映射成标准 action key，并提供成本、前置、冲突信息 |
| Ordering Agent | 在前置、冲突、成本提示下对 action 排序 |
| Supply Planner | 默认托管补给站插入，避免供给卡死 |

Ordering 阶段不会用代码补齐 LLM 漏掉的动作。漏项和非法项会写入轨迹 JSON，用来保留模型评估信号。

### 3. 命令式执行调度

`ExecutionScheduler` 每帧执行 action 队列，核心机制包括：

- `PENDING / WAITING / RUNNING / DONE / ABANDONED` 状态机
- 独立 waiter 槽，等待资源、科技、人口或执行单位
- P0 / P1 / P2 优先级扫描：补 supply 的动作优先，不耗 supply 的动作次之，训练单位最后
- waiter 资源预留和同档超车
- 科技前置检测和缺失前置自动插入
- Terran 普通建筑的 DirectBuild 独立 reservation / target
- train / addon / morph 可调用 Executor Agent 选择执行单位
- waiting 超时和 running 卡死放弃，避免宏观队列永久堵塞

### 4. 策略工具轨

`create_plan()` 并行运行两条轨：

- `ExecutionScheduler`：执行五阶段流水线产出的资源动作。
- 当前策略自己的 `strategy_tools.py`：只放不消耗 minerals / gas / supply 的辅助战术工具。

没有全局后台战术 fallback。某个策略缺少侦察、攻击或防守工具时，需要在该策略自己的 `strategy_tools.py` 中补。

### 5. BO list 直接执行模式（旁路 LLM 流水线）

除上面的「固定策略 + 五阶段流水线」之外，`UniversalLLMBot` 还支持一种 **BO 直接执行模式**：完全跳过 Naming / Ordering / Supply Planner，把一份事先排好的标准 action 序列一次性灌进 `ExecutionScheduler`。

启用方式（与 `--force-strategy` 互斥）：

```bash
python run_vs_ai.py --bo-list marine_rush
```

策略目录结构（注册才能用，与 SKILL 同模式）：

```text
BO_list/terran/registry.json          # "registered_strategies": ["marine_rush", ...]
BO_list/terran/<name>/
  BO.json                             # JSON 数组，元素是标准 action 名（如 TERRANBUILD_SUPPLYDEPOT、BARRACKSTRAIN_MARINE）
  strategy_tools.py                   # 不耗资源的后台战术，与 SKILL 同名文件等价
```

行为约定：

- **跳过 LLM**：Stage 2/4 的 Naming / Ordering Agent 不再被调用；`--naming-model` / `--ordering-model` 在该模式下变为可选（不报错、不调用）。
- **保留 Executor LLM**：`--executor-model` 仍然生效。`ExecutionScheduler` 在 train / addon / morph 这种存在多个候选生产单位时，依然会通过 `executor_agent` 让 LLM 选择具体执行单位。
- **保留 Scheduler 全部能力**：独立 waiter 槽、矿/气/人口预留、同档超车、跨档隔离、deferred 同名 build、`wait_abandon` / `running_abandon` 超时这些机制全部继续生效。BO 模式只是把"action 列表的来源"从 LLM 改成了 BO.json，下游执行机制一字不改。
- **不做循环 / 不回退**：BO 全部执行完之后，scheduler 队列保持空闲；后台 `strategy_tools.py` 继续运行；不会回退到 LLM 流水线，也不会循环重放 BO。
- **注册校验**：未在 `BO_list/<race>/registry.json` 的 `registered_strategies` 中列出的名字会直接报错。
- **互斥**：`--force-strategy` 与 `--bo-list` 两选一，同时显式指定会报错。

详细的资源预留 / 超车语义参见 [docs/系统文档.md](docs/系统文档.md) §4.2 / §4.3。

## 环境配置

推荐环境：

- Python 3.11
- conda 环境名：`SC2_0615`
- 本地安装 StarCraft II，并设置 `SC2PATH`
- Windows 下建议设置 `PYTHONUTF8=1`

详细步骤请看：

- [docs/环境配置教程.md](docs/环境配置教程.md)
- [docs/系统文档.md](docs/系统文档.md)

最小安装示例：

```bash
conda create -n SC2_0615 python=3.11 pip -y
conda activate SC2_0615

pip install \
  "burnysc2==7.1.3" \
  "s2clientprotocol" \
  "mpyq" "portpicker" \
  "openai" "requests" "aiohttp" \
  "numpy" "scipy" "scikit-learn" \
  "opencv-python-headless" \
  "more-itertools" "six" \
  "protobuf==3.20.3" \
  "loguru"

pip install "pytest<7.0.0" "pytest-asyncio==0.20.3"
```

Windows PowerShell 常用环境变量：

```powershell
$env:SC2PATH='C:\Program Files (x86)\StarCraft II'
$env:PYTHONUTF8='1'
```

Linux 示例：

```bash
export SC2PATH=/data2/SC2/StarCraftII/
```

## LLM 配置

复制或参考 `API_config/config.example.json`，编辑 `API_config/config.json`：

```json
{
  "llm_agents_pool": {
    "DeepSeek-V4-flash": {
      "api_url": "https://api.example.com/v1",
      "api_key": "YOUR_SECRET_API_KEY",
      "model_name": "vendor-model-name",
      "temperature": 0.7,
      "top_p": null,
      "max_tokens": null,
      "is_reasoning": false,
      "enable_identity": false,
      "identity_prompt": ""
    }
  }
}
```

`model_key` 必须和运行参数一致。例如默认配置会使用：

- `DeepSeek-V4-flash`

可按阶段覆盖：

```bash
python run_vs_ai.py \
  --force-strategy marine_rush \
  --naming-model DeepSeek-V4-flash \
  --ordering-model DeepSeek-V4-flash \
  --executor-model DeepSeek-V4-flash
```

注意：`API_config/config.json` 可能包含真实 API key，请不要提交或公开。

## 运行

### 单局对战

最简单方式是先编辑 `run_vs_ai.py` 顶部的 `DEFAULT_*` 常量，然后运行：

```bash
python run_vs_ai.py
```

当前默认值包括：

- 我方：`universal_llm.terran`
- 地图：`KairosJunctionLE`
- 对手：内置 AI `terran.harder.macro`
- 固定策略：`marine_rush`

也可以用 CLI 覆盖：

```bash
python run_vs_ai.py \
  --bot-race terran \
  --enemy-race terran \
  --enemy-difficulty medium \
  --enemy-build random \
  --force-strategy battle_cruisers \
  --batch-name demo
```

短时冒烟测试可限制游戏时长：

```bash
SC2_GAME_TIME_LIMIT=240 python run_vs_ai.py \
  --enemy-difficulty medium \
  --enemy-build random \
  --force-strategy marine_rush \
  --batch-name smoke
```

Windows 如果路径过长，推荐用 `run_custom.py` 指定短记录目录：

```powershell
$env:SC2_GAME_TIME_LIMIT='60'
New-Item -ItemType Directory -Force -Path .\game_records\smoke | Out-Null

python run_custom.py `
  -m KairosJunctionLE `
  -p1 universal_llm.terran `
  -p2 ai.terran.easy.macro `
  --record-dir .\game_records\smoke `
  --match-id smoke `
  --force-strategy marine_rush
```

### 批量对战

```bash
bash start_experiments.sh
```

或直接调用底层脚本：

```bash
bash run_vs_ai_batch.sh <总局数> <并发数> [fg|tmux]
```

批量脚本常用环境变量：

```bash
export MY_BOT_NAME="universal_llm"
export BOT_RACE="terran"
export ENEMY_RACE="zerg"
export ENEMY_DIFFICULTY="harder"
export ENEMY_BUILD="air"
export FORCE_STRATEGY="marine_rush"
export NAMING_MODEL="DeepSeek-V4-flash"
export ORDERING_MODEL="DeepSeek-V4-flash"
export EXECUTOR_MODEL="DeepSeek-V4-flash"
```

## 对局产物

默认写入：

```text
game_records/<batch_name>/<match_id>/
```

常见文件：

| 文件 | 内容 |
|---|---|
| `<match_id>.log` | Sharpy 与 UniversalLLMBot 运行日志 |
| `<match_id>.SC2Replay` | SC2 录像 |
| `<match_id>.json` | 宏观流水线交互记录和结构化观测 |
| `<match_id>.llm_calls.json` | 每一次 LLM 调用的 prompt 和 output |

轨迹 JSON 会记录：

- 固定策略名和策略说明
- 每次触发原因：`initial_step`、`sequence_drained`、`executable_drained`
- 当前 strategy step
- Naming 原始输出和解析后的实体
- DATA_TOOLS 映射结果
- Ordering 原始输出、合法排序、漏项和丢弃项
- Supply Planner 插入的补给动作
- 注入 scheduler 的 action 序列
- 决策时英文 obs 和结构化快照

## 测试

安装测试依赖后运行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD" \
  python -m pytest tools/tests -q -p pytest_asyncio
```

Windows PowerShell：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONPATH=(Resolve-Path .).Path
python -m pytest tools/tests -q -p pytest_asyncio
```

当前仓库里的轻量测试主要覆盖 prompt 标签、Ordering Agent prompt 等逻辑。完整游戏验证仍需要本地 SC2 客户端、地图和可用 LLM API。

## 常用策略名

当前 `SKILL/terran/registry.json` 中登记的策略：

- `marine_rush`
- `battle_cruisers`
- `banshees`
- `bio`
- `cyclones`
- `one_base_turtle`
- `rusty`
- `safe_tvt_raven`
- `terran_silver_bio`
- `two_base_tanks`

## 相关文档

| 文档 | 内容 |
|---|---|
| [docs/系统文档.md](docs/系统文档.md) | 新版 LLM 增量驱动和命令式执行系统总览 |
| [docs/环境配置教程.md](docs/环境配置教程.md) | Linux / Windows 环境安装、SC2PATH、冒烟测试 |
| [docs/测试运行流程记录.md](docs/测试运行流程记录.md) | 测试和运行记录 |
| [docs/直接建造执行器经验总结_20260617.md](docs/直接建造执行器经验总结_20260617.md) | DirectBuild、reservation、deferred 机制经验 |
| [docs/readme_bot.md](docs/readme_bot.md) | Sharpy Bot 继承关系和 dummies 说明 |
| [docs/README_sharpy.md](docs/README_sharpy.md) | Sharpy 底层框架说明 |
| [docs/sharpy模块与配置说明.md](docs/sharpy模块与配置说明.md) | Sharpy 模块和配置说明 |

## 维护建议

- README 只保留入口级信息；实现细节放到 `docs/系统文档.md`。
- 策略改动优先同步 `SKILL/terran/<strategy>/Top_agent_0.md` 和 `strategy_tools.py`。
- 新增策略后检查 `registry.json`、运行参数和批量脚本中的 `FORCE_STRATEGY`。
- 修改执行调度后，用短时冒烟对局确认 `.json` 和 `.llm_calls.json` 能正常落盘。
