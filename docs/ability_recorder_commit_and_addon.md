# Ability 落地记录与附属建筑命名

本文档记录 `AbilityRecorderManager`（`sharpy/managers/extensions/ability_recorder.py`）在实现「动作落地后再写入序列」与「TechLab/Reactor 按宿主建筑命名」时的设计要点与踩坑经验。

相关实现：

| 文件 | 职责 |
|------|------|
| `sharpy/managers/extensions/ability_recorder.py` | pending / commit 状态机、序列写入 |
| `sharpy/tools/data_ref_loader.py` | 能力语义解析、`BUILD_TECHLAB_*` 后缀映射 |
| `sharpy/knowledges/skeleton_bot.py` | 在 `do()` 中调用 `recorder.record()` |

---

## 一、为什么要「落地后再记录」

### 问题背景

早期实现是在 bot **每次尝试**下发命令时立即写入 `sequence`。这会导致：

1. **重复条目**：例如 `UPGRADETOORBITAL_ORBITALCOMMAND`，bot 在升级完成前会连续多帧重复下发，序列里出现十几条相同动作。
2. **未真正执行的动作也被记录**：资源不足、队列满等情况下，bot 可能「尝试」了但 SC2 并未接受。

### 目标

- 只在动作**被游戏接受并开始执行**后，才写入 `sequence`。
- 同一次真实落地的动作只记一条（按 `(unit_tag, resolved_ability, target_key)` 去重）。

---

## 二、落地检测：pending → commit 机制

### 流程概览

```
bot.do(action)
    → record()：写入 _pending（不立刻进 sequence）
    → 本帧 post_update()：动作尚未发给 SC2，通常检测不到
    → _after_step()：把 actions 发给 SC2
下一帧 observation 更新
    → post_update()：检查 pending 是否已落地
        → 已落地：_commit() 写入 sequence
        → 超时 8s 仍未落地：丢弃（视为未执行）
```

### 时序要点

Sharpy / python-sc2 的单帧顺序为：

1. `knowledge.update()` — 刷新单位缓存与观测
2. `execute()` → `do()` → `record()` — 命令进入 `self.actions`，同时进入 `_pending`
3. `knowledge.post_update()` — 检查 `_pending`（**此时本帧命令尚未发出**）
4. `_after_step()` — 真正把 `self.actions` 发给 SC2

因此：**至少在 `record()` 的下一帧**，才能在 `unit.orders` 里看到该命令。

### 落地判定（`_is_committed`）

按优先级检查：

| 条件 | 适用场景 |
|------|----------|
| `unit.orders` 中含该 `ability_id` | 训练、建造、研究、变形进行中等 |
| 执行单位 `type_id` 已变为变形结果 | `CC → OC`、`OC → PF` |
| 宿主建筑 `add_on_tag` 存在且 addon 未 `is_ready` | `BUILD_TECHLAB_*` / `BUILD_REACTOR_*` |

查找单位使用 `unit_cache.by_tag(tag)`，不要用不存在的 `unit_cache.get(tag)`。

### 关键 Bug：`order.ability` 的类型

python-sc2 中 `unit.orders` 返回 `UnitOrder`，其 `ability` 字段是 **`AbilityData`**，不是 **`AbilityId`**。

```python
# 错误 — 永远为 False，导致所有 pending 超时丢弃，sequence 为空
if order.ability == ability_id:

# 正确
if order.ability.id == ability_id:
```

这是 smoke 测试出现 `sequence_count: 0` 的根因。

### 变形类去重（record 阶段）

对 `UPGRADETOORBITAL_ORBITALCOMMAND` 等变形能力：若 actor 的 `orders` 里**已经有**该 ability，说明升级已在进行中，不再新建 pending，避免重复记录。

### 启动顺序问题

`record()` 会在 `on_before_start`（训练第一个 SCV）时被调用，此时 manager 可能尚未 `start()`，`self.knowledge` 不存在。对 `issued_iteration` 使用：

```python
getattr(getattr(self, "knowledge", None), "iteration", 0)
```

### 超时策略

