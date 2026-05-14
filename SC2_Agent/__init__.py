"""SC2_Agent 包：三层 Agent Prompt 管理。

将 LLM 的 Prompt 构建逻辑从 Bot 主文件中剥离，按层级拆分：

* ``top_agent``  — 全局指挥官：策略选择 & 阶段评估
* ``mid_agent``  — 运营执行官：宏观任务规划
* ``down_agent`` — 微操执行官：自然语言→动作 JSON 翻译
"""

from .top_agent import (
    build_initial_strategy_messages,
    build_phase_assessment_messages,
    parse_strategy_selection,
    parse_phase_assessment,
)
from .mid_agent import (
    build_planning_messages,
    parse_planning_response,
)
from .down_agent import (
    build_translation_messages,
    parse_translation_response,
)

__all__ = [
    "build_initial_strategy_messages",
    "build_phase_assessment_messages",
    "parse_strategy_selection",
    "parse_phase_assessment",
    "build_planning_messages",
    "parse_planning_response",
    "build_translation_messages",
    "parse_translation_response",
]
