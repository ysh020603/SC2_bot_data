# 直接建造执行器经验总结 2026-06-17

## 背景

这次问题集中在 Terran 同类建筑的多数量建造上，典型现象是策略要求 `TERRANBUILD_BARRACKS x3`，但实战中只稳定下出 1-2 个建造命令，后续 macro cycle 又因为观察到建筑不足而追加 `BARRACKS x2`，最终表现成“该建的不一定建齐，不该追加时又重复追加”。

原执行方式把建筑动作交给 Sharpy 的 `GridBuilding.execute()` 生命周期管理。这个方式适合声明式规划，但与当前项目的命令式 `ExecutionScheduler` 有冲突：scheduler 需要精确知道“这条 PlannedAction 已经下了几个命令、还有几个没下、是否只是等资源、是否已经有 SCV 正在路上”。如果这些状态藏在 Sharpy Act 内部，就容易出现提前 DONE、重复 append、等待超时误杀等问题。

## 最终方案

保留 Sharpy 的强项，只拿它做两件事：

1. 用 `GridBuilding.position_terran()` 选择建筑落点。
2. 用 `GridBuilding.get_worker_builder()` 选择执行 SCV。

真正的建造命令由项目自己的执行器直接发送：

```python
worker.build(unit_type, position)
```

也就是说，建筑动作从“把整个生命周期交给 Sharpy”改成“Sharpy 只做决策辅助，scheduler 直接拥有命令下发和数量语义”。

## 代码改动

### 1. 新增 DirectBuildExecutor

文件：`SC2_Agent/execution/direct_build.py`

核心职责：

- 判断哪些建筑可以走直接建造路径：`SUPPLYDEPOT`、`BARRACKS`、`FACTORY`、`STARPORT` 等普通 Terran 建筑。
- 为每个 `PlannedAction` 记录起始数量 `base_count` 和目标数量 `target_count`。
- 每次只发一个真实建造命令，发出后把位置加入该 PA 自己的 fresh reservation。
- 统计三类**归属于该 PA** 的进度：
  - `existing`：该 PA 记录过的 reservation 位置附近已经生成的结构。
  - `en_route`：该 PA 的 reservation 位置附近存在 SCV build order，且目标点附近还没有结构。
  - `fresh`：该 PA 刚刚下令、还没被游戏引擎回写到 worker order 的短期预留。
- 当该 PA 自己的 `existing + en_route >= target` 时把 PA 标记为 DONE。

关键经验：每个独立 PA 必须绑定自己的 reservation/target，不能共享同一个全局 `en_route` 完成条件。否则第一个兵营一旦 `en_route`，后面几个相同的 `BARRACKS x1` PA 也会看到同一个 `en_route`，然后一起被误判 `DONE`。这条规则不只适用于兵营，也适用于所有类似普通 Terran 建筑。

另一个计数细节：不要把“已有建筑附近的 worker order”再算一次 `en_route`。建筑开始后，SCV 通常仍有 build order，如果同时把 structure 和 worker order 都算进去，会把 2 个兵营误判成 3 个进度。

### 2. PlannedAction 增加 direct build 状态

文件：`SC2_Agent/execution/command.py`

新增字段用于保存运行时状态：

- `_direct_build_helper`
- `_direct_build_base_count`
- `_direct_build_target_count`
- `_direct_build_worker_tag`
- `_direct_build_reserved_positions`
- `_direct_build_completed_positions`
- `_direct_build_last_issue_time`
- `_direct_build_attempts`

这些字段不进入序列化，只服务当前对局里的执行状态。

### 3. Scheduler 接入直接建造

文件：`SC2_Agent/execution/scheduler.py`

主要变化：

- build/research/addon 分支中，普通 Terran 建筑优先走 `DirectBuildExecutor.issue_one()`。
- 在资源 gate 前调用 `mark_done_if_satisfied()`，避免刚花完矿以后因为资源不足而错过 DONE 判断。
- WAITING 超时前调用 `keep_waiting_if_progressing()`，如果这条多建筑 PA 已经下过部分命令，只是正在等资源，就刷新等待时间，不把它误判为卡死。
- replace/abandon 时清理 direct build worker tag，避免继承旧 SCV 状态。

关键经验：多数量建造不是一个瞬时动作。`BARRACKS x3` 在 400 矿不足的情况下本来就会跨几十秒逐个下单，所以不能用普通 WAITING abandon 逻辑粗暴杀掉已经有进度的 PA。

### 4. append 阶段增加建筑延后释放

文件：`SC2_Agent/execution/scheduler.py`

这次还有一个独立问题：Step1 的 `BARRACKS x3` 已经正确下出 3 个命令时，触发 `executable_drained` 后 Step2 又会 append 上 `BARRACKS x2`。这两个兵营从策略语义上仍然是要造的，不能丢弃；但也不能立刻合并进 Step1 的 PA，否则会把 Step1 的目标从 3 混成 5。

因此在 `set_actions(mode="append")` 增加 deferred 机制：

