# Sharpy 模块与配置说明

本文档按目录列出 `sharpy/knowledges`、`sharpy/managers`、`sharpy/plans`、`sharpy/combat` 中各文件的职责，并说明根目录下 `config.ini`、`config.py`、`run_custom.py` 的配置关系。

---

## 一、`sharpy/knowledges/`：Bot 基类与 Knowledge 体系

| 文件 | 说明 |
|------|------|
| `__init__.py` | 导出 `Knowledge`、`KnowledgeBot`、`SkeletonBot`。 |
| `knowledge.py` | **核心上下文对象**：持有 `SkeletonBot` 引用、`ConfigParser`、管理器列表；负责 `pre_start` / `start` / `update` / `post_update` 生命周期；提供 `get_manager`、`reserve` / `can_afford`、配置读取 `get_*_setting`、单位摧毁事件、对局结束汇总等。 |
| `skeleton_bot.py` | **最薄 Bot 基类**：继承 `BotAI`，构造时 `get_config()` 注入 `self.config`；组合 `Knowledge`；子类实现 `configure_managers()`；`on_step` 中调用 `knowledge.update` → `execute()` → `post_update`；根据配置设置 `client.game_step` 等。 |
| `knowledge_bot.py` | **带完整 Manager 栈的基类**：继承 `SkeletonBot`，预置内存、战损、敌军、缓存、价值、角色、寻路、区域、建造求解、收入、冷却、`GroupCombatManager`、热力图、集结点、`PreviousUnitsManager`、`GameAnalyzer`、`DataManager` 等；`on_start` 中组装列表并追加 `CustomFuncManager`、`ActManager(create_plan)`；子类实现异步 `create_plan()` 返回 `BuildOrder`。 |
| `knowledge_test.py` | **单元测试**：验证 `Knowledge.get_manager` 在默认/子类替换管理器时的类型解析行为。 |

**关系简述**：`SkeletonBot` 负责与 python-sc2 的交互节拍；`Knowledge` 聚合配置与管理器；`KnowledgeBot` 在 Sharpy 典型 Bot 上提供默认管理器 + 计划（Act）驱动。

---

## 二、`sharpy/managers/`：信息管理器

管理器均继承 `core/manager_base.py` 中的 `ManagerBase`（并常混入 `sharpy/general/component.py` 的 `Component`），由 `Knowledge` 按序调用 `start` → `update` → `post_update`。

### 2.1 包入口

| 文件 | 说明 |
|------|------|
| `__init__.py` | 仅再导出 `ManagerBase`。 |
| `core/__init__.py` | 导出核心管理器类型（供 `KnowledgeBot` 等 import）。 |
| `extensions/__init__.py` | 导出扩展管理器类型。 |

### 2.2 `managers/core/` 核心

| 文件 | 说明 |
|------|------|
| `manager_base.py` | 管理器抽象基类：`update` / `post_update` / `on_end`；`print` 委托到 `knowledge`；`real_type` 等单位类型辅助。 |
| `act_manager.py` | 在 `post_start` 中解析 `create_plan` 协程或静态 `ActBase`，每步 `execute()`，调试下 `debug_draw()`。 |
| `action_manager.py` | 记录已下达指令，避免重复建造/重复订单（与 realtime 下帧延迟配合）。 |
| `log_manager.py` | 统一日志输出；可按 `[debug_log]` 按 tag 过滤；自定义对局中非 1 号玩家可静默。 |
| `version_manager.py` | 版本/提交信息相关（与 `version.txt` 等配合）。 |
| `unit_cache_manager.py` | 单位集合缓存与查询（己方/敌方可走类型等）。 |
| `unit_value.py` | 单位价值、战力评估、工人类型等数值工具；实现 `IUnitValues`。 |
| `unit_value_test.py` | `UnitValue` 相关测试。 |
| `zone_manager.py` | 地图分区、扩张区、主矿/自然区等区域逻辑。 |
| `pathing_manager.py` | 可走网格、路径与可走性查询。 |
| `cooldown_manager.py` | 技能/能力冷却跟踪。 |
| `building_solver.py` | 建筑落位、碰撞与建造可行性求解。 |
| `income_calculator.py` | 经济收入与采集相关估算。 |
| `unit_role_manager.py` | 单位角色分配与任务（与 `core/roles/` 配合）。 |
| `enemy_units_manager.py` | 敌军单位状态维护与查询。 |
| `lostunitsmanager.py` | 己方损失单位统计与展示。 |
| `previousunitsmanager.py` | 上一帧单位快照，用于摧毁事件、对比等。 |
| `gather_point_solver.py` | 部队/工人集结点求解。 |

