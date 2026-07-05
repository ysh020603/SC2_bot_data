# Terran BO 轨迹批量采集

本文说明如何使用本仓库的 Terran bot 采集 BO/action 轨迹。采集产物是后续 v8 step 标注和 SFT 构造的数据源。

## 采集入口

推荐使用 pipeline 包装入口：

```powershell
python -m sft_pipeline.collect.run_collect `
  --output bo_collection_runs/<run_id> `
  --map KairosJunctionLE `
  --bots bio marine tank `
  --races zerg protoss terran `
  --difficulties hard harder veryhard `
  --workers 4
```

底层脚本是：

```powershell
python tools/collect_terran_bo.py ...
```

`run_collect.py` 会记录 `run_manifest.json`，更适合作为标准流程入口。

## Action 采集语义

当前采集协议为 `sc2-outcome-v2`。Bot 下达命令时，Recorder 只保存 pending
候选和下令时 observation；命令本身、短暂出现的 `unit.orders` 或生产队列都不构成金标。
只有对应的 SC2 引擎结果事件出现后，Action 才进入 sequence：

| 语义类型 | SC2 结果回执 |
| --- | --- |
| Build / BuildOnUnit / BuildInstant | `structure_started`；漏过开工事件时用 `structure_completed` 兜底 |
| Train | `unit_created` |
| Morph | `unit_type_changed` |
| Research | `upgrade_completed` |

最终 sequence 按 `issued_time` 的下令顺序排列，但只保留已有结果回执的 Action。
`obs` / `local_obs` 是下令时快照，`confirmed_time` 是结果发生时间。未落实命令不会
进入 `sequence` 或 `order_list`，只会计入 meta 的 `expired_unconfirmed_action_count`、
`superseded_unconfirmed_action_count` 或 `pending_unconfirmed_action_count`。

## 地图命名要求

所有命令行参数中的地图都必须使用 SC2 引擎英文 map id：

```text
KairosJunctionLE
AcropolisLE
ThunderbirdLE
YearZeroLE
```

不要使用客户端中文显示名。采集侧会把输入的英文 map id 写入：

```json
{
  "meta": {
    "map": "KairosJunctionLE",
    "map_localized": "凯罗斯中转站-天梯版"
  }
}
```

`meta.map`、sequence 文件名、step Markdown 文件名和 SFT 元数据都应使用英文 map id。`map_localized` 只用于人工参考。

## 并发

`--workers` 是采集对局的最大并发数。Windows 上建议先用：

```text
--workers 1
```

确认 SC2 能稳定启动和关闭后，再逐步增加到 `2` 或 `4`。并发过高会导致端口冲突、SC2 客户端卡死或资源不足。

`--port-offset` 可用于错开端口：

```powershell
python -m sft_pipeline.collect.run_collect `
  --output bo_collection_runs/run_a `
  --map KairosJunctionLE `
  --workers 2 `
  --port-offset 0
```

## 参数

| 参数 | 说明 |
| --- | --- |
| `--output` | 输出根目录，建议为 `bo_collection_runs/<run_id>` |
| `--map` | 英文 map id；不传时从已知 melee 地图中选择 |
| `--bots` | bot key，例如 `bio marine tank` |
| `--races` | 内置 AI 种族：`protoss zerg terran` |
| `--difficulties` | 内置 AI 难度：`medium mediumhard hard harder veryhard` |
| `--workers` | 最大并发对局数 |
| `--repeats` | 每个 race / difficulty 组合重复采集次数，默认 1 |
| `--port-offset` | 起始端口偏移 |

可用 bot key 在 `tools/collect_terran_bo.py` 的 `TERRAN_BOTS` 中维护。

并行采集时，每局在启动前就分配唯一的 `match_id` 和固定 sequence 文件路径。Recorder
使用临时文件原子写入，worker 在返回成功前会校验文件存在且 `meta.match_id` 一致。因此
`results.json` 的 `sequence_file` 可以直接用于 log / replay / sequence 一一配对，不再通过
共享目录的前后文件差推断结果。

