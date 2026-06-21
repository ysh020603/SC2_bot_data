# SC2_bot_data 仓库 Git 管理与结构说明

本文档总结本仓库当前的 Git 配置、目录归属、子模块策略及日常操作方式，便于协作与换机部署。

---

## 一、仓库概览

| 项目 | 内容 |
|------|------|
| **远程仓库** | `git@github.com:ysh020603/SC2_bot_data.git` |
| **默认远程名** | `origin`（fetch / push 均走 SSH） |
| **主分支** | `main` |
| **额外 remote** | `agent260510` → `git@github.com:ysh020603/SC2-Agent-260510.git`（指向 Agent 子仓库，可选） |

GitHub 上 clone 地址示例：

```bash
git clone git@github.com:ysh020603/SC2_bot_data.git
cd SC2_bot_data
git submodule update --init --recursive
```

---

## 二、目录与 Git 归属

顶层目录按「如何被 Git 管理」可分为三类：

### 1. 本仓库直接管理的代码与数据

以下内容随 `SC2_bot_data` 的 commit 一起提交、一起 push：

| 目录 / 文件 | 说明 |
|-------------|------|
| `sharpy/` | 本仓库内的 sharpy 框架与扩展（Ability 记录器等） |
| `dummies/` | Terran dummy bot 定义 |
| `tools/` | 批量采集脚本等工具 |
| `data_ref/` | 能力/实体标准命名与技术图 |
| `obs_system/` | LLM 观测与 Executor 相关代码摘录 |
| `bo_collection_runs/` | BO 轨迹采集运行结果 |
| `bo_2_nlstep/` | Build Order → 自然语言 Step 流水线（工具、数据集、序列等） |
| `docs/` | 项目文档 |
| `python-sc2/` | **已 vendored 进本仓库** 的 python-sc2 源码（见下文说明） |
| `jsonpickle/`、`sc2pathlib/`、`libs/`、`maps/`、`bot_loader/`、`test/` | 依赖与辅助代码 |

### 2. Git Submodule（子模块）

| 路径 | 远程 | 跟踪分支 | 当前版本（示例） |
|------|------|----------|------------------|
| `SC2-Agent-260510/` | `git@github.com:ysh020603/SC2-Agent-260510.git` | `sc2_old` | `e74ffd9` — 更改了一下执行器的 prompt |

外层仓库**不保存**子模块内的完整代码树，只保存一个 **commit 指针**。GitHub 上显示为：

```text
SC2-Agent-260510 @ e74ffd9
```

`.gitmodules` 片段：

```ini
[submodule "SC2-Agent-260510"]
    path = SC2-Agent-260510
    url = git@github.com:ysh020603/SC2-Agent-260510.git
    branch = sc2_old
```

> **注意**：Submodule 绑定的是**固定 commit**，不是实时跟踪分支。`.gitmodules` 里的 `branch = sc2_old` 仅在使用 `--remote` 更新时作为参考分支。

### 3. `python-sc2` 的特殊情况

`.gitmodules` 中仍有一条 `python-sc2` 记录，但当前仓库里 **`python-sc2/` 是普通目录（vendored 源码）**，并非活跃子模块：

- `git ls-tree` 中 `python-sc2` 为 `040000 tree`，不是 submodule 的 `160000 commit`
- 目录内没有独立 `.git`
- `git submodule update` 不会单独拉取 python-sc2

日常维护 python-sc2 时，按本仓库普通文件处理即可。

---

## 三、`.gitignore` 与敏感文件

以下内容**不会**进入 Git，仅保留在本地：

| 规则 | 说明 |
|------|------|
| `*.log` | 全局忽略日志（含 `bo_2_nlstep` 下各 run 的 logs） |
| `__pycache__/`、`*.pyc` | Python 编译缓存 |
| `.env`、`venv/` 等 | 虚拟环境与环境变量文件 |
| `games/`、`Bots/` | 本地游戏/Bot 目录 |
| `tools/build_order.json` | 本地 build order 配置 |
| `bo_2_nlstep/API_config/config.json` | LLM 代理池配置（含 `api_key`） |
| `bo_2_nlstep/api_call/config/provider_config.json` | 提供商 API 配置（含 `api_key`） |

**可安全提交的配置模板：**

- `bo_2_nlstep/api_call/config/provider_config.example.json`