`PENDING_EXPIRE_SECONDS = 8.0`：8 秒内未检测到落地则丢弃。未落地的 pending **不会**进入 `sequence`，也不会进入 `other_abilities`。

---

## 三、附属建筑（TechLab / Reactor）命名

### 问题背景

SC2 API 中挂 TechLab / Reactor 的能力名是通用的 `BUILD_TECHLAB`、`BUILD_REACTOR`，但 `data_ref` 里按宿主建筑拆分为：

- `BUILD_TECHLAB_BARRACKS` / `BUILD_TECHLAB_FACTORY` / `BUILD_TECHLAB_STARPORT`
- `BUILD_REACTOR_BARRACKS` / `BUILD_REACTOR_FACTORY` / `BUILD_REACTOR_STARPORT`

若直接记录 `BUILD_TECHLAB`，无法与知识图谱对齐，也无法区分挂在哪类建筑上。

### SC2 命令结构

挂 addon 时：

- **`action.unit`**：宿主建筑（Barracks / Factory / Starport，含飞行态 `*FLYING`）
- **`action.target`**：常为 `None` 或 `Point2`，**不是**宿主 Unit

因此不能依赖 `target` 推断后缀，应使用 `action.unit.type_id`。

### 实现（`data_ref_loader.py`）

```python
ADDON_ABILITY_HOSTS = {
    "BUILD_TECHLAB": {
        "BARRACKS": "BUILD_TECHLAB_BARRACKS",
        "BARRACKSFLYING": "BUILD_TECHLAB_BARRACKS",
        ...
    },
    "BUILD_REACTOR": { ... },
}
```

`resolve_recorded_ability_name(ability_name, target)` 根据宿主 `type_id.name` 返回带后缀的名称。

`ability_recorder.record()` 中：当 `ability_name in ("BUILD_TECHLAB", "BUILD_REACTOR")` 且 `target` 无 `type_id` 时，用 `action.unit` 作为解析目标。

### 落地检测补充

addon 的 `_is_committed` 中，若 `target` 不是 Unit，则把 **actor（宿主建筑）** 当作 host，检查：

1. host 的 `orders` 含该 ability
2. host.`add_on_tag` 存在且对应 addon 正在建造（`not addon.is_ready`）

---

## 四、与 `other_abilities` 的边界

`should_record_in_sequence()` 为真的动作（Build / Train / Research / 允许的 Morph 等）走 pending → commit → `sequence`。

其余（`ATTACK`、`EFFECT_REPAIR`、`MOVE_MOVE`、法术等）只记入 `other_abilities` 集合，**不含**逐步 obs，也不进 `order_list`。

---

## 五、调试清单

| 现象 | 可能原因 |
|------|----------|
| `sequence_count: 0`，`other_abilities` 有内容 | `order.ability == ability_id` 类型比较错误 |
| `BUILD_TECHLAB` 无后缀 | 未用 `action.unit` 解析宿主 |
| `UPGRADETOORBITAL` 重复很多条 | 未做 pending，或变形去重未生效 |
| 启动即崩溃 `no attribute knowledge` | `record()` 在 manager `start()` 之前被调用 |
| addon 从不 commit | `_is_committed` 只查了 `target` 未查 actor |

### 快速验证

```bash
python tools/collect_terran_bo.py \
  --output bo_collection_runs/smoke_test \
  --map KairosJunctionLE \
  --bots banshee \
  --workers 1
```

检查输出 JSON：

- `sequence_count` > 0
- `order_list` 含 `BUILD_TECHLAB_BARRACKS` 等带后缀名称
- `UPGRADETOORBITAL_ORBITALCOMMAND` 次数 ≈ 基地数量（通常 1～2），而非十几条

---

## 六、版本说明

| 采集目录 | 记录逻辑 | 是否可用 |
|----------|----------|----------|
| `*_research_v4` | 尝试即记录，`BUILD_TECHLAB` 无后缀 | 否 |
| `*_addonfix_smoke_*` | pending 有 bug，sequence 为空 | 否 |
| `*_commitfix_v5` | 落地 commit + addon 后缀 | **是（正式数据）** |
