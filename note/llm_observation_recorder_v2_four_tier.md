# LLM Observation Recorder v2：四级分类 + `[Tag]` 分段

> 日期：2026-05-10（v2 升级）
> 关联文件：`sharpy/managers/extensions/llm_observation_recorder.py`
> 前置文档：`note/llm_observation_recorder.md`

本次只动了 `LLMObservationRecorder` 一个文件，但**改了它最核心的两块**：

1. 将「自家单位/建筑」的扁平计数升级为 **四级分类**：`Completed` / `Under Construction` / `Workers En Route` / `Active Queues`，让 LLM 能够区分「已建好」「正在建」「派工人在路上」「队列中正在生产/科研」。
2. 把英文文本输出改为 **`[Tag]` 分段**结构（`[Time]` / `[Economy]` / `[Own Forces & Infrastructure]` / `[Enemy Intelligence]` / `[Map Control]` / `[Combat Analysis]` / `[Threat Flags]`），方便 LLM 注意力直接定位到所需区块。

---

## 一、问题背景

旧版 `[Own Forces]` 只输出一行：

```
Own forces: 15 PROBE, 5 ZEALOT.
```

LLM 看到这行无法区分：

- 1 个 Starport 是不是已经建完了？
- Factory 还在打地基没建完？
- 派去造星港的 SCV 是不是还在路上？
- Barracks 队列里到底有几只 Marine 在排队？

经常导致大模型重复下达建造指令，或对当前节奏判断错误。

---

## 二、四级分类的语义定义

| Tier | 含义 | 检测规则 |
| --- | --- | --- |
| **Completed** | 已建好、立即可用的单位/建筑 | `unit.is_ready` (build_progress == 1)；`is_structure` 与否都进入此桶 |
| **Under Construction** | 已经下了地基、正在敲击的建筑 | `unit.is_structure and 0 < build_progress < 1` |
| **Workers En Route** | 工人接到 BUILD_X 指令、但建筑地基**还没出现**在地图上 | 见下文「en route 判定」 |
| **Active Queues** | 已建好的生产建筑里排着的兵种 / 正在研发的科技；以及 Zerg 的 egg/cocoon 形态单位 | 见下文「队列判定」 |

### 2.1 Workers En Route 判定

```
worker.orders[0].ability.exact_id  ∈  BUILD_ABILITY_TO_STRUCTURE
AND target 不属于「我方已有的、正在建造的建筑 tag 集合」
AND  (target 是 Point2)  OR  (target 是单位 tag 且该单位是 vespene 气矿)
```

- 工人指令的目标是 **`Point2`** → 还在飞向地基预定地点 → 是 en route
- 工人指令的目标是 **气矿（vespene geyser）类型** → Refinery/Extractor/Assimilator 还没生成 → 是 en route
- 工人指令的目标是 **我方已开建的建筑 tag** → 工人已抵达正在敲击 → 跳过（这一栋已经被算进 Under Construction，不重复计入）

这条规则在三族通用：
- Terran SCV 在 BUILD_X 抵达后，order target 会变成「正在建造的 building tag」；我们看到这个 tag 在 `under_construction` 集合里，就跳过；
- Protoss Probe 抵达后会切换到 idle，自动不再触发 BUILD_X 命中；
- Zerg Drone 抵达后变形为建筑本身（Drone 消失），同样自动不再被命中。

### 2.2 Active Queues 判定

对每个**已建好**的建筑（`is_ready and is_structure`）的 `unit.orders` 全量遍历：

- `ability.exact_id ∈ TRAIN_ABILITY_TO_UNIT` → 记为 `Training <UNIT_NAME>`，例：`Training MARINE`
- `ability.exact_id ∈ RESEARCH_ABILITY_TO_UPGRADE` → 记为 `Researching <UPGRADE_NAME>`，例：`Researching STIMPACK`

对所有**未建好的非建筑**（即 Zerg 的 egg / cocoon），按它的 `type_id` 直接归入 `active_queues`：

- 例：BANELINGCOCOON 仍在孵化 → `Training BANELINGCOCOON: 1`
- 例：larva 化身 EGG → `Training EGG: 1`

> 这里没有强制把 EGG 解码为「正在生成的最终单位」，因为 SC2 数据里 egg 的状态包括 morph type 信息但映射成本较高且容易随版本错位；保留原 type 即可让 LLM 正确感知「池子里还有 N 个 egg 在转」。

### 2.3 ability dict 是怎么得到的

利用 `python-sc2` 已自动生成的两个映射：

