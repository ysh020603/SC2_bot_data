# SFT 数据格式

本文说明 `sft_pipeline` 生成的 Qwen3 SFT 数据格式。当前 pipeline 使用 ShareGPT 格式，并为 Naming、Ordering、Executor 三个任务分别生成 thinking / nothink 两种版本。

## 输出文件

标准输出目录：

```text
sft_pipeline_outputs/<run_id>/sft_agent_aligned/
  naming/
    sc2_naming_qwen3_thinking_sft.json
    sc2_naming_qwen3_nothink_sft.json
  ordering/
    sc2_ordering_qwen3_thinking_sft.json
    sc2_ordering_qwen3_nothink_sft.json
  executor/
    sc2_executor_qwen3_thinking_sft.json
    sc2_executor_qwen3_nothink_sft.json
  dataset_info.fragment.json
  qa_report.json
```

`sft_agent_aligned` 表示 prompt/context 与 `SC2-Agent-260510` 中三个线上 Agent 对齐。

## ShareGPT 样本结构

每条样本：

```json
{
  "system": "system prompt",
  "conversations": [
    {
      "from": "human",
      "value": "user prompt"
    },
    {
      "from": "gpt",
      "value": "assistant answer"
    }
  ]
}
```

LLaMA-Factory 注册字段：

```json
{
  "formatting": "sharegpt",
  "columns": {
    "messages": "conversations",
    "system": "system"
  },
  "tags": {
    "role_tag": "from",
    "content_tag": "value",
    "user_tag": "human",
    "assistant_tag": "gpt"
  }
}
```

`dataset_info.fragment.json` 会自动生成上述六个数据集的注册片段。

## Thinking 与 Nothink

Qwen3 使用原生 thinking 格式，不额外添加 `<answer>` 标签。

Thinking 样本：

```text
<think>
reasoning content
</think>

final answer
```

当前 pipeline 暂时把 reasoning 留空：

```text
<think>

</think>

{"items":[...]}
```

后续可以用独立工具注入 reasoning。

Nothink 样本：

```text
final answer only
```

不包含 `<think>`，也不包含空 thinking block。

## LLaMA-Factory 配置要点

Thinking：

```yaml
template: qwen3
enable_thinking: true
```

Nothink：

```yaml
template: qwen3
enable_thinking: false
```

建议先分开训练 thinking 和 nothink adapter，不要一开始混合。

## Naming Answer

Naming 的 assistant answer 是 JSON object：

```json
{
  "items": [
    {"name": "Marine", "count": 4},
    {"name": "BarracksTechLab", "count": 1}
  ]
}
```

这些 item 从标准 `ordered_actions` 反推 entity/count。Naming 不表达 action 顺序。

## Ordering Answer

Ordering 的 assistant answer 是 JSON object：

```json
{
  "ordered_actions": [
    "TERRANBUILD_BARRACKS",
    "BUILD_TECHLAB_BARRACKS",
    "BARRACKSTRAIN_MARINE"
  ]
}
```

输入 action 会被打乱，但 multiset 与答案一致：

```text
Counter(input_actions) == Counter(answer.ordered_actions)
```

答案顺序等于胜局中该 step 的真实 `ordered_actions` 顺序。

## Executor Answer

Executor 的 assistant answer 是 JSON list，只有一个短 tag：

```json
[417]
```

短 tag 来自：

```text
prompt_tag = real_tag % 1000
```

真实长 tag 不应出现在 prompt 或 answer 中。真实长 tag 只保存在原始 `executor_context`，用于构造 `tag_map` 和校验。

## 数据质量检查

训练前检查：

- `v6_steps/manifest.json` 中 `require_victory == true`
- `sft_agent_aligned/qa_report.json`
- `labeled_steps.jsonl` 中 `result` 只有 `Victory`
- executor prompt/answer 没有 7 位以上真实长 tag
- 地图字段和文件名使用英文 map id，没有中文地图名
