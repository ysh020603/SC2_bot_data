# SC2 Bot Data Pipeline

本仓库用于采集 StarCraft II Terran bot 的宏观动作轨迹，并把胜局轨迹构造成可训练小模型的 SFT 数据。目标是替换 `SC2-Agent-260510` 中的三个 LLM 位置：

- Naming：把自然语言 strategy step 和当前 obs 转成 canonical entity/count。
- Ordering：把 Naming 映射后的 action multiset 排成可执行顺序。
- Executor：在 train action 有多个候选生产建筑时选择执行单位。

采集对局使用本仓库的 bot 与工具；`SC2-Agent-260510` 只作为 prompt/context 的参考标准，不作为采集 bot 使用。

## 当前标准流程

```text
1. 采集胜局轨迹
   tools/collect_terran_bo.py
   sft_pipeline.collect.run_collect

2. 校验 obs
   sft_pipeline.collect.validate_obs

3. 转 v6 step
   sft_pipeline.label_steps.build_v6_steps
   复用 bo_2_nlstep/Tools/bo_to_doc_v6.py

4. 构造 Agent-aligned SFT
   sft_pipeline.build_sft.build_all
```

默认要求 step 数据与最终 SFT 数据都来自胜局：

```text
meta.result == "Victory"
```

## 关键约束

- 地图名必须使用传给 SC2 引擎的英文 map id，例如 `KairosJunctionLE`、`AcropolisLE`、`ThunderbirdLE`。
- 不要在采集参数、文件名、`meta.map`、step Markdown 文件名或 SFT 元数据里使用客户端中文地图名。
- 如果游戏接口返回中文地图名，只能作为 `meta.map_localized` 参考字段保存。
- train 且候选执行单位数量大于 1 时，才保存 `executor_context` 并构造 Executor SFT。
- addon/morph 不再走 Executor LLM，不构造这两类 executor 样本。
- Executor prompt/answer 使用短 tag：`prompt_tag = real_tag % 1000`；真实长 tag 只留在采集原始数据里用于校验。
- Ordering prompt 不包含 executor candidate/tag 上下文；Executor 是独立模型位置。
- SFT prompt 必须与 `SC2-Agent-260510` 中三个 Agent 的线上上下文对齐。

## 安装 StarCraft II 与配置地图

运行本仓库的 bot 与采集脚本前，需要本地安装 StarCraft II 并配置地图。免费 Starter Edition 即可满足 headless 采集需求。

### 安装游戏

**Windows / macOS**

