# Git 与目录管理

本文说明当前仓库中代码、参考子项目、采集数据和 SFT 输出的管理方式。

## 仓库角色

```text
SC2_bot_data/
  本仓库，负责采集数据、标注 step、构造 SFT。

SC2-Agent-260510/
  参考 Agent 子项目，用于对齐 Naming / Ordering / Executor 的 prompt/context。
  不作为采集 bot 使用。
```

## 关键目录

| 路径 | 说明 |
| --- | --- |
| `sharpy/` | 本仓库维护的采集框架与 `AbilityRecorderManager` |
| `dummies/` | 采集用 Terran bot |
| `tools/` | 采集脚本 |
| `sft_pipeline/` | 模块化 SFT 数据平台 |
| `bo_2_nlstep/` | action order -> v8 step 的标注工具 |
| `data_ref/` | ability/entity 图谱参考数据 |
| `bo_collection_runs/` | 原始采集 run |
| `sft_pipeline_outputs/` | obs QA、v8 steps、最终 SFT |
| `docs/` | 文档 |
| `SC2-Agent-260510/` | 参考 Agent prompt/context |

## 数据目录管理

每次实验建议使用同一个 `run_id`：

```text
bo_collection_runs/<run_id>/
sft_pipeline_outputs/<run_id>/obs_qa.json
sft_pipeline_outputs/<run_id>/v8_steps/
sft_pipeline_outputs/<run_id>/sft_agent_aligned/
```

管理原则：

- `bo_collection_runs/<run_id>` 是原始数据源，尽量不要手工改内容。
- `v8_steps/json/labeled_steps.jsonl` 是 SFT 构造标准输入。
- `sft_agent_aligned/` 是最终训练数据目录。
- 同一批轨迹如果用不同模型重标 step，应使用新的 `<run_id>` 或新的 step 输出目录，避免覆盖。
- 地图字段和文件名统一使用英文 map id。

## 敏感文件

不要提交 API key 或本地 provider 配置。尤其注意：

```text
bo_2_nlstep/API_config/config.json
bo_2_nlstep/api_call/config/provider_config.json
```

只提交 example/template 配置。

## SC2-Agent-260510

`SC2-Agent-260510` 是参考项目。SFT pipeline 通过以下模块对齐 prompt：

```text
SC2_Agent.naming_agent
SC2_Agent.ordering_agent
SC2_Agent.executor_agent
SC2_Agent.data_tools.*
```

不要在 `sft_pipeline` 中手写近似 prompt 替代这些参考函数。

## 日常检查

```powershell
git status --short
git diff --stat
```

提交前建议检查：

- 是否误提交 API key。
- 是否误提交不需要的大型 replay/log。
- README 与 `docs/` 是否同步当前流程。
- `sft_pipeline/README.md` 是否与 `docs/sft_pipeline_usage.md` 方向一致。

## 新机器初始化

```bash
git clone <repo-url>
cd SC2_bot_data
git submodule update --init --recursive
```

配置 Python/SC2 环境见：

```text
docs/windows_environment_setup.md
```
