# dummies 目录中的 Bot：继承关系与构成要素

本文自底向上说明 `dummies/` 下示例 Bot 在 Sharpy-SC2 中的类层次，以及一个 Bot 通常由哪些「积木」拼成、各自含义。

---

## 一、自底向上的继承关系

### 1. 框架基类（不在 `dummies/`，但所有继承都终止于此）

| 层次 | 类 | 作用简述 |
|------|-----|----------|
| 最底层 | `sc2.bot_ai.BotAI` | python-sc2 提供的 AI 基类：单位、建筑、地图、`on_step` 等 |
| 中间层 | `sharpy.knowledges.SkeletonBot` | 在 `BotAI` 上挂 `Knowledge`，抽象 `configure_managers()`，由 `Knowledge` 驱动每帧更新 |
| 常用层 | `sharpy.knowledges.KnowledgeBot` | 在 `SkeletonBot` 上预置一组核心 Manager（单位缓存、分矿/区域、战斗等），并抽象 `create_plan()`；在 `on_start` 里用 `ActManager` 执行计划 |

关系：**`KnowledgeBot` → `SkeletonBot` → `BotAI`**。

### 2. `dummies/` 里实际出现的几种接法

```
BotAI
 ├── RoachRush（zerg/roach_rush.py，手写 on_step，不用 Sharpy 计划系统）
 ├── IdleDummy / UseNeuralParasiteDummy / DetectNeuralParasiteDummy（debug，最小 BotAI）
 │
SkeletonBot
 ├── TemplateSkeletonBot（debug，演示空壳 + 手写 execute）
 ├── Stalkers4Gate（protoss/gate4.py，**手动** configure_managers，内含 ActManager(create_plan)）
 │
KnowledgeBot
 ├── 绝大多数「正式」dummy：各 *Bot 类（见下表）
 ├── EvadeDummy / DebugUnitsDummy / RestorePowerDummy / ExpandDummy（debug）
 │
LadderBot（多数文件末尾）
 └── 继承具体 *Bot，仅增加 `my_race` 等，供天梯/打包识别
```

说明：

- **`KnowledgeBot` 子类**：实现异步 `create_plan() -> BuildOrder`；框架在启动后通过 `ActManager` 调用它并得到一棵「行为树」式的计划。
- **`SkeletonBot` 直接用**（如 `Stalkers4Gate`）：需要自己返回完整的 `configure_managers()` 列表，并把 `ActManager(self.create_plan())` 或等价物接进去；**不**经过 `KnowledgeBot` 的默认 Manager 打包。
- **`BotAI` 直接用**：不使用 `Knowledge` / `BuildOrder`，逻辑全部在 `on_step` 等里手写（`RoachRush`、部分 debug）。

### 3. 按种族/文件：具体 Bot 类 → 直接父类

下列仅列出「作为可玩 Bot 主类」的命名类（不含文件内的 `BuildOrder` 子类、`LadderBot` 包装、`GenericMicro` 等辅助类）。

**Protoss**

| 文件 | 主 Bot 类 | 父类 |
|------|-----------|------|
| gate4.py | `Stalkers4Gate` | `SkeletonBot` |
| adept_allin.py | `AdeptRush` | `KnowledgeBot` |
| cannon_rush.py | `CannonRush` | `KnowledgeBot` |
| dark_templar_rush.py | `DarkTemplarRush` | `KnowledgeBot` |
| disruptor.py | `SharpSphereBot` | `KnowledgeBot` |
| macro_stalkers.py | `MacroStalkers` | `KnowledgeBot` |
| one_base_tempests.py | `OneBaseTempests` | `KnowledgeBot` |
| protoss_silver.py | `SilverProtoss` | `KnowledgeBot` |
| proxy_zealot_rush.py | `ProxyZealotRushBot` | `KnowledgeBot` |
| robo.py | `MacroRobo` | `KnowledgeBot` |
| voidray.py | `MacroVoidray` | `KnowledgeBot` |
| protoss_random.py | `RandomProtossBot` | 随机选中的 `LadderBot`（底层仍是上表某一 `KnowledgeBot` 或 `Stalkers4Gate`） |

