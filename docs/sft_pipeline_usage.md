# SC2 SFT 数据构造流程

本文说明如何使用 `sft_pipeline/` 分阶段构造训练数据，用于替换 `SC2-Agent-260510` 中的 Naming、Ordering、Executor 三个 LLM 环节。

更完整的模块说明见：

```text
sft_pipeline/README.md
```

## 流程概览

```text
采集对局
  -> sequence JSON + obs
  -> train 多候选时保存 executor_context

v8 Step 标注
  -> Markdown
  -> labeled_steps.jsonl / step_index.json

SFT 构造
  -> naming thinking/nothink
  -> ordering thinking/nothink
  -> executor thinking/nothink
```

默认只使用胜局：

```text
meta.result == "Victory"
```

## 1. 采集轨迹

标准入口：

```powershell
$env:SC2PATH = 'C:\Program Files (x86)\StarCraft II'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'

& $py -m sft_pipeline.collect.run_collect `
  --output 'C:\code\SC2_bot_data\bo_collection_runs\<run_id>' `
  --map KairosJunctionLE `
  --bots bio marine tank `
  --races zerg protoss terran `
  --difficulties hard harder veryhard `
  --workers 4
```

要求：

- 使用本仓库 bot 采集，不使用 `SC2-Agent-260510` 的 bot。
- `--map` 使用 SC2 引擎英文 map id，不使用中文地图名。
- `--workers` 是采集对局的最大并发数，Windows 建议先从 `1` 开始。

输出：

```text
bo_collection_runs/<run_id>/
  run_manifest.json
  summary.json
  <bot>/
    sequences/*.json
    replays/*.SC2Replay
    logs/*.log
    results.json
```

sequence 中：

- `meta.map` 是英文 map id。
- `meta.map_localized` 可选，仅作为中文本地化参考。
- `obs.text` / `obs.structured` 是 Naming/Ordering 的输入来源。
- `executor_context` 只保存 train 多候选样本。

## 2. Obs QA

```powershell
& $py -m sft_pipeline.collect.validate_obs `
  --run 'C:\code\SC2_bot_data\bo_collection_runs\<run_id>' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\obs_qa.json'
```

重点检查：

```text
missing_obs_text == 0
missing_obs_structured == 0
order_mismatch == 0
```

## 3. v8 Step 标注

复用：

```text
bo_2_nlstep/Tools/bo_to_doc_v8.py
```

以后进行 action list 到 NL step 标注时，默认使用 v8。v8 继承 v7 的 no-ordinal、concise final step、summary 和数量描述规则；同时在 normal step 中加入宏观控制提示，让下游模型结合 obs 推理 worker saturation、supply headroom、gas/refinery capacity。Refinery/gas 不绑定具体任务，只表达 gas income / gas flexibility / tech capacity。normal step 不加入 enemy/scout/pressure/threat、active queues、idle production 或泛泛 tech readiness 分析。

命令：

```powershell
& $py -m sft_pipeline.label_steps.build_v8_steps `
  --data-dir 'C:\code\SC2_bot_data\bo_collection_runs\<run_id>' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\v8_steps' `
  --model-key deepseek-v4-flash `
  --workers 4
```

`--workers` 是并发标注 trajectory 的数量。API 限流时使用 `1` 或 `2`。

输出：

```text
sft_pipeline_outputs/<run_id>/v8_steps/
  md/
    *.md
  json/
    labeled_steps.jsonl
    step_index.json
  manifest.json
```

`labeled_steps.jsonl` 只包含有真实 action range 的 step。v8 Markdown 的最后一个 final step 是战略总结，不进入 SFT。JSONL 中 `step_text_v8` 是标准字段，`step_text_v7` / `step_text_v6` 作为兼容字段保留。

如果需要从旧 v7 Markdown 离线恢复 JSONL，可以继续使用 legacy 恢复工具：

```powershell
& $py -m sft_pipeline.label_steps.recover_v7_json_from_md `
  --data-dir 'C:\code\SC2_bot_data\bo_collection_runs\<run_id>' `
  --md-dir 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\v7_steps\md' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\v7_steps'
```

### v8 QA

标注完成后建议校验产物完整性：

```powershell
& $py -m sft_pipeline.label_steps.validate_v8_steps `
  --data-dir 'C:\code\SC2_bot_data\bo_collection_runs\<run_id>' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\v8_steps' `
  --report 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\v8_steps\v8_qa.json'
```

Linux 多地图批量标注（Obs QA → 按地图标注 → 失败重试 → v8 QA）：

```bash
DATA_DIR=bo_collection_runs/<run_id> \
OUTPUT=sft_pipeline_outputs/<run_id>/v8_steps \
bash tools/run_v8_label_pipeline.sh
```

## 4. SFT 构造

```powershell
& $py -m sft_pipeline.build_sft.build_all `
  --labeled-steps 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\v8_steps\json\labeled_steps.jsonl' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\sft_agent_aligned' `
  --shuffle-variants 1
