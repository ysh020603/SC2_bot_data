"""LLM Caller.

封装对 OpenAI 兼容协议的底层调用，四大职责：

1. **配置加载**：
   - ``load_llm_settings()``：从 ``API_config/llm_settings.json`` 加载 profile 配置。
   - ``load_agent_pool()``：从 ``API_config/config.json`` 加载 ``llm_agents_pool`` 模型池。
2. **多入口调用**：
   - ``profile`` 方式（向后兼容）：按 profile 名从 ``llm_settings.json`` 取参。
   - ``model_key`` 方式（新）：按 key 从 ``config.json`` 的 ``llm_agents_pool`` 取参。
   两种方式互斥，``model_key`` 优先。
3. **多厂商分发**：根据模型名子串命中 ``vendor_dispatch.rules``，把
   ``is_reasoning`` 翻译成各家私有字段。
4. **输出清洗**：截掉 ``<think>...</think>``（含悬挂 think）。

模块对外暴露：

- ``call_openai(...)`` —— 主入口。
- ``load_llm_settings()`` —— profile 配置加载。
- ``load_agent_pool()`` —— 模型池配置加载。
- ``strip_think_tags()`` —— 输出清洗工具函数。

设计取舍：

- 同步实现（基于 ``openai.OpenAI`` 客户端）。
- 失败回退到字面级降级（返回 ``""``），**绝不抛异常打断游戏循环**。
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
_AGENT_POOL_REL_PATH = os.path.join("API_config", "config.json")

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_DANGLING_THINK_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)

# 进程级缓存：按「解析后的绝对路径」分文件缓存，避免每次调用都打开 JSON。
_settings_cache: Dict[str, Dict[str, Any]] = {}
_settings_cache_lock = threading.Lock()

_agent_pool_cache: Dict[str, Dict[str, Any]] = {}
_agent_pool_cache_lock = threading.Lock()


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
# Agent Pool (config.json)
# ----------------------------------------------------------------------


def _resolve_agent_pool_abs_path(config_path: Optional[str]) -> str:
    """把 ``config_path`` 解析为绝对路径（规则同 settings，默认指向 config.json）。"""
    root = _repo_root()
    raw = (config_path or "").strip()
    if not raw:
        rel = _AGENT_POOL_REL_PATH
    else:
        norm = raw.replace("\\", "/")
        if os.path.isabs(raw):
            return os.path.abspath(os.path.normpath(raw))
        if "/" not in norm:
            rel = os.path.join("API_config", norm)
        else:
            rel = norm
    return os.path.abspath(os.path.join(root, rel))


def load_agent_pool(
    config_path: Optional[str] = None,
    force_reload: bool = False,
) -> Dict[str, Any]:
    """加载 ``API_config/config.json`` 中的 ``llm_agents_pool``。

    返回整个 JSON（顶层含 ``llm_agents_pool`` 字段）。
    """
    abs_path = _resolve_agent_pool_abs_path(config_path)

    with _agent_pool_cache_lock:
        if not force_reload and abs_path in _agent_pool_cache:
            return _agent_pool_cache[abs_path]
        if force_reload and abs_path in _agent_pool_cache:
            del _agent_pool_cache[abs_path]

    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        logger.warning("Agent pool config not found at %s; using empty.", abs_path)
        data = {"llm_agents_pool": {}}
    except Exception as exc:
        logger.warning("Failed to parse agent pool %s (%s); using empty.", abs_path, exc)
        data = {"llm_agents_pool": {}}

    with _agent_pool_cache_lock:
        _agent_pool_cache[abs_path] = data
    return data


def _resolve_model_from_pool(
    model_key: str,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """根据 ``model_key`` 从 ``llm_agents_pool`` 中取出单条模型配置。

    返回 pool 中的原始 dict；找不到时返回空 dict。
    """
    pool_data = load_agent_pool(config_path=config_path)
    pool = pool_data.get("llm_agents_pool") or {}
    cfg = pool.get(model_key)
    if cfg is None:
        logger.warning("model_key=%r not found in llm_agents_pool.", model_key)
        return {}
    return cfg


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
# Identity injection
# ----------------------------------------------------------------------


def _inject_identity_prompt(
    messages: List[Dict[str, str]],
    identity_prompt: str,
) -> List[Dict[str, str]]:
    """将 identity_prompt 注入到第一条 system 消息前面（如有）。"""
    messages = list(messages)
    if messages and messages[0].get("role") == "system":
        messages[0] = {
            **messages[0],
            "content": f"{identity_prompt}\n\n{messages[0]['content']}",
        }
    else:
        messages.insert(0, {"role": "system", "content": identity_prompt})
    return messages


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
    model_key: Optional[str] = None,
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
    config_path: Optional[str] = None,
    **passthrough: Any,
) -> str:
    """同步调用 OpenAI 兼容协议的 chat completion，返回清洗后的纯文本。

    参数解析优先级：调用方显式参数 > ``model_key`` 池 > ``profile`` 字段 > 不传/默认。

    两种模式互斥，``model_key`` 优先于 ``profile``：

    * **model_key 模式**：从 ``config.json`` 的 ``llm_agents_pool[model_key]`` 取参。
    * **profile 模式**（向后兼容）：从 ``llm_settings.json`` 的 ``profiles[profile]`` 取参。

    :param model_key:       从 ``config.json`` 模型池查找的 key（新接口）。
    :param config_path:     model_key 对应的配置文件路径；默认 ``API_config/config.json``。
    :param profile:         走哪个 profile（见 llm_settings JSON）。
    :param settings_path:   profile 对应的配置文件路径。
    :return: 已经剥离 ``<think>`` 段的字符串。失败时返回空串。
    """
    # --- 从 model_key（池）或 profile 解析基础参数 ---
    pool_cfg: Dict[str, Any] = {}
    if model_key:
        pool_cfg = _resolve_model_from_pool(model_key, config_path)

    settings = load_llm_settings(settings_path=settings_path)

    profile_name = profile or settings.get("default_profile") or "stage1_reasoning"
    if model_key and pool_cfg:
        profile_cfg: Dict[str, Any] = {}
    else:
        profile_cfg = (settings.get("profiles") or {}).get(profile_name, {})

    def _pick(explicit, pool_key, profile_key, env_keys=(), default=None):
        """按优先级选值：显式 > 池 > profile > 环境变量 > 默认。"""
        if explicit is not None:
            return explicit
        pool_keys = [pool_key] if isinstance(pool_key, str) else (pool_key or [])
        for k in pool_keys:
            v = pool_cfg.get(k)
            if v is not None:
                return v
        prof_keys = [profile_key] if isinstance(profile_key, str) else (profile_key or [])
        for k in prof_keys:
            v = profile_cfg.get(k)
            if v is not None:
                return v
        for ek in env_keys:
            v = os.environ.get(ek)
            if v:
                return v
        return default

    final_model = _pick(model, "model_name", ["model", "model_name"])
    final_temperature = _pick(temperature, "temperature", "temperature")
    final_max_tokens = _pick(max_tokens, "max_tokens", "max_tokens")
    final_is_reasoning = _pick(is_reasoning, "is_reasoning", "is_reasoning", default=False)
    if final_is_reasoning is None:
        final_is_reasoning = False
    final_api_key = _pick(
        api_key, "api_key", "api_key",
        env_keys=("LLM_API_KEY", "OPENAI_API_KEY"),
    )
    final_base_url = _pick(
        base_url, "api_url", ["base_url", "api_url"],
        env_keys=("LLM_BASE_URL", "OPENAI_BASE_URL"),
    )
    final_timeout = _pick(timeout, "timeout_seconds", "timeout_seconds")
    final_response_format = _pick(response_format, "response_format", "response_format")

    # identity_prompt 注入（仅 model_key 模式支持）
    if pool_cfg.get("enable_identity") and pool_cfg.get("identity_prompt"):
        messages = _inject_identity_prompt(messages, pool_cfg["identity_prompt"])

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


__all__ = ["call_openai", "load_llm_settings", "load_agent_pool", "strip_think_tags"]
