# readme_test

这个文件用于**详细介绍** `sharpy-sc2` 这个仓库的整体结构、如何运行/配置，以及仓库内已经实现的“微操（micro）”类型、可操作单位与对应源码位置。

> 说明：本仓库是一个基于 `python-sc2` 的 SC2 Bot 开发框架。它既包含框架本身（`sharpy/`），也包含大量用于练习/测试的 dummy bots（`dummies/`），以及启动对局的脚手架（`bot_loader/`、`run_custom.py`、`ladder.py`）。

---

## 1) 仓库结构（按用途划分）

下面是根目录关键内容的“用途导向”说明（不是严格的全部文件列表）：

- **`sharpy/`**：框架主体代码
  - `sharpy/knowledges/`：Bot 基类与 Knowledge/Manager 体系（如 `SkeletonBot`、`KnowledgeBot`）
  - `sharpy/managers/`：各种信息管理器（缓存、路径、区域、经济、侦察、数据等）
  - `sharpy/plans/`：Build/Act/Tactics 等计划系统（宏观决策、执行动作、战术模块）
  - `sharpy/combat/`：战斗与微操系统（分组、模型、规则、各单位 micro 实现）

- **`dummies/`**：示例/陪练 Bot（practice dummies）
  - `dummies/protoss/`、`dummies/terran/`、`dummies/zerg/`：不同种族的 dummy bots
  - `dummies/debug/`：调试用途 bot（例如模板骨架 bot、扩张测试 bot）

- **`bot_loader/`**：启动器与 bot 定义集合
  - `bot_loader/bot_definitions.py`：把“可运行的 bot key”注册到 `BotDefinitions`（包括 dummy、人类、内置AI、梯子 bot）
  - `bot_loader/game_starter.py`：命令行启动本地对局（选地图、选双方玩家类型、是否 realtime 等）
  - `bot_loader/ladder_bot.py`：用于 LadderManager 外部对局的启动封装（读取 `ladderbots.json`，拼接启动命令）

- **`python-sc2/`**：上游 `python-sc2` 代码（作为子目录引入）

- **配置与入口脚本**
  - `config.ini`：默认运行配置（日志、步长、调试开关等）
  - `config.py`：读取配置（支持 `config-local.ini` 覆盖）
  - `run_custom.py`：本地启动入口（调用 `GameStarter`）
  - `ladder.py`：Ladder/本地单机对局入口（也包含“对战内置AI”的单机逻辑）

---

## 2) 这里有没有“机器人能直接与 SC2 内置AI 对战”的实现？

**有。并且有两种常用方式：**

### 2.1 用 `run_custom.py`（推荐：参数清晰，支持选择对手为内置AI）

入口在 `run_custom.py`，它会创建 `BotDefinitions` 并调用 `GameStarter.play()` 来解析命令行参数并开局。

- 入口：`run_custom.py`
- 命令行解析与对局启动：`bot_loader/game_starter.py`
- **内置AI player 类型 key**：`ai`（在 `bot_loader/bot_definitions.py` 的 `BotDefinitions._ai()` 里定义）

`GameStarter` 对“内置AI对手”的说明直接写在 `--help` 的 epilog 里：

- `For ingame ai, use ai.race.difficulty.build where all arguments are optional`
- `ingame ai defaults to ai.random.veryhard.random`

也就是说，**你可以把 player2 写成**：

- `ai`（全部默认）
- `ai.terran`（只指定种族）
- `ai.terran.hard`（指定种族+难度）
- `ai.terran.hard.rush`（种族+难度+build）

其中允许的值来自 `bot_loader/bot_definitions.py`：

- race：`races` 字典（protoss/zerg/terran/random）
- difficulty：`difficulty` 字典（veryeasy...veryhard...cheat 等）
- build：`builds` 字典（random/rush/timing/power/macro/air）

#### 示例（与内置AI对战）

在仓库根目录执行（确保已安装依赖，并能找到 SC2 的 Maps 与游戏安装路径——这部分由 `python-sc2`/`sc2pathlib` 负责）：

```bash
python run_custom.py -p1 4gate -p2 ai.terran.veryhard.rush -m random
```

- `-p1 4gate`：玩家1使用 dummy bot（key 来自 `BotDefinitions.add_dummies()`）
- `-p2 ai.terran.veryhard.rush`：玩家2使用**内置AI**（Terran / VeryHard / Rush）
- `-m random`：随机选择已安装地图

你也可以 `-p1 human` 让自己当玩家1（此时会强制 real-time，并偏向 release 配置）。

### 2.2 用 `ladder.py` 的单机模式（快速测：默认 VeryHard 随机种族）

