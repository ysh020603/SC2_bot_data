# AbilityRecorder 设计说明

`AbilityRecorderManager` 位于：

```text
sharpy/managers/extensions/ability_recorder.py
```

它负责把 bot 实际落地的宏观动作写入 sequence JSON，并为后续 step/SFT 流程保存 obs 和 executor 上下文。

## 核心原则

Recorder 不在 bot 尝试下发命令时立即写入 sequence，而是在动作被 SC2 接受并开始执行后再 commit。

这样可以避免：

- 资源不足、队列满等未执行动作污染数据。
- 同一个 morph/research/build 被多帧重复尝试而重复记录。
- `order_list` 与真实落地顺序不一致。

简化流程：

```text
bot.do(action)
  -> recorder.record(action) 写入 pending
  -> SC2 接受命令并进入 unit.orders / morph / addon building 状态
  -> recorder.post_update() 检测已落地
  -> _commit() 写入 sequence
```

## sequence 记录内容

每个已 commit 的 action 会写入：

```json
{
  "seq": 12,
  "ability": "BARRACKSTRAIN_MARINE",
  "unit_tag": 4355260417,
  "unit_type": "BARRACKS",
  "issued_time": 123.4,
  "obs": {
    "text": "...",
    "structured": {}
  },
  "local_obs": {}
}
```

`obs` 来自 LLM observation recorder，供 Naming/Ordering 的 prompt 使用。`local_obs` 是机器可读局部实体快照。

## 地图名

Recorder 写入 `meta.map` 时优先使用采集入口传入的英文 map id：

```text
KairosJunctionLE
ThunderbirdLE
YearZeroLE
```

客户端本地化地图名只写入：

```json
{"map_localized": "凯罗斯中转站-天梯版"}
```

不要使用中文地图名作为 sequence 文件名、`meta.map` 或后续 SFT 元数据。

## TechLab / Reactor 命名

SC2 API 中 addon ability 可能是通用名：

```text
BUILD_TECHLAB
BUILD_REACTOR
```

但训练数据和图谱需要 host-specific action：

```text
BUILD_TECHLAB_BARRACKS
BUILD_TECHLAB_FACTORY
BUILD_TECHLAB_STARPORT
BUILD_REACTOR_BARRACKS
BUILD_REACTOR_FACTORY
BUILD_REACTOR_STARPORT
```

Recorder 会根据 `action.unit.type_id` 解析宿主建筑，写入带后缀的标准 action 名。不要依赖 `action.target` 判断宿主，因为 addon 的 target 常常不是 host unit。

## Executor context

当前标准中，Executor LLM 只用于：

```text
Train action 且候选执行单位数量 > 1
```

因此 Recorder 只为这类 action 保存 `executor_context`：

```json
{
  "executor_context": {
    "ability_name": "BARRACKSTRAIN_MARINE",
    "selected_tag": 4355260417,
    "selected_type": "BARRACKS",
    "candidate_executors": [
      {
        "tag": 4355260417,
        "type": "BARRACKS",
        "is_idle": true,
        "add_on": "Reactor",
        "add_on_tag": 4351000000,
        "orders": []
      }
    ],
    "candidate_count": 2,
    "cost_hint": "minerals 50, gas 0, supply 1",
    "pending_actions_summary": "",
    "waiting_actions_summary": "",
    "executor_conflict_hints": ""
  }
}
```

说明：

- `selected_tag` 是真实长 tag，只保存在原始采集数据中。
- SFT 构造时会转换成短 tag：`prompt_tag = real_tag % 1000`。
- 如果短 tag 碰撞，该 executor 样本会被丢弃。
- addon/morph 不保存 executor context，也不构造 executor SFT。

当前采集侧可能没有真实 scheduler pending/waiting snapshot，所以 `pending_actions_summary` 与 `executor_conflict_hints` 可能为空。SFT 构造阶段会用当前 step 中“当前 executor action 之后的剩余 actions”进行 fallback 重建。`waiting_actions_summary` 没有可靠值时保持 `(none)`。

## Pending 超时

未在限定时间内检测到落地的 pending action 会被丢弃，不进入 `sequence` 和 `order_list`。这保证 `order_list` 更接近真实执行顺序。

## 快速验证

```powershell
python -m sft_pipeline.collect.run_collect `
  --output bo_collection_runs/smoke_test `
  --map KairosJunctionLE `
  --bots marine `
  --races zerg `
  --difficulties hard `
  --workers 1
```

检查：

- `sequence_count > 0`
- `order_list_count == len(order_list)`
- `meta.map` 是英文 map id
- addon action 带 host 后缀
- train 多候选样本有 `executor_context`