## 输出目录

```text
bo_collection_runs/<run_id>/
  run_manifest.json
  summary.json
  <bot_folder>/
    sequences/
      *.json
    logs/
      *.log
    replays/
      *.SC2Replay
    results.json
```

sequence JSON 的核心字段：

```json
{
  "meta": {
    "bot_name": "Rusty Infantry",
    "opponent_id": "bio-ai.zerg.hard",
    "map": "KairosJunctionLE",
    "map_localized": "凯罗斯中转站-天梯版",
    "my_race": "Terran",
    "enemy_race": "Zerg",
    "result": "Victory",
    "sequence_count": 165,
    "order_list_count": 165,
    "confirmation_schema": "sc2-outcome-v2",
    "match_id": "bio-ai.zerg.hard_KairosJunctionLE_...",
    "expired_unconfirmed_action_count": 12,
    "superseded_unconfirmed_action_count": 3,
    "pending_unconfirmed_action_count": 1
  },
  "sequence": [
    {
      "seq": 0,
      "ability": "COMMANDCENTERTRAIN_SCV",
      "issued_time": 0.0,
      "confirmed_time": 12.2,
      "confirmation": {
        "kind": "unit_created",
        "game_time": 12.2,
        "entity_tag": 4355260999,
        "entity_type": "SCV",
        "actor_tag": 4355260417
      },
      "obs": {
        "text": "...",
        "structured": {}
      },
      "local_obs": {}
    }
  ],
  "order_list": ["COMMANDCENTERTRAIN_SCV"]
}
```

train 且候选执行单位大于 1 时，`sequence[]` 会额外包含：

```json
{
  "executor_context": {
    "ability_name": "BARRACKSTRAIN_MARINE",
    "selected_tag": 4355260417,
    "selected_type": "BARRACKS",
    "candidate_count": 2,
    "candidate_executors": []
  }
}
```

addon/morph 不保存 executor LLM 上下文，因为当前标准流程中它们不再由 LLM 选择执行单位。

## 胜局要求

采集阶段可以保留所有结果，但标准 step 和 SFT 阶段默认只使用：

```text
meta.result == "Victory"
```

因此采集完成后应查看 `summary.json` / `results.json`，确认胜局数量足够。非胜局不会进入默认 SFT 数据。

## Obs QA

采集后运行：

```powershell
python -m sft_pipeline.collect.validate_obs `
  --run bo_collection_runs/<run_id> `
  --output sft_pipeline_outputs/<run_id>/obs_qa.json
```

重点检查：

- `missing_obs_text == 0`
- `missing_obs_structured == 0`
- `order_mismatch == 0`
- `executor_context_train_multi` 是否合理
- 每条 `sequence[]` 都存在 `confirmation`，且回执类型与 `semantic_target.type` 一致
- `game_time == issued_time`，并且 `confirmed_time >= issued_time`
- `results.json` 中所有 `sequence_file` 唯一、存在，并且其 `meta.match_id` 与该局一致

## 下一步

转 v8 step：

```powershell
python -m sft_pipeline.label_steps.build_v8_steps `
  --data-dir bo_collection_runs/<run_id> `
  --output sft_pipeline_outputs/<run_id>/v8_steps `
  --model-key deepseek-v4-flash `
  --workers 4
```

校验 v8 标注产物：

```powershell
python -m sft_pipeline.label_steps.validate_v8_steps `
  --data-dir bo_collection_runs/<run_id> `
  --output sft_pipeline_outputs/<run_id>/v8_steps `
  --report sft_pipeline_outputs/<run_id>/v8_steps/v8_qa.json
```

构造 SFT：

```powershell
python -m sft_pipeline.build_sft.build_all `
  --labeled-steps sft_pipeline_outputs/<run_id>/v8_steps/json/labeled_steps.jsonl `
  --output sft_pipeline_outputs/<run_id>/sft_agent_aligned `
  --shuffle-variants 1
```