`ladder.py` 里有 `stand_alone_game(bot)`，其 docstring 明确写了：

- `Play a game against the ladder build or test the bot against ingame ai`

当不是 LadderManager 模式时，会直接运行：

- `[bot, Computer(Race.Random, Difficulty.VeryHard)]`

对应代码位置：`ladder.py` 的 `stand_alone_game()`。

---

## 3) 如何配置（运行参数与配置文件）

### 3.1 `config.ini` / `config-local.ini`

配置读取在 `config.py:get_config(local: bool = True)`：

- 默认会读取：`config.ini` **以及** `config-local.ini`（如果存在，后者覆盖前者）
- 如果你想忽略本地覆盖，在本地启动脚本里有 `--release` 参数（见 `bot_loader/game_starter.py`），会让 bot 只读取 `config.ini`

`config.ini` 里常用的（与运行体验强相关）配置项：

- **`[general] game_step_size`**：每步推进的 game step（影响非 realtime 下每次 `on_step` 的步长/性能/细节）
- **`[general] log_level` / `log_file`**：日志等级与是否写入文件
- **`[general] write_gamelogs` / `write_data`**：是否写入对局日志/数据（不同入口脚本使用方式略有差异）

### 3.2 本地启动常用参数（`run_custom.py` / `GameStarter`）

`bot_loader/game_starter.py` 支持的常用参数（建议先 `--help` 查看完整列表）：

- **`-m/--map`**：地图名（默认 `random`）
- **`-p1/--player1`**：玩家1（bot key 或 human）
- **`-p2/--player2`**：玩家2（bot key，或 `ai...` 内置AI）
- **`-rt/--real-time`**：realtime 模式
- **`-r/--release`**：只读 `config.ini`，忽略 `config-local.ini`
- **`--port`**：指定起始端口（避免多实例冲突）
- **`-raw/--raw_selection`**：raw affects selection（提升一点性能用）

---

## 4) 微操（micro）系统：实现了哪些类型？能操作哪些单位？源码在哪？

本仓库的微操核心是 `sharpy/combat/micro_rules.py` 的 `MicroRules.load_default_micro()`：它把 **`UnitTypeId -> MicroStep`** 做了默认映射；战斗执行时由 `sharpy/combat/group_combat_manager.py` 根据单位类型选择对应 micro（找不到则回退到 `GenericMicro`）。

### 4.1 通用/基础微操（所有单位共用的“框架能力”）

- **分组/推进/撤退/集结逻辑（group-level）**：`sharpy/combat/default_micro_methods.py`
- **通用单体微操模型（kite、后撤、push 时的前进路径、focus fire 等）**：`sharpy/combat/generic_micro.py`
- **微操规则与默认映射表（把单位绑定到对应 micro 类）**：`sharpy/combat/micro_rules.py`
- **微操接口与回调点（init_group / group_solve_combat / unit_solve_combat 等）**：`sharpy/combat/micro_step.py`

### 4.2 默认已实现的“单位微操清单”（按 `UnitTypeId` → 类 → 文件）

> 下表是“默认映射”（即不开自定义 rules 的情况下框架会自动使用的 micro）。如果你在自己的 bot 里替换 `MicroRules`，映射可能不同。

#### 4.2.1 工人

- **DRONE / PROBE / SCV** → `MicroWorkers` → `sharpy/combat/micro_workers.py`

#### 4.2.2 Protoss

- **ARCHON** → `NoMicro` → `sharpy/combat/no_micro.py`
- **ADEPT** → `MicroAdepts` → `sharpy/combat/protoss/micro_adepts.py`
- **CARRIER** → `MicroCarriers` → `sharpy/combat/protoss/micro_carriers.py`
- **COLOSSUS** → `MicroColossi` → `sharpy/combat/protoss/micro_colossi.py`
- **DARKTEMPLAR** → `MicroZerglings`（作为近战通用逻辑复用）→ `sharpy/combat/zerg/micro_zerglings.py`
- **DISRUPTOR / DISRUPTORPHASED** → `MicroDisruptor` / `MicroPurificationNova` → `sharpy/combat/protoss/micro_disruptor.py`
- **HIGHTEMPLAR** → `MicroHighTemplars` → `sharpy/combat/protoss/micro_hightemplars.py`
- **OBSERVER** → `MicroObservers` → `sharpy/combat/protoss/micro_observers.py`
- **ORACLE** → `MicroOracles` → `sharpy/combat/protoss/micro_oracles.py`
- **PHOENIX** → `MicroPhoenixes` → `sharpy/combat/protoss/micro_phoenixes.py`
- **SENTRY** → `MicroSentries` → `sharpy/combat/protoss/micro_sentries.py`
- **STALKER** → `MicroStalkers`（包含 blink、优先级集火等）→ `sharpy/combat/protoss/micro_stalkers.py`
- **WARPPRISM** → `MicroWarpPrism` → `sharpy/combat/protoss/micro_warp_prism.py`
- **VOIDRAY** → `MicroVoidrays` → `sharpy/combat/protoss/micro_voidrays.py`
- **ZEALOT** → `MicroZealots` → `sharpy/combat/protoss/micro_zealots.py`

