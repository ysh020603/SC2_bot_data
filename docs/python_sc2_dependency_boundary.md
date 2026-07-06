# python-sc2 依赖边界

根仓库和独立 Agent 各自维护一份固定的 `python-sc2` 快照，运行时不得从另一个仓库借用，也不得依赖 conda 环境中碰巧安装的 `burnysc2`。

| 使用方 | 固定运行时来源 | 加载方式 |
|---|---|---|
| 轨迹采集仓库 | `sharpy-sc2/python-sc2` | `tools/collect_terran_bo.py` 使用仓库绝对路径；Linux 批量脚本也设置该目录的 `PYTHONPATH` |
| 独立 Agent | `SC2-Agent-260510/python-sc2` | Agent 根目录的 `sc2_runtime.py` 在任何 Agent/Sharpy 业务导入前加载并校验 |

这两份代码当前来自同一快照，但生命周期独立。更新时应显式同步、记录来源版本并分别运行回归测试，不能改回 `sys.path.insert(..., "python-sc2")` 这种依赖当前工作目录的相对路径。

## 采集端自检

在根仓库执行：

```bash
PYTHONPATH="$PWD/python-sc2" python -c "
import sc2
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.ids.upgrade_id import UpgradeId
print(sc2.__file__)
print(UPGRADE_RESEARCHED_FROM[UpgradeId.RAVENCORVIDREACTOR])
"
```

输出路径必须位于当前根仓库的 `python-sc2/sc2/`。

## Agent 自检

进入 Agent 仓库执行：

```bash
python -c "from sc2_runtime import ensure_bundled_python_sc2; print(ensure_bundled_python_sc2())"
```

输出路径必须位于 `SC2-Agent-260510/python-sc2/sc2/`。详细规则见 Agent 仓库的 `docs/python-sc2-runtime.md`。