#### `managers/core/roles/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `unit_task.py` | 单位任务枚举或结构。 |
| `units_in_role.py` | 按角色筛选单位集合。 |

#### `managers/core/grids/`（网格与建造区域）

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `grid.py` | 通用网格基础。 |
| `grid_area.py` | 网格区域抽象。 |
| `build_grid.py` | 建造用网格。 |
| `build_area.py` | 建造区域。 |
| `zone_area.py` | 与区域管理联动的区域形状。 |
| `rectangle.py` | 矩形区域。 |
| `cliff.py` | 悬崖/高度相关网格信息。 |
| `blocker_type.py` | 阻挡类型枚举或分类。 |

### 2.3 `managers/extensions/` 扩展

| 文件 | 说明 |
|------|------|
| `memory_manager.py` | 跨步记忆（如历史决策、标记）。 |
| `data_manager.py` | 对局数据记录；受 `config.ini` 中 `write_data` 控制是否写入。 |
| `game_analyzer.py` | 局势分析（优劣、阶段等）。 |
| `build_detector.py` | 根据侦察推断对手建造/开局。 |
| `enemy_army_predicter.py` | 敌军部队组成/规模预测。 |
| `enemy_vision_manager.py` | 视野与可见性相关逻辑。 |
| `heat_map.py` | 热力图（压力、威胁等空间分布）。 |
| `chat_manager.py` | 游戏内聊天（受 `general.chat` 等影响）。 |
| `archon.py` | 执政官（Archon）相关特殊逻辑。 |
| `custom_func_manager.py` | 包装 `KnowledgeBot.pre_step_execute` 等自定义每步钩子。 |

#### `managers/extensions/game_states/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `advantage.py` | 优劣势状态判断。 |
| `air_army.py` | 空军规模/形态相关状态。 |

#### `managers/extensions/predict/`

| 文件 | 说明 |
|------|------|
| `composition_guesser.py` | 根据线索猜测对手兵种构成。 |

---

## 三、`sharpy/plans/`：Build / Act / Require / Tactics

计划系统以 `ActBase` 为节点，组合成 `BuildOrder`（见 `build_order.py`），由 `ActManager` 每步执行。`require/` 下为条件节点；`acts/` 为具体动作；`tactics/` 为战术行为；种族相关子目录放对应族专用 Act/Tactic。

### 3.1 根级与组合

| 文件 | 说明 |
|------|------|
| `__init__.py` | 导出 `BuildOrder`、`Step`、`StepBuildGas`、`SequentialList`、`SubActs`、`BuildId`、`IfElse`。 |
| `build_order.py` | **建造/执行计划容器**：顺序执行子 `Act`，不阻塞式推进。 |
| `build_step.py` | 单步建造定义（与 `Step` 等配合）。 |
| `sequential_list.py` | 顺序列表组合 Act。 |
| `sub_acts.py` | 子计划并行或组合封装。 |
| `if_else.py` | 条件分支 Act。 |
| `step_gas.py` | 气矿相关步骤封装。 |
| `BuildId.py` | 开局/流派 ID 枚举或常量。 |
| `terran.py` / `protoss.py` / `zerg.py` | 种族相关的计划片段或预设入口（依项目用法）。 |

### 3.2 `plans/require/` 条件（Require）