1. 从 [StarCraft II 官网](https://starcraft2.blizzard.com/) 下载并安装游戏。
2. （推荐）在 Battle.net 启动器设置中将游戏语言改为 English，便于与引擎 map id 对齐。

**Linux**

1. 从 [s2client-proto 仓库](https://github.com/Blizzard/s2client-proto?tab=readme-ov-file#linux-packages) 下载 Linux 游戏包。
2. 设置 `SC2PATH` 指向安装根目录（目录下应包含 `Maps/`、`Versions/`）：

```bash
export SC2PATH="/path/to/StarCraftII"
```

### 配置地图

1. 从 [s2client-proto 地图包](https://github.com/Blizzard/s2client-proto?tab=readme-ov-file#map-packs) 下载 `Melee.zip`。
2. 在 StarCraft II 安装目录下创建 `Maps` 文件夹（若尚不存在）。
3. 将 `Melee.zip` 解压到 `Maps/` 目录。

本仓库采集与测试常用天梯地图英文 map id，例如 `KairosJunctionLE`、`AcropolisLE`、`ThunderbirdLE`。对应 `.SC2Map` 文件也需放入 `$SC2PATH/Maps/`（可放在子目录中）。运行前可用以下命令检查地图是否已被引擎识别：

```bash
python -c "
from bot_loader.game_starter import GameStarter
maps = GameStarter.installed_maps()
for m in ['KairosJunctionLE', 'AcropolisLE', 'ThunderbirdLE']:
    print(m, 'OK' if m in maps else 'MISSING')
"
```

若显示 `MISSING`，请补全地图文件后再开始采集。

### 环境变量

无论平台，运行前至少设置：

```text
SC2PATH          # StarCraft II 安装根目录
PYTHONUTF8=1     # 避免中文环境下日志/JSON 编码问题
PYTHONIOENCODING=utf-8
```

Linux 服务器还需将仓库内 `python-sc2` 加入 `PYTHONPATH`；Windows 下 `SC2PATH` 示例见下方「快速开始」。

## 快速开始

Windows PowerShell 示例：

```powershell
$env:SC2PATH = 'C:\Program Files (x86)\StarCraft II'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'
```

采集：

```powershell
& $py -m sft_pipeline.collect.run_collect `
  --output 'C:\code\SC2_bot_data\bo_collection_runs\my_run' `
  --map KairosJunctionLE `
  --bots bio marine tank `
  --races zerg protoss terran `
  --difficulties hard harder veryhard `
  --workers 4
```

校验 obs：

```powershell
& $py -m sft_pipeline.collect.validate_obs `
  --run 'C:\code\SC2_bot_data\bo_collection_runs\my_run' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\obs_qa.json'
```

转 v6 step，可以并发标注多个 trajectory：

```powershell
& $py -m sft_pipeline.label_steps.build_v6_steps `
  --data-dir 'C:\code\SC2_bot_data\bo_collection_runs\my_run' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\v6_steps' `
  --model-key kimi-k2.5 `
  --no-thinking `
  --workers 4
```

构造 SFT：

```powershell
& $py -m sft_pipeline.build_sft.build_all `
  --labeled-steps 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\v6_steps\json\labeled_steps.jsonl' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\sft_agent_aligned' `
  --shuffle-variants 1
```

## 数据目录

建议每次运行使用一个稳定的 `run_id`：

```text
bo_collection_runs/<run_id>/                 # 原始对局轨迹、obs、replay、log
sft_pipeline_outputs/<run_id>/obs_qa.json    # obs QA
sft_pipeline_outputs/<run_id>/v6_steps/      # Markdown + labeled_steps.jsonl
sft_pipeline_outputs/<run_id>/sft_agent_aligned/
```

`v6_steps/json/labeled_steps.jsonl` 是 SFT 构造的标准输入。`sft_agent_aligned/` 是最终训练数据目录。

## 主要目录

```text
bo_2_nlstep/                 # action order -> v6 natural-language step 工具
bo_collection_runs/          # 原始采集数据
data_ref/                    # ability/entity 图谱参考数据
docs/                        # 操作与设计文档
dummies/                     # 采集用 Terran bot
sft_pipeline/                # 模块化 SFT 数据平台
sft_pipeline_outputs/        # step 与 SFT 输出
sharpy/                      # 本仓库维护的采集框架
SC2-Agent-260510/            # 参考 Agent prompt/context 的子项目
tools/                       # 采集脚本
```

## 文档入口

[`docs/`](docs/) 目录收录了本仓库相关工作的完整操作与设计指南，建议按平台与任务按需阅读。入口索引见 [docs/README.md](docs/README.md)。

**环境与运行**

| 文档 | 说明 |
|------|------|
| [docs/windows_environment_setup.md](docs/windows_environment_setup.md) | Windows conda 环境、依赖、`SC2PATH`、地图检查 |
| [docs/windows_run_bots.md](docs/windows_run_bots.md) | 手动运行 bot、查看可用 bot/地图/AI 参数 |
| [docs/linux_trajectory_collection.md](docs/linux_trajectory_collection.md) | Linux 服务器环境、tmux 后台采集、并发与进度监控 |

**采集与 SFT 数据**

| 文档 | 说明 |
|------|------|
| [docs/collect_terran_bo.md](docs/collect_terran_bo.md) | 批量采集 Terran BO 轨迹（参数与输出格式） |
| [docs/sft_pipeline_usage.md](docs/sft_pipeline_usage.md) | 采集 → v6/v8 step → SFT 完整流程 |
| [docs/sft_data_format.md](docs/sft_data_format.md) | Qwen3 thinking/nothink ShareGPT 格式 |
| [sft_pipeline/README.md](sft_pipeline/README.md) | SFT pipeline 模块说明 |

**设计与参考**

| 文档 | 说明 |
|------|------|
| [docs/ability_recorder_commit_and_addon.md](docs/ability_recorder_commit_and_addon.md) | AbilityRecorder 设计 |
| [docs/cot_generation_validation_notes.md](docs/cot_generation_validation_notes.md) | CoT 后处理与调参 |
| [docs/agent_bot_test_and_trajectory_review.md](docs/agent_bot_test_and_trajectory_review.md) | 单局轨迹与日志审查 |
| [docs/git_repo_management.md](docs/git_repo_management.md) | 仓库结构与子模块管理 |