首次使用 `bo_2_nlstep` 时，请从 example 复制为本地 `config.json` / `provider_config.json` 并填入密钥，这些文件不会被提交。

---

## 四、SC2-Agent-260510 子模块管理

### 4.1 设计目标

- 外层仓库引用 Agent 代码的**特定版本**，便于复现与对齐
- 本地可在子模块内改代码做实验，**默认不会误推到 Agent 远程**

### 4.2 防误推配置

子模块内已将 push 地址设为 `DISABLED`：

```text
origin  git@github.com:ysh020603/SC2-Agent-260510.git (fetch)
origin  DISABLED (push)
```

仍可 `fetch` / `pull` 拉取远程更新；在子模块目录执行 `git push` 会失败，从而避免把本地实验改动推到 `SC2-Agent-260510`。

### 4.3 更新子模块到远程最新 `sc2_old`

在外层仓库根目录执行：

```bash
git submodule update --remote SC2-Agent-260510
git add SC2-Agent-260510
git commit -m "Update SC2-Agent-260510 submodule"
git push origin main
```

仅更新本地、暂不推云端时，省略最后一步 `push` 即可。

### 4.4 本地开发而不影响远程

```bash
cd SC2-Agent-260510
git switch -c local-work    # 可选：建本地实验分支
# 修改代码...
git status                  # 改动只存在于本地工作区
```

只要不 `commit` + `push` 到 Agent 仓库，远程 `sc2_old` 不变。若需恢复远程干净版本：

```bash
git switch sc2_old
git pull
```

### 4.5 恢复子模块 push 能力（一般不需要）

若确需向 Agent 仓库推送（需有权限且明确意图）：

```bash
cd SC2-Agent-260510
git remote set-url --push origin git@github.com:ysh020603/SC2-Agent-260510.git
```

---

## 五、常用 Git 操作速查

### 新机器首次拉取

```bash
git clone git@github.com:ysh020603/SC2_bot_data.git
cd SC2_bot_data
git submodule update --init --recursive
```

### 查看子模块当前版本

```bash
git submodule status SC2-Agent-260510
cd SC2-Agent-260510 && git log -1 --oneline
```

### 提交本仓库改动并同步云端（SSH）

```bash
git status
git add <files>
git commit -m "your message"
git push origin main
```

### 查看远程与本地是否一致

```bash
git fetch origin
git status
git log --oneline origin/main..HEAD   # 本地领先远程的 commit
git log --oneline HEAD..origin/main   # 远程领先本地的 commit
```

---

## 六、近期提交脉络（参考）

| Commit | 说明 |
|--------|------|
| `d6395a3` | Initial commit: SC2 bot data and sharpy-sc2 codebase |
| `50433f5` | docs: replace README with action collection intro |
| `245e146` | Add SC2-Agent-260510 sc2_old as submodule |
| `eb091db` | Add bo_2_nlstep pipeline and ignore local API credentials |
| `99f66ef` | Update SC2-Agent-260510 submodule to e74ffd9 |

---

## 七、关系示意

```text
SC2_bot_data (origin/main, SSH)
├── sharpy/                  ← 本仓库源码（采集、记录器等）
├── bo_2_nlstep/             ← 本仓库源码 + 数据（API 密钥本地忽略）
├── bo_collection_runs/      ← 本仓库数据
├── python-sc2/              ← vendored 源码（非活跃 submodule）
└── SC2-Agent-260510/        ← submodule → SC2-Agent-260510 @ sc2_old 某 commit
         │
         └── 独立仓库 git@github.com:ysh020603/SC2-Agent-260510.git
```

---

## 八、注意事项

1. **Submodule 与根目录 `sharpy/` 是两套代码**：根目录 `sharpy/` 为本仓库维护的采集框架；`SC2-Agent-260510/` 为 Agent 执行器子项目，版本由 commit 指针锁定。
2. **更新子模块后必须在外层 commit 一次**，否则他人 clone 仍会得到旧的 `@ commit`。
3. **不要提交 API key 文件**；若曾误提交，需轮换密钥并从历史中清除（本仓库已通过 `.gitignore` 屏蔽上述路径）。
4. **大体积数据**（如 `.SC2Replay`、序列 JSON）已纳入 `bo_2_nlstep/` 等目录一并管理，push 前注意体积与网络时间。

---

*文档随仓库 Git 策略变更而更新；子模块当前版本以 `git submodule status` 为准。*
