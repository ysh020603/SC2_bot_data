# Windows 环境配置指南

本文说明如何在 Windows 下配置本仓库的 StarCraft II bot 运行环境。示例路径按当前机器约定：

```text
仓库目录: C:\code\SC2_bot_data
conda 环境: SC2_0615
SC2 默认安装目录: C:\Program Files (x86)\StarCraft II
```

## 1. 前置软件

需要先安装：

- Windows 版 StarCraft II
- Anaconda 或 Miniconda
- Git
- PowerShell

建议使用 64 位 Python 3.11。当前仓库推荐 conda 环境名统一为 `SC2_0615`。

## 2. 创建 conda 环境

如果 `conda` 已经在 PATH 中：

```powershell
conda create -n SC2_0615 python=3.11 pip -y
conda activate SC2_0615
```

如果 `conda` 不在 PATH 中，但安装在默认 Anaconda 目录：

```powershell
& 'C:\ProgramData\anaconda3\Scripts\conda.exe' create -n SC2_0615 python=3.11 pip -y
& 'C:\ProgramData\anaconda3\Scripts\conda.exe' activate SC2_0615
```

在某些 Windows 终端里，`conda activate` 可能不可用。可以直接使用环境内 Python：

```powershell
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'
& $py --version
```

## 3. 安装 Python 依赖

进入仓库根目录：

```powershell
cd C:\code\SC2_bot_data
conda activate SC2_0615
```

安装运行依赖：

```powershell
pip install burnysc2==7.1.3 s2clientprotocol mpyq portpicker requests aiohttp `
  numpy scipy scikit-learn opencv-python-headless more-itertools six `
  protobuf==3.20.3 loguru
```

可选：安装测试依赖：

```powershell
pip install "pytest<7.0.0" "pytest-asyncio==0.20.3"
```

说明：

- `burnysc2` 提供 `import sc2`。
- `protobuf==3.20.3` 用于避免 `s2clientprotocol` 与新版 protobuf 不兼容。
- Windows 下仓库内已有 `sc2pathlib/sc2pathlib.py` fallback，不需要额外 pip 安装 `sc2pathlib`。

## 4. 配置 StarCraft II 路径

确认 SC2 安装目录存在：

```powershell
Test-Path 'C:\Program Files (x86)\StarCraft II'
```

把 `SC2PATH` 写入 conda 环境变量：

```powershell
& 'C:\ProgramData\anaconda3\Scripts\conda.exe' env config vars set `
  -n SC2_0615 SC2PATH='C:\Program Files (x86)\StarCraft II'
```

重新打开 PowerShell 后激活环境并检查：

```powershell
conda activate SC2_0615
$env:SC2PATH
Get-ChildItem "$env:SC2PATH\Versions" -Recurse -Filter 'SC2_x64.exe' |
  Select-Object -First 3 FullName
```

如果不想依赖 conda 环境变量，也可以在当前 PowerShell 会话临时设置：

```powershell
$env:SC2PATH = 'C:\Program Files (x86)\StarCraft II'
```

## 5. 安装和检查地图

本仓库常用地图是 `KairosJunctionLE`。地图文件需要在 SC2 的 `Maps` 目录下。

命令行里所有参数都要使用英文 key / 英文地图 ID，不要使用游戏界面显示的中文名。例如地图要写：

```text
KairosJunctionLE
```

不要写：

```text
凯罗斯中转站-天梯版
```

同理，bot、race、difficulty、build 等参数也要写英文，例如 `saferaven`、`terran`、`veryhard`、`macro`。

检查地图：

```powershell
Get-ChildItem "$env:SC2PATH\Maps" -Recurse -Filter '*Kairos*' |
  Select-Object -First 5 FullName
```

也可以用仓库运行入口列出已识别地图：

```powershell
$env:PYTHONUTF8 = '1'
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'
& $py run_custom.py --help
```

输出中的 `Installed maps` 里能看到 `KairosJunctionLE` 即可。

## 6. 设置 UTF-8 输出

Windows PowerShell 遇到中文地图名或日志时，建议设置：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

如果每次都要设置，可以写入自己的 PowerShell profile；如果 profile 因执行策略无法加载，不影响手动设置环境变量。

## 7. 导入自检

在仓库根目录执行：

```powershell
cd C:\code\SC2_bot_data
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'

@'
import sc2
import sc2pathlib
import sharpy
from dummies.terran.safe_tvt_raven import LadderBot
print("IMPORTS OK")
'@ | & $py -
```

看到 `IMPORTS OK` 说明主要依赖可导入。

## 8. 代码编译自检

可以先编译关键入口，确认语法无误：

```powershell
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'
& $py -m py_compile `
  run_custom.py `
  tools\collect_terran_bo.py `
  bot_loader\game_starter.py `
  bot_loader\bot_definitions.py `
  dummies\terran\safe_tvt_raven.py
```

没有输出通常表示编译通过。

## 9. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `No module named 'sc2'` | 确认已在 `SC2_0615` 中安装 `burnysc2==7.1.3` |
| `No module named 'sc2pathlib.sc2pathlib'` | 确认仓库内存在 `sc2pathlib/sc2pathlib.py` |
| 找不到 SC2 | 检查 `$env:SC2PATH` 是否指向 StarCraft II 根目录 |
| 找不到地图 | 检查 `$env:SC2PATH\Maps` 下是否有对应 `.SC2Map` |
| 中文或地图名输出乱码 | 设置 `$env:PYTHONUTF8='1'` 和 `$env:PYTHONIOENCODING='utf-8'` |
| PowerShell profile 报执行策略错误 | 只要命令继续执行即可；也可以临时使用 `powershell -NoProfile` |
| 上一局残留进程 | 用 `Get-Process python,SC2_x64 -ErrorAction SilentlyContinue` 检查 |

## 10. 最小可用检查清单

```powershell
cd C:\code\SC2_bot_data
$env:SC2PATH = 'C:\Program Files (x86)\StarCraft II'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = 'C:\Users\Descfly\.conda\envs\SC2_0615\python.exe'

& $py --version
& $py -c "import sc2, sc2pathlib, sharpy; print('OK')"
Get-ChildItem "$env:SC2PATH\Maps" -Recurse -Filter '*Kairos*' |
  Select-Object -First 3 FullName
```

以上都通过后，就可以按 `docs/windows_run_bots.md` 运行 bot。
