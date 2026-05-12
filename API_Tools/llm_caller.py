"""LLM Caller.

封装对 OpenAI 兼容协议的底层调用，三大职责：

1. **配置加载**：默认从 ``API_config/llm_settings.json`` 读取静态参数（model / base_url / api_key
   等）；可通过 ``load_llm_settings(settings_path=...)`` / ``call_openai(..., settings_path=...)``
   指定其它 JSON（如 ``llm_settings2.json``）。上层只需传业务级开关（``is_reasoning``、``profile``、消息体等）。
2. **多厂商分发**：根据模型名子串命中 ``vendor_dispatch.rules`` 中的规则，把
   ``is_reasoning`` 翻译成各家的私有字段（GLM 的 ``extra_body.thinking.type``，
   Kimi/DeepSeek/Qwen 的 ``extra_body.enable_thinking`` 等）。Fallback 走 OpenAI 兼容
   透传，本地 VLLM 也走这条路径。
3. **输出清洗**：对返回 ``content`` 截掉 ``<think>...</think>``（含未闭合的悬挂
   ``<think>`` 段），让两阶段流水线下游拿到的都是"已经收尾"的纯输出。

模块对外只暴露两个函数：

- ``call_openai(...)`` —— 主入口，签名兼容 OpenAI SDK 的 ``chat.completions.create``，
  同时增加 ``is_reasoning`` / ``profile`` 等业务参数。
- ``load_llm_settings()`` —— 给调试 / 单元测试用的纯加载函数；支持多配置文件按路径分别缓存。

设计取舍：

- 同步实现（基于 ``openai.OpenAI`` 客户端）。上层 Bot 在 asyncio 事件循环里通过
  ``asyncio.to_thread`` 调度本函数，避免阻塞游戏帧。
- 失败回退到字面级降级（返回 ``""``），调用方决定是否重试，**记录器层面绝不抛异常打断
  游戏循环**。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from copy import deepcopy
from typing import Any, Dict, List, Optional

logger = logging.getLogger("API_Tools.llm_caller")

# 默认配置文件路径：相对于本仓库根目录。
_CONFIG_REL_PATH = os.path.join("API_config", "llm_settings.json")

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
# 悬挂 think（如 <think>...</think> 没闭合的开头一段）：从首个 <think> 一直吃到串尾。
_DANGLING_THINK_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)

# 进程级缓存：按「解析后的绝对路径」分文件缓存，避免每次调用都打开 JSON。
_settings_cache: Dict[str, Dict[str, Any]] = {}
_settings_cache_lock = threading.Lock()


def _repo_root() -> str:
    """返回仓库根目录绝对路径（即 sharpy-sc2/）。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _resolve_settings_abs_path(settings_path: Optional[str]) -> str:
    """把 ``settings_path`` 解析为绝对路径。

    * ``None`` / 空串：仓库根下默认 ``API_config/llm_settings.json``。
    * 无目录分隔符的文件名：视为 ``API_config/<文件名>``（便于写 ``llm_settings2.json``）。
    * 含 ``/`` 的相对路径：相对仓库根（如 ``API_config/foo.json``）。
    * 绝对路径：原样规范化后使用。
    """
    root = _repo_root()
    raw = (settings_path or "").strip()
    if not raw:
        rel = _CONFIG_REL_PATH
    else:
        norm = raw.replace("\\", "/")
        if os.path.isabs(raw):
            return os.path.abspath(os.path.normpath(raw))
        if "/" not in norm:
            rel = os.path.join("API_config", norm)
        else:
            rel = norm
    return os.path.abspath(os.path.join(root, rel))