**Terran**

| 文件 | 主 Bot 类 | 父类 |
|------|-----------|------|
| banshees.py | `Banshees` | `KnowledgeBot` |
| battle_cruisers.py | `BattleCruisers` | `KnowledgeBot` |
| bio.py | `BioBot` | `KnowledgeBot` |
| cyclones.py | `CycloneBot` | `KnowledgeBot` |
| marine_rush.py | `MarineRushBot` | `KnowledgeBot` |
| one_base_turtle.py | `OneBaseTurtle` | `KnowledgeBot` |
| rusty.py | `Rusty` | `KnowledgeBot` |
| safe_tvt_raven.py | `TerranSafeTvT` | `KnowledgeBot` |
| terran_silver_bio.py | `TerranSilverBio` | `KnowledgeBot` |
| two_base_tanks.py | `TwoBaseTanks` | `KnowledgeBot` |
| terran_random.py | `RandomTerranBot` | 随机选中的 `LadderBot` |

**Zerg**

| 文件 | 主 Bot 类 | 父类 |
|------|-----------|------|
| roach_rush.py | `RoachRush` | `BotAI` |
| twelve_pool.py | `TwelvePool` | `KnowledgeBot` |
| worker_rush.py | `WorkerRush` | `KnowledgeBot` |
| mutalisk.py | `MutaliskBot` | `KnowledgeBot` |
| roach_burrow.py | `RoachBurrowBot` | `KnowledgeBot` |
| roach_hydra.py | `RoachHydra` | `KnowledgeBot` |
| macro_zerg_v2.py | `MacroZergV2` | `KnowledgeBot` |
| zerg_silver.py | `ZergSilver` | `KnowledgeBot` |
| lurkers.py | `LurkerBot` | `KnowledgeBot` |
| lings.py | `LingFlood` | `KnowledgeBot` |
| macro_roach.py | `MacroRoach` | `KnowledgeBot` |
| zerg_random.py | `RandomZergBot` | 随机选中的 `LadderBot` |

**Debug**

| 文件 | 主 Bot 类 | 父类 |
|------|-----------|------|
| idle_dummy.py | `IdleDummy` | `BotAI` |
| template_skeleton_bot.py | `TemplateSkeletonBot` | `SkeletonBot` |
| evade_dummy.py | `EvadeDummy` | `KnowledgeBot` |
| debug_units_dummy.py | `DebugUnitsDummy` | `KnowledgeBot` |
| restore_power_dummy.py | `RestorePowerDummy` | `KnowledgeBot` |
| expand_bot.py | `ExpandDummy` | `KnowledgeBot` |
| use_neural_parasite_dummy.py | `UseNeuralParasiteDummy` | `BotAI` |
| detect_neural_parasite_dummy.py | `DetectNeuralParasiteDummy` | `BotAI` |

### 4. `LadderBot` 是什么

很多文件在末尾定义：

```python
class LadderBot(SomeMainBot):
    @property
    def my_race(self):
        return Race.Xxx
```

它**不是**框架里的单独基类，而是对「主 Bot 类」的薄包装，主要为了：

- 明确 `my_race`（天梯或列表展示）；
- 与 `DummyBuilder` / 打包 zip 约定：注册时往往用「主类」或 `LadderBot`，视 `bot_definitions.py` 而定。

随机族 Bot（`RandomProtossBot` 等）在**导入时**随机选一个文件的 `LadderBot` 作为父类，因此运行时种族固定，但策略随机。

---

## 二、一个 Bot 靠哪些要素定义（各指什么）

下面按「从骨架到细节」说明；**典型 Sharpy 流程**是 `KnowledgeBot` + `create_plan()`。

### 1. 基类与生命周期钩子

