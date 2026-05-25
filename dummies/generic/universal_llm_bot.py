"""Universal LLM Bot — 跨种族三层 Agent 架构 Bot.

架构总览::

    ┌─────────────────────────────────────────────────────────────────────┐
    │ Top Agent  (全局指挥官)                                              │
    │   t=0 → 选择 SKILL/{race}/ 策略                                    │
    ├─────────────────────────────────────────────────────────────────────┤
    │ Mid Agent  (运营执行官)                                              │
    │   每 N 秒 → 结合 obs + Top 上下文 → 自然语言宏观任务列表              │
    ├─────────────────────────────────────────────────────────────────────┤
    │ Down Agent (微操执行官)                                              │
    │   逐条翻译 → {"action": key, "to_count": int}                      │
    ├─────────────────────────────────────────────────────────────────────┤
    │ create_plan() → BuildOrder([                                        │
    │     DynamicBaseTactics(...),     # 补给 + Orbital + 静态战术（并行）   │
    │     ActLLMOngoingTasks(...),     # 动态运营层 (Mid/Down Agent 驱动)  │
    │ ])                                                                  │
    └─────────────────────────────────────────────────────────────────────┘

关键变化（相较于旧 ``dummies/terran/llm_bot.py``）：

* Prompt 全部从 ``SC2_Agent/`` 三个模块导入，Bot 本身不再包含长文本 Prompt。
* 种族通过 ``race_name`` 参数传入，动作空间 / 策略 / 战术均按种族动态加载。
* 新增 Top Agent 层：t=0 策略选择。
* 模型通过 ``model_key`` 从 ``config.json`` 池动态获取。
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import shutil
import time as _wall_time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId

from sharpy.interfaces import IZoneManager
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder
from sharpy.plans.acts import ActBase

from API_Tools.llm_caller import call_openai
from SC2_Agent.top_agent import (
    CUSTOM_STRATEGY_NAME,
    build_initial_strategy_messages,
    build_strategy_generation_messages,
    build_view_followup_user_message,
    find_similar_strategies,
    parse_generated_strategy,
    parse_initial_action,
    parse_top_agent_0_md,
)
from SC2_Agent.mid_agent import (
    build_planning_messages,
    parse_planning_response,
)
from SC2_Agent.down_agent import (
    build_translation_messages,
    parse_translation_response,
)

logger = logging.getLogger("UniversalLLMBot")

_RACE_MAP = {
    "terran": Race.Terran,
    "zerg": Race.Zerg,
    "protoss": Race.Protoss,
    "random": Race.Random,
}


# ======================================================================
# 动态运营层执行器（与原版一致）
# ======================================================================


class ActLLMOngoingTasks(ActBase):
    """每帧扫一遍动作历史，把每个目标态翻译成具体 Sharpy 调用。"""

    def __init__(self, active_tasks_ref: List[Dict[str, Any]]):
        super().__init__()
        self.active_tasks = active_tasks_ref

    async def execute(self) -> bool:
        for task in self.active_tasks:
            if task.get("_disabled", False):
                continue

            action_key = task.get("action")
            try:
                to_count = int(task.get("to_count", 1))
            except (TypeError, ValueError):
                logger.warning("Invalid to_count in action history item %s; disabling.", task)
                task["_disabled"] = True
                task["_error"] = "invalid_to_count"
                continue

            if not action_key:
                task["_disabled"] = True
                task["_error"] = "missing_action"
                continue

            act: Optional[ActBase] = task.get("_act")
            if act is None:
                get_action_fn = task.get("_get_action_fn")
                if get_action_fn is None:
                    task["_disabled"] = True
                    task["_error"] = "no_action_resolver"
                    continue
                try:
                    act = get_action_fn(action_key, to_count)
                except Exception as exc:
                    logger.warning(
                        "Failed to instantiate act for action=%s to_count=%s: %s",
                        action_key, to_count, exc,
                    )
                    task["_disabled"] = True
                    task["_error"] = f"instantiate_failed: {exc}"
                    continue
                task["_act"] = act
                task["_started"] = False

            if not task.get("_started", False):
                try:
                    await self.start_component(act, self.knowledge)
                except Exception as exc:
                    logger.warning("Failed to start act for action=%s: %s", action_key, exc)
                    task["_disabled"] = True
                    task["_error"] = f"start_failed: {exc}"
                    continue
                task["_started"] = True

            try:
                await act.execute()
            except Exception as exc:
                logger.warning("Act execute failed for action=%s: %s", action_key, exc)

        return True


# ======================================================================
# 默认空战术（当无策略可加载时的兜底）
# ======================================================================


class EmptyTactics(BuildOrder):
    """种族无关的最小兜底战术列表（并行执行）——当 SKILL 目录无对应策略时使用。

    只包含跨种族通用的 tactics primitive；任何种族专属逻辑（如 Terran 的 Repair
    / ContinueBuilding / CallMule）都不能放在这里，否则会在 Zerg/Protoss 上 crash。
    """

    def __init__(self):
        from sharpy.plans.tactics import (
            PlanZoneDefense, PlanZoneAttack, PlanFinishEnemy, DistributeWorkers,
        )
        super().__init__([
            PlanZoneDefense(),
            DistributeWorkers(),
            PlanZoneAttack(40),
            PlanFinishEnemy(),
        ])


# ======================================================================
# UniversalLLMBot 主类
# ======================================================================


class UniversalLLMBot(KnowledgeBot):
    """跨种族、三层 Agent 架构的通用 LLM Bot。"""

    MID_AGENT_POLL_INTERVAL: float = 12.0
    #: t=0 多轮交互式策略选择的最大轮次，防止 LLM 死循环 VIEW。
    TOP_AGENT_INITIAL_MAX_TURNS: int = 5
    #: t=0 相似策略检索的 Top-K（用于 GENERATE 模式的参考样本）。
    STRATEGY_GENERATION_TOPK: int = 3

    zone_manager: IZoneManager

    def __init__(
        self,
        race_name: str = "terran",
        instruct: str = "",
        top_model_key: str = "",
        mid_model_key: str = "",
        down_model_key: str = "",
        record_dir: str = "",
        *,
        force_strategy: Optional[str] = None,
    ):
        super().__init__("Universal LLM Bot")
        self.race_name = race_name.strip().lower()
        self.instruct = instruct.strip()
        self.top_model_key = top_model_key.strip()
        self.mid_model_key = mid_model_key.strip()
        self.down_model_key = down_model_key.strip()
        self.record_dir = record_dir.strip()
        force = (force_strategy or "").strip()
        self.force_strategy: Optional[str] = force if force and force.lower() != "none" else None

        # --- Top Agent 状态 ---
        self.selected_strategy: Optional[str] = None
        self.strategy_description: str = ""
        self._top_agent_initialized: bool = False

        # --- Mid/Down Agent 状态 ---
        self.active_tasks: List[Dict[str, Any]] = []
        self.current_natural_tasks: List[str] = []
        self._last_mid_agent_time: float = -self.MID_AGENT_POLL_INTERVAL

        # --- 种族动态模块 ---
        self._get_action_fn: Optional[Callable] = None
        self._get_action_space_fn: Optional[Callable] = None
        self._action_space_cache: Optional[Dict[str, str]] = None
        # 形如 ``{name: {"summary": str, "detail": str}}``。
        self._available_strategies: Dict[str, Dict[str, str]] = {}

        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

    # ------------------------------------------------------------------
    # 种族动态加载
    # ------------------------------------------------------------------

    def _load_race_action_module(self) -> None:
        """动态加载 ``SKILL.{race}.Action`` 模块。"""
        module_path = f"SKILL.{self.race_name}.Action"
        try:
            mod = importlib.import_module(module_path)
            self._get_action_fn = getattr(mod, "get_action", None)
            self._get_action_space_fn = getattr(mod, "get_action_space", None)
        except ImportError:
            logger.warning("Action module %s not found; action space will be empty.", module_path)
            self._get_action_fn = None
            self._get_action_space_fn = None

        self._action_space_cache = (
            self._get_action_space_fn() if self._get_action_space_fn else {}
        )

    # ------------------------------------------------------------------
    # SKILL 目录工具
    # ------------------------------------------------------------------

    @property
    def _skill_root(self) -> str:
        """``SKILL/`` 根目录绝对路径。"""
        return os.path.normpath(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                os.pardir, os.pardir,
                "SKILL",
            )
        )

    @property
    def _skill_race_dir(self) -> str:
        """``SKILL/{race}/`` 绝对路径。"""
        return os.path.normpath(os.path.join(self._skill_root, self.race_name))

    # ------------------------------------------------------------------
    # SKILL 注册表 (registry.json)
    # ------------------------------------------------------------------

    @property
    def _registry_path(self) -> str:
        """``SKILL/{race}/registry.json`` 绝对路径。"""
        return os.path.join(self._skill_race_dir, "registry.json")

    def _load_registry(self) -> Optional[List[str]]:
        """读取 ``registry.json`` 中的 ``registered_strategies`` 白名单。

        :return: 已注册策略名列表；若文件不存在/损坏则返回 ``None``（表示"无注册表"）。
        """
        path = self._registry_path
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Failed to read registry %s: %s", path, exc)
            return None
        names = data.get("registered_strategies")
        if not isinstance(names, list):
            return None
        return [n for n in names if isinstance(n, str) and n.strip()]

    def _register_strategy(self, name: str) -> None:
        """把新生成的策略名追加进 ``registry.json``。

        若注册表文件不存在则自动创建。已存在的名称会被去重。
        """
        path = self._registry_path
        existing: List[str] = []
        data: Dict[str, Any] = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("registered_strategies")
                if isinstance(raw, list):
                    existing = [n for n in raw if isinstance(n, str) and n.strip()]
            except Exception as exc:
                logger.warning("Failed to update registry %s: %s", path, exc)
                data = {}

        if name in existing:
            return
        existing.append(name)
        data["registered_strategies"] = existing
        data.setdefault(
            "_comment",
            "Whitelist of Terran strategy folders exposed to the T=0 LLM selection list. "
            "Auto-updated by UniversalLLMBot._materialise_strategy_folder().",
        )
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            self._llm_infer_emit(
                f"    [registry] auto-registered new strategy '{name}' -> {path}"
            )
        except Exception as exc:
            logger.warning("Failed to write registry %s: %s", path, exc)

    def _discover_strategies(self) -> Dict[str, Dict[str, str]]:
        """遍历 ``SKILL/{race}/`` 目录，发现所有 ``Top_agent_0.md`` 策略并解析。

        **注册表过滤**：若同目录下存在 ``registry.json``，则仅返回其
        ``registered_strategies`` 列表中的策略，未列入的目录即使物理存在也被忽略。
        若注册表缺失/损坏，回退到旧行为（遍历所有含 ``Top_agent_0.md`` 的目录）。

        :return: ``{策略名: {"summary": str, "detail": str}}``。
                 不包含 ``generic/`` 目录（它仅为兜底指导文件载体）。
        """
        skill_dir = self._skill_race_dir
        strategies: Dict[str, Dict[str, str]] = {}
        if not os.path.isdir(skill_dir):
            logger.info("SKILL directory %s not found.", skill_dir)
            return strategies

        registry = self._load_registry()
        if registry is None:
            self._llm_infer_emit(
                "    [registry] %s not found; falling back to filesystem scan.",
                self._registry_path,
            )
            candidates = [
                entry for entry in sorted(os.listdir(skill_dir))
                if entry != "generic"
                and os.path.isdir(os.path.join(skill_dir, entry))
            ]
        else:
            self._llm_infer_emit(
                "    [registry] loaded %d registered strategies: %s",
                len(registry), registry,
            )
            candidates = registry

        for entry in candidates:
            if entry == "generic":
                continue
            entry_path = os.path.join(skill_dir, entry)
            if not os.path.isdir(entry_path):
                self._llm_infer_emit(
                    "    [registry] WARNING: registered strategy '%s' has no folder; skipped.",
                    entry,
                )
                continue
            top0_path = os.path.join(entry_path, "Top_agent_0.md")
            if not os.path.isfile(top0_path):
                self._llm_infer_emit(
                    "    [registry] WARNING: '%s' missing Top_agent_0.md; skipped.",
                    entry,
                )
                continue
            try:
                with open(top0_path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as exc:
                logger.warning("Failed to read %s: %s", top0_path, exc)
                continue
            parsed = parse_top_agent_0_md(raw)
            strategies[entry] = {
                "summary": parsed["summary"] or "(no summary)",
                "detail": parsed["detail"] or raw.strip(),
            }
        return strategies

    # ------------------------------------------------------------------
    # 动态战术加载（importlib）
    # ------------------------------------------------------------------

    def _load_dynamic_tactics(self) -> BuildOrder:
        """根据 ``selected_strategy`` 动态 import 对应的 base_tactics 模块。

        约定：``SKILL.{race}.{strategy}.base_tactics`` 模块中，
        第一个继承 ``BuildOrder`` 的战术类（非 ``BuildOrder`` 基类本身）即为战术执行器。
        若策略文件夹不存在（例如旧版的 ``CUSTOM_STRATEGY_NAME`` 占位），直接使用兜底 EmptyTactics。
        """
        if not self.selected_strategy:
            logger.info("No strategy selected; using EmptyTactics.")
            return EmptyTactics()

        if self.selected_strategy == CUSTOM_STRATEGY_NAME:
            logger.info("Custom-generated strategy has no base_tactics; using EmptyTactics.")
            return EmptyTactics()

        module_path = f"SKILL.{self.race_name}.{self.selected_strategy}.base_tactics"
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            logger.warning(
                "Cannot import tactics module %s; falling back to EmptyTactics.",
                module_path,
            )
            return EmptyTactics()

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BuildOrder)
                and attr is not BuildOrder
            ):
                try:
                    return attr()
                except TypeError:
                    try:
                        return attr(20)
                    except Exception as exc:
                        logger.warning("Failed to instantiate %s: %s", attr_name, exc)

        logger.warning("No BuildOrder tactics subclass found in %s.", module_path)
        return EmptyTactics()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_start(self):
        # ★ 关键：种族模块加载 + Top Agent t=0 策略选择必须在 super().on_start() 之前完成。
        # 因为 super().on_start() → knowledge.start() → ActManager.post_start() → create_plan()，
        # 而 create_plan() 需要 self.selected_strategy 才能动态加载对应的 base_tactics。
        self._load_race_action_module()
        self._available_strategies = self._discover_strategies()

        # 模块三：``--force_strategy`` 优先级最高，直接绕过 t=0 LLM 选择/生成逻辑。
        if self.force_strategy:
            self._apply_forced_strategy(self.force_strategy)
        else:
            self._run_top_agent_initial_blocking()

        self._top_agent_initialized = True

        await super().on_start()

        self.zone_manager = self.knowledge.get_required_manager(IZoneManager)
        self.llm_observation_recorder.interval_seconds = self.MID_AGENT_POLL_INTERVAL
        if self.record_dir:
            self.llm_observation_recorder.output_folder = self.record_dir

    async def pre_step_execute(self):
        """每帧 Tick 入口：检查 Mid Agent 的触发条件。"""
        # --- Mid Agent: N 秒轮询 ---
        if self.time - self._last_mid_agent_time >= self.MID_AGENT_POLL_INTERVAL:
            self._last_mid_agent_time = self.time
            self._run_mid_agent_pipeline_blocking(trigger_reason="poll")

    # ------------------------------------------------------------------
    # Top Agent
    # ------------------------------------------------------------------

    def _run_top_agent_initial_blocking(self) -> None:
        """t=0 多轮交互式策略选择（SELECT / VIEW / GENERATE，同步阻塞）。

        除日志外，会把完整流程作为一条 ``trigger_reason="top_agent_initial_t0"`` 的
        记录追加进 ``llm_observation_recorder.llm_interactions``，便于事后回放。
        """
        pipeline_start = _wall_time.monotonic()
        self._llm_infer_emit(">>> TOP AGENT: t=0 interactive strategy selection START")

        # 即便没有任何预定义策略，依旧允许 LLM GENERATE 一套自定义策略。
        if not self._available_strategies:
            self._llm_infer_emit(
                "    No predefined strategies in SKILL/%s/; LLM will be asked to GENERATE.",
                self.race_name,
            )

        summaries: Dict[str, str] = {
            name: info.get("summary", "") for name, info in self._available_strategies.items()
        }
        self._llm_infer_emit(
            f"    Top Agent t=0 inputs: race={self.race_name!r}, "
            f"instruct={self.instruct!r}, "
            f"available_strategies={list(summaries.keys())}"
        )

        messages = build_initial_strategy_messages(
            race=self.race_name,
            instruct=self.instruct,
            strategy_summaries=summaries,
        )

        valid_names = list(self._available_strategies.keys())
        viewed: List[str] = []
        max_turns = self.TOP_AGENT_INITIAL_MAX_TURNS

        turn_records: List[Dict[str, Any]] = []
        final_action: str = "UNKNOWN"
        final_error: Optional[str] = None

        for turn in range(1, max_turns + 1):
            turn_log: Dict[str, Any] = {
                "turn": turn,
                "messages_sent": [dict(m) for m in messages],
                "raw_response": "",
                "parsed_action": None,
                "wall_elapsed_seconds": None,
                "note": "",
            }
            self._llm_infer_emit(
                f"    >>> TOP AGENT t=0 turn {turn}/{max_turns} REQUEST "
                f"({len(messages)} messages)"
            )

            t_start = _wall_time.monotonic()
            try:
                raw = self._call_llm(messages, agent="top")
            except Exception as exc:
                turn_log["wall_elapsed_seconds"] = round(_wall_time.monotonic() - t_start, 3)
                turn_log["note"] = f"llm_call_exception: {exc!r}"
                turn_records.append(turn_log)
                logger.warning("[TopAgent] LLM call failed (turn %d): %s", turn, exc)
                self._llm_infer_emit(f"    Top Agent t=0 turn {turn} EXCEPTION: {exc!r}")
                final_error = repr(exc)
                final_action = "EXCEPTION"
                break
            turn_log["wall_elapsed_seconds"] = round(_wall_time.monotonic() - t_start, 3)
            turn_log["raw_response"] = raw

            if not raw:
                turn_log["note"] = "empty_response"
                turn_records.append(turn_log)
                self._llm_infer_emit(f"    Top Agent t=0 turn {turn} returned EMPTY.")
                final_action = "EMPTY"
                break

            self._llm_infer_emit(
                f"    <<< TOP AGENT t=0 turn {turn} RESPONSE "
                f"({turn_log['wall_elapsed_seconds']:.2f}s, {len(raw)} chars): "
                f"{raw.strip()!r}"
            )
            action = parse_initial_action(raw)
            turn_log["parsed_action"] = action
            if action is None:
                turn_log["note"] = "invalid_action_json"
                turn_records.append(turn_log)
                self._llm_infer_emit(
                    f"    Top Agent turn {turn} produced invalid action JSON; aborting loop."
                )
                final_action = "INVALID_JSON"
                break

            self._llm_infer_emit(f"    Top Agent turn {turn} parsed action: {action}")
            # 把 LLM 这一轮的原始输出作为 assistant 消息追加进历史。
            messages.append({"role": "assistant", "content": raw})

            if action["action"] == "SELECT":
                resolved = self._resolve_strategy_name(action["strategy"], valid_names)
                if resolved is None:
                    turn_log["note"] = f"select_unknown:{action['strategy']!r}"
                    turn_records.append(turn_log)
                    self._llm_infer_emit(
                        f"    SELECT target {action['strategy']!r} not in available list; "
                        f"re-prompting LLM."
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your SELECT target '{action['strategy']}' is not in the available list. "
                            "Please choose strictly from the listed strategy names, or VIEW more "
                            "detail, or GENERATE a custom strategy."
                        ),
                    })
                    continue
                self.selected_strategy = resolved
                self.strategy_description = self._available_strategies[resolved]["detail"]
                turn_log["note"] = f"selected:{resolved}"
                turn_records.append(turn_log)
                self._llm_infer_emit(
                    f"    Top Agent SELECTED existing strategy: '{resolved}' (turn {turn})"
                )
                final_action = "SELECT"
                break

            if action["action"] == "VIEW":
                requested = action["strategies"]
                view_details: Dict[str, str] = {}
                unknown: List[str] = []
                for name in requested:
                    resolved = self._resolve_strategy_name(name, valid_names)
                    if resolved is None:
                        unknown.append(name)
                        continue
                    view_details[resolved] = self._available_strategies[resolved]["detail"]
                    if resolved not in viewed:
                        viewed.append(resolved)

                self._llm_infer_emit(
                    f"    Top Agent VIEW requested {requested!r}; "
                    f"resolved={list(view_details.keys())}, unknown={unknown}"
                )
                followup = build_view_followup_user_message(view_details)
                if unknown:
                    followup["content"] = (
                        f"WARNING: these requested names were not found and ignored: {unknown}.\n\n"
                        + followup["content"]
                    )
                messages.append(followup)
                turn_log["note"] = (
                    f"viewed:{list(view_details.keys())}; unknown:{unknown}"
                )
                turn_records.append(turn_log)
                continue

            if action["action"] == "GENERATE":
                generated_text = action["strategy"].strip()
                if not generated_text:
                    turn_log["note"] = "generate_empty"
                    turn_records.append(turn_log)
                    self._llm_infer_emit("    GENERATE returned empty strategy text; aborting.")
                    final_action = "GENERATE_EMPTY"
                    break

                # 模块一：要求 LLM 输出标准化对象（Strategy_Name + Strategy_Description）
                # 并将其作为新策略持久化到 ``SKILL/{race}/{name}/``。
                persisted = self._persist_generated_strategy(
                    initial_freeform_text=generated_text,
                )
                turn_log["persisted_strategy"] = persisted
                if persisted is None:
                    # 持久化失败 → 退化为旧版“匿名占位策略”。
                    self.selected_strategy = CUSTOM_STRATEGY_NAME
                    self.strategy_description = generated_text
                    turn_log["note"] = (
                        f"generated_fallback_custom:{len(generated_text)}chars"
                    )
                    turn_records.append(turn_log)
                    self._llm_infer_emit(
                        f"    Top Agent GENERATED a custom strategy ({len(generated_text)} chars, "
                        f"turn {turn}); persistence failed, marked as '{CUSTOM_STRATEGY_NAME}'."
                    )
                else:
                    self.selected_strategy = persisted["name"]
                    self.strategy_description = persisted["description"]
                    # 持久化成功的新策略加入 discovery 缓存，方便后续日志查询。
                    self._available_strategies[persisted["name"]] = {
                        "summary": persisted["description"].split("\n\n")[0][:500],
                        "detail": persisted["description"],
                    }
                    turn_log["note"] = (
                        f"generated_persisted:{persisted['name']}"
                    )
                    turn_records.append(turn_log)
                    self._llm_infer_emit(
                        f"    Top Agent GENERATED & PERSISTED a new strategy "
                        f"'{persisted['name']}' (turn {turn}) → "
                        f"{persisted['folder']}"
                    )
                final_action = "GENERATE"
                break

            # 未知 action（理论上 parse_initial_action 已过滤）
            turn_log["note"] = f"unknown_action:{action['action']!r}"
            turn_records.append(turn_log)
            self._llm_infer_emit(f"    Unknown action {action['action']!r}; aborting loop.")
            final_action = "UNKNOWN_ACTION"
            break
        else:
            # for-else: 5 轮全部跑完仍未 break -> 视为超过最大轮次
            final_action = "MAX_TURNS_EXCEEDED"
            self._llm_infer_emit(
                f"    Top Agent t=0 reached max turns ({max_turns}) without decision."
            )

        # —— 未成功决策：fallback ——
        fallback_used: Optional[str] = None
        if not self.selected_strategy:
            fallback = valid_names[0] if valid_names else None
            if fallback is not None:
                self.selected_strategy = fallback
                self.strategy_description = self._available_strategies[fallback]["detail"]
                fallback_used = fallback
                self._llm_infer_emit(
                    f"    Top Agent loop exhausted; fallback to first strategy '{fallback}'."
                )
            else:
                self._llm_infer_emit(
                    "    Top Agent loop exhausted with no available strategies; "
                    "will run with EmptyTactics."
                )

        total_elapsed = _wall_time.monotonic() - pipeline_start
        self._llm_infer_emit(
            f"<<< TOP AGENT: t=0 END "
            f"(final_action={final_action}, "
            f"selected_strategy={self.selected_strategy!r}, "
            f"turns={len(turn_records)}, "
            f"wall={total_elapsed:.2f}s)"
        )

        # —— 追加 JSON 记录 ——
        self._record_llm_interaction({
            "game_time": 0.0,
            "trigger_reason": "top_agent_initial_t0",
            "wall_elapsed_seconds": round(total_elapsed, 3),
            "top_agent_initial": {
                "race": self.race_name,
                "instruct": self.instruct,
                "available_strategies": valid_names,
                "strategy_summaries": summaries,
                "max_turns": max_turns,
                "turns": turn_records,
                "viewed_strategies": viewed,
                "final_action": final_action,
                "fallback_used": fallback_used,
                "selected_strategy": self.selected_strategy,
                "strategy_description": self.strategy_description,
                "error": final_error,
            },
        })

    @staticmethod
    def _resolve_strategy_name(name: str, valid_names: List[str]) -> Optional[str]:
        """大小写不敏感地把 LLM 给出的策略名映射到合法名（找不到返回 None）。"""
        if not name:
            return None
        if name in valid_names:
            return name
        lower_map = {n.lower(): n for n in valid_names}
        return lower_map.get(name.lower())

    # ------------------------------------------------------------------
    # 模块一：T=0 动态策略生成与持久化
    # ------------------------------------------------------------------

    def _persist_generated_strategy(
        self,
        *,
        initial_freeform_text: str = "",
    ) -> Optional[Dict[str, Any]]:
        """让 LLM 输出 ``Strategy_Name + Strategy_Description`` 并落盘。

        流程：
        1. 用玩家指令 + 上一轮 freeform 草稿 + 开局观测构造查询，从 ``self._available_strategies``
           中按相似度（Jaccard）取 ``STRATEGY_GENERATION_TOPK`` 个参考策略。
        2. 调 LLM ``build_strategy_generation_messages``，要求标准化 JSON 输出。
        3. 解析 + 校验名称（``parse_generated_strategy`` 会自动避免重名）。
        4. 创建 ``SKILL/{race}/{name}/`` 目录，写入 ``Top_agent_0.md``，并把
           ``SKILL/{race}/generic/base_tactics.py`` 拷贝过去（硬编码指令）。

        :return: ``{"name": str, "description": str, "folder": str}`` 或 ``None``（失败）。
        """
        # 1) 检索相似策略
        query_parts: List[str] = [self.instruct]
        if initial_freeform_text:
            # 取前 600 字作为相似度查询的浓缩语义。
            query_parts.append(initial_freeform_text[:600])
        try:
            opening_obs = self._capture_observation_text()
        except Exception:
            opening_obs = ""
        if opening_obs:
            query_parts.append(opening_obs[:400])
        query_text = "\n".join(p for p in query_parts if p)

        similar = find_similar_strategies(
            query_text=query_text,
            available_strategies=self._available_strategies,
            top_k=self.STRATEGY_GENERATION_TOPK,
        )
        self._llm_infer_emit(
            "    [Strategy GENERATE] retrieved %d similar strategies: %s",
            len(similar),
            list(similar.keys()),
        )

        existing_names = list(self._available_strategies.keys())
        messages = build_strategy_generation_messages(
            race=self.race_name,
            instruct=self.instruct,
            similar_strategies=similar,
            existing_strategy_names=existing_names,
            obs_text=opening_obs,
        )

        # 2) 调 LLM
        try:
            raw = self._call_llm(messages, agent="top")
        except Exception as exc:
            self._llm_infer_emit(f"    [Strategy GENERATE] LLM call FAILED: {exc!r}")
            return None
        if not raw:
            self._llm_infer_emit("    [Strategy GENERATE] LLM returned EMPTY response.")
            return None

        # 3) 解析
        parsed = parse_generated_strategy(raw, existing_strategy_names=existing_names)
        if parsed is None:
            self._llm_infer_emit(
                "    [Strategy GENERATE] failed to parse JSON; raw=%r",
                raw[:300],
            )
            return None
        name = parsed["name"]
        description = parsed["description"]

        # 4) 持久化到文件系统
        try:
            folder = self._materialise_strategy_folder(name=name, description=description)
        except Exception as exc:
            self._llm_infer_emit(
                f"    [Strategy GENERATE] persistence FAILED for '{name}': {exc!r}"
            )
            return None

        return {"name": name, "description": description, "folder": folder}

    def _materialise_strategy_folder(self, *, name: str, description: str) -> str:
        """在 ``SKILL/{race}/{name}/`` 下创建目录、写入 MD、拷贝 base_tactics、注册。"""
        race_dir = self._skill_race_dir
        target_dir = os.path.join(race_dir, name)
        os.makedirs(target_dir, exist_ok=True)

        # Top_agent_0.md (英文标题，与 parse_top_agent_0_md 兼容)
        md_path = os.path.join(target_dir, "Top_agent_0.md")
        md_body = (
            "# Summary\n\n"
            f"{description.split(chr(10) + chr(10))[0].strip()[:500]}\n\n"
            "# Details\n\n"
            f"{description.strip()}\n"
        )
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_body)

        # base_tactics.py：硬编码从 generic 拷贝
        src_tactics = os.path.join(race_dir, "generic", "base_tactics.py")
        dst_tactics = os.path.join(target_dir, "base_tactics.py")
        if os.path.isfile(src_tactics):
            shutil.copyfile(src_tactics, dst_tactics)
            self._llm_infer_emit(
                f"    [Strategy GENERATE] copied base_tactics: {src_tactics} -> {dst_tactics}"
            )
        else:
            self._llm_infer_emit(
                f"    [Strategy GENERATE] WARNING: generic base_tactics not found at {src_tactics}; "
                f"strategy '{name}' will fall back to EmptyTactics."
            )

        # 注册到 registry.json，下一次 _discover_strategies 即可被 SELECT/VIEW 看到
        self._register_strategy(name)

        return target_dir

    # ------------------------------------------------------------------
    # 模块三：``--force_strategy`` 跳过 t=0 LLM
    # ------------------------------------------------------------------

    def _apply_forced_strategy(self, name: str) -> None:
        """强制把当前对局的策略锁定为 ``name``（绕过 LLM SELECT/VIEW/GENERATE）。"""
        race_dir = self._skill_race_dir
        target_dir = os.path.join(race_dir, name)
        md_path = os.path.join(target_dir, "Top_agent_0.md")

        if not os.path.isdir(target_dir):
            self._llm_infer_emit(
                f"    [force_strategy] ERROR: folder not found: {target_dir}; "
                f"falling back to regular t=0 selection."
            )
            self._run_top_agent_initial_blocking()
            return

        detail = ""
        if os.path.isfile(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    raw = f.read()
                parsed = parse_top_agent_0_md(raw)
                detail = parsed.get("detail") or raw.strip()
            except Exception as exc:
                self._llm_infer_emit(
                    f"    [force_strategy] failed to read {md_path}: {exc!r}"
                )

        self.selected_strategy = name
        self.strategy_description = detail
        self._llm_infer_emit(
            f">>> TOP AGENT: t=0 BYPASSED by --force_strategy='{name}' "
            f"(description={len(detail)} chars)"
        )

        # 把强制路径也写入 LLM 交互记录，便于后续解析批量结果。
        self._record_llm_interaction({
            "game_time": 0.0,
            "trigger_reason": "top_agent_initial_t0_forced",
            "wall_elapsed_seconds": 0.0,
            "top_agent_initial": {
                "race": self.race_name,
                "instruct": self.instruct,
                "forced_strategy": name,
                "selected_strategy": name,
                "strategy_description": detail,
                "final_action": "FORCED",
            },
        })

    # ------------------------------------------------------------------
    # Mid Agent + Down Agent Pipeline
    # ------------------------------------------------------------------

    def _run_mid_agent_pipeline_blocking(self, *, trigger_reason: str = "unknown") -> None:
        """Mid Agent → Down Agent 双阶段流水线（同步阻塞）。"""
        game_time = self.time
        pipeline_start = _wall_time.monotonic()
        obs_text: str = ""
        obs_snapshot: Optional[Dict[str, Any]] = None
        previous_natural_tasks = list(self.current_natural_tasks)
        mid_agent_text: str = ""
        mid_agent_tasks: List[str] = []
        down_agent_translations: List[Dict[str, Any]] = []
        parsed_tasks: List[Dict[str, Any]] = []
        error_text: Optional[str] = None

        self._llm_infer_emit(
            f">>> MID AGENT START (trigger={trigger_reason}, game_time={game_time:.1f}s, "
            f"active_task_count={len(self.active_tasks)})",
            include_active_tasks=True,
        )

        try:
            obs_text, obs_snapshot = self._capture_observation_bundle()
            self._llm_infer_emit(
                f"observation_at_decision_time (game_time={game_time:.1f}s):\n{obs_text}"
            )

            self._llm_infer_emit("    calling Mid Agent (planning)...")
            s1_start = _wall_time.monotonic()
            mid_agent_text = self._call_mid_agent(
                obs_text,
                previous_natural_tasks,
            )
            s1_elapsed = _wall_time.monotonic() - s1_start

            if not mid_agent_text:
                self._llm_infer_emit(f"    Mid Agent returned EMPTY ({s1_elapsed:.2f}s).")
                return

            self._llm_infer_emit(
                f"    Mid Agent done in {s1_elapsed:.2f}s, {len(mid_agent_text)} chars."
            )
            self._llm_infer_emit(f"    Mid Agent output: {mid_agent_text.strip()!r}")

            parsed_mid_tasks = parse_planning_response(mid_agent_text)
            if parsed_mid_tasks is None:
                error_text = "invalid_mid_agent_json"
                self._llm_infer_emit("    Mid Agent output failed JSON validation.")
                return
            mid_agent_tasks = parsed_mid_tasks
            self._llm_infer_emit(
                f"    Mid Agent parsed {len(mid_agent_tasks)} natural-language tasks."
            )

            # ===================== Down Agent: Translation ================
            for index, natural_task in enumerate(mid_agent_tasks, start=1):
                self._llm_infer_emit(
                    f"    calling Down Agent #{index}/{len(mid_agent_tasks)} "
                    f"for task={natural_task!r}..."
                )
                translation_record: Dict[str, Any] = {
                    "raw": natural_task, "response": "", "parsed": None,
                }
                s2_start = _wall_time.monotonic()
                try:
                    down_text = self._call_down_agent(natural_task, obs_text)
                    translation_record["response"] = down_text
                    s2_elapsed = _wall_time.monotonic() - s2_start

                    if not down_text:
                        translation_record["error"] = "empty_response"
                        self._llm_infer_emit(
                            f"    Down Agent #{index} returned EMPTY ({s2_elapsed:.2f}s)."
                        )
                        continue

                    legal_keys = set((self._action_space_cache or {}).keys())
                    parsed_task = parse_translation_response(down_text, legal_keys)
                    if parsed_task is None:
                        translation_record["error"] = "invalid_json_or_action"
                        self._llm_infer_emit(f"    Down Agent #{index} failed validation.")
                        continue

                    translation_record["parsed"] = self._slim_action(parsed_task)
                    parsed_tasks.append(parsed_task)
                    self._llm_infer_emit(
                        f"    Down Agent #{index} accepted in {s2_elapsed:.2f}s: "
                        f"{translation_record['parsed']}"
                    )
                except Exception as exc:
                    translation_record["error"] = repr(exc)
                    self._llm_infer_emit(
                        f"    Down Agent #{index} EXCEPTION: {exc!r}"
                    )
                finally:
                    down_agent_translations.append(translation_record)

            self._replace_active_tasks(parsed_tasks)
            self.current_natural_tasks = list(mid_agent_tasks)
            self._llm_infer_emit(
                f"    REFRESHED active_tasks with {len(parsed_tasks)} parsed actions."
            )
        except Exception as exc:
            error_text = repr(exc)
            self._llm_infer_emit(f"    EXCEPTION: {exc!r}")
            logger.warning("[UniversalLLMBot] Pipeline failed: %s", exc)
        finally:
            total_elapsed = _wall_time.monotonic() - pipeline_start
            self._record_llm_interaction({
                "game_time": round(game_time, 2),
                "trigger_reason": trigger_reason,
                "wall_elapsed_seconds": round(total_elapsed, 3),
                "top_agent_strategy": self.selected_strategy,
                "observation_at_this_moment": obs_text,
                "observation_structured": obs_snapshot,
                "mid_agent_input_previous_tasks": previous_natural_tasks,
                "mid_agent_raw_response": mid_agent_text,
                "mid_agent_output_new_tasks": mid_agent_tasks,
                "down_agent_translations": down_agent_translations,
                "active_tasks_after_refresh": self._serialise_active_tasks(),
                "error": error_text,
            })
            self._llm_infer_emit(
                f"<<< MID AGENT END (total {total_elapsed:.2f}s wall, "
                f"active_task_count={len(self.active_tasks)})"
            )

    # --- 调用封装 ------------------------------------------------------

    def _call_llm(self, messages: List[Dict[str, str]], agent: str = "mid") -> str:
        """根据 agent 类型选择对应的 model_key 调用 LLM。"""
        key_map = {
            "top": self.top_model_key,
            "mid": self.mid_model_key,
            "down": self.down_model_key,
        }
        model_key = key_map.get(agent, "")

        if not model_key:
            logger.warning(
                "[UniversalLLMBot] No model_key for agent=%r; set top/mid/down_model_key.",
                agent,
            )
            return ""

        return call_openai(messages=messages, model_key=model_key)

    def _call_mid_agent(
        self,
        obs_text: str,
        previous_tasks: List[str],
    ) -> str:
        """构造 Mid Agent Prompt 并调用 LLM。"""
        messages = build_planning_messages(
            race=self.race_name,
            obs_text=obs_text,
            previous_tasks=previous_tasks,
            strategy_description=self.strategy_description,
        )
        return self._call_llm(messages, agent="mid")

    def _call_down_agent(self, task_description: str, obs_text: str) -> str:
        """构造 Down Agent Prompt 并调用 LLM。"""
        messages = build_translation_messages(
            race=self.race_name,
            task_description=task_description,
            obs_text=obs_text,
            action_space=self._action_space_cache or {},
        )
        return self._call_llm(messages, agent="down")

    # --- 观测 ---------------------------------------------------------

    def _capture_observation_bundle(self) -> Tuple[str, Optional[Dict[str, Any]]]:
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return "(LLMObservationRecorder unavailable)", None
        try:
            snapshot = recorder._build_snapshot()
            return recorder._generate_english_text_obs(snapshot), snapshot
        except Exception as exc:
            logger.warning("[UniversalLLMBot] failed to build observation: %s", exc)
            return "(observation unavailable)", None

    def _capture_observation_text(self) -> str:
        text, _ = self._capture_observation_bundle()
        return text

    # --- 任务管理 -----------------------------------------------------

    def _replace_active_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        self.active_tasks.clear()
        for idx, task in enumerate(tasks, start=1):
            self.active_tasks.append({
                "sequence": idx,
                "action": task.get("action"),
                "to_count": task.get("to_count"),
                "_get_action_fn": self._get_action_fn,
            })

    @staticmethod
    def _slim_action(task: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if task is None:
            return None
        slim = {"action": task.get("action"), "to_count": task.get("to_count")}
        if "sequence" in task:
            slim["sequence"] = task.get("sequence")
        return slim

    def _serialise_active_tasks(self) -> List[Dict[str, Any]]:
        tasks: List[Dict[str, Any]] = []
        for idx, task in enumerate(self.active_tasks, start=1):
            item: Dict[str, Any] = {
                "sequence": task.get("sequence", idx),
                "action": task.get("action"),
                "to_count": task.get("to_count"),
            }
            if task.get("_disabled", False):
                item["disabled"] = True
                if task.get("_error"):
                    item["error"] = task.get("_error")
            tasks.append(item)
        return tasks

    def _active_tasks_snapshot(self) -> str:
        tasks = self._serialise_active_tasks()
        return json.dumps(tasks, ensure_ascii=False) if tasks else "[]"

    # --- 日志与记录 ---------------------------------------------------

    def _record_llm_interaction(self, record: Dict[str, Any]) -> None:
        recorder = getattr(self, "llm_observation_recorder", None)
        if recorder is None:
            return
        append_func = getattr(recorder, "record_llm_interaction", None)
        if append_func is None:
            return
        try:
            append_func(record)
        except Exception as exc:
            logger.warning("[UniversalLLMBot] failed to record LLM interaction: %s", exc)

    def _llm_infer_emit(self, message: str, *args, include_active_tasks: bool = False) -> None:
        if args:
            message = message % args
        line = f"[UniversalLLMBot][LLM-INFER] {message}"
        if include_active_tasks:
            line += f" | active_tasks={self._active_tasks_snapshot()}"

        # 优先经由 knowledge.print (loguru, 进 .log 文件) 输出。
        # ★ 关键：在 ``super().on_start()`` 之前（即 Top Agent t=0 流程中），
        #   ``self.knowledge`` 尚未初始化；此时回退到直接写 ``sc2.main.logger``。
        #   游戏 ``.log`` 文件的 loguru sink 用 ``filter="sharpy"`` 筛选，因此我们
        #   通过 ``logger.patch`` 把 record name 改成以 ``sharpy.`` 开头，保证
        #   t=0 多轮交互的日志也能落到同一份 ``.log`` 文件中、格式与游戏中一致。
        try:
            self.knowledge.print(line, stats=False)
            return
        except Exception:
            pass

        try:
            from sc2.main import logger as _loguru_logger

            _loguru_logger.patch(
                lambda r: r.update(name="sharpy.universal_llm_bot")
            ).opt(depth=1).info(line)
        except Exception:
            logger.info("%s", line)

    # ------------------------------------------------------------------
    # Sharpy plan 入口
    # ------------------------------------------------------------------

    async def create_plan(self) -> BuildOrder:
        """组装 BuildOrder：基础战术（并行后台动作）+ LLM 动态运营（并行）。"""
        base_tactics = self._load_dynamic_tactics()

        llm_executor = ActLLMOngoingTasks(
            active_tasks_ref=self.active_tasks,
        )

        return BuildOrder([
            llm_executor,
            base_tactics,
        ])


# ----------------------------------------------------------------------
# Ladder 兼容入口
# ----------------------------------------------------------------------


class LadderBot(UniversalLLMBot):
    @property
    def my_race(self):
        return _RACE_MAP.get(self.race_name, Race.Terran)