```

输出：

```text
sft_agent_aligned/
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

## 5. Agent-aligned Prompt 来源

SFT 构造必须复用 `SC2-Agent-260510` 中的 prompt 构造函数：

- Naming：`SC2_Agent.naming_agent.build_naming_messages()`
- Ordering：`SC2_Agent.ordering_agent.build_ordering_messages()`
- Executor：`SC2_Agent.executor_agent.build_executor_messages()`

`sft_pipeline/common/agent_reference.py` 负责调用这些参考函数和对应 data tools。

## 6. Naming 规则

输入：

```text
[Current Observation]
[Strategy Step]
```

system 中包含：

- Strategy Summary
- Canonical Terran Units
- Canonical Terran Upgrades
- jargon / upgrade hints

答案从该 step 的 `ordered_actions` 反推 entity/count：

```text
BARRACKSTRAIN_MARINE x4 -> {"name":"Marine","count":4}
BUILD_TECHLAB_BARRACKS  -> {"name":"BarracksTechLab","count":1}
```

Naming target 不表达 action 顺序。

## 7. Ordering 规则

Ordering 输入是同一个 step 的 action multiset，但会打乱：

```text
standard: [A, B, B, C]
input:    [B, C, A, B]
answer:   [A, B, B, C]
```

要求：

```text
Counter(input) == Counter(answer)
```

Ordering prompt 不包含 executor candidate/tag 上下文。它只使用：

- shuffled actions
- Strategy Step
- Current Observation
- prerequisite / tech-chain hints
- producer-conflict hints
- cost/time hints
- Strategy Summary

## 8. Executor 规则

Executor 样本条件：

```text
Train action
candidate_count > 1
selected_tag in candidate tags
```

训练 prompt/answer 使用短 tag：

```text
prompt_tag = real_tag % 1000
```

示例：

```text
[Candidate Executors]
  - tag=417 BARRACKS [idle, no add-on]
  - tag=987 BARRACKS [idle, no add-on]
```

答案：

```json
[417]
```

真实长 tag 只存在于原始 `executor_context`，不暴露给 SFT prompt/answer。

pending/conflict 上下文：

- 优先使用采集侧真实 `pending_actions_summary` / `executor_conflict_hints`。
- 如果为空，使用当前 step 中当前 executor action 后面的剩余 actions 重建。
- `waiting_actions_summary` 没有可靠采集值时保持 `(none)`。

离线重建的 pending summary 通常是 `0/N issued`，因为离线数据没有真实 scheduler `issued_count`。

## 9. 数据管理

建议每次使用同一个 `run_id` 管理：

```text
bo_collection_runs/<run_id>/
sft_pipeline_outputs/<run_id>/obs_qa.json
sft_pipeline_outputs/<run_id>/v8_steps/
sft_pipeline_outputs/<run_id>/sft_agent_aligned/
```

训练前检查：

- `v8_steps/manifest.json` 中 `require_victory == true`
- `sft_agent_aligned/qa_report.json`
- executor prompt/answer 中没有真实长 tag
- 地图字段和文件名没有中文地图名

## 10. 可选进阶步骤

基础 `build_all` 产出 thinking/nothink SFT 后，可按任务需要继续：

### CoT 注入（thinking 训练）

为 `*_thinking_sft.json` 注入经规则与 teacher 筛选的 CoT。详见 [cot_generation_validation_notes.md](cot_generation_validation_notes.md) 与 `sft_pipeline/README.md` §3。

```powershell
& $py -m sft_pipeline.build_sft.inject_cot_sft `
  --input 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\sft_agent_aligned' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\sft_agent_aligned_cot' `
  --tasks all `
  --gen-model-key qwen3-think `
  --teacher-model-key kimi-k2.5 `
  --max-workers 4
```

### Executor 规则黄金标签

从 Agent 对局 `executor_qa.json` 生成规则排序的 `golden_tags`。详见 [../sft_pipeline/build_sft/executor_golden_rank.md](../sft_pipeline/build_sft/executor_golden_rank.md)。

```bash
python3 -m sft_pipeline.build_sft.build_executor_golden_rank \
  --input <executor_qa.json> \
  --output-dir sft_pipeline_outputs/<run_id>/executor_golden
```

### Naming CoT 精选与重采样

多模型 CoT 合并、类别重采样、last-step 补充等。详见 [../sft_pipeline/build_sft/naming_data_and_training_notes.md](../sft_pipeline/build_sft/naming_data_and_training_notes.md)。