| 文件 | 说明 |
|------|------|
| `__init__.py` | 导出各 Require。 |
| `require_base.py` | 条件节点基类。 |
| `require_custom.py` | 自定义可调用条件。 |
| `all.py` / `any.py` | 逻辑与/或组合。 |
| `once.py` | 仅满足一次。 |
| `time.py` | 游戏时间条件。 |
| `minerals.py` / `gas.py` / `supply.py` / `supply_left.py` | 资源与人口条件。 |
| `count.py` | 单位数量条件。 |
| `unit_ready.py` / `unit_exists.py` | 单位就绪/存在。 |
| `tech_ready.py` | 科技就绪。 |
| `enemy_unit_exists.py` / `enemy_unit_exists_after.py` / `enemy_building_exists.py` | 敌军单位/建筑相关条件。 |
| `enemy_bases.py` | 敌方基地数量或状态。 |
| `methods.py` | Require 通用辅助。 |
| `supply_test.py` | 测试用。 |

### 3.3 `plans/acts/` 动作（Act）

| 文件 | 说明 |
|------|------|
| `__init__.py` | 导出各 Act 与工具。 |
| `act_base.py` | **所有 Act 基类**：建造能力白名单、通用执行与完成判定。 |
| `act_unit.py` / `act_unit_once.py` | 训练/生产单位（一次或持续）。 |
| `act_building.py` | 建造建筑。 |
| `act_zerg_morph.py` | 虫族变形类动作。 |
| `act_custom.py` | 自定义可调用 Act。 |
| `methods.py` | Act 辅助函数。 |
| `workers.py` / `auto_worker.py` | 工人分配与自动补农民。 |
| `expand.py` | 开矿扩张。 |
| `build_gas.py` | 建造气矿建筑。 |
| `build_ramp.py` | 路口/坡道相关建造。 |
| `build_position.py` / `position_building.py` / `grid_building.py` | 指定位置或网格上建造。 |
| `defensive_building.py` | 防御建筑。 |
| `tech.py` / `tech_test.py` | 升级科技。 |
| `reserve.py` | 资源预留。 |
| `morph_building.py` / `cancel_building.py` | 建筑变形/取消。 |
| `mine_open_blocked_base.py` | 处理被堵矿等开矿逻辑。 |
| `morph_warp_gates.py` | 折跃门变形。 |

#### `plans/acts/terran/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `terran_unit.py` | 人族单位生产 Act。 |
| `auto_depot.py` | 自动补给站/房子。 |
| `morph_orbitals.py` / `morph_planetary.py` | 指挥中心升级轨道/行星要塞。 |
| `build_addon.py` | 挂件建造。 |

#### `plans/acts/protoss/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `protoss_unit.py` | 神族单位生产。 |
| `auto_pylon.py` | 自动水晶。 |
| `chrono_*.py` | 各种chrono加速（单位/建筑/科技）。 |
| `warp_unit.py` | 折跃单位。 |
| `defensive_cannons.py` | 炮台防御。 |
| `restore_power.py` | 恢复供电。 |
| `artosis_pylon.py` | 特定 pylon 位策略。 |
| `archon.py` | 合体执政官相关。 |

#### `plans/acts/zerg/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `zerg_unit.py` | 虫族单位生产。 |
| `morph_units.py` / `morph_townhall.py` / `morph_greater_spire.py` | 各类变形。 |
| `auto_overlord.py` | 自动房子人口。 |

### 3.4 `plans/tactics/` 战术

| 文件 | 说明 |
|------|------|
| `__init__.py` | 战术模块导出。 |
| `zone_attack.py` / `zone_attack_all_in.py` | 分区域进攻 / 一波流。 |
| `zone_defense.py` | 分区防守。 |
| `zone_gather.py` | 分区集结。 |
| `attack_expansions.py` | 压制分矿。 |
| `distribute_workers.py` / `distribute_workers_test.py` | 采矿分配。 |
| `speed_mining.py` | 速采优化。 |
| `worker_scout.py` / `worker_rally_point.py` | 农民侦查与集结。 |
| `worker_only_defense.py` / `worker_counterattack.py` | 纯农民防/反打。 |
| `cancel_building.py` | 战术性取消建造。 |
| `warn_build_macro.py` | 宏观警告提示类战术。 |

