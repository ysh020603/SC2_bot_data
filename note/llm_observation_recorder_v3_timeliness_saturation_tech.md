# LLM Observation Recorder v3：时效、饱和度与科技

> 日期：2026-05-15（v3 升级）
> 关联文件：`sharpy/managers/extensions/llm_observation_recorder.py`
> 前置文档：`note/llm_observation_recorder.md`、`note/llm_observation_recorder_v2_four_tier.md`

本次只动了 `LLMObservationRecorder` 一个文件，新增了 **三类 LLM 一直拿不到的信号**：

1. **敌军情报时效性**：让 LLM 区分「30 秒前看到的大军」与「5 分钟前看到的残兵」。
2. **经济饱和度**：让 LLM 知道当前农民数相对「理想满载数」的位置，避免补农节奏失准。
3. **已完成科技**：让 LLM 看见本方部队的质变点（兴奋剂、折跃门、攻防等级…）。

文本与结构化输出**全部向后兼容地扩展**：`[Tag]` 分段维持不变，新增 `[Research & Technology]`；`enemy` 字段从扁平 dict 改为含 `composition / last_observation_time / seconds_since_last_seen` 的复合对象；`economy` 字段追加 `ideal_worker_count`；snapshot 顶层新增 `upgrades`。

---

## 一、问题背景

v2 后的实跑里仍可见三类失误：

| 现象 | LLM 看到的原文 | 真实情况 | 失误 |
| --- | --- | --- | --- |
| 「敌情过期 LLM 当作新情报」 | `[Enemy Intelligence] 12 PROBE, 1 GATEWAY.` | 这是 5 分钟前侦察出的，对手早出兵 | 提前回防或保守过度 |
| 「不知道该不该停产农民」 | `Supply: 40/50 (workers 24, army 16).` | 二矿才刚建好、ideal 是 32 | 停产农民 → 中期穷死 |
| 「质变科技完成 LLM 不感知」 | `Active Queues: Researching WARPGATERESEARCH.` 之后该字段就消失 | 折跃门已研发完，可以折跃进攻 | 仍按 Gateway 节奏想问题 |

v2 已经能很好地表达「正在做什么 / 队列里排什么」，但缺少 **「过去做完了什么」** 与 **「情报是不是已经过期」** 这两类时效/状态信号。v3 的目标就是补齐它们。

---

## 二、增强信号定义

### 2.1 敌军情报时效性 (`seconds_since_last_seen`)

**核心理念**：判断时效性的关键不是 `IEnemyUnitsManager` 的累积集合（它一旦记得就永远记得），而是「当前帧我们的视野里还能不能看见一个活的敌方机动单位」。

实现：

```python
def _refresh_enemy_seen_timestamp(self):
    visible_mobile = self.ai.enemy_units  # currently-visible non-structures
    if visible_mobile:
        self.last_enemy_seen_at = float(self.ai.time)
```

调用时机：写在 `update()` 中 **早于** 那行 `interval_seconds` 节流的 `return`。原因——若放在节流后，则两次 snapshot 之间的中间帧观察会被丢掉，最后落在 JSON 里的「上一次看到敌人」时间戳会被 `interval_seconds` 量化到 12 / 20 秒的倍数，时效语义被严重稀释。

为什么用 `self.ai.enemy_units` 而非 `IEnemyUnitsManager.unit_types`：

- `IEnemyUnitsManager` 维护「曾经见过」的集合，**永远非空**（首次接敌之后），用它做新鲜度判断结果会一直 ≈0s；
- `BotAI.enemy_units` 是 python-sc2 在 `bot_ai_internal.py` 每帧根据可见性重新组装的 `Units`，包含**仅当前帧能看见**的非建筑敌方单位，正好是「LLM 眼里的活敌情」。

为什么排除建筑（`enemy_structures`）：

- 已侦察的 Nexus / Hatchery / Command Center 在迷雾里仍以 snapshot 形态「永远知道在哪」，把它们计入新鲜度只会让时间戳一直翻新，无法反映「敌军主力是不是还在视线内」这条 LLM 最想要的信号。
- 用户 spec 原话「30秒前看到的**大军** vs 5分钟前看到的**残兵**」——大军/残兵都是机动单位语义。

### 2.2 经济饱和度 (`ideal_worker_count`)

```python
def _calculate_ideal_worker_count(self) -> int:
    total = 0
    for th in self.ai.townhalls.ready:
        total += th.ideal_harvesters
    for gas in self.ai.gas_buildings.ready:
        total += gas.ideal_harvesters
    return total
```

`Unit.ideal_harvesters` 由 SC2 引擎直接给出：