- 作用于 direct build 支持的普通 Terran 建筑，例如 `SUPPLYDEPOT`、`BARRACKS`、`ENGINEERINGBAY`、`FACTORY`、`ARMORY`、`MISSILETURRET`、`BUNKER`、`SENSORTOWER`、`GHOSTACADEMY`、`STARPORT`、`FUSIONCORE`。
- 如果同名 build action 仍在队列里，append 进来的同名 build 不丢弃，而是标记为 deferred。
- 如果场上已有同类建筑 under construction，或 SCV 正在路上建同类建筑，append 进来的同类 build 也标记为 deferred。
- deferred PA 不抢资源、不占独立 waiter 槽、不参与当前 direct build 的 target count。
- 当前一批同类建筑没有 active PA，且场上没有同类 in-flight 后，再把 deferred PA 释放为普通 PENDING action。
- `COMMANDCENTER`、`REFINERY`、addon、research、train、morph 不走这套普通建筑 deferred 机制。
- deferred 的目标不是“丢掉重复请求”，而是让后续 PA 等前一批 in-flight 结束后再独立绑定自己的 reservation/target。

关键日志：

```text
[Scheduler] append deferred TERRANBUILD_BARRACKS x2: BARRACKS already in flight
[Scheduler] append released TERRANBUILD_BARRACKS x2: BARRACKS is no longer in flight
```

### 5. LLM bot 日志改为显示真实 active queue

文件：`dummies/generic/universal_llm_bot.py`

之前日志只打印“请求安装到 scheduler 的 pairs”，看不出哪些 PA 被 scheduler 延后执行。现在新增：

```text
Scheduler active queue after install: [...]
```

以后看问题时要以 active queue 为准，requested pairs 只代表 macro pipeline 的输入。

## 验证结果

旧版硬拦截实验目录：

`game_records/marine_rush_full/20260617_154658_mr_retest`

关键日志：

```text
[DirectBuild] BARRACKS target=3 progress=1 ... attempt=1
[DirectBuild] BARRACKS target=3 progress=2 ... attempt=2
[DirectBuild] BARRACKS target=3 progress=3 ... attempt=3
[DirectBuild] BARRACKS DONE target=3 existing=2 en_route=1
```

Cycle2 决策时观察到：

```text
Under Construction: 2 BARRACKS.
Workers En Route: 1 BARRACKS.
```

旧版本中 Step2 的重复兵营曾被硬拦截：

```text
[Scheduler] append guard dropped TERRANBUILD_BARRACKS x2: BARRACKS already in flight
```

新版 deferred 机制验证目录：

`game_records/marine_rush_full/20260617_160934_mr_retest`

新版改为保留并延后释放：

```text
[Scheduler] append deferred TERRANBUILD_BARRACKS x2: BARRACKS already in flight
[Scheduler] append released TERRANBUILD_BARRACKS x2: BARRACKS is no longer in flight
```

释放后的 Step2 `BARRACKS x2` 正常继续下单：

```text
[DirectBuild] BARRACKS target=5 progress=4 ... attempt=1
[DirectBuild] BARRACKS target=5 progress=5 ... attempt=2
[DirectBuild] BARRACKS DONE target=5 existing=4 en_route=1
```

Step3 的 `BARRACKS x1` 在 Step2 仍未完成时也被 deferred，之后释放并正常下单：

```text
[Scheduler] append deferred TERRANBUILD_BARRACKS x1: same build action is still WAITING
[Scheduler] append released TERRANBUILD_BARRACKS x1: BARRACKS is no longer in flight
[DirectBuild] BARRACKS target=6 progress=6 ... attempt=1
[DirectBuild] BARRACKS DONE target=6 existing=5 en_route=1
```

新版验证结果：

```text
Result: Victory
GameAnalyzerEnd BARRACKS total: 6 alive: 6 dead: 0
```

训练类动作没有进入 deferred。当前 scheduler 对 train 不做 append drop，也不与旧 PA 合并；后续训练请求会保留为独立 PA，在 producer 可用时继续 issue。上述新版实验中多轮 `BARRACKSTRAIN_MARINE xN` 都保留在 active queue 中，没有出现类似建筑的“被丢弃”问题。

## 后续排查 Checklist

排查同类建筑问题时，优先看这些信号：

1. `DirectBuild` 是否出现连续 attempt，数量是否等于目标数量。
2. `DONE target=N existing=X en_route=Y` 中 `X + Y` 是否达到目标。
3. 如果多个同名 PA 同时结束，确认它们是否各自有独立 reservation，而不是共享了同一个 SCV build order。
4. 决策观察里的 `Under Construction` 和 `Workers En Route` 是否能解释当前进度。
5. append cycle 中是否出现 `append deferred`。
6. 后续是否出现对应的 `append released`。
7. LLM bot 的 `Scheduler active queue after install` 是否显示 `[deferred]`。

经验原则：

- 对游戏引擎有真实副作用的动作，状态所有权最好留在 scheduler。
- Sharpy 的工具可以用，但不要让 Sharpy Act 和 scheduler 同时拥有同一条动作的生命周期。
- 计数时必须区分“已生成结构”“SCV 正在路上”“刚下令等待引擎确认”，否则很容易提前 DONE 或重复下单。
- 对多 PA 同类建筑，计数还必须区分“这是谁的 reservation”；全局同类 `en_route` 只能用于判断是否需要 deferred，不能作为所有 PA 的 DONE 条件。
- append 是预取，不是强制扩建；预取阶段必须尊重场上 in-flight 状态。
