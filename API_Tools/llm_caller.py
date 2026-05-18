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
    **passthrough: Any,
) -> str:
    """同步调用 OpenAI 兼容 chat completion，返回清洗后的纯文本。

    参数优先级：调用方显式参数 > ``config.json`` 中 ``llm_agents_pool[model_key]``。

    ``is_reasoning``（可在 config.json 每条模型里配置）：
      - ``true``  : 不附加关闭 thinking 的 extra_body；
      - ``false`` : 按模型名注入厂商对应的关闭 thinking 参数；
      - ``null`` / 未配置：不干预，按服务端默认行为。
    """
    if not model_key:
        logger.warning("call_openai: model_key is required (configure llm_agents_pool in config.json).")
        return ""

    pool_cfg = _resolve_model_from_pool(model_key, config_path)
    if not pool_cfg:
        return ""

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

    if pool_cfg.get("enable_identity") and pool_cfg.get("identity_prompt"):
        messages = _inject_identity_prompt(messages, pool_cfg["identity_prompt"])

    if not final_model or final_model is _MISSING:
        logger.warning("call_openai: no model configured for model_key=%s", model_key)
        return ""
    if not final_api_key or final_api_key is _MISSING:
        logger.warning("call_openai: no api_key configured for model_key=%s", model_key)
        return ""

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai SDK is not installed; please `pip install openai`.")
        return ""

    client_kwargs: Dict[str, Any] = {"api_key": final_api_key}
    if final_base_url is not _MISSING and final_base_url:
        client_kwargs["base_url"] = final_base_url
    if final_timeout is not _MISSING and final_timeout is not None:
        client_kwargs["timeout"] = final_timeout

    try:
        client = OpenAI(**client_kwargs)
    except Exception as exc:
        logger.warning("call_openai: failed to build OpenAI client (%s)", exc)
        return ""

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
    if extra_body:
        request_kwargs["extra_body"] = dict(extra_body)

    if final_is_reasoning is False:
        _apply_reasoning_disable(str(final_model), request_kwargs)

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

    return strip_think_tags(content)


__all__ = ["call_openai", "load_agent_pool", "strip_think_tags"]