#### `plans/tactics/terran/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `man_the_bunkers.py` | 进驻地堡。 |
| `repair.py` | 维修。 |
| `scan_enemy.py` | 雷达扫描。 |
| `call_mule.py` | 矿骡。 |
| `addon_swap.py` | 挂件交换。 |
| `lower_depots.py` | 降地堡开门。 |
| `continue_building.py` | 持续建造/SCV跟进。 |
| `zone_gather_terran.py` | 人族特化集结。 |

#### `plans/tactics/protoss/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `dt_attack.py` | 隐刀骚扰。 |
| `double_adept_scout.py` | 双使徒侦查。 |
| `main_defender.py` | 主家防守。 |
| `protoss_rally_point.py` | 神族集结。 |
| `plan_heat_observer.py` / `plan_heat_defender.py` | 观察者相关计划热力。 |
| `plan_hallucinations.py` / `hallucinated_phoenix_scout.py` | 幻象与凤凰侦查。 |

#### `plans/tactics/zerg/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `spread_creep.py` / `spread_creep2.py` | 铺菌毯。 |
| `inject_larva.py` | 注卵。 |
| `overlord_scout.py` / `ling_scout.py` | 房子/小狗侦查。 |
| `plan_heat_overseer.py` | 监察王虫热力计划。 |
| `counter_terran_tie.py` | 对抗人族特定战术。 |

#### `plans/tactics/scouting/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `scout.py` | 通用侦查调度。 |
| `scout_base_action.py` / `scout_location.py` / `scout_around_main.py` | 侦查目标与路径行为。 |

#### `plans/tactics/weak/`

| 文件 | 说明 |
|------|------|
| `__init__.py` | 子包导出。 |
| `weak_attack.py` / `weak_defense.py` | 弱化版攻/防（测试或简单 AI）。 |

### 3.5 `plans/debug/`

| 文件 | 说明 |
|------|------|
| `no_double_orders.py` | 调试重复订单检测。 |

---

## 四、`sharpy/combat/`：战斗与微操

### 4.1 核心框架

| 文件 | 说明 |
|------|------|
| `__init__.py` | 导出战斗公共类型（`CombatUnits`、`GenericMicro`、`MicroStep`、`Action`、`MoveType`、`NoMicro`、`MicroWorkers`、`MicroRules` 等）。 |
| `group_combat_manager.py` | **战斗总控**：分组、接敌、调用 `MicroRules` 注册的单位级 micro；实现 `ICombatManager`。 |
| `combat_units.py` | 战斗单位集合封装与战力。 |
| `generic_micro.py` | 通用微操逻辑与 `CombatModel`（接战模型）。 |
| `micro_step.py` | 单步微操状态机片段。 |
| `micro_rules.py` | 按单位类型绑定具体 micro 类与规则链。 |
| `default_micro_methods.py` | 默认微操方法加载。 |
| `action.py` / `no_micro.py` | 微操动作结果 / 空微操占位。 |
| `move_type.py` | 移动/阵型类型枚举。 |
| `micro_workers.py` | 工人战斗或拉扯。 |

### 4.2 `combat/terran/`、`combat/protoss/`、`combat/zerg/`

各 `micro_*.py` 为对应兵种的 `GenericMicro` 子类或专用逻辑；`__init__.py` 为子包导出。

**人族**（`combat/terran/`）：`micro_bio.py`、`micro_medivacs.py`、`micro_tanks.py`、`micro_vikings.py`、`micro_liberators.py`、`micro_widowmines.py`、`micro_ravens.py`、`micro_reaper.py`、`micro_battlecruisers.py`。

**神族**（`combat/protoss/`）：`micro_zealots.py`、`micro_stalkers.py`、`micro_sentries.py`、`micro_adepts.py`、`micro_hightemplars.py`、`micro_disruptor.py`、`micro_colossi.py`、`micro_phoenixes.py`、`micro_voidrays.py`、`micro_oracles.py`、`micro_carriers.py`、`micro_observers.py`、`micro_warp_prism.py`。

**虫族**（`combat/zerg/`）：`micro_zerglings.py`、`micro_roaches.py`、`micro_ravagers.py`、`micro_lurkers.py`、`micro_swarmhosts.py`、`micro_infestors.py`、`micro_vipers.py`、`micro_queens.py`、`micro_overseers.py`。

---

