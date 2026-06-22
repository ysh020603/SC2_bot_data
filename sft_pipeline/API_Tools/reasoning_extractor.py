"""Reasoning extraction helpers for OpenAI-compatible chat completions.

The extraction modes describe response shapes, not model names. Keep model
specific choices in ``API_config/config.json``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Tuple


REASONING_NONE = "none"
REASONING_AUTO = "auto"
FIELD_REASONING_CONTENT = "field_reasoning_content"
FIELD_REASONING = "field_reasoning"
FIELD_REASONING_DETAILS = "field_reasoning_details"
CONTENT_THINK_TAGS = "content_think_tags"
CONTENT_REDACTED_THINKING_TAGS = "content_redacted_thinking_tags"

DEFAULT_REASONING_EXTRACT_MODE = REASONING_AUTO

_THINK_BLOCK_RE = re.compile(
    r"<think\b[^>]*>(?P<reasoning>.*?)</think>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_DANGLING_RE = re.compile(
    r"<think\b[^>]*>(?P<reasoning>.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
_REDACTED_BLOCK_RE = re.compile(
    r"<redacted_thinking\b[^>]*>(?P<reasoning>.*?)</redacted_thinking>",
    re.IGNORECASE | re.DOTALL,
)
_REDACTED_DANGLING_RE = re.compile(
    r"<redacted_thinking\b[^>]*>(?P<reasoning>.*)\Z",
    re.IGNORECASE | re.DOTALL,
)


def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _first_choice(completion: Any) -> Any:
    choices = _get_attr_or_key(completion, "choices", None)
    if not choices:
        return None
    try:
        return choices[0]
    except Exception:
        return None


def get_chat_message(completion_or_message: Any) -> Any:
    """Return the assistant message from a completion, or the input itself."""
    if completion_or_message is None:
        return None
    if _get_attr_or_key(completion_or_message, "content", None) is not None:
        return completion_or_message
    choice = _first_choice(completion_or_message)
    if choice is None:
        return completion_or_message
    return _get_attr_or_key(choice, "message", completion_or_message)


def _stringify_reasoning(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("reasoning")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(value).strip()


def _extract_field(message: Any, field_name: str, source: str, raw_content: str) -> Optional[Dict[str, str]]:
    reasoning = _stringify_reasoning(_get_attr_or_key(message, field_name, None))
    if not reasoning:
        return None
    return {
        "reasoning": reasoning,
        "final_content": raw_content.strip(),
        "raw_content": raw_content,
        "source": source,
    }


def _extract_tagged_content(
    raw_content: str,
    *,
    pattern: re.Pattern[str],
    dangling_pattern: re.Pattern[str],
    source: str,
    dangling_source: str,
) -> Optional[Dict[str, str]]:
    if not raw_content:
        return None

    match = pattern.search(raw_content)
    if match:
        reasoning = (match.group("reasoning") or "").strip()
        final_content = (raw_content[: match.start()] + raw_content[match.end() :]).strip()
        return {
            "reasoning": reasoning,
            "final_content": final_content,
            "raw_content": raw_content,
            "source": source,
        }

    dangling = dangling_pattern.search(raw_content)
    if dangling:
        reasoning = (dangling.group("reasoning") or "").strip()
        final_content = raw_content[: dangling.start()].strip()
        return {
            "reasoning": reasoning,
            "final_content": final_content,
            "raw_content": raw_content,
            "source": dangling_source,
        }

    return None


def _empty_result(raw_content: str, mode: str) -> Dict[str, str]:
    return {
        "reasoning": "",
        "final_content": raw_content.strip(),
        "raw_content": raw_content,
        "source": REASONING_NONE,
        "mode": mode,
    }


def _mode_candidates(mode: str) -> Iterable[str]:
    normalized = (mode or DEFAULT_REASONING_EXTRACT_MODE).strip().lower()
    if normalized == REASONING_AUTO:
        return (
            FIELD_REASONING_CONTENT,
            FIELD_REASONING,
            FIELD_REASONING_DETAILS,
            CONTENT_THINK_TAGS,
            CONTENT_REDACTED_THINKING_TAGS,
        )
    return (normalized,)


def extract_reasoning(completion_or_message: Any, mode: str = DEFAULT_REASONING_EXTRACT_MODE) -> Dict[str, str]:
    """Split reasoning text from final content.

    ``completion_or_message`` may be an OpenAI SDK completion object, a message
    object, or a plain dict with equivalent fields.
    """
    message = get_chat_message(completion_or_message)
    raw_content = _get_attr_or_key(message, "content", "") or ""
    normalized_mode = (mode or DEFAULT_REASONING_EXTRACT_MODE).strip().lower()

    if normalized_mode == REASONING_NONE:
        return _empty_result(raw_content, normalized_mode)

    result: Optional[Dict[str, str]] = None
    for candidate in _mode_candidates(normalized_mode):
        if candidate == FIELD_REASONING_CONTENT:
            result = _extract_field(
                message,
                "reasoning_content",
                "message.reasoning_content",
                raw_content,
            )
        elif candidate == FIELD_REASONING:
            result = _extract_field(message, "reasoning", "message.reasoning", raw_content)
        elif candidate == FIELD_REASONING_DETAILS:
            result = _extract_field(
                message,
                "reasoning_details",
                "message.reasoning_details",
                raw_content,
            )
        elif candidate == CONTENT_THINK_TAGS:
            result = _extract_tagged_content(
                raw_content,
                pattern=_THINK_BLOCK_RE,
                dangling_pattern=_THINK_DANGLING_RE,
                source="content.think",
                dangling_source="content.think.dangling",
            )
        elif candidate == CONTENT_REDACTED_THINKING_TAGS:
            result = _extract_tagged_content(
                raw_content,
                pattern=_REDACTED_BLOCK_RE,
                dangling_pattern=_REDACTED_DANGLING_RE,
                source="content.redacted_thinking",
                dangling_source="content.redacted_thinking.dangling",
            )
        else:
            result = None
        if result is not None:
            result["mode"] = normalized_mode
            return result

    return _empty_result(raw_content, normalized_mode)


def extract_final_content(completion_or_message: Any, mode: str = DEFAULT_REASONING_EXTRACT_MODE) -> str:
    """Return only the final assistant content."""
    return extract_reasoning(completion_or_message, mode=mode)["final_content"]


__all__ = [
    "CONTENT_REDACTED_THINKING_TAGS",
    "CONTENT_THINK_TAGS",
    "DEFAULT_REASONING_EXTRACT_MODE",
    "FIELD_REASONING",
    "FIELD_REASONING_CONTENT",
    "FIELD_REASONING_DETAILS",
    "REASONING_AUTO",
    "REASONING_NONE",
    "extract_final_content",
    "extract_reasoning",
    "get_chat_message",
]
