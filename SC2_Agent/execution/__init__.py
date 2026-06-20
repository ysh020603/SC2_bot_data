"""命令式执行层。

* :mod:`mapping`         — DB 标准名 -> AbilityId/UnitTypeId/UpgradeId/sharpy Act
* :mod:`command`         — ``PlannedAction`` 数据结构与状态机
* :mod:`executor_select` — train/morph 候选执行者筛选（规则层）+ 冲突提示
* :mod:`scheduler`       — ``ExecutionScheduler``（每帧驱动整条序列）
"""
