"""Strategy file parsing helpers.

The runtime no longer uses an interactive opening strategy chooser. A strategy
must be specified explicitly by folder name, then this module only parses that
folder's ``Top_agent_0.md`` into summary/detail text.
"""

from __future__ import annotations

import re
from typing import Dict


# Compatible with "# Summary" / "# Abstract" and the older Chinese heading.
_SUMMARY_HEADER_RE = re.compile(
    r"^\s*#\s*(?:\u6458\u8981|Summary|Abstract)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# Compatible with "# Detail" / "# Details" / "# Full" / "# Content" and the
# older Chinese heading.
_DETAIL_HEADER_RE = re.compile(
    r"^\s*#\s*(?:\u8be6\u7ec6\u5185\u5bb9|Detail|Details|Full|Content)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def parse_top_agent_0_md(text: str) -> Dict[str, str]:
    """Split ``Top_agent_0.md`` into ``{"summary", "detail"}``.

    If the file has no explicit headings, the whole file becomes ``detail`` and
    the first non-empty paragraph is used as a fallback summary.
    """
    if not text:
        return {"summary": "", "detail": ""}
    raw = text.strip()

    summary_match = _SUMMARY_HEADER_RE.search(raw)
    detail_match = _DETAIL_HEADER_RE.search(raw)

    if summary_match and detail_match:
        if summary_match.start() < detail_match.start():
            summary = raw[summary_match.end():detail_match.start()].strip()
            detail = raw[detail_match.end():].strip()
        else:
            detail = raw[detail_match.end():summary_match.start()].strip()
            summary = raw[summary_match.end():].strip()
    elif summary_match:
        summary = raw[summary_match.end():].strip()
        detail = raw
    elif detail_match:
        detail = raw[detail_match.end():].strip()
        summary = _fallback_summary_from_detail(detail)
    else:
        detail = raw
        summary = _fallback_summary_from_detail(detail)

    return {"summary": summary, "detail": detail}


def _fallback_summary_from_detail(detail: str) -> str:
    if not detail:
        return ""
    for paragraph in detail.split("\n\n"):
        line = paragraph.strip()
        if not line:
            continue
        line = re.sub(r"^[#>*\-\+\s]+", "", line).strip()
        if line:
            return line[:500]
    return ""


__all__ = ["parse_top_agent_0_md"]
