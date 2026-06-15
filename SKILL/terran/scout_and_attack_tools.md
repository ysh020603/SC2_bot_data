# Tactics 并行工具参考

本文档汇总 Sharpy 框架中 **`base_tactics.py` / `dummies/terran` tactics 列表**里所有**后台并行运行**的工具组件，并按 **矿 / 气 / Supply 消耗**分类。涵盖：

- **运营宏**（经济、建造、修理）
- **侦察**
- **防守 / 集结 / 进攻**
- **条件门控**（`Step`）

配置来源：`SKILL/terran/**/base_tactics.py`、`dummies/terran/*.py` 的 tactics 并行列表。

---

## 1. 整体架构

战术列表通常作为 `BuildOrder` 的**并行（tactics）部分**运行：列表中每个 `ActBase` 子类在每帧都会被调用，互不阻塞（少数进攻组件会 `return False` 以阻塞后续 macro）。

```python
from sharpy.plans import BuildOrder
from sharpy.plans.build_step import Step
from sharpy.plans.tactics import *
from sharpy.plans.tactics.terran import *

tactics = BuildOrder([
    PlanZoneDefense(),
    Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1)),
    PlanZoneGatherTerran(),
    PlanZoneAttack(60),
    PlanFinishEnemy(),
])
```

典型执行顺序（逻辑上）：

```
运营宏（经济/建造/修理） ─┬─ 侦察获取信息
                          ├─ 区域防守
                          ├─ 部队集结
                          └─ 条件满足后进攻 → 收尾全压
```

以上各项在 tactics 列表中**同时并行**，每帧各自 `execute()`。

---

## 2. 资源消耗分类

以下按工具**执行动作时**是否直接扣除 **矿 / 气 / Supply** 分类。  
「不消耗」指该工具本身不会下建造/训练指令；前置单位或建筑需在 build order 中另行安排。

### 2.1 执行时会消耗矿 / 气 / Supply

| 工具 | 矿 | 气 | Supply | 说明 |
|------|:--:|:--:|:------:|------|
| **`DefensiveBuilding`** | ✅ | 视建筑 | ❌ | 主动发起防御建筑建造；执行时调用 `can_afford` 并派农民建造 |
| **`AutoDepot`** | ✅ | ❌ | ❌ | 预测 Supply 缺口后自动建造补给站（100 矿/座，+8 Supply 上限） |
| **`MorphOrbitals`** | ✅ | ❌ | ❌ | 将指挥中心升级为轨道基地（150 矿/次） |
| **`Repair`** | ✅ | 视单位 | ❌ | 派 SCV 修理；修理过程中**持续消耗矿**（机械单位另耗气） |

**`DefensiveBuilding` 常见建筑造价（SC2 标准）：**

| 建筑 | 矿 | 气 | Supply | dummies 中的用法 |
|------|----|----|--------|------------------|
| `BUNKER` | 100 | 0 | 0（不占人口） | `banshees.py`、`battle_cruisers.py` |
| `MISSILETURRET` | 100 | 25 | 0 | `bio.py`、`rusty.py`、`terran_silver_bio.py` |

> 注：防御建筑不占 Supply，但会消耗矿/气；农民建造期间也会短暂占用 1 个 SCV（已有单位，非新训练）。

---

### 2.2 执行时不消耗矿 / 气 / Supply

| 工具 | 实际消耗 | 占用已有资源 | 说明 |
|------|----------|--------------|------|
| **`WorkerScout`** | 无 | 1 SCV（已有） | 从空闲农民中选 1 个探路；**不训练新单位**，但 SCV 离开采矿线（机会成本） |
| **`ScanEnemy`** | **能量 50**（非矿/气/supply） | 1 轨道基地（已有） | 轨道能量 > 50 时释放扫描；**不扣矿/气/supply** |
| **`Scout`** | 无 | 指定侦察单位（已有） | 复用已训练好的单位执行探路动作；单位本身由 build order 生产 |
| **`PlanZoneDefense`** | 无 | 军队 / 农民（已有） | 调度已有单位防守各分矿；不训练、不建造 |
| **`PlanWorkerOnlyDefense`** | 无 | 农民（已有） | `supply_army ≤ 3` 时拉农民防守；不训练新单位 |
| **`ManTheBunkers`** | 无 | 地堡 + 机枪（已有） | 命令已有机枪进入已有地堡；**地堡/机枪的矿气在 build order 中已付** |
| **`WeakDefense`** | 无 | 同 `PlanZoneDefense` | 弱化版区域防守，同样只调度已有单位 |
| **`WorkerCounterAttack`** | 无 | 农民（已有） | 检测到农民 Rush 时拉农民反打；本仓库未使用 |
| **`PlanZoneGatherTerran`** | 无 | 军队（已有） | 移动已有部队到集结点 |
| **`PlanZoneGather`** | 无 | 军队（已有） | 通用版集结，同上 |
| **`PlanZoneAttack`** | 无 | 军队（已有） | 达阈值后调度已有部队进攻；不现场造兵 |
| **`PlanZoneAttackAllIn`** | 无 | 军队（已有） | 继承 `PlanZoneAttack`，不退却 |
| **`PlanFinishEnemy`** | 无 | 军队（已有） | 让所有 idle 战斗单位全压 |
| **`DodgeRampAttack`** | 无 | 军队（已有） | `PlanZoneAttack` 子类，遇力场小退 |
| **`JumpIn`** | 无 | 大和（已有） | 使用折跃技能（冷却制）；**技能本身不扣矿/气/supply** |
| **`WeakAttack`** | 无 | 军队（已有） | 弱化版 `PlanZoneAttack` |
| **`Step`** | 无 | — | 条件门控包装器，本身不产生任何资源消耗 |
| **`CallMule`** | **能量**（≥参数值） | 轨道基地（已有） | 能量 > `on_energy`（默认 100）时叫矿骡；**不扣矿/气/Supply** |
| **`LowerDepots`** | 无 | 已有补给站 | 无敌方地面单位靠近时降下补给站，有敌人时升起 |
| **`MineOpenBlockedBase`** | 无 | 已有 SCV | 分矿被矿块挡住时，派 SCV 去采开放矿 |
| **`SpeedMining`** | 无 | 采矿 SCV | 优化农民采矿路径（shift+click 式移动） |
| **`ContinueBuilding`** | 无 | 已有 SCV + 未完工建筑 | 为无人建造的半成品建筑补派 SCV |
| **`DistributeWorkers`** | 无 | 已有 SCV | 分配 idle 农民到矿/气，撤离受攻击分矿 |
| **`PlanCancelBuilding`** | 退还部分 | 建造中建筑 | 建筑快被毁时取消建造（返还部分资源，非新消耗） |