- `sc2.dicts.unit_train_build_abilities.TRAIN_INFO[trainer][produced]["ability"]`
- `sc2.dicts.unit_research_abilities.RESEARCH_INFO[building][upgrade]["ability"]`

记录器在 `start()` 中懒构造三张反向表：

```python
self._build_ability_to_structure   = {ability: produced  for SCV/Probe/Drone in TRAIN_INFO}
self._train_ability_to_unit         = {ability: produced  for 其它 trainer    in TRAIN_INFO}
self._research_ability_to_upgrade   = {ability: upgrade   for everything       in RESEARCH_INFO}
```

随 SC2 数据文件再生，自动跟新；不需要手维护一份长长的 ability 列表。

---

## 三、文本格式：`[Tag]` 分段

新模板（每段独立成行，便于 LLM 抽段）：

```
[Time] 03:15 (195.0s).
[Economy] 400 minerals, 100 vespene; income 850 mins/min, 210 gas/min. Supply: 26/31 (workers 16, army 10).
[Own Forces & Infrastructure]
  Completed: 16 SCV, 2 MARINE, 1 BARRACKS, 1 COMMANDCENTER, 1 SUPPLYDEPOT.
  Under Construction: 1 FACTORY, 1 REFINERY.
  Workers En Route: 1 STARPORT.
  Active Queues: Training 2 MARINE, Researching STIMPACK.
[Enemy Intelligence] 12 PROBE, 1 CYBERNETICSCORE, 1 GATEWAY, 1 NEXUS.
[Map Control] 1 own bases, 1 known enemy bases, 14 neutral expansions remaining.
[Combat Analysis] army advantage = Even, income advantage = SlightAdvantage, predicted = Even. Power: 15 vs 12. Losses: own 50 minerals/0 gas, enemy 0 minerals/0 gas.
[Threat Flags] proxy buildings spotted near base.
```

空状态优雅降级：

```
[Own Forces & Infrastructure]
  Completed: nothing built yet.
  Under Construction: none.
  Workers En Route: none.
  Active Queues: none.
[Enemy Intelligence] nothing scouted yet.
[Threat Flags] none.
```

### Active Queues 的语法

- `Training` 项展开为 `Training <count> <NAME>`（队列里可能堆 N 个同款，所以 count 有意义）。
- `Researching` 项展开为 `Researching <NAME>`（每项研究是 0/1 的，写 count 反而冗余）。

由 `_format_active_queues` 单独负责，不影响 `_format_count_dict` 在其他段（Completed / Enemy Intelligence 等）的复用。

---

## 四、数据结构变化

### 4.1 master snapshot

旧：

```json
{ "army": { "PROBE": 15, "ZEALOT": 5 } }
```

新：

```json
{
  "own_forces": {
    "completed":         { "SCV": 16, "MARINE": 2, "BARRACKS": 1, ... },
    "under_construction":{ "FACTORY": 1, "REFINERY": 1 },
    "workers_en_route":  { "STARPORT": 1 },
    "active_queues":     { "Training MARINE": 2, "Researching STIMPACK": 1 }
  }
}
```

> 顶层 key 从 `army` 改为 `own_forces`，更准确反映其内涵（包含战斗单位、工人、建筑、队列、计划）。

### 4.2 兼容性

- 所有改动都是单独的提取器内部 + 文本生成器内部，**不影响其他 sharpy manager**。
- JSON schema 是新增/扩展，下游消费者（LLM prompt / RL state）需要按新 key 读取。
- 因为还没有外部 schema 用户，直接替换是安全的。

---

## 五、新增的辅助方法

| 方法 | 作用 |
| --- | --- |
| `_build_ability_lookups()` | 在 `start()` 末尾被调，构造 `BUILD/TRAIN/RESEARCH` 三张反向表 |
| `_extract_own_forces_infrastructure()` | 替换原 `_extract_own_army_state`，输出四级 dict |
| `_safe_ability_id(ability)` | 容忍多种 `UnitOrder.ability` 形态（`exact_id`/`id`/raw int），避免单点 throw |
| `_format_count_dict(data, empty)` | 通用「count name」拼接，按 count 降序、name 升序 |
| `_format_active_queues(data, empty)` | 队列段专用：识别 `Training` / `Researching` 前缀产出更自然的英文 |

模块级常量：

```python
_WORKER_TYPES         = {Race.Protoss: PROBE, Race.Terran: SCV, Race.Zerg: DRONE}
_VESPENE_GEYSER_TYPES = {VESPENEGEYSER, RICHVESPENEGEYSER, PROTOSSVESPENEGEYSER,
                         PURIFIERVESPENEGEYSER, SHAKURASVESPENEGEYSER}
```

---

