"""向后兼容包装：将原有的 LLMBot / MyLLMBot 重定向到 UniversalLLMBot。

原有的 ``LLMBot`` 类硬编码为人族 (Terran)，现已迁移至
``dummies.generic.universal_llm_bot.UniversalLLMBot``（跨种族通用版）。

本文件保留原有类名以兼容：
* ``bot_loader/bot_definitions.py`` 中的注册
* ``dummies/terran/__init__.py`` 中的导出
* 任何外部通过 ``from dummies.terran.llm_bot import LLMBot`` 的引用
"""

from __future__ import annotations

from sc2.data import Race
from dummies.generic.universal_llm_bot import UniversalLLMBot


class LLMBot(UniversalLLMBot):
    """向后兼容的人族 LLM Bot。

    接受原有的 2 参数构造签名 ``(build_name, llm_settings_file)``。
    """

    def __init__(
        self,
        build_name: str = "default",
        llm_settings_file: str = "",
        record_dir: str = "",
    ):
        super().__init__(
            race_name="terran",
            record_dir=record_dir,
        )
        self.build_name = build_name


class MyLLMBot(UniversalLLMBot):
    """使用备用配置的人族 LLM Bot（向后兼容）。"""

    def __init__(self, build_name: str = "default"):
        super().__init__(race_name="terran")
        self.build_name = build_name


class LadderBot(LLMBot):
    @property
    def my_race(self):
        return Race.Terran
