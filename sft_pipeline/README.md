# SC2 SFT Pipeline

`sft_pipeline/` 是一个模块化训练数据平台，用来从本仓库的 SC2 bot 对局中采集轨迹、保存 obs、把 action order 标注成 v6 step，再构造用于替换 `SC2-Agent-260510` 中三个 LLM 位置的 SFT 数据。

三个目标位置是：

- Naming：`strategy step + current obs -> canonical entity/count`
- Ordering：`expanded action multiset + hints -> ordered action list`
- Executor：`train action + candidate producers -> selected producer tag`

注意：采集对局必须使用本仓库的 bot 和采集工具，不使用 `SC2-Agent-260510` 里的 bot。`SC2-Agent-260510` 只作为 prompt/context 标准的参考来源。

## 目录结构

```text
sft_pipeline/
  collect/
    run_collect.py          # 批量采集对局轨迹
    validate_obs.py         # 检查 obs/action 记录完整性
  label_steps/
    build_v6_steps.py       # 调 bo_2_nlstep v6，把胜局轨迹转成 Markdown + JSONL
    recover_v6_json_from_md.py
  build_sft/
    build_all.py            # 生成 naming/ordering/executor 的 thinking/nothink SFT
    build_naming_sft.py
    build_ordering_sft.py
    build_executor_sft.py
  common/
    agent_reference.py      # 复用 SC2-Agent-260510 的 prompt 与数据工具
    io.py
    sc2_graph.py
```

标准输出目录建议放在：

```text
bo_collection_runs/<run_id>/                 # 原始采集数据
sft_pipeline_outputs/<run_id>/v6_steps/      # step Markdown + JSONL
sft_pipeline_outputs/<run_id>/sft_agent_aligned/
```

## 1. 采集轨迹

采集阶段包装了 `tools/collect_terran_bo.py`，可以指定 bot、地图、对手种族、难度和最大并发数。

```powershell
$env:SC2PATH = 'C:\Program Files (x86)\StarCraft II'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'

& $py -m sft_pipeline.collect.run_collect `
  --output 'C:\code\SC2_bot_data\bo_collection_runs\my_run' `
  --map KairosJunctionLE `
  --bots bio marine tank `
  --races zerg protoss terran `
  --difficulties hard harder veryhard `
  --workers 4 `
  --port-offset 0
```

`--workers` 是采集对局的最大并发数。实际并发还会受 SC2 客户端、机器资源、端口和地图加载影响；建议从 `2` 或 `4` 开始。

地图名称必须使用传给 SC2 引擎的英文 map id，例如：

```text
KairosJunctionLE
AcropolisLE
ThunderbirdLE
YearZeroLE
```

不要在采集参数、文件名、`meta.map`、step Markdown 文件名或 SFT 元数据里使用客户端本地化中文地图名。采集侧会把输入的英文 map id 写入 `meta.map`，如果游戏接口返回了中文本地化名，只能作为 `meta.map_localized` 保留参考。

采集输出大致为：

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

每条 sequence 中会保存：

- `order_list`：整局 action 标准序列。
- `sequence[].ability`：每一步真实执行的 action。
- `sequence[].obs.text`：LLM prompt 可读的当前观测文本。
- `sequence[].obs.structured`：机器可读 obs。
- `sequence[].local_obs`：局部记录。
- `sequence[].executor_context`：只在 train 且候选执行单位数量大于 1 时保存。
- `meta.map`：输入 SC2 引擎的英文 map id。
- `meta.map_localized`：可选，本地化地图名，只作参考，不用于命名和训练主字段。

`executor_context` 只覆盖 train 类 action。addon/morph 已经不再走 Executor LLM，所以不保存这两类的 LLM 上下文。

采集后可以检查 obs 完整性：

```powershell
& $py -m sft_pipeline.collect.validate_obs `
  --run 'C:\code\SC2_bot_data\bo_collection_runs\my_run' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\obs_qa.json'
```

## 2. 转 v6 Step

step 标注阶段复用：

```text
bo_2_nlstep/Tools/bo_to_doc_v6.py
```

产物必须包含 Markdown，同时额外输出机器可读 JSONL。

```powershell
& $py -m sft_pipeline.label_steps.build_v6_steps `
  --data-dir 'C:\code\SC2_bot_data\bo_collection_runs\my_run' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\v6_steps' `
  --model-key kimi-k2.5 `
  --no-thinking `
  --workers 4
