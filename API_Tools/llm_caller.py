"""LLM Caller.

封装对 OpenAI 兼容协议的底层调用：

1. **配置加载**：从 ``API_config/config.json`` 的 ``llm_agents_pool`` 按 ``model_key`` 取参。
2. **推理开关**：每条模型配置的 ``is_reasoning`` 控制是否向厂商注入关闭 thinking 的
   ``extra_body``（``true`` 不干预；``false`` 按模型名分发；``null`` 不干预）。
3. **输出清洗**：剥离 ``<think>...</think>`` 等 think 段。

失败时返回空串，不抛异常打断游戏循环。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional

from API_Tools.reasoning_extractor import (
    DEFAULT_REASONING_EXTRACT_MODE,
    REASONING_NONE,
    extract_reasoning,
)

logger = logging.getLogger("API_Tools.llm_caller")

_AGENT_POOL_REL_PATH = os.path.join("API_config", "config.json")

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_DANGLING_THINK_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)
_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)

_MISSING = object()

_agent_pool_cache: Dict[str, Dict[str, Any]] = {}
_agent_pool_cache_lock = threading.Lock()


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _resolve_agent_pool_abs_path(config_path: Optional[str]) -> str:
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
    """加载 ``API_config/config.json``（含 ``llm_agents_pool``）。"""
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
    pool_data = load_agent_pool(config_path=config_path)
    pool = pool_data.get("llm_agents_pool") or {}
    cfg = pool.get(model_key)
    if cfg is None:
        logger.warning("model_key=%r not found in llm_agents_pool.", model_key)
        return {}
    return cfg


def strip_think_tags(text: str) -> str:
    """剥离 think 段，返回纯输出。"""
    if not text:
        return text or ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _DANGLING_THINK_RE.sub("", cleaned)
    cleaned = _THINK_PATTERN.sub("", cleaned)
    return cleaned.strip()


def _inject_identity_prompt(
    messages: List[Dict[str, str]],
    identity_prompt: str,
) -> List[Dict[str, str]]:
    messages = list(messages)
    if messages and messages[0].get("role") == "system":
        messages[0] = {
            **messages[0],
            "content": f"{identity_prompt}\n\n{messages[0]['content']}",
        }
    else:
        messages.insert(0, {"role": "system", "content": identity_prompt})
    return messages


def _apply_reasoning_disable(model: str, request_kwargs: Dict[str, Any]) -> None:
    """``is_reasoning=False`` 时，按模型厂商注入关闭 thinking 的 extra_body。"""
    model_name_l = (model or "").lower()
    extra_body = dict(request_kwargs.get("extra_body") or {})

    if "kimi" in model_name_l:
        request_kwargs["temperature"] = 0.6
        extra_body.update({
            "thinking": {"type": "disabled"},
            "chat_template_kwargs": {"thinking": False},
        })
    elif "glm" in model_name_l or "deepseek" in model_name_l:
        extra_body["thinking"] = {"type": "disabled"}
    else:
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}

    request_kwargs["extra_body"] = extra_body


def _deep_merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _merge_extra_body(request_kwargs: Dict[str, Any], extra_body: Optional[Dict[str, Any]]) -> None:
    if not extra_body:
        return
    current = dict(request_kwargs.get("extra_body") or {})
    request_kwargs["extra_body"] = _deep_merge_dict(current, extra_body)


def _message_to_dict(message: Any) -> Dict[str, Any]:
    if message is None:
        return {}
    if isinstance(message, dict):
        return dict(message)
    for method_name in ("model_dump", "dict"):
        method = getattr(message, method_name, None)
        if callable(method):
            try:
                return dict(method())
            except Exception:
                pass
    result: Dict[str, Any] = {}
    for key in (
        "role",
        "content",
        "reasoning_content",
        "reasoning",
        "reasoning_details",
        "tool_calls",
    ):
        if hasattr(message, key):
            result[key] = getattr(message, key)
    return result


def _empty_call_result(
    *,
    model_key: Optional[str],
    model: Optional[str] = None,
    is_reasoning: Any = None,
    reasoning_extract_mode: str = DEFAULT_REASONING_EXTRACT_MODE,
    error: str = "",
) -> Dict[str, Any]:
    return {
        "content": "",
        "reasoning": "",
        "raw_content": "",
        "reasoning_source": REASONING_NONE,
        "reasoning_extract_mode": reasoning_extract_mode,
        "model_key": model_key or "",
        "model": model or "",
        "is_reasoning": None if is_reasoning is _MISSING else is_reasoning,
        "raw_message": {},
        "raw_message_keys": [],
        "error": error,
    }


def call_openai(
    messages: List[Dict[str, str]],
    *,
    model_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    is_reasoning: Any = _MISSING,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
    response_format: Optional[Dict[str, Any]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
    top_p: Optional[float] = None,
    reasoning_extract_mode: Optional[str] = None,
    **passthrough: Any,
) -> str:
    """同步调用 OpenAI 兼容 chat completion，返回清洗后的纯文本。

    参数优先级：调用方显式参数 > ``config.json`` 中 ``llm_agents_pool[model_key]``。

    ``is_reasoning``（可在 config.json 每条模型里配置）：
      - ``true``  : 不附加关闭 thinking 的 extra_body；
      - ``false`` : 按模型名注入厂商对应的关闭 thinking 参数；
      - ``null`` / 未配置：不干预，按服务端默认行为。
    """
    result = call_openai_detailed(
        messages=messages,
        model_key=model_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        is_reasoning=is_reasoning,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        response_format=response_format,
        extra_body=extra_body,
        config_path=config_path,
        top_p=top_p,
        reasoning_extract_mode=reasoning_extract_mode,
        **passthrough,
    )
    return result.get("content", "") or ""


def call_openai_detailed(
    messages: List[Dict[str, str]],
    *,
    model_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    is_reasoning: Any = _MISSING,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
    response_format: Optional[Dict[str, Any]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
    top_p: Optional[float] = None,
    reasoning_extract_mode: Optional[str] = None,
    **passthrough: Any,
) -> Dict[str, Any]:
    """同步调用 OpenAI 兼容 chat completion，返回正式内容和 reasoning 明细。"""
    if not model_key:
        logger.warning("call_openai: model_key is required (configure llm_agents_pool in config.json).")
        return _empty_call_result(model_key=model_key, error="missing_model_key")

    pool_cfg = _resolve_model_from_pool(model_key, config_path)
    if not pool_cfg:
        return _empty_call_result(model_key=model_key, error="missing_model_config")

    def _pick(explicit: Any, pool_key: str, default: Any = _MISSING) -> Any:
        if explicit is not _MISSING and explicit is not None:
            return explicit
        if pool_key in pool_cfg:
            return pool_cfg[pool_key]
        return default

    final_model = _pick(model, "model_name", _MISSING)
    if final_model is _MISSING:
        final_model = pool_cfg.get("model")
    final_temperature = _pick(temperature, "temperature", _MISSING)
    final_max_tokens = _pick(max_tokens, "max_tokens", _MISSING)
    final_is_reasoning = _pick(is_reasoning, "is_reasoning", _MISSING)
    final_api_key = _pick(api_key, "api_key")
    if final_api_key is _MISSING:
        final_api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    final_base_url = _pick(base_url, "api_url")
    if final_base_url is _MISSING:
        final_base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    final_timeout = _pick(timeout, "timeout_seconds", _MISSING)
    final_response_format = _pick(response_format, "response_format", _MISSING)
    final_top_p = _pick(top_p, "top_p", _MISSING)
    final_reasoning_extract_mode = (
        reasoning_extract_mode
        or pool_cfg.get("reasoning_extract_mode")
        or DEFAULT_REASONING_EXTRACT_MODE
    )

    if pool_cfg.get("enable_identity") and pool_cfg.get("identity_prompt"):
        messages = _inject_identity_prompt(messages, pool_cfg["identity_prompt"])

    if not final_model or final_model is _MISSING:
        logger.warning("call_openai: no model configured for model_key=%s", model_key)
        return _empty_call_result(
            model_key=model_key,
            is_reasoning=final_is_reasoning,
            reasoning_extract_mode=final_reasoning_extract_mode,
            error="missing_model",
        )
    if not final_api_key or final_api_key is _MISSING:
        logger.warning("call_openai: no api_key configured for model_key=%s", model_key)
        return _empty_call_result(
            model_key=model_key,
            model=str(final_model),
            is_reasoning=final_is_reasoning,
            reasoning_extract_mode=final_reasoning_extract_mode,
            error="missing_api_key",
        )

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai SDK is not installed; please `pip install openai`.")
        return _empty_call_result(
            model_key=model_key,
            model=str(final_model),
            is_reasoning=final_is_reasoning,
            reasoning_extract_mode=final_reasoning_extract_mode,
            error="missing_openai_sdk",
        )

    client_kwargs: Dict[str, Any] = {"api_key": final_api_key}
    if final_base_url is not _MISSING and final_base_url:
        client_kwargs["base_url"] = final_base_url
    if final_timeout is not _MISSING and final_timeout is not None:
        client_kwargs["timeout"] = final_timeout

    try:
        client = OpenAI(**client_kwargs)
    except Exception as exc:
        logger.warning("call_openai: failed to build OpenAI client (%s)", exc)
        return _empty_call_result(
            model_key=model_key,
            model=str(final_model),
            is_reasoning=final_is_reasoning,
            reasoning_extract_mode=final_reasoning_extract_mode,
            error=f"client_init_failed: {exc}",
        )

    request_kwargs: Dict[str, Any] = {
        "model": final_model,
        "messages": messages,
    }
    if final_temperature is not _MISSING and final_temperature is not None:
        request_kwargs["temperature"] = final_temperature
    if final_max_tokens is not _MISSING and final_max_tokens is not None:
        request_kwargs["max_tokens"] = final_max_tokens
    if final_top_p is not _MISSING and final_top_p is not None:
        request_kwargs["top_p"] = final_top_p
    if final_response_format is not _MISSING and final_response_format is not None:
        request_kwargs["response_format"] = final_response_format

    if final_is_reasoning is True:
        _merge_extra_body(request_kwargs, pool_cfg.get("reasoning_extra_body"))
    elif final_is_reasoning is False:
        if pool_cfg.get("non_reasoning_extra_body"):
            _merge_extra_body(request_kwargs, pool_cfg.get("non_reasoning_extra_body"))
            if pool_cfg.get("non_reasoning_temperature") is not None:
                request_kwargs["temperature"] = pool_cfg["non_reasoning_temperature"]
        else:
            _apply_reasoning_disable(str(final_model), request_kwargs)
    if extra_body:
        _merge_extra_body(request_kwargs, extra_body)

    request_kwargs.update(passthrough)

    try:
        completion = client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        logger.warning(
            "call_openai: chat.completions.create failed (model=%s, err=%s)",
            final_model,
            exc,
        )
        return _empty_call_result(
            model_key=model_key,
            model=str(final_model),
            is_reasoning=final_is_reasoning,
            reasoning_extract_mode=final_reasoning_extract_mode,
            error=f"completion_failed: {exc}",
        )

    try:
        message = completion.choices[0].message
    except Exception as exc:
        logger.warning("call_openai: cannot parse completion (%s)", exc)
        return _empty_call_result(
            model_key=model_key,
            model=str(final_model),
            is_reasoning=final_is_reasoning,
            reasoning_extract_mode=final_reasoning_extract_mode,
            error=f"parse_failed: {exc}",
        )

    extraction = extract_reasoning(completion, mode=final_reasoning_extract_mode)
    raw_message = _message_to_dict(message)
    content = extraction.get("final_content", "") or ""
    if not content and final_reasoning_extract_mode == REASONING_NONE:
        content = strip_think_tags(extraction.get("raw_content", "") or "")
    return {
        "content": content,
        "reasoning": extraction.get("reasoning", "") or "",
        "raw_content": extraction.get("raw_content", "") or "",
        "reasoning_source": extraction.get("source", REASONING_NONE) or REASONING_NONE,
        "reasoning_extract_mode": extraction.get("mode", final_reasoning_extract_mode),
        "model_key": model_key,
        "model": str(final_model),
        "is_reasoning": None if final_is_reasoning is _MISSING else final_is_reasoning,
        "raw_message": raw_message,
        "raw_message_keys": sorted(raw_message.keys()),
        "error": "",
    }


__all__ = ["call_openai", "call_openai_detailed", "load_agent_pool", "strip_think_tags"]