def load_llm_settings(
    force_reload: bool = False,
    settings_path: Optional[str] = None,
) -> Dict[str, Any]:
    """加载 LLM 静态配置 JSON。

    默认文件为 ``API_config/llm_settings.json``；``settings_path`` 非空时按
    :func:`_resolve_settings_abs_path` 规则解析。多路径各自缓存；``force_reload=True``
    时只刷新当前路径对应条目。
    """
    abs_path = _resolve_settings_abs_path(settings_path)

    with _settings_cache_lock:
        if not force_reload and abs_path in _settings_cache:
            return _settings_cache[abs_path]
        if force_reload and abs_path in _settings_cache:
            del _settings_cache[abs_path]

    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        logger.warning("LLM settings not found at %s; using empty defaults.", abs_path)
        data = {"profiles": {}, "vendor_dispatch": {"rules": []}, "post_processing": {}}
    except Exception as exc:  # JSON 损坏等
        logger.warning("Failed to parse LLM settings %s (%s); using empty defaults.", abs_path, exc)
        data = {"profiles": {}, "vendor_dispatch": {"rules": []}, "post_processing": {}}

    with _settings_cache_lock:
        _settings_cache[abs_path] = data
    return data


# ----------------------------------------------------------------------
# Vendor dispatch
# ----------------------------------------------------------------------


def _set_nested(container: Dict[str, Any], path: List[str], value: Any) -> None:
    """按路径写入嵌套 dict。中间节点若缺失自动创建。"""
    cursor = container
    for key in path[:-1]:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[key] = nxt
        cursor = nxt
    cursor[path[-1]] = value