---

### 2.3 汇总对照

```
消耗矿/气（执行时主动建造 / 修理）
├── DefensiveBuilding
├── AutoDepot（100 矿/补给站）
├── MorphOrbitals（150 矿/轨道）
└── Repair（修理过程持续扣矿/气）

不消耗矿/气/Supply（仅调度 / 使用技能 / 消耗其它资源）
├── 侦察：WorkerScout, ScanEnemy(能量), Scout
├── 防守：PlanZoneDefense, PlanWorkerOnlyDefense, ManTheBunkers, WeakDefense, WorkerCounterAttack
├── 集结：PlanZoneGatherTerran, PlanZoneGather
├── 进攻：PlanZoneAttack, PlanZoneAttackAllIn, PlanFinishEnemy, DodgeRampAttack, JumpIn, WeakAttack
├── 运营：CallMule(能量), LowerDepots, MineOpenBlockedBase, SpeedMining, ContinueBuilding, DistributeWorkers, PlanCancelBuilding
└── 门控：Step
```

### 2.4 前置依赖（不在工具内扣费，但需提前准备）

部分「不消耗」工具依赖 build order 中已造好的单位/建筑，配置 tactics 时需一并考虑：

| 工具 | 前置依赖 | 典型造价（供 build order 参考） |
|------|----------|--------------------------------|
| `WorkerScout` | ≥1 空闲 SCV | SCV：50 矿，占 1 Supply |
| `ScanEnemy` | 轨道基地 `ORBITALCOMMAND` | CC 升级轨道：150 矿；扫描另耗 50 能量/次 |
| `ManTheBunkers` | 地堡 + 机枪 | Bunker 100 矿；Marine 50 矿 1 Supply |
| `PlanZoneAttack` 等进攻工具 | 战斗单位 | 由 build order 中的 `ActUnit` 等步骤生产 |
| `JumpIn` | ≥2 大和 | Battlecruiser 400 矿 300 气，3 Supply |
| `DefensiveBuilding` | 农民 | 建筑造价见 §2.1 |
| `AutoDepot` | 农民 | 补给站 100 矿 |
| `MorphOrbitals` | 指挥中心 | CC 升级轨道 150 矿 |
| `CallMule(50)` | 轨道基地 | 叫矿骡耗 50 能量；矿骡不占 Supply |

---

## 3. 侦察工具

