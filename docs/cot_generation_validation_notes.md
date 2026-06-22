# CoT Generation And Validation Notes

本文记录 `sft_pipeline` 中 CoT 后处理模块的设计经验、一次整条轨迹测试结果，以及后续调参建议。

## 目标

基础 SFT 构建阶段已经能生成 `thinking` / `nothink` 两类数据，但 `thinking` 文件中的 `<think>` 内容为空。CoT 后处理模块用于在不改动原始 SFT 构建流程的前提下，为已有 `*_thinking_sft.json` 注入经过筛选的 CoT。

核心原则：

- 生成模型必须基于原始 `system` 和 `human prompt` 重新答题，不提供 gold answer。
- 生成模型输出 CoT 与 generated answer。
- 程序先做确定性硬规则检查，明显不合格样本直接丢弃。
- teacher 模型再做三选一：`drop`、`use_gold_answer`、`use_generated_answer`。
- CoT 本身是训练目标；如果 CoT 有实质事实、逻辑、科技前置、排序或 tag 选择错误，必须 `drop`，不能靠换成 gold answer 挽救。

## 后处理入口

模块：

```text
sft_pipeline/build_sft/inject_cot_sft.py
```

典型命令：

```powershell
python -m sft_pipeline.build_sft.inject_cot_sft `
  --input C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\sft_agent_aligned `
  --output C:\code\SC2_bot_data\sft_pipeline_outputs\<run_id>\sft_agent_aligned_cot `
  --tasks all `
  --gen-model-key Qwen3-1.7b_think `
  --teacher-model-key Kimi-k2.5 `
  --config-path C:\code\SC2_bot_data\sft_pipeline\API_config\config.json `
  --max-workers 4 `
  --max-retries 0 `
  --teacher-temperature 0