def _apply_vendor_dispatch(
    model: str,
    is_reasoning: bool,
    extra_body: Dict[str, Any],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    """根据 ``model`` 命中规则，把 ``is_reasoning`` 注入到对应字段。

    返回更新后的 ``extra_body``（深拷贝结果，原对象不变）。命中不到任何规则时
    保留原样透传——这是 OpenAI 官方模型、本地 VLLM / xinference 等通用入口的
    正确行为。
    """
    extra_body = deepcopy(extra_body) if extra_body else {}
    rules = (settings.get("vendor_dispatch") or {}).get("rules") or []

    model_lower = (model or "").lower()
    for rule in rules:
        keywords = rule.get("match_keywords") or []
        if not any(kw.lower() in model_lower for kw in keywords):
            continue

        path = rule.get("reasoning_field_path")
        if not path:
            continue

        if is_reasoning:
            value = rule.get("enabled_value", True)
        else:
            value = rule.get("disabled_value", False)

        # 路径是相对 kwargs 整体写还是相对 extra_body 写：约定 path 的首段若为
        # "extra_body" 则 strip 掉，剩下相对 extra_body 写入；否则原样写入 extra_body
        # 内（兼容简写）。
        normalized_path = path[1:] if path and path[0] == "extra_body" else path
        if normalized_path:
            _set_nested(extra_body, normalized_path, value)
        break

    return extra_body


# ----------------------------------------------------------------------
# Content cleaning
# ----------------------------------------------------------------------


def strip_think_tags(text: str) -> str:
    """剥离 ``<think>...</think>`` 与悬挂 think 段，返回纯输出。

    一些厂商（GLM, Qwen3 等）即使关闭 thinking 也可能在 content 里夹带 think 段；
    一些 reasoning 模型 thinking 字段返回在 ``message.reasoning_content``，而正文部分
    可能仍残留半截 think。本函数对两类情况都做兜底。
    """
    if not text:
        return text or ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _DANGLING_THINK_RE.sub("", cleaned)
    return cleaned.strip()


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def call_openai(
    messages: List[Dict[str, str]],
    *,
    profile: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    is_reasoning: Optional[bool] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
    response_format: Optional[Dict[str, Any]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    settings_path: Optional[str] = None,
    **passthrough: Any,
) -> str:
    """同步调用 OpenAI 兼容协议的 chat completion，返回清洗后的纯文本。

    使用顺序：调用方显式参数 > ``profile`` 中的字段 > 不传/默认。

    :param messages: 标准 OpenAI ``messages``，例如 ``[{"role": "system", ...}, ...]``。
    :param profile: 走哪个 profile（见对应 settings JSON）。不传则用 ``default_profile``。
    :param model: 覆盖 profile 中的 model；同时驱动 vendor_dispatch 命中。
    :param temperature: 覆盖 profile 中的 temperature。
    :param max_tokens: 覆盖 profile 中的 max_tokens。
    :param is_reasoning: 覆盖 profile 的 is_reasoning。两阶段流水线**第二阶段强制传 False**，
        让本函数把 ``thinking={"type": "disabled"}`` 注入到 GLM 这类厂商。
    :param api_key: 覆盖 profile 的 api_key；不传则用 profile，最终回退到环境变量
        ``OPENAI_API_KEY`` / ``LLM_API_KEY``。
    :param base_url: 覆盖 profile 的 base_url；同时支持环境变量 ``OPENAI_BASE_URL`` /
        ``LLM_BASE_URL`` 兜底。
    :param timeout: 单次调用超时（秒）。
    :param response_format: OpenAI 的 ``response_format`` 字段，例如
        ``{"type": "json_object"}``。第二阶段建议传入以约束输出格式。
    :param extra_body: 显式注入的 ``extra_body``；与 vendor_dispatch 自动注入字段会合并，
        显式优先。
    :param settings_path: 非空时从该文件加载配置（规则同 :func:`load_llm_settings`）；
        ``None`` 则使用默认 ``API_config/llm_settings.json``。
    :param passthrough: 透传给 SDK 的其它 keyword 参数（如 ``top_p`` 等）。
    :return: 已经剥离 ``<think>`` 段、调用方可直接消费的字符串。失败时返回空串。
    """
    settings = load_llm_settings(settings_path=settings_path)
    profile_name = profile or settings.get("default_profile") or "stage1_reasoning"
    profile_cfg: Dict[str, Any] = (settings.get("profiles") or {}).get(profile_name, {})

    final_model = model or profile_cfg.get("model") or profile_cfg.get("model_name")
    final_temperature = (
        temperature if temperature is not None else profile_cfg.get("temperature")
    )
    final_max_tokens = (
        max_tokens if max_tokens is not None else profile_cfg.get("max_tokens")
    )
    final_is_reasoning = (
        is_reasoning if is_reasoning is not None else profile_cfg.get("is_reasoning", False)
    )
    final_api_key = (
        api_key
        or profile_cfg.get("api_key")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    final_base_url = (
        base_url
        or profile_cfg.get("base_url")
        or profile_cfg.get("api_url")
        or os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
    )
    final_timeout = timeout if timeout is not None else profile_cfg.get("timeout_seconds")
    final_response_format = response_format or profile_cfg.get("response_format")

    if not final_model:
        logger.warning("call_openai: no model configured for profile=%s", profile_name)
        return ""
    if not final_api_key:
        logger.warning("call_openai: no api_key configured for profile=%s", profile_name)
        return ""

    merged_extra_body = _apply_vendor_dispatch(
        model=final_model,
        is_reasoning=final_is_reasoning,
        extra_body=extra_body or {},
        settings=settings,
    )

    try:
        from openai import OpenAI  # 延迟导入：未装 openai 包时不影响其它模块加载
    except ImportError:
        logger.warning(
            "openai SDK is not installed; please `pip install openai` to enable LLM calls."
        )
        return ""

    client_kwargs: Dict[str, Any] = {"api_key": final_api_key}
    if final_base_url:
        client_kwargs["base_url"] = final_base_url
    if final_timeout is not None:
        client_kwargs["timeout"] = final_timeout

    try:
        client = OpenAI(**client_kwargs)
    except Exception as exc:  # 配置无效等
        logger.warning("call_openai: failed to build OpenAI client (%s)", exc)
        return ""

    request_kwargs: Dict[str, Any] = {
        "model": final_model,
        "messages": messages,
    }
    if final_temperature is not None:
        request_kwargs["temperature"] = final_temperature
    if final_max_tokens is not None:
        request_kwargs["max_tokens"] = final_max_tokens
    if final_response_format is not None:
        request_kwargs["response_format"] = final_response_format
    if merged_extra_body:
        request_kwargs["extra_body"] = merged_extra_body
    request_kwargs.update(passthrough)

    try:
        completion = client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        logger.warning(
            "call_openai: chat.completions.create failed (model=%s, err=%s)",
            final_model,
            exc,
        )
        return ""

    try:
        message = completion.choices[0].message
        content = getattr(message, "content", "") or ""
    except Exception as exc:
        logger.warning("call_openai: cannot parse completion (%s)", exc)
        return ""

    post = settings.get("post_processing") or {}
    if post.get("strip_think_tags", True):
        content = strip_think_tags(content)

    return content


__all__ = ["call_openai", "load_llm_settings", "strip_think_tags"]
