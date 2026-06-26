from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator


def read_json(path: str | os.PathLike[str]) -> Any:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str | os.PathLike[str], data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def write_jsonl(path: str | os.PathLike[str], rows: Iterable[dict[str, Any]]) -> int:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def reset_jsonl(path: str | os.PathLike[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("", encoding="utf-8")


def append_jsonl(path: str | os.PathLike[str], row: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def iter_sequence_files(root: str | os.PathLike[str]) -> Iterator[Path]:
    root_path = Path(root)
    if root_path.is_file() and root_path.suffix.lower() == ".json":
        yield root_path
        return
    for path in root_path.rglob("*.json"):
        if path.name in {"summary.json", "results.json", "step_index.json", "manifest.json"}:
            continue
        try:
            data = read_json(path)
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("sequence"), list) and data.get("order_list"):
            yield path


def safe_stem(text: str) -> str:
    safe = []
    for char in text:
        if char.isalnum() or char in ("-", "_"):
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "sample"