## 五、配置：`config.ini`、`config.py`、`run_custom.py`

### 5.1 `config.ini`（默认）

位于仓库根目录，使用 INI 分段：

- **`[general]`**
  - `chat`：是否允许游戏内聊天类逻辑（`Knowledge.pre_start` 读入 `is_chat_allowed`）。
  - `debug`：总调试开关；为真时各 `Component` 可通过 `knowledge.debug` 与 `[debug]` 分项绘制调试信息。
  - `log_level`：日志级别（如 `INFO`）。
  - `log_file`：是否将日志写入文件（由 `GameStarter.play()` 在开局前配置 `LoggingUtility`）。
  - `game_step_size`：非实时模式下每次 `on_step` 推进的 game step（`SkeletonBot` 设置 `self.client.game_step`）。
  - `write_data`：`DataManager` 是否写入数据文件。
  - `write_gamelogs`：其他入口（如天梯 `ladder.py`）可选用。

- **`[debug]`**  
  键名为**类名**（如 `ZoneManager`、`PlanZoneAttack`）。`Component.start` 中通过 `knowledge.get_boolean_setting("debug.<类名>")` 设置 `_debug`，用于屏幕调试绘制等。

- **`[debug_log]`**  
  键名为 **Log tag**（`knowledge.print(..., tag=...)`）。`LogManager.print` 中若该 tag 存在且为 `no`/`false`，则抑制该 tag 的日志；缺省为输出。

- **`[build]`**  
  预留或具体 Bot 自定义键位（当前示例文件可为空）。

### 5.2 `config.py`（读取与覆盖）

- `get_config(local: bool = True)`：
  - `local=True`（默认）：依次读取 **`config.ini`** 与 **`config-local.ini`**（后者覆盖前者同名字段）。任一存在即可。
  - `local=False`：只读 **`config.ini`**。
  - 若所需文件均不存在则抛出 `ValueError`。
- `get_version()`：读取 `version.txt` 返回提交信息元组（失败时记警告并返回空元组）。

### 5.3 `run_custom.py`（本地入口）

- 将 `python-sc2` 加入 `sys.path`。
- 调用 `version.update_version_txt()` 更新版本文件。
- 构造 `BotDefinitions(ladder_bots_path)`（默认扫描仓库下 `Bots/`），再构造 **`GameStarter(definitions)`** 并 `starter.play()`。
- **`GameStarter`**（`bot_loader/game_starter.py`）构造时使用 **`get_config()`**（即默认合并 `config-local.ini`）；解析命令行参数后：
  - 若 `general.log_file` 为真，按 `log_level` 写入 `games/` 下对局同名 `.log`；否则仅控制台/logger。
  - **`setup_bot`**：对 Bot 实例设置 `opponent_id`、`run_custom=True`，以及 **`--release` 时** 将 **`my_bot.config = get_config(False)`**，强制忽略本地覆盖，仅使用发布用 `config.ini`。

### 5.4 配置在运行时的传递链

1. **`SkeletonBot.__init__`**：`self.config = get_config()`。  
2. **`Knowledge.pre_start`**：`self.config = self.ai.config`，并读取 `general.chat`、`general.debug`。  
3. **`LogManager` / `DataManager` / 各 Component`**：通过 `knowledge.config` 或 `get_boolean_setting` 读取分项。  
4. **本地自定义**：在根目录添加 **`config-local.ini`**，只写需要覆盖的键即可，无需改仓库内 `config.ini`。

---

## 六、相关路径速查

| 路径 | 角色 |
|------|------|
| `sharpy/knowledges/` | Bot 基类 + `Knowledge` 聚合 |
| `sharpy/managers/` | 每帧更新的信息管理器 |
| `sharpy/plans/` | 宏观计划、条件、动作与战术 |
| `sharpy/combat/` | 接战分组与单位微操 |
| `config.ini` / `config-local.ini` | 默认与本地覆盖配置 |
| `config.py` | 配置解析 API |
| `run_custom.py` → `bot_loader/game_starter.py` | 本地对局与日志、release 模式下的配置切换 |

文档生成自仓库当前文件结构；若后续增减文件，请以目录为准同步本说明。
