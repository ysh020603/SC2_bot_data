# docs 目录索引

本目录下的文档按用途分为环境配置、bot 运行与测试、数据采集、框架说明和仓库管理五类。建议按以下顺序阅读。

---

## 环境配置

**[windows_environment_setup.md](windows_environment_setup.md)**
Windows 环境下配置 SC2 bot 运行环境的一站式指南。涵盖 conda 环境创建（`SC2_0615`）、Python 依赖安装、`config.ini` 配置以及 sharpy 子模块初始化。所有其他操作都依赖此文档先行完成。

## Bot 运行与测试

**[windows_run_bots.md](windows_run_bots.md)**
如何在 Windows 上运行 bot 的操作手册。包含启动前环境变量设置、查看可用 bot/地图/AI 参数的方法、单局运行命令以及批量对战示例。是日常调试和手动测试的入口。

**[agent_bot_test_and_trajectory_review.md](agent_bot_test_and_trajectory_review.md)**
面向 Agent 的测试指南。规定了测试时使用的标准参数（地图 `KairosJunctionLE`、对手 AI `ai.terran.veryhard` 等），以及如何检查 bot 对局后生成的动作轨迹（trajectory）JSON 文件。关键约束：所有参数必须使用英文 ID，不要用 SC2 客户端的中文显示名。

## 数据采集

**[collect_terran_bo.md](collect_terran_bo.md)**
批量采集 Terran dummy bot 的 BO 轨迹的方案说明。定义了完整的对战矩阵：10 个 Terran bot 分别对抗三族 × 五档难度的内置 AI，总计 150 局。文档说明了采集机制（`AbilityRecorderManager` 记录 macro ability sequence）、输出格式（含 meta 和 sequence 的 JSON）和并行策略（每个 bot 内部 15 局并行，bot 之间串行）。

**[ability_recorder_commit_and_addon.md](ability_recorder_commit_and_addon.md)**
`AbilityRecorderManager` 核心设计的技术笔记。重点记录了两个关键设计决策：(1) 为什么要在动作被游戏引擎接受并开始执行后才写入 sequence（避免重复条目和未执行动作的污染）；(2) TechLab/Reactor 这类附属建筑的命名和去重策略。供阅读和修改 recorder 代码时参考。

## 框架说明

**[sharpy_original_readme.md](sharpy_original_readme.md)**
sharpy-sc2 框架的原始 README。sharpy 是基于 python-sc2 的 SC2 AI bot 快速开发框架，也是 Sharpened Edge bot 所用框架。本仓库中的所有 Terran dummy bot 均构建于 sharpy 之上。文档记录了框架的定位、环境要求（Python 3.8/3.9/3.11 64-bit）、基本用法和项目结构概览。

## 仓库管理

**[git_repo_management.md](git_repo_management.md)**
本仓库的 Git 配置与目录归属说明。覆盖：远程仓库地址（`git@github.com:ysh020603/SC2_bot_data.git`）、子模块（sharpy-sc2、sc2-pathlib 等）的初始化与更新策略、各顶层目录的 Git 追踪归属分类，以及日常分支操作和换机部署方式。

---

## 阅读顺序建议

如果你是**初次配置环境**，按以下顺序阅读：

1. [windows_environment_setup.md](windows_environment_setup.md) — 搭好环境
2. [git_repo_management.md](git_repo_management.md) — 了解仓库结构
3. [sharpy_original_readme.md](sharpy_original_readme.md) — 了解框架背景
4. [windows_run_bots.md](windows_run_bots.md) — 跑起来

如果你要**批量采集 BO 数据**：

1. [collect_terran_bo.md](collect_terran_bo.md) — 了解采集矩阵和方案
2. [ability_recorder_commit_and_addon.md](ability_recorder_commit_and_addon.md) — 理解 recorder 的设计逻辑

如果你要让 **Agent 自动测试**：

1. [agent_bot_test_and_trajectory_review.md](agent_bot_test_and_trajectory_review.md) — Agent 视角的测试流程