| 建筑 | 返回值 |
| --- | --- |
| Command Center / Nexus / Hatchery（满矿区） | `2 * <矿块数>`（通常 16） |
| 矿块部分耗尽的基地 | 自动随剩余矿块下调 |
| Refinery / Assimilator / Extractor | `3` |
| **未建好的建筑** | `0`（所以我们只统计 `.ready`） |

只统计 `.ready` 的原因：施工中的扩张如果按「计划满载」算进去，LLM 会以为已经有 32 ideal，于是过早扩招农民；按 SC2 的真实可挖坑位（finished only）算更稳。

文本格式从

```
Supply: 26/31 (workers 16, army 10).
```

变为

```
Supply: 32/46 (workers 22/30 current/ideal, army 10).
```

只动 `workers` 字段，其它保持原样。LLM 看到 `22/30` 就立刻知道「分矿还没饱，先补 8 个农民再开始拉兵线」；看到 `30/30` 就知道「该开新矿或者把多余农民拉去其它工作了」。

> **2026-05-15 微调**：原本只输出 `workers 22/30`，与 SC2 默认的 `Supply: X/Y` 中 `Y=cap` 的约定不同，LLM 在没有上下文时容易把 `30` 误读成「农民上限」之类的概念。补一句 `current/ideal` 的内联说明后，**`48/76 current/ideal` 与 `current=48, ideal=76` 一一对位**，LLM 即使没有外部文档也能读懂。代价是每条 snapshot 多 ~14 字符 / ~4 tokens。如果未来要进一步压缩 prompt，可以把这段注解抽到 system prompt 的「术语表」里只说一次。

### 2.3 已完成科技 (`upgrades`)

数据源：`self.ai.state.upgrades` 是 python-sc2 在 `game_state.py` 每帧从 raw observation 还原的 `set[UpgradeId]`，含义就是「**这个游戏里，本方已经研究完成的所有升级**」。

实现：

```python
def _extract_upgrades(self) -> List[str]:
    names = [u.name for u in self.ai.state.upgrades if u not in _UPGRADE_BLOCKLIST]
    names.sort()
    return names
```

- **不需要懒加载映射表**：`UpgradeId` 本身就是一个 `IntEnum`，`.name` 即可拿到大写字符串（`WARPGATERESEARCH` / `STIMPACK` / `PROTOSSGROUNDWEAPONSLEVEL1` …），不需要像 v2 处理 `AbilityId -> UnitTypeId` 那样再额外造表。
- **过滤策略**：默认 `_UPGRADE_BLOCKLIST = set()`，等同「全收」。`state.upgrades` 在实战中几乎只会出现兵种 / 攻防 / 折跃 / 埋地这类与战斗或宏观直接相关的项，所以默认行为已经吻合 spec 里「忽略基础采集类（若有）」的口径。后续如发现某个比赛模式或地图脚本会塞进诡异升级，**只改这一个常量即可**精准过滤，不动 `_extract_upgrades` 本体。
- **排序**：字母升序。LLM 读 prompt 时不在乎顺序，但稳定排序保证两次 snapshot diff 起来干净（便于追溯「在哪一段时间窗里点出了 STIMPACK」）。

---

## 三、文本输出对照

完整段落（饱和、有时效、有科技）：

```
[Time] 03:15 (195.0s).
[Economy] 400 minerals, 100 vespene; income 850 mins/min, 210 gas/min. Supply: 32/46 (workers 22/30 current/ideal, army 10).
[Own Forces & Infrastructure]
  Completed: 22 SCV, 10 MARINE, 1 BARRACKS, 1 COMMANDCENTER.
  Under Construction: 1 FACTORY.
  Workers En Route: none.
  Active Queues: Training 2 MARINE, Researching STIMPACK.
[Enemy Intelligence] 12 PROBE, 1 NEXUS. (Last seen: 45s ago).
[Map Control] 1 own bases, 1 known enemy bases, 14 neutral expansions remaining.
[Combat Analysis] army advantage = Even, income advantage = SlightAdvantage, predicted = Even. Power: 15 vs 12. Losses: own 50 minerals/0 gas, enemy 0 minerals/0 gas.
[Research & Technology] PROTOSSGROUNDWEAPONSLEVEL1, SHIELD1, WARPGATERESEARCH.
[Threat Flags] none.
```

空状态优雅降级：

| 状态 | 输出 |
| --- | --- |
| 从未侦察到任何敌人 | `[Enemy Intelligence] nothing scouted yet.`（**省略**「Last seen」后缀，避免输出 `... yet. (Last seen: ...)` 这种自相矛盾的句子） |
| 一度看到、后失去视野 | `[Enemy Intelligence] 12 PROBE, 4 STALKER.` |
| 没有任何已完成升级 | `[Research & Technology] none.`（与 `[Threat Flags] none.` 保持同一空状态语法） |
| 农民完全饱和 | `Supply: 32/46 (workers 30/30 current/ideal, army 10).` |