| 要素 | 含义 |
|------|------|
| **继承 `KnowledgeBot`** | 自动获得一组默认 Manager，并实现「计划驱动」的主循环。 |
| **`__init__(self)` 中 `super().__init__("显示名称")`** | 注册 Bot 的人类可读名字；可在此初始化成员（如自定义 `WeakAttack`）。 |
| **`configure_managers()`** | 返回**额外**的 `ManagerBase` 列表（如 `BuildDetector`、`ChatManager`），与内置 Manager 合并；用于侦察、聊天等扩展。 |
| **`async create_plan(self) -> BuildOrder`** | **核心**：返回整局游戏的宏观行为计划（建造、经济、战术步骤）。`KnowledgeBot` 通过 `ActManager` 在 `post_start` 里 `await` 该协程得到 `BuildOrder`。 |
| **`async on_step` / `execute` 等** | 少数 Bot 会重写以插入特殊逻辑（如 `TerranSilverBio` 里对 `execute` 的扩展）；一般逻辑应尽量放在 `BuildOrder` 的 Act 里。 |

继承 **`SkeletonBot`** 时：没有默认 Manager 包，必须自己列全（含 `ActManager`）；**`Stalkers4Gate`** 是范例。

继承 **`BotAI`** 时：无 `create_plan`，一切在 `on_step` / 自建状态机中完成。

### 2. 计划层：`BuildOrder` 与「步骤」

| 要素 | 含义 |
|------|------|
| **`BuildOrder`** | 继承自 `ActBase`，内部是一串子 `ActBase`（可包含列表，列表会收成 `SequentialList`）。表示**并行推进**的多条订单线（都执行，都完成则整单完成）。 |
| **`SequentialList`** | 严格按顺序执行的 Act 列表。 |
| **`Step(requirement, action, skip=..., skip_until=...)`** | 单步：**条件 `requirement` 满足**才执行 `action`；`skip` / `skip_until` 控制跳过或延迟。是「如果…就做…」的结构化写法。 |
| **具体 `Act*`** | 如 `ActUnit`、`GridBuilding`、`DistributeWorkers`、`PlanZoneAttack` 等，表示一类**可执行的游戏行为**（造单位、放建筑、分配农民、进攻等）。 |

### 3. 条件层：`Require*`（与 `Step` 配合）

| 要素 | 含义 |
|------|------|
| **`UnitReady`、`Supply`、`Gas`、`UnitExists`、`TechReady` 等** | 描述「当前游戏状态是否满足某条件」。 |
| **`Any` / `All`** | 多个条件的逻辑组合。 |
| **`EnemyUnitExists` 等** | 基于敌方信息的条件，用于反制、切换阶段。 |

条件与 Act 分离，便于复用同一套「建造脚本」，用不同 `Step` 切换阶段。

### 4. 可选：自定义 `BuildOrder` 子类

例如 `BuildBio`、`LingFloodBuild`：把一长串开局建筑/单位顺序封装成类，在 `create_plan` 里像 `BuildOrder([BuildBio(), tactics])` 一样组合「经济/build」与「战术列表」。

### 5. 战术与微操（扩展）

| 要素 | 含义 |
|------|------|
| **`sharpy.plans.tactics`** | 如 `PlanZoneAttack`、`DistributeWorkers`、各族专用战术类，偏**地图区域与部队调度**。 |
| **`GenericMicro` 子类** | 战斗微操（如 `roach_burrow` 里的 `MicroBurrowRoaches`），挂在 GroupCombat 或特定 Act 上使用。 |

### 6. 与 `bot_loader` / 对战的注册信息（库外配置）

在 `bot_loader/bot_definitions.py` 中，`DummyBuilder` 用下列字段把 Python 类注册成可选对手（**定义「怎么从菜单选到这个 Bot」**）：

| 字段 | 含义 |
|------|------|
| **`key`** | 字符串 ID，用于命令行或列表选择。 |
| **`name`** | 展示名 / 打包名（如天梯 zip）。 |
| **`race`** | `Race.Protoss` / `Terran` / `Zerg`。 |
| **`file_name`** | `dummies/<族>/xxx.py` 中的文件名。 |
| **`bot_type`** | 实际实例化的类（如 `TerranSilverBio`）。 |
| **`params_count`** | 构造参数个数，用于从启动参数传变体（如 cannon rush 模式、marine 变体）。 |