```

`--workers` 是转 step 的最大并发 trajectory 数。每个 trajectory 内部仍按 v6 标准逐 step 调 LLM；多个对局可以并发标注。API 限流比较紧时，把它设成 `1` 或 `2`。

默认只保留胜局：

```text
meta.result == "Victory"
```

如果显式加 `--include-non-victory`，才会包含非胜局。标准训练流程不建议这样做，因为后续 step 与 SFT 都要求来自胜率对局。

输出结构：

```text
sft_pipeline_outputs/<run_id>/v6_steps/
  md/
    <sample>.md
  json/
    labeled_steps.jsonl
    step_index.json
  manifest.json
```

`labeled_steps.jsonl` 中每一行对应一个真实 action range：

```json
{
  "sample_id": ".../step_003",
  "source_sequence_file": "...",
  "md_path": "...",
  "result": "Victory",
  "step_id": 3,
  "action_range": [20, 34],
  "ordered_actions": ["..."],
  "step_text_v6": "[Step 3] ...",
  "obs_at_step_start": {},
  "obs_at_each_action": []
}
```

v6 Markdown 的最后一个 final step 是战略总结/风格描述，没有 action range，不进入 SFT 构造。

如果 LLM 标注过程中被中断，但 Markdown 已经落盘，可以用离线恢复工具重建 JSONL：

```powershell
& $py -m sft_pipeline.label_steps.recover_v6_json_from_md `
  --data-dir 'C:\code\SC2_bot_data\bo_collection_runs\my_run' `
  --md-dir 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\v6_steps\md' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\v6_steps'
```

这个恢复工具不调用 LLM，只用已有 Markdown + 原始胜局 sequence 恢复 `labeled_steps.jsonl`。

## 3. 构造 SFT

一键构造三类任务、两种 Qwen3 模式：

```powershell
& $py -m sft_pipeline.build_sft.build_all `
  --labeled-steps 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\v6_steps\json\labeled_steps.jsonl' `
  --output 'C:\code\SC2_bot_data\sft_pipeline_outputs\my_run\sft_agent_aligned' `
  --shuffle-variants 1
```

也可以单独构造：

```powershell
& $py -m sft_pipeline.build_sft.build_naming_sft `
  --labeled-steps '...\v6_steps\json\labeled_steps.jsonl' `
  --output '...\sft_agent_aligned'

& $py -m sft_pipeline.build_sft.build_ordering_sft `
  --labeled-steps '...\v6_steps\json\labeled_steps.jsonl' `
  --output '...\sft_agent_aligned' `
  --shuffle-variants 3

& $py -m sft_pipeline.build_sft.build_executor_sft `
  --labeled-steps '...\v6_steps\json\labeled_steps.jsonl' `
  --output '...\sft_agent_aligned'
```

输出结构：

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

SFT 格式遵循 `docs/sft_data_format.md`：

- thinking：assistant 为 `<think>\n...\n</think>\n\nfinal answer`
- nothink：assistant 只包含 final answer

当前 reasoning 先留空，后续可以单独注入。

## 4. Agent-aligned Prompt 来源

SFT 的 system/user prompt 不再手写近似版，而是通过 `sft_pipeline/common/agent_reference.py` 复用 `SC2-Agent-260510` 中真实 Agent 的构造函数：

- Naming：`SC2_Agent.naming_agent.build_naming_messages()`
- Ordering：`SC2_Agent.ordering_agent.build_ordering_messages()`
- Executor：`SC2_Agent.executor_agent.build_executor_messages()`

这保证训练输入和线上模型位置一致。`sft_pipeline` 只负责从采集数据中构造这些函数需要的参数。

## 5. Naming 数据规则

线上 Naming 输入是：

```text
system:
  Strategy Summary
  Canonical Terran Units
  Canonical Terran Upgrades
  name hints / jargon / upgrade categories

user:
  [Current Observation]
  [Strategy Step]
```

这些元素来源：

- `Strategy Summary`：从 v6 Markdown 的 `# Summary` 读取。
- `Current Observation`：来自 `labeled_steps.jsonl.obs_at_step_start.text`。
- `Strategy Step`：来自 `step_text_v6`。
- canonical names：来自 `SC2-Agent-260510/SC2_Agent/data_tools/terran_names.py`。

Naming target 从该 step 的标准 `ordered_actions` 反推：

```text
COMMANDCENTERTRAIN_SCV  -> SCV
TERRANBUILD_BARRACKS    -> Barracks
BARRACKSTRAIN_MARINE    -> Marine
BUILD_TECHLAB_BARRACKS  -> BarracksTechLab
RESEARCH_COMBATSHIELD   -> ShieldWall
```

重复 action 聚合成 `count`：

```json
{"items":[{"name":"Marine","count":4},{"name":"Barracks","count":1}]}
```

