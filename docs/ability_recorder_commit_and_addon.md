# AbilityRecorder 设计说明

`AbilityRecorderManager` 位于：

```text
sharpy/managers/extensions/ability_recorder.py
```

它负责把 bot 实际落地的宏观动作写入 sequence JSON，并为后续 step/SFT 流程保存 obs 和 executor 上下文。

## 核心原则

Recorder 不在 bot 尝试下发命令时立即写入 sequence，而是在动作已经造成可验证的
SC2 引擎状态变化后再 commit。仅仅在 worker 的 `unit.orders` 中短暂看到 Build
命令不算落地，因为多个 worker 可能同时前往同一个位置，最终只有一个建筑会开始建造。

这样可以避免：

- 资源不足、队列满等未执行动作污染数据。
- 同一个 morph/research/build 被多帧重复尝试而重复记录。
- 未落地 Action 混入 `order_list`，成为错误训练金标。

简化流程：

```text
bot.do(action)
  -> recorder.record(action) 写入 pending
  -> Build/Addon: SC2 回调建筑开工或完工事件
  -> Train: SC2 回调对应单位的 on_unit_created 事件
  -> Morph: SC2 回调 actor 的 on_unit_type_changed 事件
  -> Research: SC2 回调 on_upgrade_complete 事件
  -> recorder 将带回执的 action 写入 sequence
```

每个新建筑实体 tag 最多只能确认一个 pending Build。这样即使四个 SCV 对同一落点
下达命令，sequence 也只会记录真正产生建筑实体的一个 Action。

## sequence 记录内容

每个已 commit 的 action 会写入：

```json
{
  "seq": 12,
  "ability": "BARRACKSTRAIN_MARINE",
  "issued_time": 123.4,
  "confirmed_time": 141.2,
  "confirmation": {
    "kind": "unit_created",
    "game_time": 141.2,
    "actor_tag": 4355260417,
    "entity_tag": 4355260999,
    "entity_type": "MARINE"
  },
  "obs": {
    "text": "...",
    "structured": {}
  },
  "local_obs": {}
}
```

`obs` 和 `local_obs` 都在 `issued_time` 捕获，而不是在结果确认时捕获。这样训练样本看到的是 bot
作出决策时的环境；`confirmed_time` 仅用于审计该动作何时真正在引擎中落实。

游戏结束写文件时，已确认 Action 按原始下令顺序排列并重新生成连续 `seq`。
因此 sequence 表达“按决策顺序排列、且最终确实产生引擎结果的 Action”，而不是按单位
生产完成时间重新排序。Meta 中的 `confirmation_schema` 固定为 `sc2-outcome-v2`。

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

未在限定时间内检测到落地的 pending action 会被丢弃，不进入 `sequence` 和 `order_list`。
不同结果使用与其耗时匹配的窗口：Build 90 秒、Train 180 秒、Morph 120 秒、Research
300 秒。超时或游戏结束时仍未收到引擎结果事件的动作不会进入 sequence。输出 meta 会记录：

```text
expired_unconfirmed_action_count
superseded_unconfirmed_action_count
pending_unconfirmed_action_count
```

这三个字段用于审计 bot 尝试过、但没有在引擎中产生对应效果的宏观动作。

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
- 每个 Build / BuildOnUnit / BuildInstant 都有 `structure_started`（极少数漏过开工回调时为 `structure_completed`）回执
- 每个 Train 都有 `confirmation.kind == "unit_created"`
- 每个 Morph 都有 `confirmation.kind == "unit_type_changed"`
- 每个 Research 都有 `confirmation.kind == "upgrade_completed"`
- 同一引擎结果（`confirmation.kind + entity_tag`）在同一局中只能被消费一次；同一建筑后续 Morph 可以合法复用其 tag
- addon action 带 host 后缀
- train 多候选样本有 `executor_context`