#### 4.2.3 Zerg

- **ZERGLING** → `MicroZerglings` → `sharpy/combat/zerg/micro_zerglings.py`
- **ULTRALISK** → `NoMicro` → `sharpy/combat/no_micro.py`
- **OVERSEER** → `MicroOverseers` → `sharpy/combat/zerg/micro_overseers.py`
- **QUEEN** → `MicroQueens` → `sharpy/combat/zerg/micro_queens.py`
- **RAVAGER** → `MicroRavagers` → `sharpy/combat/zerg/micro_ravagers.py`
- **LURKERMP** → `MicroLurkers` → `sharpy/combat/zerg/micro_lurkers.py`
- **INFESTOR** → `MicroInfestors` → `sharpy/combat/zerg/micro_infestors.py`
- **SWARMHOSTMP** → `MicroSwarmHosts` → `sharpy/combat/zerg/micro_swarmhosts.py`
- **LOCUSTMP / LOCUSTMPFLYING** → `NoMicro` → `sharpy/combat/no_micro.py`
- **VIPER** → `MicroVipers` → `sharpy/combat/zerg/micro_vipers.py`

#### 4.2.4 Terran

- **HELLIONTANK** → `NoMicro` → `sharpy/combat/no_micro.py`
- **SIEGETANK** → `MicroTanks` → `sharpy/combat/terran/micro_tanks.py`
- **VIKINGFIGHTER** → `MicroVikings` → `sharpy/combat/terran/micro_vikings.py`
- **MARINE / MARAUDER** → `MicroBio`（包含 stim 触发等）→ `sharpy/combat/terran/micro_bio.py`
- **BATTLECRUISER** → `MicroBattleCruisers` → `sharpy/combat/terran/micro_battlecruisers.py`
- **RAVEN** → `MicroRavens` → `sharpy/combat/terran/micro_ravens.py`
- **MEDIVAC** → `MicroMedivacs` → `sharpy/combat/terran/micro_medivacs.py`
- **LIBERATOR** → `MicroLiberators` → `sharpy/combat/terran/micro_liberators.py`
- **REAPER** → `MicroReaper` → `sharpy/combat/terran/micro_reaper.py`
- **WIDOWMINE** → `MicroMines` → `sharpy/combat/terran/micro_widowmines.py`

### 4.3 “微操类型”总结（从现有实现能看出来的能力点）

这里按“行为类型”归纳（对应实现文件见上表）：

- **集火/目标优先级（focus fire）**：通用逻辑在 `sharpy/combat/default_micro_methods.py`；例如 `MicroStalkers` 自带高优先级字典（`sharpy/combat/protoss/micro_stalkers.py`）
- **kite / backstep / 受伤后撤（基于 combat model 的通用走位）**：`sharpy/combat/generic_micro.py`
- **技能施放类微操（示例）**
  - Stalker **Blink**：`sharpy/combat/protoss/micro_stalkers.py`
  - Marine/Marauder **Stim**：`sharpy/combat/terran/micro_bio.py`
  - Roach **Burrow up/down**（血量阈值触发）：`sharpy/combat/zerg/micro_roaches.py`
  - Disruptor **Purification Nova**：`sharpy/combat/protoss/micro_disruptor.py`
  - 其他单位（Raven/Viper/Infestor/Lurker/Oracle 等）也有专属 micro 文件，具体技能逻辑在各自 `micro_*.py` 内

---

## 5) 额外：如何把自己的 bot 接到启动器里

如果你要把“你写的新 bot”接入 `run_custom.py` 的 `-p1/-p2` 列表，通常有两种思路：

- **作为 dummy bots**：放到 `dummies/<race>/` 并在 `bot_loader/bot_definitions.py` 的 `BotDefinitions.add_dummies()` 里注册一个 key
- **作为 ladder bots**：放到 `Bots/<YourBotName>/ladderbots.json`（由 `BotDefinitions._get_ladder_bots()` 扫描目录自动发现），并通过 `bot_loader/ladder_bot.py` 启动