```

输出：

- CoT 增强版训练文件。
- `cot_injection_report.json`：汇总统计。
- `<task>/cot_audit.jsonl`：保留样本的决策记录。
- `<task>/cot_rejected_samples.jsonl`：丢弃样本的原因。

## 整条轨迹测试

测试日期：2026-06-22

使用的完整轨迹：

```text
match_02_marine_protoss_mediumhard_AcropolisLE
```

原始 sequence：

```text
C:\code\SC2_bot_data\bo_collection_runs\2026-06-21_sft_10game_kimi\match_02_marine_protoss_mediumhard_AcropolisLE\marine_rush\sequences\marine-ai.protoss.mediumhard_滨海卫城-天梯版_2026-06-21 19_42_53_336966.json
```

轨迹规模：

```text
v6 steps: 9
真实 actions: 92
```

由该轨迹重新构建出的 SFT 子集：

```text
naming: 9
ordering: 7
executor: 65
total: 81 thinking samples
```

测试配置：

```text
generation_model_key: Qwen3-1.7b_think
generation_model: qwen3-1.7b
teacher_model_key: Kimi-k2.5
teacher_model: kimi-k2.5
max_workers: 4
max_retries: 0
teacher_temperature: 0
```

完整测试输出：

```text
C:\code\SC2_bot_data\sft_pipeline_outputs\2026-06-21_sft_10game_kimi\one_trajectory_match_02\sft_agent_aligned_cot_qwen17b_kimi
```

## 测试结果

汇总：

| task | total | generated | rule_pass | rule_drop | teacher_use_gold | teacher_use_generated | teacher_drop | kept |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| naming | 9 | 9 | 1 | 8 | 0 | 1 | 0 | 1 |
| ordering | 7 | 7 | 4 | 3 | 0 | 1 | 3 | 1 |
| executor | 65 | 65 | 65 | 0 | 13 | 19 | 33 | 32 |
| total | 81 | 81 | 70 | 11 | 13 | 21 | 36 | 34 |

总体保留率：

```text
34 / 81 = 41.98%
```

任务保留率：

```text
naming: 1 / 9 = 11.11%
ordering: 1 / 7 = 14.29%
executor: 32 / 65 = 49.23%
```

## 主要失败模式

### Naming

失败集中在硬规则层。

典型原因：

```text
generated total count must be 10-15, got 7
generated answer misses gold item types: ['SCV']; generated total count must be 10-15, got 5
generated total count must be 10-15, got 6
```

经验：

- Qwen3-1.7B 容易只抽取策略 step 中最显眼的战斗单位，漏掉持续生产的 SCV。
- 10-15 总量规则很有效，能快速挡掉过短或只抽关键动作的 naming answer。
- 对 naming 来说，生成模型需要更强的格式和计数约束，否则 teacher 前的硬规则会筛掉大多数样本。

### Ordering

失败来自两层：

- 硬规则：action 数量不一致、multiset 不一致。
- teacher：虽然 multiset 通过，但 CoT 对排序依据解释错误。

典型原因：

```text
generated action count 10 != gold count 12; generated action multiset differs from gold
generated action count 3 != gold count 10; generated action multiset differs from gold
```

teacher 典型判断：

```text
The generated CoT contains multiple serious errors that make it unsafe for training.
It places all SCVs at the end after all buildings, while the strategy says to keep worker production rolling.
```

经验：

- Ordering 的硬规则必须保留 `Counter(generated_actions) == Counter(gold_actions)`。
- 单纯数量正确还不够，teacher 对“为什么这么排”的检查很关键。
- 小模型容易把“先造建筑，再补生产”当成通用策略，忽略 worker rolling、supply、producer conflict 这类 prompt 信息。

### Executor

所有 executor 样本都通过硬规则，但约一半被 teacher 丢弃。

典型 teacher drop 原因：

```text
CoT incorrectly states all candidates are busy when one candidate is explicitly idle.
CoT claims a Barracks might have an add-on when the prompt says no add-on.
CoT ignores pending BUILD_REACTOR_BARRACKS conflict and chooses arbitrarily.
```

经验：

- Executor 的 tag 硬规则容易通过，因为候选 tag 集合明确。
- 真正的质量风险在 CoT 对 idle/busy、add-on、pending conflict 的解释。
- teacher 必须被明确要求：CoT 有实质错误时必须 `drop`，不能因为最终 tag 正确就 `use_gold_answer`。

## Teacher Prompt 经验

初版 teacher prompt 曾出现一个问题：teacher 识别出 executor CoT 的 reasoning 错误，但仍选择 `use_gold_answer`。这会把错误 CoT 保留下来，不符合训练目标。

修正后的原则：

```text
The generated CoT itself is a training target.
If it contains any substantive factual, logical, game-rule, prerequisite, ordering,
or tag-selection error, choose drop.
Do not salvage a wrong CoT by pairing it with the gold answer or generated answer.
```

这个约束加入后，同一条错误 executor CoT 被正确判为 `drop`。

## 规则检查经验

### Naming 硬规则

当前有效规则：

- generated answer 必须可解析为 `{"items": [...]}`。
- generated name 种类不能漏 gold name 种类。
- 不要求每个种类数量完全一致。
- generated 总 count 必须在 10-15。
- name 必须来自 Agent 使用的 canonical Terran names，而不是仅来自 `data_ref`。

注意：测试时发现 `data_ref` 与 Agent canonical upgrade 名存在差异，例如 `Stimpack`、`ShieldWall`、`PunisherGrenades`。因此 canonical source 应使用：

```text
sft_pipeline.common.agent_reference.canonical_terran_names()
```

### Ordering 硬规则

当前有效规则：

- generated answer 必须可解析为 `{"ordered_actions": [...]}`。
- generated action 数量必须等于 gold action 数量。
- `Counter(generated_actions) == Counter(gold_actions)`。
- prompt 中明确的 tech prerequisite 顺序必须满足。

保留理由：

- 如果 multiset 不一致，answer 已经不是同一个任务，不应再交给 teacher 浪费 token。
- 科技前置和 action 数量属于确定性错误，应该在程序层拦截。

### Executor 硬规则

当前有效规则：

- generated answer 必须可解析为 `[tag]`。
- tag 必须来自 `[Candidate Executors]`。
- CoT 如果显式提到 tag 选择，必须与 generated answer 一致。

经验：

- executor 的硬规则只负责合法性，不负责质量。
- 质量主要依赖 teacher 检查 prompt 与 CoT 的一致性。

## 对 Qwen3-1.7B 的结论

本次完整轨迹测试说明：

- API 和 reasoning 抽取链路可用，`content_think_tags` 能正确抽到 Qwen3-1.7B 的 CoT。
- Qwen3-1.7B 能产出可解析答案，但 naming/order 稳定性偏弱。
- executor tag 经常能答到合法范围内，但 CoT 的解释质量波动较大。
- 在当前高质量门槛下，Qwen3-1.7B 更适合作为低成本初筛或 executor 类样本补充，不适合作为高通过率 CoT 生成主力。

## 后续建议

1. 对 generation prompt 分任务增强。

   Naming 需要强调：

   ```text
   Include ongoing worker production if the step requires it.
   Preserve every concrete unit/building/upgrade type implied by the prompt.
   Total planned increments should be 10-15 unless the task clearly says otherwise.
   ```

   Ordering 需要强调：

   ```text
   Use every input action exactly once, preserving duplicates.
   Keep worker production rolling when the prompt says so.
   Do not group all macro actions at the end unless the prompt requires it.
   ```

   Executor 需要强调：

   ```text
   Check idle/busy, add-on status, and pending conflicts before choosing.
   If the prompt says a pending add-on action conflicts with a bare Barracks, mention it explicitly.
   ```

2. `max_retries` 建议从 0 提到 1 或 2。

   本次测试为节省成本使用 `--max-retries 0`。对于低通过率任务，允许重试能显著提高 kept 数，但会增加 token 成本。

3. 分任务选择生成模型。

   可以考虑：

   - naming / ordering 用更强 thinking 模型。
   - executor 先用 Qwen3-1.7B 生成，再靠 Kimi teacher 筛选。

4. 保留 `cot_rejected_samples.jsonl`。

   reject 文件对调 prompt 很有价值。尤其是 naming 的 missing type 与 count 失败、executor 的 teacher_drop 原因，能直接转化为下一轮 generation prompt 约束。

5. 大批量运行前先做整条轨迹级 smoke test。

   推荐流程：

   ```text
   选择 1 条完整 trajectory
   -> 过滤 labeled_steps.jsonl
   -> build_all 生成子集
   -> inject_cot_sft 跑完整后处理
   -> 查看 cot_injection_report / rejected / audit
   -> 再扩大到全 run
   ```
