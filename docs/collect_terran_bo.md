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
| `--port-offset` | 起始端口偏移 |

可用 bot key 在 `tools/collect_terran_bo.py` 的 `TERRAN_BOTS` 中维护。

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
    "order_list_count": 165
  },
  "sequence": [
    {
      "seq": 0,
      "ability": "COMMANDCENTERTRAIN_SCV",
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