Naming 不学习 action 顺序。当前 target item 顺序是稳定排序，不代表执行顺序。

## 6. Ordering 数据规则

Ordering 的标准答案是同一个 step 的真实胜局 action 顺序：

```text
labeled_steps.jsonl.ordered_actions
```

Ordering 输入会把同一个 action multiset 打乱：

```text
standard:
[A, B, B, C]

input:
[B, C, A, B]

answer:
[A, B, B, C]
```

构造时会校验：

```text
Counter(shuffled_input) == Counter(answer)
shuffled_input != answer
```

线上 Ordering prompt 的来源：

- `actions`：打乱后的 expanded action list。
- `obs_text`：当前 step 起点 obs text。
- `strategy_step_text`：v6 step 文本。
- `strategy_summary`：v6 Markdown summary。
- `prereq_hints`：复用 `SC2_Agent.data_tools.check_action_prereqs` 和 `tech_chain_relations`。
- `conflict_hints`：复用 `SC2_Agent.data_tools.detect_action_conflicts`。
- `cost_hints`：复用 `SC2_Agent.data_tools.action_cost.cost_for_action`。

Ordering prompt 不包含 executor candidate/tag 上下文。Executor 是单独模型位置。

## 7. Executor 数据规则

Executor 只为 train 且候选执行单位大于 1 的样本构造：

```text
action semantic type == Train
candidate_count > 1
selected_tag in candidate tags
```

采集侧保存真实长 tag，但训练 prompt/answer 使用 `SC2-Agent-260510` 的短 tag 机制：

```text
prompt_tag = real_tag % 1000
```

如果短 tag 碰撞，则丢弃该样本。正常样本形态：

```text
[Candidate Executors]
  - tag=417 BARRACKS [idle, no add-on]
  - tag=987 BARRACKS [idle, no add-on]
```

```json
[417]
```

Executor system prompt 中的元素：

- `[Ability to execute]`：来自 `executor_context.ability_name`。
- `[Pending actions not yet executed]`：
  - 优先使用采集侧保存的 `pending_actions_summary`。
  - 如果为空，则从当前 step 内“当前 executor action 后面的剩余 actions”离线重建。
- `[Actions currently waiting]`：
  - 如果采集侧没有可靠值，可以保持 `(none)`。
- `[Possible conflicts in pending actions]`：
  - 优先使用采集侧保存的 `executor_conflict_hints`。
  - 如果为空，则用候选执行单位类型 + 剩余 pending actions，复用参考 Agent 的 executor index 机制重建。

离线重建的 pending summary 中 `issued` 通常是 `0/N issued`。这是因为离线 sequence 只能知道当前 action 后还有多少同类 action，不知道线上 scheduler 当时真实的 `issued_count`。如果要完全复现真实运行 prompt，后续采集侧需要保存 scheduler 的真实 pending/waiting snapshot。

## 8. 数据存放与管理

建议每次采集和构造都使用同一个 `run_id`，并把原始数据、中间数据、最终数据分开存放：

```text
bo_collection_runs/<run_id>/
  原始对局轨迹、obs、replay、log、summary

sft_pipeline_outputs/<run_id>/obs_qa.json
  obs 完整性检查报告

sft_pipeline_outputs/<run_id>/v6_steps/
  md/
    v6 Markdown step 文档
  json/
    labeled_steps.jsonl
    step_index.json
  manifest.json

sft_pipeline_outputs/<run_id>/sft_agent_aligned/
  naming/
  ordering/
  executor/
  dataset_info.fragment.json
  qa_report.json
```

推荐的 `run_id` 命名：

```text
YYYY-MM-DD_<purpose>_<model_or_labeler>
```

例如：

```text
2026-06-21_terran_10win_kimi
2026-06-22_terran_hard_maps_kimi
2026-06-23_executor_more_barracks
```

管理原则：

- `bo_collection_runs/<run_id>` 是原始数据源，尽量不要手工修改。
- `v6_steps/json/labeled_steps.jsonl` 是 SFT 构造的标准输入。
- `sft_agent_aligned/` 是最终训练数据目录，优先使用这个目录下的数据训练。
- 地图相关字段和文件名统一使用英文 map id；不要混入中文地图名。
- 如果同一批轨迹用不同模型重标 step，应新建新的 `<run_id>` 或新的 `v6_steps_<labeler>` 目录，避免覆盖。
- 如果只重建 SFT，不需要重新采集或重新标 step，直接复用同一个 `labeled_steps.jsonl` 即可。
- 训练前检查 `qa_report.json` 和 `manifest.json`，确认 `require_victory == true`，并确认 executor prompt/answer 中没有真实长 tag。
