# Executor 黄金排序（Golden Rank）

为 Terran **Executor** 层（train 类动作的多候选生产者选择）提供一套**纯规则**的黄金标签生成器。输入为 executor prompt（`system` + `user`），输出为 `golden_tags`（可含多个并列最优 tag）。**不保留、不依赖原始 LLM 回答。**

## 适用场景

Executor 只在「多个建筑/基地都能执行同一 train ability」时被调用，例如：

- 多个 `BARRACKS` 都能 `BARRACKSTRAIN_MARINE`
- 多个 `ORBITALCOMMAND` / `COMMANDCENTER` 都能 `COMMANDCENTERTRAIN_SCV`

`build` / `research` / `addon` / `morph` 不走 Executor 多选，不在本工具范围内。

## 代码位置

| 模块 | 路径 |
|------|------|
| 解析与打分核心 | `sft_pipeline/common/executor_golden_rank.py` |
| 批量标注 CLI | `sft_pipeline/build_sft/build_executor_golden_rank.py` |
| 单元测试 | `sft_pipeline/tests/test_executor_golden_rank.py` |

## 输入格式

与 `SC2-Agent-260510` 中 `executor_agent.build_executor_messages` 生成的 prompt 一致。

**System** 需包含：

- `[Ability to execute] <ACTION_NAME>`
- `[Possible conflicts in pending actions]` 列表（每行 `- ACTION`）

**User** 需包含：

```text
[Candidate Executors]
  - tag=578 BARRACKS [idle, has TechLab]
  - tag=417 BARRACKS [busy: Train Marine (27%), has Reactor]
```

候选行由规则层 `executor_select.candidate_executors` 预过滤，保证 listed 单位**此刻能执行该 ability**。

## 输出格式

每条样本保留 prompt 与规则标签，**剔除** `answer` / `cot` / `raw_content` 等 LLM 推理字段：

```json
{
  "system": "...",
  "user": "...",
  "ability": "BARRACKSTRAIN_MARINE",
  "golden_tags": [129],
  "golden_rank": {
    "ability": "BARRACKSTRAIN_MARINE",
    "conflict_actions": ["BUILD_REACTOR_BARRACKS"],
    "reservation_active": true,
    "fallback_no_eligible": false,
    "golden_tags": [129],
    "rankings": [...]
  }
}
```

`golden_tags` 为合法答案集合；若多个候选 `rank_key` 完全相同，则**全部保留**。

## 排序规则

采用多级字典序；先过滤，再比速度，再比附属/基地 tier。`rank_key = (eligible, ready_score, addon_tier, base_tier)`。

### 1. 硬过滤：为 pending 预留生产者

**触发条件**：`[Possible conflicts in pending actions]` 中出现：

- `BUILD_TECHLAB_*` / `BUILD_REACTOR_*`（挂附属）
- `UPGRADETO*`（升级基地）

且当前 `[Ability to execute]` **不是** addon/upgrade 动作本身（挂 TechLab 时必须选裸建筑，不能误过滤）。

**过滤对象**：

| 冲突动作类型 | 排除的候选 |
|--------------|------------|
| `BUILD_TECHLAB_BARRACKS` 等 | 同类型建筑且 `no add-on` |
| `BUILD_REACTOR_*` 同上 | 同上 |
| `UPGRADETOORBITAL_*` 等 | `COMMANDCENTER`（未升级基地） |

若过滤后无合法候选 → **回退**：保留全部候选，标记 `fallback_no_eligible=true`，再按速度分排序。

### 2. 速度分 `ready_score`（越大越优）

| 候选状态 | 分数 |
|----------|------|
| idle | 1000（Reactor 额外 +1 → 1001） |
| busy，进度 P% | P × 10（如 97% → 970） |
| busy + Reactor 且进度 < 50% | 视为第二队列可能空闲，`max(P×10, 500)` |

原则：**越快能开始/完成本次训练越好**；idle 优于 busy；busy 时进度越高越好。

### 3. 并列打破 `addon_tier` / `base_tier`

当 `ready_score` 与 `eligible` 相同时：

**附属建筑**（Barracks/Factory/Starport）：

```text
Reactor (3) > TechLab (2) > 裸建筑 (0)
```

**基地**：

```text
ORBITALCOMMAND / PLANETARYFORTRESS (2) > COMMANDCENTER (1)
```

### 4. 多最优保留

凡 `rank_key` 与最优值相同的候选，全部写入 `golden_tags`。`tag` 仅用于 `sort_key` 稳定排序，**不**削减 golden 集合大小。

## 使用方式

```bash
python3 -m sft_pipeline.build_sft.build_executor_golden_rank \
  --input  <executor_qa.json> \
  --output-dir sft_pipeline_outputs/<your_run>/executor_golden
```

输出目录通常包含：

- `executor_qa_golden.json` / `executor_qa_golden.jsonl` — 完整标注（含 `golden_rank` 评分明细与对局元数据）
- `executor_qa_golden_slim.json` / `executor_qa_golden_slim.jsonl` — **精简版**，每条仅 `system`、`user`、`golden_tags`
- `build_report.json` — 条数、多最优比例、预留过滤触发次数等汇总

精简版单条示例：

```json
{
  "system": "...",
  "user": "[Candidate Executors]\n  - tag=433 ORBITALCOMMAND [idle]\n  ...",
  "golden_tags": [433]
}
```

## 与游戏流水线的关系

- Executor prompt 的 pending/conflicts 来自调度器当前队列摘要。
- `UPGRADETO*` 在宏观 `ordered_actions` 中常见，但**通常不出现在 executor prompt 的 conflicts/pending**（升级由规则层直接执行）。
- 因此「因待升级而保留 CC」的硬过滤在真实 prompt 中极少触发；**OC > CC** 的并列打破在「CC 与 OC 同时候选」场景中更常见。

## 程序化调用

```python
from sft_pipeline.common.executor_golden_rank import rank_executor_prompt

result = rank_executor_prompt(system_text, user_text)
print(result.golden_tags)
print(result.to_dict())
```