> **2026-05-15 微调**：实跑里 `(Last seen: Ns ago)` 在 12s/20s snapshot 节流下几乎只输出 12 / 24 / 36 的倍数，对 LLM 的决策贡献微弱却占据 prompt 视线，因此在 `_format_enemy_section` 里把这一段后缀**注释下线**——结构化字段 `last_observation_time` / `seconds_since_last_seen` 仍写进 JSON，方便回放与训练特征；想恢复文本展示只需放开 `_format_enemy_section` 末尾的几行注释即可，无须改动任何其它代码。

### 3.1 段落顺序

新增的 `[Research & Technology]` 放在 `[Combat Analysis]` 与 `[Threat Flags]` 之间。语义上「我已掌握的质变科技」与「战力评估」是同一个抽象层（自己一方的战斗能力盘点），放在一起便于 LLM 用同一注意力窗口处理。

---

## 四、Master Snapshot 数据结构变化

```diff
 {
   "time": 195.0,
   "time_formatted": "03:15",
   "economy": {
     "minerals": 400,
     "vespene": 100,
     "supply_used": 32,
     "supply_cap": 46,
     "supply_left": 14,
     "supply_workers": 22,
     "supply_army": 10,
     "minerals_per_min": 850.0,
-    "vespene_per_min": 210.0
+    "vespene_per_min": 210.0,
+    "ideal_worker_count": 30
   },
   "own_forces": { ... unchanged ... },
-  "enemy": { "PROBE": 12, "NEXUS": 1 },
+  "enemy": {
+    "composition":             { "PROBE": 12, "NEXUS": 1 },
+    "last_observation_time":   150.0,
+    "seconds_since_last_seen": 45.0
+  },
   "map_control":  { ... unchanged ... },
   "combat":       { ... unchanged ... },
   "memory_flags": { ... unchanged ... },
+  "upgrades":     [ "PROTOSSGROUNDWEAPONSLEVEL1", "SHIELD1", "WARPGATERESEARCH" ]
 }
```

向后兼容性提示：

- **`enemy` 不再是扁平 dict**——下游已经直接读 `enemy.<UNIT_NAME>` 的代码必须改成 `enemy["composition"].<UNIT_NAME>`。当前仓内未发现这类外部消费者，仅文本格式器内部使用，因此一次性切换是安全的。
- `last_observation_time / seconds_since_last_seen` 可能是 `None`（从未侦察）——下游若做时间计算需要先判空。
- `economy.ideal_worker_count` 永远是非负整数，最差为 0（开局极限或全基地被消灭）。
- `upgrades` 永远是 list（可能为空），元素是大写字符串。

---

## 五、关键决策回顾

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 「Last seen」的源数据 | `BotAI.enemy_units`（仅本帧可见的机动敌方单位） | `IEnemyUnitsManager` 记忆累积，永远非空，做不出时效；建筑因 snapshot/迷雾常驻已知不能反映「敌主力位置新鲜度」 |
| 时间戳刷新位置 | `update()` 中**早于** interval 节流的 return | snapshot 间隔可能是 12s 或 20s，若把刷新放在节流后，时间戳就会被量化为间隔倍数，失真严重 |
| 「时效」字段命名 | `seconds_since_last_seen`（相对秒数）+ `last_observation_time`（绝对秒数） | 相对秒便于 LLM 直接读，绝对秒便于回放 / 训练时做相关性分析 |
| 时效从未发生时 | 文本段省略后缀；JSON 用 `None` | 句子完整、JSON 清晰；避免 `nothing scouted yet. (Last seen: 0s ago).` 的自相矛盾 |
| 时效秒数取整 | 文本格式器里取 `int(round())`；JSON 保留 1 位小数 | LLM 读 prompt 不需要小数；下游分析需要精度 |
| `ideal_worker_count` 只统计 `.ready` | 否则正在建的扩张会用 0 占位，结果偏低或被误读 | 用 `.ready` 既符合 SC2 的「真实可挖坑位」语义，也避免误差 |
| 升级翻译方式 | 直接 `UpgradeId.name`，无需懒加载表 | python-sc2 的 `UpgradeId` 本就是 `IntEnum`，`.name` 即可；无需 v2 那样的 ability→type 反查 |
| 升级过滤 | 默认空 blocklist，作为「将来按需扩展」的单一调谐点 | `state.upgrades` 实战中基本只含战斗/宏观项，过早过滤反而可能漏掉用户的关键诉求；保留 hook 即可 |
| 输出排序 | 字母升序 | 两次 snapshot 之间 diff 干净；LLM 不在意顺序 |
| `[Research & Technology]` 段位 | 排在 `[Combat Analysis]` 与 `[Threat Flags]` 之间 | 与本方战力盘点同抽象层；保持「己方—地图—战斗—威胁」的视线流 |

