#!/usr/bin/env python3
"""测试 Kimi-k2.5 API：关闭 reasoning 与 开启 reasoning 两种模式。

逻辑与 ``API_Tools/llm_caller.py`` 中 ``is_reasoning`` 一致：
  - ``is_reasoning=False``：注入关闭 thinking 的 extra_body（Kimi 专用）；
  - ``is_reasoning=True`` ：不注入，使用服务端默认 thinking 行为。

用法:
  python API_Tools/test_kimi_k25_api.py              # 依次测试两种模式
  python API_Tools/test_kimi_k25_api.py --no-reasoning
  python API_Tools/test_kimi_k25_api.py --reasoning
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# --- 与 config.json 中 Kimi-k2.5_base / Kimi-k2.5_base_think 一致 ---
API_BASE_URL = "http://172.18.39.161:19003/v1"
API_KEY = "sk-rzQ220PtbX9y6AudNocvlcdLnLaLtvDjeoOEWtXF4iBS59xI"
MODEL_NAME = "kimi-k2.5"
TEMPERATURE_REASONING_ON = 1.0
TEMPERATURE_REASONING_OFF = 0.6  # llm_caller 在关闭 Kimi thinking 时会覆盖为 0.6
REQUEST_TIMEOUT_SEC = 120.0

TEST_MESSAGES: List[Dict[str, str]] = [
    {
        "role": "user",
        "content": "简单介绍一下你自己",
    },
]

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>(.*?)</think>", re.IGNORECASE | re.DOTALL)
_DANGLING_THINK_RE = re.compile(r"<think\b[^>]*>(.*)", re.IGNORECASE | re.DOTALL)
_THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_THINK_STRIP_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_STRIP_DANGLING_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)
_THINK_STRIP_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class CompletionResult:
    content: str
    reasoning_content: Optional[str] = None


def strip_think_tags(text: str) -> str:
    cleaned = _THINK_STRIP_BLOCK_RE.sub("", text or "")
    cleaned = _THINK_STRIP_DANGLING_RE.sub("", cleaned)
    cleaned = _THINK_STRIP_PATTERN.sub("", cleaned)
    return cleaned.strip()


def extract_think_from_content(text: str) -> Optional[str]:
    """从 content 内嵌的 think 标签中提取推理文本（部分部署会走此格式）。"""
    if not text:
        return None
    parts: List[str] = []
    for pattern in (_THINK_BLOCK_RE, _THINK_PATTERN, _DANGLING_THINK_RE):
        for match in pattern.finditer(text):
            body = (match.group(1) or "").strip()
            if body:
                parts.append(body)
    if not parts:
        return None
    return "\n\n---\n\n".join(parts)


def _get_reasoning_content(message: Any) -> Optional[str]:
    """优先读 API 返回的 reasoning_content，否则解析 content 中的 think 标签。"""
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning is None:
        dumped = message.model_dump() if hasattr(message, "model_dump") else {}
        reasoning = dumped.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    content = getattr(message, "content", None) or ""
    embedded = extract_think_from_content(content)
    return embedded


def _make_openai_client():
    """内网地址不走系统代理，避免 httpx 的 Connection error。"""
    from openai import OpenAI

    try:
        import httpx
    except ImportError:
        return OpenAI(
            api_key=API_KEY,
            base_url=API_BASE_URL,
            timeout=REQUEST_TIMEOUT_SEC,
        )

    http_client = httpx.Client(
        timeout=REQUEST_TIMEOUT_SEC,
        trust_env=False,
    )
    return OpenAI(
        api_key=API_KEY,
        base_url=API_BASE_URL,
        timeout=REQUEST_TIMEOUT_SEC,
        http_client=http_client,
    )


def check_connectivity() -> bool:
    """预检：能否连上 base_url（GET /models）。"""
    try:
        import httpx
    except ImportError:
        print("未安装 httpx，跳过连通性预检（pip install httpx）")
        return True

    parsed = urlparse(API_BASE_URL)
    root = f"{parsed.scheme}://{parsed.netloc}"
    url = f"{root}/models"
    print(f"连通性预检: GET {url} (trust_env=False，不走代理)")
    try:
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {API_KEY}"})
        print(f"  -> HTTP {resp.status_code}")
        if resp.status_code >= 500:
            print("  服务端异常，请确认 Kimi 服务是否在 19003 端口运行。")
            return False
        return True
    except Exception as exc:
        print(f"  -> 失败: {type(exc).__name__}: {exc}")
        _print_connection_hints()
        return False


def _print_connection_hints() -> None:
    host = urlparse(API_BASE_URL).hostname or ""
    print(
        "\n排查建议:\n"
        f"  1. 本机能否访问服务: curl -v {API_BASE_URL.rstrip('/')}/models "
        f'-H "Authorization: Bearer <key>"\n'
        f"  2. 若设置了 http_proxy/https_proxy，内网需加 NO_PROXY，例如:\n"
        f"     export NO_PROXY=127.0.0.1,localhost,{host}\n"
        f"  3. 确认 {host}:19003 上推理服务已启动\n"
        "  4. 脚本已对 OpenAI SDK 使用 trust_env=False，避免代理误连内网"
    )


def _format_api_error(exc: BaseException) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause:
        parts.append(f"  原因: {type(cause).__name__}: {cause}")
    return "\n".join(parts)


def apply_reasoning_disable_kimi(request_kwargs: Dict[str, Any]) -> None:
    """与 llm_caller._apply_reasoning_disable 中 Kimi 分支一致。"""
    extra_body = dict(request_kwargs.get("extra_body") or {})
    request_kwargs["temperature"] = TEMPERATURE_REASONING_OFF
    extra_body.update({
        "thinking": {"type": "disabled"},
        "chat_template_kwargs": {"thinking": False},
    })
    request_kwargs["extra_body"] = extra_body


def chat_completion(*, enable_reasoning: bool) -> CompletionResult:
    try:
        from openai import OpenAI  # noqa: F401
    except ImportError:
        print("请先安装: pip install openai", file=sys.stderr)
        sys.exit(1)

    client = _make_openai_client()

    request_kwargs: Dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": TEST_MESSAGES,
        "temperature": TEMPERATURE_REASONING_ON,
    }

    if not enable_reasoning:
        apply_reasoning_disable_kimi(request_kwargs)

    completion = client.chat.completions.create(**request_kwargs)
    message = completion.choices[0].message
    raw = message.content or ""
    reasoning = _get_reasoning_content(message) if enable_reasoning else None
    return CompletionResult(content=raw, reasoning_content=reasoning)


def run_test(label: str, enable_reasoning: bool) -> None:
    mode = "reasoning ON (Kimi-k2.5_base_think)" if enable_reasoning else "reasoning OFF (Kimi-k2.5_base)"
    print("=" * 60)
    print(f"[{label}] {mode}")
    print(f"  base_url={API_BASE_URL}")
    print(f"  model={MODEL_NAME}")
    if enable_reasoning:
        print(f"  temperature={TEMPERATURE_REASONING_ON}, extra_body=(无)")
    else:
        print(
            f"  temperature={TEMPERATURE_REASONING_OFF}, "
            'extra_body={"thinking": {"type": "disabled"}, '
            '"chat_template_kwargs": {"thinking": false}}'
        )
    print("-" * 60)

    try:
        result = chat_completion(enable_reasoning=enable_reasoning)
    except Exception as exc:
        print(f"请求失败:\n{_format_api_error(exc)}")
        if "Connection" in type(exc).__name__ or "connection" in str(exc).lower():
            _print_connection_hints()
        return

    raw = result.content
    cleaned = strip_think_tags(raw)

    if enable_reasoning:
        print("【推理过程 reasoning_content】")
        if result.reasoning_content:
            print(result.reasoning_content)
        else:
            print("(未返回 reasoning_content，且 content 中无 think 标签)")
        print()

    print("【最终回复 content】")
    display = cleaned if (enable_reasoning and cleaned) else raw
    print(display if display else "(空)")
    if not enable_reasoning and cleaned != raw.strip():
        print("\n【去除 think 标签后】")
        print(cleaned if cleaned else "(空)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="测试 Kimi-k2.5 reasoning 开/关")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--no-reasoning",
        action="store_true",
        help="仅测试关闭 reasoning（对应 Kimi-k2.5_base）",
    )
    group.add_argument(
        "--reasoning",
        action="store_true",
        help="仅测试开启 reasoning（对应 Kimi-k2.5_base_think）",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="跳过启动前的 GET /models 连通性检查",
    )
    args = parser.parse_args()

    if not args.skip_preflight and not check_connectivity():
        sys.exit(1)
    print()

    if args.no_reasoning:
        run_test("1/1", enable_reasoning=False)
    elif args.reasoning:
        run_test("1/1", enable_reasoning=True)
    else:
        run_test("1/2", enable_reasoning=False)
        run_test("2/2", enable_reasoning=True)


if __name__ == "__main__":
    main()