## 六、关键决策回顾

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| Workers en route 的去重 | 排除 target tag 已属于「自家在建建筑」的 worker | Terran SCV 抵达后还会持有 BUILD_X 指令，不去重会双计 |
| Refinery / Extractor / Assimilator | target 是 vespene 气矿 → 仍算 en route | 气矿建筑下达指令时地基不会立即出现，工人是真的在路上 |
| Active queues 的兵种用 `name` 还是 game_data 友好名 | 用 `UnitTypeId.name`（全大写） | 与 `completed` / `under_construction` 一致，LLM 不在乎大小写 |
| Researching 是否带数量 | 不带 | 单个建筑同时只能研究一个升级，count==1 是常态，写出来反而绕 |
| Empty-state 文本 | 显式输出 `nothing built yet` / `none` | 避免 LLM 看到空白以为字段被截断 |
| `[Tag]` 之间换行 | 每段独立换行 | 让 LLM 把 `[Section]` 当独立 chunk 处理；同时 `Own Forces & Infrastructure` 内部用两空格缩进做子项 |

---

## 七、验证

- `ast.parse` 全文件解析通过。
- 隔离 stub 测试 `_generate_english_text_obs` 输出与 user spec 几乎一字不差：
  ```
  [Own Forces & Infrastructure]
    Completed: 16 SCV, 2 MARINE, 1 BARRACKS, 1 COMMANDCENTER, 1 SUPPLYDEPOT.
    Under Construction: 1 FACTORY, 1 REFINERY.
    Workers En Route: 1 STARPORT.
    Active Queues: Training 2 MARINE, Researching STIMPACK.
  ```
- 空状态走另一条分支验证文本不会塌掉。
- linter 仅剩 6 条 `sc2.*` 路径解析的 warning，均为 sharpy 全仓既存现象（运行时通过 `sys.path.insert` 加载 python-sc2 引发），与本次改动无关。

---

## 八、消费侧影响

- LLM Prompt：可以直接整段塞入对话上下文。`[Tag]` 标记让 system prompt 里写「请只关注 `[Combat Analysis]`」之类的指令时模型注意力更准。
- 强化学习状态向量：`structured_state.own_forces.{completed, under_construction, workers_en_route, active_queues}` 各自是稀疏 dict，训练前可拍成 4 段 one-hot，分别对应「已有库存」「短期管线」「工人调度延迟」「生产队列」。
- 单元测试：`test_format_active_queues / test_classify_own_state` 都建议在后续补一遍真实 SC2 单位的样例，本次先用 stub 验证字符串渲染。

---

## 九、补丁：`Workers En Route` 与 `Under Construction` 去重修复

### 9.1 现象

第一版上线后，从实跑 JSON（`games/ai.terran.hard.macro_KairosJunctionLE_2026-05-10 17_39_21_755697.json`）中观察到一类异常输出：

```json
"under_construction": { "SUPPLYDEPOT": 1, "BARRACKS": 1 },
"workers_en_route":   { "SUPPLYDEPOT": 1, "BARRACKS": 1 }
```

`workers_en_route` 总是把 `under_construction` 中已包含的部分**重复列出**一份。LLM 看到「正在建 1 BARRACKS + 路上还有 1 BARRACKS」会误判产能管线深度，进而出现「再加一栋」的多余指令。

### 9.2 根因

旧版用「数量减法」做去重：

```python
en_route = build_orders_count[type] - under_construction[type]
```

这条减法在三族行为差异面前并不充分：

| 场景 | 行为 | 减法结果 | 真实期望 |
| --- | --- | --- | --- |
| Terran SCV 抵达后**继续敲打** partial 建筑 | SCV 仍持有 `TERRANBUILD_X` 一阶指令，`order.target` 是 **Point2**（建筑位置），不是 tag | `2 workers - 1 partial = 1` | 应为 0：第二个 worker 实际就是那一栋的施工人 |
| sharpy `GridBuilding` 在矿不够时**重复派单**到同一坐标 | 多个 SCV 收到同一 `BUILD_X + Point2` 指令，但只有第一个真正铺地基 | `2 workers - 1 partial = 1` | 应为 0：其余 worker 是冗余命令，不会真的产生第二栋 |
| Refinery/Extractor/Assimilator 抵达气矿后施工 | worker 的 target 是 vespene geyser **tag**，但 geyser 的 `position` 与 partial 气矿建筑同位 | tag 既不命中「partial tag 黑名单」也不是 Point2，旧逻辑会把它算成 en route | 应为 0：worker 已经在敲气矿建筑 |