### 3.1 `WorkerScout` — 农民探路

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/worker_scout.py` |
| **作用** | 从空闲农民中选一个，依次探查敌方出生点、斜坡、主矿、分矿 |
| **探路逻辑** | 未找到敌方 → 探出生点；找到主矿 → 绕圈确认；主矿探完 → 探敌方前 5 个分矿 |
| **是否阻塞** | 否（`return True`） |
| **资源消耗** | ❌ 不消耗矿/气/Supply；占用 1 已有 SCV（见 [§2.2](#22-执行时不消耗矿--气--supply)） |

```python
# 补给站/兵营就绪后再派农民（避免影响早期建造）
Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.SUPPLYDEPOT, 1))
Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.BARRACKS, 1))
```

**使用此工具的 Bot：**

| 来源 | 策略 |
|------|------|
| SKILL | `bio`, `banshees`, `rusty`, `cyclones`, `generic`, `marine_rush`, `battle_cruisers`, `two_base_tanks*` |
| dummies | `bio.py`, `banshees.py`, `rusty.py`, `cyclones.py`, `marine_rush.py`, `battle_cruisers.py`, `two_base_tanks.py` |
| **未使用** | `safe_tvt_raven`（依赖铁鸦等其它侦察手段） |

---

### 3.2 `ScanEnemy` — 轨道基地扫描

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/terran/scan_enemy.py` |
| **作用** | 用轨道基地能量释放 Scanner Sweep，获取敌方视野 |
| **参数** | `interval_seconds`：常规扫描间隔，默认 60 秒 |
| **额外逻辑** | 每 15 秒检测隐形单位热点（`HeatMapManager`），优先扫隐形 |
| **扫哪里** | 优先扫最久未侦察的敌方分矿区域 |
| **是否阻塞** | 否 |
| **资源消耗** | ❌ 不消耗矿/气/Supply；每次扫描消耗轨道 **50 能量**（见 [§2.2](#22-执行时不消耗矿--气--supply)） |

**常见写法：**

```python
ScanEnemy()           # 默认 60 秒间隔
ScanEnemy(120)        # two_base_tanks 系列：2 分钟扫一次，省能量

# 5 分钟后再启用扫描（早期省能量、专注运营）
Step(None, ScanEnemy(), skip_until=Time(5 * 60))
```

**使用此工具的 Bot：**

| 来源 | 策略 | 备注 |
|------|------|------|
| SKILL / dummies | 大多数标准 TvX 策略 | 5 分钟后启用或立即启用 |
| dummies | `rusty.py` | 立即 `ScanEnemy()`，无时间门控 |
| dummies | `two_base_tanks.py` | `ScanEnemy(120)` |
| **未使用** | `safe_tvt_raven`, `one_base_turtle`, `terran_silver_bio` | |

---

### 3.3 `Scout` — 通用单位侦察框架（本仓库人族 Bot 未直接使用）

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/scouting/scout.py` |
| **作用** | 指定单位类型与数量，循环执行 `ScoutLocation` 等子动作 |
| **适用** | 神族 Adept/幻象、虫族 Overlord/Ling 等；人族 dummies/SKILL 中未使用 |

---

## 4. 防守工具

### 4.1 `PlanZoneDefense` — 区域防守

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/zone_defense.py` |
| **作用** | 遍历己方各分矿区域，发现敌人时分派防守单位（含必要时拉农民） |
| **逻辑** | 敌人消失后延迟 3 秒才撤防，防止视野丢失误判 |
| **是否阻塞** | 否 |

**几乎所有 terran 策略均使用。**

---

### 4.2 `PlanWorkerOnlyDefense` — 前期农民应急防守

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/worker_only_defense.py` |
| **作用** | 当 `supply_army ≤ 3` 时，用农民防守己方矿区入侵 |
| **退出条件** | 军队补给 > 3 后自动释放农民 |
| **是否阻塞** | 否 |

**使用此工具的 Bot：** `safe_tvt_raven`（SKILL + dummies）

---

### 4.3 `ManTheBunkers` — 地堡进驻

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/terran/man_the_bunkers.py` |
| **作用** | 将最近机枪兵送入未满（<4 人）的 Bunker |
| **是否阻塞** | 否 |

**使用此工具的 Bot：** `bio`, `banshees`, `rusty`, `cyclones`, `marine_rush`, `battle_cruisers`, `one_base_turtle` 等；`safe_tvt_raven` 和 `two_base_tanks*` 未使用。

---

### 4.4 `DefensiveBuilding` — 静态防御建筑（建造阶段，非 tactics 并行项）

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/acts/defensive_building.py` |
| **作用** | 在指定位置建造地堡、导弹塔等防御建筑 |
| **位置枚举** | `DefensePosition.Entrance`（入口）、`CenterMineralLine`（矿线中央）等 |
| **资源消耗** | ✅ **消耗矿/气**（Bunker 100 矿；Missile Turret 100 矿 25 气；不占 Supply），见 [§2.1](#21-执行时会消耗矿--气--supply) |

**dummies 中的用法（build order 部分，非 tactics）：**

```python
# bio.py / rusty.py / terran_silver_bio.py
Step(None, DefensiveBuilding(UnitTypeId.MISSILETURRET, DefensePosition.Entrance, 2)),
Step(None, DefensiveBuilding(UnitTypeId.MISSILETURRET, DefensePosition.CenterMineralLine, None)),

# banshees.py / battle_cruisers.py
DefensiveBuilding(UnitTypeId.BUNKER, DefensePosition.Entrance, 1),
```

---

### 4.5 `WeakDefense` — 弱化版区域防守

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/weak/weak_defense.py` |
| **作用** | 与 `PlanZoneDefense` 类似，但 AI 强度更低（用于低段位 Bot） |
| **使用** | `dummies/terran/terran_silver_bio.py` |

---

### 4.6 `WorkerCounterAttack` — 农民反打（本仓库未使用）

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/worker_counterattack.py` |
| **作用** | 检测到敌方农民 Rush 时，拉农民反打 |

---

## 5. 集结工具

进攻前需先把部队拉到集结点，避免散兵游勇。

### 5.1 `PlanZoneGatherTerran` — 人族集结

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/terran/zone_gather_terran.py` |
| **作用** | 将可攻击单位拉向 `IGatherPointSolver` 计算的集结点；靠近主矿斜坡时会微调位置 |
| **是否阻塞** | 否 |

**大多数 terran 策略使用。**

---

### 5.2 `PlanZoneGather` — 通用集结

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/zone_gather.py` |
| **作用** | 种族无关的集结逻辑，支持 Protoss 关门等 |
| **使用** | `safe_tvt_raven`（SKILL + dummies） |

---

## 6. 进攻工具

### 6.1 `PlanZoneAttack` — 区域进攻（核心）

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/zone_attack.py` |
| **参数** | `start_attack_power`：触发进攻的**己方军队价值阈值**（默认 20） |
| **核心逻辑** | 统计空闲可攻击单位总价值 → 达到阈值且局势允许 → 向目标区域进攻 |
| **撤退** | 劣势或部队损失严重时撤退至集结点（`RETREAT_TIME = 20` 秒） |
| **优势判断** | 有 `IGameAnalyzer` 时，会根据局势优劣决定是否进攻 |
| **是否阻塞** | **是**（`return False`，阻塞 macro 直到进攻状态结束） |

**`attack_value` 在各策略中的典型取值：**

| 策略 | 阈值 | 说明 |
|------|------|------|
| `safe_tvt_raven` | 4 | 兴奋剂完成后即攻 |
| `one_base_turtle` | 4 | 需 18 机枪才启用 |
| `marine_rush` | 3 / 10 / 20 | 随对手不同动态调整 |
| `bio` | 26 | |
| `cyclones` | 40 | 需飓风锁定升级完成 |
| `two_base_tanks*` | 30 / 60 | rush 系列偏激进 |
| `rusty` / `battle_cruisers` | 50–80 随机 | |
| `terran_silver_bio` | 30（WeakAttack） | 弱化版 |

---

### 6.2 `PlanZoneAttackAllIn` — 永不撤退版

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/zone_attack_all_in.py` |
| **作用** | 继承 `PlanZoneAttack`，设 `retreat_multiplier = 0`，进攻后不退 |
| **使用** | 本仓库 terran dummies/SKILL 中未直接使用 |

---

### 6.3 `PlanFinishEnemy` — 收尾全压

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/attack_expansions.py` |
| **作用** | 让所有 idle 且可攻击的单位攻击最近敌方建筑或分矿点 |
| **是否阻塞** | 否 |

**几乎所有 terran 策略在 tactics 列表末尾均包含此项。**

---

### 6.4 自定义进攻子类

#### `DodgeRampAttack`（`marine_rush`）

| 属性 | 说明 |
|------|------|
| **源码** | `SKILL/terran/marine_rush/base_tactics.py`、`dummies/terran/marine_rush.py` |
| **作用** | 继承 `PlanZoneAttack`；检测到神族力场（Force Field）挡路时，部队小退至自然矿 |
| **参数** | 使用 `num_marines` 作为 `start_attack_power` |

#### `JumpIn`（`battle_cruisers`）

| 属性 | 说明 |
|------|------|
| **源码** | `SKILL/terran/battle_cruisers/base_tactics.py`、`dummies/terran/battle_cruisers.py` |
| **作用** | 2+ 大和时，折跃至敌方主矿矿后（`EFFECT_TACTICALJUMP`），属于骚扰/先手打击 |
| **门控** | `Step(None, JumpIn(), skip=RequireCustom(lambda k: jump_index == 0))` |

#### `WeakAttack`（`terran_silver_bio`）

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/weak/weak_attack.py` |
| **作用** | 弱化版 `PlanZoneAttack`，进攻决策更保守，用于低段位 Bot |

---

## 7. 条件包装：`Step`

`Step` 用于给任意战术动作加**触发条件**或**时间门控**，本身不是侦察/攻击工具，但在配置中广泛使用。

| 参数 | 含义 |
|------|------|
| `requirement` | 满足后才执行 `action` |
| `action` | 要执行的战术动作 |
| `skip_until` | 条件满足**之前**跳过（不执行） |
| `skip` | 条件满足**之后**跳过（不再执行） |

**源码：** `sharpy/plans/build_step.py`

### 7.1 常用触发条件（`sharpy/plans/require/`）

| 条件 | 示例 | 用途 |
|------|------|------|
| `UnitExists` | `UnitExists(MARINE, 18, include_killed=True)` | 机枪数量达标才进攻 |
| `TechReady` | `TechReady(UpgradeId.STIMPACK)` | 科技完成才进攻 |
| `TechReady`（进度） | `TechReady(CYCLONELOCKONDAMAGEUPGRADE, 0.95)` | 升级 95% 时触发 |
| `Time` | `Time(5 * 60)` | 5 分钟时间点 |
| `UnitReady` | `UnitReady(BARRACKS, 1)` | 建筑就绪 |
| `RequireCustom` | `RequireCustom(lambda k: jump_index == 0)` | 自定义逻辑 |
| `lambda ai: ...` | `lambda ai: ai.client.game_step > 5` | 直接访问 AI 状态 |

### 7.2 实战示例

```python
# 兴奋剂研发完毕后再启用区域进攻
Step(TechReady(UpgradeId.STIMPACK), PlanZoneAttack(4))

# 18 机枪（含已损失）到位后才进攻
Step(UnitExists(UnitTypeId.MARINE, 18, include_killed=True), PlanZoneAttack(4))

# 5 分钟前不扫描
Step(None, ScanEnemy(), skip_until=Time(5 * 60))

# 兵营就绪后才开始农民探路
Step(None, WorkerScout(), skip_until=UnitExists(UnitTypeId.BARRACKS, 1))
```

---

## 8. 各 Bot 战术配置对照表

| Bot | 农民探路 | 扫描 | 防守 | 集结 | 进攻 | 触发条件 |
|-----|---------|------|------|------|------|----------|
| **generic** | ✅ 补给站后 | ✅ 5min 后 | Zone + Bunker | GatherTerran | Attack(60) | 无 |
| **bio** | ✅ | ✅ 5min 后 | Zone + Bunker + 导弹塔* | GatherTerran | Attack(26) | 无 |
| **safe_tvt_raven** | ❌ | ❌ | WorkerOnly + Zone | Gather | Attack(4) | 兴奋剂 |
| **one_base_turtle** | ❌ | ❌ | Bunker + Zone | GatherTerran | Attack(4) | 18 机枪 |
| **marine_rush** | ✅ | ✅ 5min 后 | Zone + Bunker | GatherTerran | DodgeRampAttack | 阈值随对手 |
| **cyclones** | ✅ | ✅ 5min 后 | Zone + Bunker | GatherTerran | Attack(40) | 飓风锁定升级 |
| **two_base_tanks** | ✅ 兵营后 | ScanEnemy(120) | Zone | GatherTerran | Attack(60) | 无 |
| **rusty** | ✅ | ✅ 立即 | Zone + Bunker + 导弹塔* | GatherTerran | Attack(50–80) | 无 |
| **battle_cruisers** | ✅ | ✅ 5min 后 | Zone + Bunker* | GatherTerran | Attack + JumpIn | 可选折跃 |
| **banshees** | ✅ | ✅ 5min 后 | Zone + Bunker* | GatherTerran | Attack(动态) | 无 |
| **terran_silver_bio** | ❌ | ❌ | WeakDefense | GatherTerran | WeakAttack(30) | 无 |

\* 导弹塔/地堡在 **build order** 阶段通过 `DefensiveBuilding` 建造，不在 tactics 并行列表中。

---

## 9. 典型 tactics 模板

### 9.1 标准 TvX（以 `generic` / `bio` 为代表）

```python
BuildOrder([
    AutoDepot(),
    Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
    MineOpenBlockedBase(),
    PlanCancelBuilding(),
    LowerDepots(),
    PlanZoneDefense(),                                          # 区域防守
    Step(None, WorkerScout(), skip_until=UnitExists(...)),      # 农民探路
    Step(None, CallMule(50), skip=Time(5 * 60)),
    Step(None, CallMule(100), skip_until=Time(5 * 60)),
    Step(None, ScanEnemy(), skip_until=Time(5 * 60)),           # 轨道扫描
    DistributeWorkers(),
    Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
    ManTheBunkers(),                                            # 地堡进驻
    Repair(),
    ContinueBuilding(),
    PlanZoneGatherTerran(),                                     # 部队集结
    PlanZoneAttack(attack_value),                               # 阈值进攻
    PlanFinishEnemy(),                                          # 收尾全压
])
```

### 9.2 防守型 TvT（`safe_tvt_raven`）

```python
BuildOrder([
    AutoDepot(),
    Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
    CallMule(50),
    LowerDepots(),
    MineOpenBlockedBase(),
    Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
    Repair(),
    ContinueBuilding(),
    PlanZoneGather(),                                           # 通用集结
    PlanWorkerOnlyDefense(),                                    # 前期农民防守
    PlanZoneDefense(),
    Step(TechReady(UpgradeId.STIMPACK), PlanZoneAttack(4)),     # 科技门控进攻
    PlanFinishEnemy(),
])
```

### 9.3 Rush 型（`marine_rush`）

```python
# 继承 PlanZoneAttack，遇力场小退
class DodgeRampAttack(PlanZoneAttack):
    async def execute(self) -> bool:
        # 检测 Force Field → small_retreat()
        return await super().execute()

BuildOrder([
    # ... 标准防守 + 侦察 ...
    PlanZoneGatherTerran(),
    DodgeRampAttack(num_marines),   # 自定义进攻
    PlanFinishEnemy(),
])
```

---

## 10. 源码索引

| 工具 | 路径 |
|------|------|
| WorkerScout | `sharpy/plans/tactics/worker_scout.py` |
| ScanEnemy | `sharpy/plans/tactics/terran/scan_enemy.py` |
| Scout（通用） | `sharpy/plans/tactics/scouting/scout.py` |
| PlanZoneDefense | `sharpy/plans/tactics/zone_defense.py` |
| PlanWorkerOnlyDefense | `sharpy/plans/tactics/worker_only_defense.py` |
| ManTheBunkers | `sharpy/plans/tactics/terran/man_the_bunkers.py` |
| DefensiveBuilding | `sharpy/plans/acts/defensive_building.py` |
| PlanZoneGather | `sharpy/plans/tactics/zone_gather.py` |
| PlanZoneGatherTerran | `sharpy/plans/tactics/terran/zone_gather_terran.py` |
| PlanZoneAttack | `sharpy/plans/tactics/zone_attack.py` |
| PlanZoneAttackAllIn | `sharpy/plans/tactics/zone_attack_all_in.py` |
| PlanFinishEnemy | `sharpy/plans/tactics/attack_expansions.py` |
| WeakAttack / WeakDefense | `sharpy/plans/tactics/weak/` |
| CallMule | `sharpy/plans/tactics/terran/call_mule.py` |
| LowerDepots | `sharpy/plans/tactics/terran/lower_depots.py` |
| Repair | `sharpy/plans/tactics/terran/repair.py` |
| ContinueBuilding | `sharpy/plans/tactics/terran/continue_building.py` |
| AutoDepot | `sharpy/plans/acts/terran/auto_depot.py` |
| MorphOrbitals | `sharpy/plans/acts/terran/morph_orbitals.py` |
| MineOpenBlockedBase | `sharpy/plans/acts/mine_open_blocked_base.py` |
| SpeedMining | `sharpy/plans/tactics/speed_mining.py` |
| DistributeWorkers | `sharpy/plans/tactics/distribute_workers.py` |
| PlanCancelBuilding | `sharpy/plans/tactics/cancel_building.py` |
| Step | `sharpy/plans/build_step.py` |

**配置示例：**

| 目录 | 说明 |
|------|------|
| `SKILL/terran/*/base_tactics.py` | LLM 生成策略的战术列表（模块化） |
| `dummies/terran/*.py` | 完整 KnowledgeBot，含 build order + tactics |

---

## 11. 快速选型指南

| 需求 | 推荐组件 |
|------|----------|
| 早期探出生点 | `WorkerScout` + `skip_until=UnitExists(SUPPLYDEPOT)` |
| 中后期获取敌方动向 | `ScanEnemy()` 或 `ScanEnemy(120)` 省能量 |
| 常规分矿防守 | `PlanZoneDefense` |
| 无兵时的农民防守 | `PlanWorkerOnlyDefense` |
| 地堡防守 | build 阶段 `DefensiveBuilding` + tactics 阶段 `ManTheBunkers` |
| 进攻前集结 | `PlanZoneGatherTerran`（人族） |
| 按军队价值进攻 | `PlanZoneAttack(threshold)` |
| 按科技/单位数进攻 | `Step(TechReady/UnitExists, PlanZoneAttack(...))` |
| 游戏收尾 | `PlanFinishEnemy` |
| 低段位 Bot | `WeakAttack` + `WeakDefense` |
| 特殊骚扰 | 继承 `PlanZoneAttack` 或自定义 `ActBase`（如 `JumpIn`） |
| 需主动扣矿/气造防御 | `DefensiveBuilding` |
| 自动补 Supply / 升轨道 | `AutoDepot`、`MorphOrbitals` |
| 叫矿骡提经济 | `CallMule(50)` 激进 / `CallMule(100)` 保守 |
| 受攻时开补给站 | `LowerDepots` |
| 建筑/单位自动修理 | `Repair`（持续消耗矿/气） |
| 后台运营全套 | 见 [§12](#12-后台运营工具tactics-并行项) |
| 查全部并行工具 | 见 [§13 完整总表](#13-完整并行工具总表) |

---

## 12. 后台运营工具（tactics 并行项）

`base_tactics.py` 中与侦察/攻防并列、负责**经济 / 建造 / 基地维护**的工具。以下每个工具均按统一格式记录：**源码 · 作用 · 是否阻塞 · 资源消耗 · 常见写法 · 策略使用情况**。

### 12.1 `AutoDepot()` — 自动补补给站

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/acts/terran/auto_depot.py` |
| **作用** | 根据兵营/重工/星港/基地数量预测 Supply 增速，自动计算并建造补给站 |
| **是否阻塞** | 可能阻塞（无农民或资源不足时 `return False`） |
| **资源消耗** | ✅ **100 矿/座**（+8 Supply 上限，不占人口） |

```python
AutoDepot(),  # 无需参数，内部动态计算 to_count
```

| 策略 | 是否使用 |
|------|:--------:|
| 大多数标准策略 | ✅ |
| `two_base_tanks_marine_rush_*` | ❌（部分注释掉） |

---

### 12.2 `MorphOrbitals()` — 升级轨道基地

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/acts/terran/morph_orbitals.py` |
| **作用** | 将所有就绪 CC 升级为轨道基地（默认目标 99 座） |
| **是否阻塞** | 可能阻塞（缺矿时 `return False`） |
| **资源消耗** | ✅ **150 矿/CC** |

```python
# 兵营就绪后再升轨道，避免早期缺矿
Step(None, MorphOrbitals(), skip_until=UnitReady(UnitTypeId.BARRACKS, 1)),
```

| 策略 | 是否使用 |
|------|:--------:|
| 几乎所有 terran 策略 | ✅ |
| `two_base_tanks_marine_rush_*` | ❌（注释掉） |

---

### 12.3 `CallMule(on_energy)` — 叫矿骡

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/terran/call_mule.py` |
| **作用** | 轨道能量 > 阈值时，在己方安全、有矿的分矿叫矿骡 |
| **参数** | `on_energy`：能量阈值，默认 100 |
| **是否阻塞** | 否 |
| **资源消耗** | ❌ 矿/气/Supply；每次叫矿骡耗 **50 能量**；矿骡不占 Supply |

| 写法 | 含义 |
|------|------|
| `CallMule()` | 能量 > 100 才叫 |
| `CallMule(50)` | 能量 > 50 即叫（`safe_tvt_raven`，更激进） |
| `CallMule(100)` | 同默认（`rusty`） |
| `CallMule(0)` | 有能量就叫（`terran_silver_bio`） |

```python
CallMule(50),                                          # 立即启用，低阈值
Step(None, CallMule(50), skip=Time(5 * 60)),           # 前 5 分钟
Step(None, CallMule(100), skip_until=Time(5 * 60)),    # 5 分钟后
```

| 策略 | 配置 |
|------|------|
| `safe_tvt_raven` | `CallMule(50)` 立即 |
| `generic` / `bio` / `cyclones` | 5min 前 50 / 后 100 |
| `two_base_tanks*` | `CallMule()` 默认 |
| `rusty` | `CallMule(100)` 立即 |

---

### 12.4 `LowerDepots()` — 升降补给站

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/terran/lower_depots.py` |
| **作用** | 补给站周围 5 码内无敌方地面单位 → 降下；有敌人 → 升起 |
| **是否阻塞** | 否 |
| **资源消耗** | ❌ 无 |

```python
LowerDepots(),  # 几乎所有策略均包含
```

---

### 12.5 `MineOpenBlockedBase()` — 采开放分矿

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/acts/mine_open_blocked_base.py` |
| **作用** | 占领分矿后，若矿块挡住矿点，派 SCV 去采该处矿物（修复特定地图问题） |
| **参数** | `units_to_clear=1`：派几个 SCV |
| **是否阻塞** | 否 |
| **资源消耗** | ❌ 无（占用已有 SCV，完成后释放） |

```python
MineOpenBlockedBase(),  # 几乎所有策略均包含
```

---

### 12.6 `SpeedMining()` — 优化采矿

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/speed_mining.py` |
| **作用** | 对采矿中的 SCV 发送优化移动指令，缩短往返路径 |
| **是否阻塞** | 否 |
| **资源消耗** | ❌ 无 |

```python
# 开局前几个 game_step 不启用，避免干扰早期建造
Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
```

---

### 12.7 `Repair()` — 自动修理

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/terran/repair.py` |
| **作用** | 按区域敌军压力，派 SCV 修理受损建筑/机械单位 |
| **触发条件** | 地堡/CC/轨道 HP < 95%；任意建筑 HP < 30%；机械 HP < 75% |
| **是否阻塞** | 否 |
| **资源消耗** | ✅ **修理过程持续扣矿**（机械单位另耗气） |

```python
Repair(),  # 几乎所有策略均包含
```

---

### 12.8 `ContinueBuilding()` — 续建半成品

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/terran/continue_building.py` |
| **作用** | 检测未完工且附近无 SCV 的建筑，派空闲农民继续建造 |
| **是否阻塞** | 否 |
| **资源消耗** | ❌ 无（建筑造价已在 build order 中支付） |

```python
ContinueBuilding(),  # 几乎所有策略均包含
```

---

### 12.9 `DistributeWorkers()` — 农民分配

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/distribute_workers.py` |
| **作用** | 处理 idle SCV；平衡矿/气分配；受攻击分矿撤离农民 |
| **参数** | `min_gas` / `max_gas` / `DistributeWorkers(4)` 等 |
| **是否阻塞** | 否 |
| **资源消耗** | ❌ 无 |

```python
DistributeWorkers(),      # 默认
DistributeWorkers(4),     # battle_cruisers / banshees：限制每气矿人数
```

| 策略 | 是否使用 |
|------|:--------:|
| 标准策略 | ✅ |
| `safe_tvt_raven` | ❌ |
| `terran_silver_bio` | ✅ |

---

### 12.10 `PlanCancelBuilding()` — 取消濒毁建筑

| 属性 | 说明 |
|------|------|
| **源码** | `sharpy/plans/tactics/cancel_building.py` |
| **作用** | 建造中建筑 HP 骤降（快被毁）时取消，**返还部分资源** |
| **是否阻塞** | 否 |
| **资源消耗** | 取消返还，非新消耗 |

```python
PlanCancelBuilding(),  # 多数策略有；safe_tvt_raven 无
```

---

## 13. 完整并行工具总表

以下汇总 **17 个 SKILL terran `base_tactics.py`** 中出现的全部并行工具（含运营 / 侦察 / 防守 / 集结 / 进攻 / 门控）。

### 13.1 按功能分类

| 分类 | 工具 | 矿 | 气 | Supply | 其它消耗 | 阻塞 |
|------|------|:--:|:--:|:------:|----------|:----:|
| **运营** | `AutoDepot` | ✅ | ❌ | ❌ | — | 可能 |
| **运营** | `MorphOrbitals` | ✅ | ❌ | ❌ | — | 可能 |
| **运营** | `CallMule` | ❌ | ❌ | ❌ | 能量 50/次 | 否 |
| **运营** | `LowerDepots` | ❌ | ❌ | ❌ | — | 否 |
| **运营** | `MineOpenBlockedBase` | ❌ | ❌ | ❌ | — | 否 |
| **运营** | `SpeedMining` | ❌ | ❌ | ❌ | — | 否 |
| **运营** | `Repair` | ✅ | 视单位 | ❌ | 修理费 | 否 |
| **运营** | `ContinueBuilding` | ❌ | ❌ | ❌ | — | 否 |
| **运营** | `DistributeWorkers` | ❌ | ❌ | ❌ | — | 否 |
| **运营** | `PlanCancelBuilding` | 退还 | — | ❌ | — | 否 |
| **侦察** | `WorkerScout` | ❌ | ❌ | ❌ | 占用 1 SCV | 否 |
| **侦察** | `ScanEnemy` | ❌ | ❌ | ❌ | 能量 50/次 | 否 |
| **防守** | `PlanZoneDefense` | ❌ | ❌ | ❌ | — | 否 |
| **防守** | `PlanWorkerOnlyDefense` | ❌ | ❌ | ❌ | — | 否 |
| **防守** | `ManTheBunkers` | ❌ | ❌ | ❌ | — | 否 |
| **防守** | `WeakDefense` | ❌ | ❌ | ❌ | — | 否 |
| **集结** | `PlanZoneGatherTerran` | ❌ | ❌ | ❌ | — | 否 |
| **集结** | `PlanZoneGather` | ❌ | ❌ | ❌ | — | 否 |
| **进攻** | `PlanZoneAttack` | ❌ | ❌ | ❌ | — | **是** |
| **进攻** | `PlanFinishEnemy` | ❌ | ❌ | ❌ | — | 否 |
| **进攻** | `DodgeRampAttack` | ❌ | ❌ | ❌ | — | **是** |
| **进攻** | `JumpIn` | ❌ | ❌ | ❌ | 技能冷却 | 否 |
| **进攻** | `WeakAttack` | ❌ | ❌ | ❌ | — | **是** |
| **门控** | `Step` | ❌ | ❌ | ❌ | — | 视子动作 |

> **build order 阶段**（非 tactics 并行）另有 `DefensiveBuilding`（造地堡/导弹塔，消耗矿/气），见 [§4.4](#44-defensivebuilding--静态防御建筑建造阶段非-tactics-并行项)。

### 13.2 各策略并行工具清单

| 工具 | generic | bio | safe_tvt_raven | one_base_turtle | marine_rush | cyclones | two_base_tanks* | rusty | battle_cruisers | banshees |
|------|:-------:|:---:|:--------------:|:---------------:|:-----------:|:--------:|:---------------:|:-----:|:---------------:|:--------:|
| AutoDepot | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| MorphOrbitals | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| CallMule | 50/100 | 50/100 | 50 | () | 50/100 | 50/100 | () | 100 | 50/100 | 50/100 |
| LowerDepots | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| MineOpenBlockedBase | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| SpeedMining | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Repair | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| ContinueBuilding | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| DistributeWorkers | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | 4 | 4 |
| PlanCancelBuilding | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| WorkerScout | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| ScanEnemy | 5min | 5min | ❌ | ❌ | 5min | 5min | 120 | 立即 | 5min | 5min |
| PlanZoneDefense | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| PlanWorkerOnlyDefense | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| ManTheBunkers | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| PlanZoneGather | — | — | ✅ | — | — | — | — | — | — | — |
| PlanZoneGatherTerran | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| PlanZoneAttack | ✅ | ✅ | 兴奋剂 | 18机枪 | Dodge | 飓风 | ✅ | ✅ | ✅+Jump | ✅ |
| PlanFinishEnemy | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

\* `two_base_tanks` 及 `two_base_tanks_marine_rush_*` 系列；`battle_cruisers` 注释掉了 AutoDepot/MorphOrbitals。

### 13.3 `safe_tvt_raven` 完整 tactics 列表（对照示例）

```python
BuildOrder([
    # ── 运营宏 ──
    AutoDepot(),                                                    # 100 矿/补给站
    Step(None, MorphOrbitals(), skip_until=UnitReady(BARRACKS, 1)), # 150 矿/轨道
    CallMule(50),                                                   # 50 能量/矿骡
    LowerDepots(),                                                  # 无消耗
    MineOpenBlockedBase(),                                          # 无消耗
    Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5), # 无消耗
    Repair(),                                                       # 修理扣矿/气
    ContinueBuilding(),                                             # 无消耗
    # ── 集结 ──
    PlanZoneGather(),                                               # 无消耗
    # ── 防守 ──
    PlanWorkerOnlyDefense(),                                        # 无消耗
    PlanZoneDefense(),                                              # 无消耗
    # ── 进攻 ──
    Step(TechReady(STIMPACK), PlanZoneAttack(attack_value)),        # 无消耗
    PlanFinishEnemy(),                                              # 无消耗
])
```

> 对比标准策略：`safe_tvt_raven` **缺少** `WorkerScout`、`ScanEnemy`、`DistributeWorkers`、`PlanCancelBuilding`、`ManTheBunkers`；**独有** `PlanWorkerOnlyDefense` + `PlanZoneGather`（非 Terran 版）。
