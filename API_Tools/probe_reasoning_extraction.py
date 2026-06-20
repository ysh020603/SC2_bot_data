"""Probe reasoning extraction for a configured OpenAI-compatible model."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from API_Tools.llm_caller import call_openai_detailed
from API_Tools.reasoning_extractor import DEFAULT_REASONING_EXTRACT_MODE


def _truncate(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _build_messages(prompt: str, system: str = "") -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt})
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-key", required=True, help="Key in API_config/config.json llm_agents_pool.")
    parser.add_argument(
        "--prompt",
        default="Which is larger, 9.11 or 9.8? Answer shortly.",
        help="User prompt used for the probe.",
    )
    parser.add_argument("--system", default="", help="Optional system prompt.")
    parser.add_argument(
        "--mode",
        default=DEFAULT_REASONING_EXTRACT_MODE,
        help="Override reasoning extraction mode for this probe.",
    )
    parser.add_argument("--config-path", default=None, help="Optional config path.")
    parser.add_argument("--max-tokens", type=int, default=None, help="Override max_tokens.")
    parser.add_argument("--temperature", type=float, default=None, help="Override temperature.")
    parser.add_argument(
        "--save-report",
        action="store_true",
        help="Save full probe report under API_Tools/reasoning_probe_reports/.",
    )
    args = parser.parse_args()

    result = call_openai_detailed(
        messages=_build_messages(args.prompt, args.system),
        model_key=args.model_key,
        config_path=args.config_path,
        reasoning_extract_mode=args.mode,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model_key": args.model_key,
        "model": result.get("model"),
        "is_reasoning": result.get("is_reasoning"),
        "configured_extract_mode": result.get("reasoning_extract_mode"),
        "detected_source": result.get("reasoning_source"),
        "reasoning_length": len(result.get("reasoning") or ""),
        "content_length": len(result.get("content") or ""),
        "raw_content_length": len(result.get("raw_content") or ""),
        "content_preview": _truncate(result.get("content") or ""),
        "reasoning_preview": _truncate(result.get("reasoning") or ""),
        "raw_content_preview": _truncate(result.get("raw_content") or ""),
        "raw_message_keys": result.get("raw_message_keys", []),
        "error": result.get("error", ""),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.save_report:
        out_dir = os.path.join(os.path.dirname(__file__), "reasoning_probe_reports")
        os.makedirs(out_dir, exist_ok=True)
        safe_key = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in args.model_key)
        out_path = os.path.join(out_dir, f"{safe_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        full_report: Dict[str, Any] = {**report, "full_result": result}
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(full_report, handle, ensure_ascii=False, indent=2)
        print(f"saved_report={out_path}")

    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