---

## 六、防御性细节

- `_refresh_enemy_seen_timestamp` 整个 try/except `self.ai.enemy_units`：在某些极端 stub / 测试场景下 `ai` 可能未挂上 `enemy_units` 属性，此时直接返回，不更新时间戳，**绝不让监测崩 snapshot 主流程**。
- `_calculate_ideal_worker_count` 同样 try/except 包裹整个累加循环；任何一个 `ideal_harvesters` 抛错都会让函数返回截至错误前累加值（部分结果好过全 0）。
- `_extract_upgrades` 在拿不到 `state.upgrades` 时直接返回空 list；某个 upgrade 没有 `.name`（极端版本错位）则退化为 `str(upgrade)`，**至少有可读字符串**。
- `_extract_enemy_intelligence` 在 `last_enemy_seen_at is None` 时显式返回两个 `None`，让下游一眼分辨「从没见过」与「刚刚见过」。

---

## 七、验证

- `ast.parse` 全文件解析通过。
- ReadLints 对修改后的文件返回零告警。
- 隔离 stub 单测（`_install_manager_base_stub` + `importlib.util.spec_from_file_location` 直接装载该单文件）覆盖 8 个用例，全部通过：

  | # | 用例 | 校验点 |
  | --- | --- | --- |
  | 1 | 全场景 snapshot 完整文本 | 三块 v3 文本同时出现且字符串子串精确匹配 |
  | 2 | 从未侦察到敌人 | 输出 `nothing scouted yet.`，**绝不**出现 `Last seen` |
  | 3 | 失视野的旧情报 | `Last seen: 270s ago.`（小数 270.5 取整为 270） |
  | 4 | 无任何已完成科技 | 输出 `[Research & Technology] none.` |
  | 5 | 农民完全饱和 | 输出 `(workers 30/30, army 10)` |
  | 6 | `_extract_upgrades` | 排序、`.name` 翻译都正确 |
  | 7 | `_extract_enemy_intelligence` 首次接敌后/再无视野 | 计算 `seconds_since_last_seen = 145.5 - 100.0 = 45.5`；`None` 路径返回两 `None` |
  | 8 | `_calculate_ideal_worker_count` | 累加两个基地（16+14）+ 两个气矿（3+3）= 36 |

- 实跑层面：与现有的 `interactions[*].observation_at_this_moment` / `observation_structured` JSON 字段平滑融合；下游 LLM prompt 拼装代码无需调整（直接整段塞入即可），仅当下游单独读 `observation_structured.enemy.<NAME>` 的旧代码需要改为 `observation_structured.enemy.composition.<NAME>`。

---

## 八、消费侧影响速记

| 消费场景 | v2 行为 | v3 行为 |
| --- | --- | --- |
| LLM prompt 整段注入 | OK | OK，多出一行 `[Research & Technology]` 与括号内的 `(Last seen: ...)` |
| 训练向量化 | 直接读 `enemy` 扁平 dict | 改读 `enemy.composition`；可额外吸收 `seconds_since_last_seen` 作为一维 scalar 特征 |
| 训练向量化 | `economy.supply_workers` 单值 | 可同时使用 `(supply_workers, ideal_worker_count)`，或派生 `saturation_ratio = supply_workers / max(1, ideal_worker_count)` |
| 训练向量化 | 升级状态由「正在研究的科技」近似 | 直接拿 `upgrades` 当多热编码输入，比「正在研究」更可靠 |

---

## 九、未来 / 已知限制

- `last_enemy_seen_at` 当前**仅依据机动单位**判断。若用户场景里希望「看见敌方分矿」也计入新鲜度，可以在 `_refresh_enemy_seen_timestamp` 里加一行 `or self.ai.enemy_structures`，但需要重新论证「分矿在迷雾中的 snapshot 是否会污染时间戳」。
- 多 LLM agent 共享同一 recorder 时，三族通用，无需特化。
- `state.upgrades` 不能区分「自己点的」与「盟军点的（如果有联盟模式）」，单挑 1v1 不受影响。
- `ideal_harvesters` 在某些极端 mod / 自定义图里可能给出非常规数字；当前 `max(0, int(...))` 已能容错负值/字符串/None。
