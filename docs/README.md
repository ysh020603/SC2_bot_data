# docs 目录索引

本目录记录环境配置、bot 运行、轨迹采集、v6 step 标注、SFT 数据构造和仓库管理。当前主线目标是：用本仓库采集胜局轨迹，并构造与 `SC2-Agent-260510` 三个 LLM 位置对齐的训练数据。

## 必读顺序

### Windows

1. [windows_environment_setup.md](windows_environment_setup.md)：Windows 环境、conda、SC2PATH、地图安装。
2. [windows_run_bots.md](windows_run_bots.md)：手动运行 bot 与调试。

### Linux

1. [linux_trajectory_collection.md](linux_trajectory_collection.md)：Linux 服务器环境、tmux 后台采集、并发与进度监控。

### 通用（采集与 SFT）

3. [collect_terran_bo.md](collect_terran_bo.md)：批量采集 Terran BO 轨迹（参数与输出格式）。
4. [sft_pipeline_usage.md](sft_pipeline_usage.md)：采集轨迹 -> v6 step -> SFT 的完整流程。
5. [sft_data_format.md](sft_data_format.md)：Qwen3 thinking/nothink 的 ShareGPT 数据格式。

## 当前统一要求

- 对局采集使用本仓库的 bot 和 `tools/collect_terran_bo.py` / `sft_pipeline.collect.run_collect`。
- `SC2-Agent-260510` 只作为 prompt/context 标准来源，不作为采集 bot。
- step 与 SFT 数据默认只使用 `Victory` 对局。
- 地图名统一使用 SC2 引擎英文 map id，例如 `KairosJunctionLE`，不要使用客户端中文显示名。
- 输出文件名、`meta.map`、step Markdown 文件名、SFT 元数据都使用英文 map id。
- 中文本地化地图名如需保留，只能放到 `meta.map_localized`。
- 最终 SFT 输出目录建议使用 `sft_agent_aligned/`。

## 数据与流程文档

**[collect_terran_bo.md](collect_terran_bo.md)**  
说明如何批量运行 Terran bot，对抗内置 AI，采集 sequence JSON、obs、replay 和 log。包含 `--workers` 并发、bot/race/difficulty/map 参数、输出目录和 QA 检查。

**[ability_recorder_commit_and_addon.md](ability_recorder_commit_and_addon.md)**  
说明 `AbilityRecorderManager` 为什么要在 action 被 SC2 接受后再写入 sequence，如何处理 TechLab/Reactor 命名，以及 train 多候选 executor context 的保存规则。

**[sft_pipeline_usage.md](sft_pipeline_usage.md)**  
说明模块化 SFT pipeline：采集、obs 校验、v6 step 标注、从 Markdown 恢复 JSONL、构造 Agent-aligned SFT。

**[sft_data_format.md](sft_data_format.md)**  
说明 Qwen3 thinking / nothink 训练样本格式。当前 pipeline 输出 ShareGPT 格式。

**[cot_generation_validation_notes.md](cot_generation_validation_notes.md)**  
说明 CoT 后处理模块 `inject_cot_sft` 的设计、整条轨迹测试结果与调参建议。

## 运行与环境文档

**[linux_trajectory_collection.md](linux_trajectory_collection.md)**  
Linux 服务器上的轨迹采集实践：`sharpy-sc2` conda 环境、`SC2PATH`/`PYTHONPATH`、tmux 后台运行、多地图批量脚本（`tools/run_terran_10bots_3maps_collect.sh`）、并发建议、进度监控与常见问题。

**[windows_environment_setup.md](windows_environment_setup.md)**  
配置 Windows 环境、conda 环境、依赖、SC2 安装路径和地图。

**[windows_run_bots.md](windows_run_bots.md)**  
手动运行 bot、查看可用 bot/地图/AI 参数、运行小规模采集测试。

**[agent_bot_test_and_trajectory_review.md](agent_bot_test_and_trajectory_review.md)**  
面向 Agent 的测试指南，适合检查单局轨迹和日志。

## 参考与管理

**[git_repo_management.md](git_repo_management.md)**  
仓库结构、子模块、忽略文件和 Git 管理说明。

**[sharpy_original_readme.md](sharpy_original_readme.md)**  
原始 sharpy-sc2 框架说明。