减法只关心「数量差」，没关心「这两类工人是不是都指向同一处地基」，所以前两类场景里减完仍然剩出虚假的 en route。

### 9.3 修复方案：位置匹配（position-based pairing）

把判定逻辑从**数量减法**改成**逐个工人按目标坐标匹配 partial 建筑位置**：

1. 第一遍遍历 `cache.own_unit_cache` 时，除了写入 `under_construction`，还顺便记录 `partial_positions[type_id] = [Point2, Point2, ...]`。
2. 遍历工人时，把 BUILD_X 指令的 target 一律解析成 Point2：
   - `target` 是 `Point2` → 直接用；
   - `target` 是单位 tag（典型为气矿） → 用该单位的 `position`；
   - `target` 未知 → 跳过该工人，避免误报。
3. 如果该 Point2 与 `partial_positions[struct_type]` 中任何一个点的距离 `< 1.0` 格，认为该工人是「在敲已有地基」或「框架重复派单到同一坐标」，**跳过不计**。
4. 否则才计入 `workers_en_route[struct_type.name]`。

这套规则同时正确处理三族 + 气矿 + sharpy 重复派单四种情形，且对**幽灵 partial 建筑**（地基存在但没有任何 worker 在敲）也安全：worker 的 target 不在 partial 集合里 → 仍按原意计为 en route。

### 9.4 代码改动

`sharpy/managers/extensions/llm_observation_recorder.py`：

- `_extract_own_forces_infrastructure()`：第一遍记录 `partial_positions`，第二遍逐工人用 `_positions_match` 跳过命中 partial 坐标的工人。
- 新增静态方法 `_positions_match(p1, p2, tol=1.0)`：手算 `dx² + dy² < tol²`，避开 `Point2` 在不同子类里 `distance_to` 行为差异。
- `from typing` 重新引入 `Set` —— 之前 v2 整理 import 时一并清掉了，导致 `_VESPENE_GEYSER_TYPES: Set[UnitTypeId]` 在加载阶段炸 `NameError`。

### 9.5 单元覆盖

为这条修复补了 6 条隔离 stub 单测，均通过：

| 用例 | partial | workers | 期望 en_route |
| --- | --- | --- | --- |
| 1 (用户报的 bug) | `1 SUPPLYDEPOT @ (5,5)` | `2 SCV → BUILD_SUPPLYDEPOT @ (5,5)` 都指向同位 | `{}` |
| 2 真实在路上 | `1 BARRACKS @ (5,5)` | `1 SCV → BUILD_BARRACKS @ (10,10)` | `{BARRACKS:1}` |
| 3 气矿正在敲 | `1 REFINERY @ (3,3)` | `1 SCV → BUILD_REFINERY` 目标=geyser tag (其 pos=(3,3)) | `{}` |
| 4 气矿在路上 | 无 | `1 SCV → BUILD_REFINERY` 目标=geyser tag (pos=(3,3)) | `{REFINERY:1}` |
| 5 多型在路上 | 无 | 各 1 SCV → 不同坐标的 BARRACKS / SUPPLYDEPOT | `{BARRACKS:1, SUPPLYDEPOT:1}` |
| 6 混合 | `1 BARRACKS @ (5,5)` | `1 SCV @ (5,5)` 在敲 + `1 SCV @ (20,20)` 在路上 | `{BARRACKS:1}` |

### 9.6 决策回顾追加

| 决策点 | 旧 v2 选择 | v2 修复后选择 | 理由 |
| --- | --- | --- | --- |
| Workers en route 去重算法 | `worker_count - partial_count`（数量减法） | 逐工人 target Point2 与 partial 坐标做距离匹配 | 减法在「重复派单」「Terran 继续敲打」两类常见场景下会留下虚假残值；位置匹配对四种情形（Terran 敲打/Protoss 走开/Zerg 变形/气矿）一视同仁 |
| 容差 | — | `tol = 1.0` 格（约半个 tile） | SC2 placement 通常在整数或 0.5 网格；1.0 容差既能吸收浮点误差也不会把不同 placement 误并 |
| target 未知（既非 Point2 也非已知单位 tag） | 计为 en route | 跳过 | 异常状态下宁可 underreport 也好过制造幻觉数字 |

### 9.7 未来若再出问题

如果以后实跑里仍发现极端异常（比如 partial 建筑被 SCV 抢救修复时 partial 坐标飘移），可以把 `_positions_match` 的容差适当放大，或在 `partial_positions` 里改用 `unit.tag` 而非 `position` 做配对（需要 sc2 的 worker.order_target 暴露 tag，目前 Terran 这条路径上拿不到 tag，所以保留 Point2 方案）。