---

## 三、关系小结（一句话）

- **类继承**：多数实战 dummy 是 **`KnowledgeBot` → 你的 XxxBot**；少数是 **`SkeletonBot`** 或纯 **`BotAI`**。  
- **行为定义**：主要靠 **`create_plan()` 返回的 `BuildOrder`**，用 **`Act*`、`Step`、`Require*`、`SequentialList`** 拼出经济与战术；**`configure_managers()`** 补充数据/聊天等 Manager。  
- **对外注册**：由 **`DummyBuilder`（key、name、race、file、bot_type、params）** 与可选 **`LadderBot`（my_race）** 完成。

如需对照具体一行代码，可从任意 `dummies/*/*.py` 中的 `class XXX(KnowledgeBot)` 与 `async def create_plan` 读起。

---

## 四、`run_vs_ai.py` / `run_custom.py` 里 `my_bot_name` 能填哪些（按种族）

这里说的“注册名字”，是 `bot_loader/bot_definitions.py` 的 `BotDefinitions.add_dummies()` / `add_debug_bots()` 里注册的 **key**（即命令行 `-p1 <key>` 的 `<key>`），**不是**各 bot `super().__init__("...")` 的显示名。

### 1) Protoss（P）

- **常用（可直接对战）**
  - `4gate`
  - `adept`
  - `cannonrush.<mode>`（支持参数，`<mode>` 默认 `default`；可填 `0/1/2`）
  - `disruptor`
  - `dt`
  - `robo`
  - `stalker`
  - `voidray`
  - `zealot`
  - `tempest`
  - `silverprotoss`
- **随机（从多个 P 策略里随机选一个）**
  - `randomprotoss`

### 2) Zerg（Z）

- **常用（可直接对战）**
  - `12pool`
  - `200roach`
  - `hydra`
  - `lings`
  - `macro`
  - `mutalisk`
  - `workerrush`
  - `lurker`
  - `roachburrow`
  - `silverzerg`
- **额外变体（不走 DummyBuilder 参数）**
  - `lingflood`（固定 aggressive 版本）
  - `lingspeed`（固定 macro/speed 版本）
- **随机（从多个 Z 策略里随机选一个）**
  - `randomzerg`

### 3) Terran（T）

- **常用（可直接对战）**
  - `banshee`
  - `bc.<mode>`（支持参数，`<mode>` 默认 `default`；常见可填 `0/1`）
  - `bio`
  - `cyclone`
  - `marine.<mode>`（支持参数，`<mode>` 默认 `default`；常见可填 `0/1/2`）
  - `oldrusty`
  - `tank`
  - `terranturtle`
  - `saferaven`
  - `silverbio`
- **随机（从多个 T 策略里随机选一个）**
  - `randomterran`

### 4) Debug（调试用，`-p1` 也能跑）

- `debugidle.<race>`（`<race>` 默认 `random`；可填 `protoss/zerg/terran/random`）
- `debugevade.<race>`（同上）
- `debugtemplate`
- `debugunits`
- `debugrestorepower`
- `debuguseneural`
- `debugdetectneural`
- `debugexpanddummy`

### 5) 其他通用 key（不分种族）

- **人类玩家（必须当 P1 才有意义）**
  - `human.<race>`（`<race>` 默认 `random`；可填 `protoss/zerg/terran/random`）
- **内置 AI（`run_vs_ai.py` 的 p2_string 就是这个格式）**
  - `ai.<race>.<difficulty>.<build>`
    - `<race>`：`protoss/zerg/terran/random`
    - `<difficulty>`：`veryeasy/easy/medium/mediumhard/hard/harder/veryhard/vision/money/insane`
    - `<build>`：`random/rush/timing/power/macro/air`
