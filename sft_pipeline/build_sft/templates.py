from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPTS = {
    "naming": {
        "thinking": "You are a StarCraft II Terran naming model. Convert the strategy step and current observation into canonical unit/upgrade names with counts. Output JSON only after reasoning. /think",
        "nothink": "You are a StarCraft II Terran naming model. Convert the strategy step and current observation into canonical unit/upgrade names with counts. Output JSON only. /no_think",
    },
    "ordering": {
        "thinking": "You are a StarCraft II Terran action-ordering model. Reorder the shuffled action multiset into the correct executable order using observation, prerequisite, cost, conflict, and executor information. Output JSON only after reasoning. /think",
        "nothink": "You are a StarCraft II Terran action-ordering model. Reorder the shuffled action multiset into the correct executable order using observation, prerequisite, cost, conflict, and executor information. Output JSON only. /no_think",
    },
    "executor": {
        "thinking": "You are a StarCraft II Terran executor selector. Pick exactly one candidate producer tag for the train action. Output JSON only after reasoning. /think",
        "nothink": "You are a StarCraft II Terran executor selector. Pick exactly one candidate producer tag for the train action. Output JSON only. /no_think",
    },
}


def assistant_value(answer: Any, mode: str, reasoning: str = "") -> str:
    if not isinstance(answer, str):
        answer = json.dumps(answer, ensure_ascii=False)
    if mode == "thinking":
        return f"<think>\n{reasoning}\n</think>\n\n{answer}"
    return answer


def sharegpt_sample(
    task: str,
    mode: str,
    user: str,
    answer: Any,
    reasoning: str = "",
    system: str | None = None,
) -> dict[str, Any]:
    return {
        "system": system if system is not None else SYSTEM_PROMPTS[task][mode],
        "conversations": [
            {"from": "human", "value": user},
            {"from": "gpt", "value": assistant_value(answer, mode, reasoning)},
        ],
    }


def dataset_info_fragment() -> dict[str, Any]:
    names = [
        "sc2_naming_qwen3_thinking_sft",
        "sc2_naming_qwen3_nothink_sft",
        "sc2_ordering_qwen3_thinking_sft",
        "sc2_ordering_qwen3_nothink_sft",
        "sc2_executor_qwen3_thinking_sft",
        "sc2_executor_qwen3_nothink_sft",
    ]
    return {
        name: {
            "file_name": f"{name}.json",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "system": "system"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }
        for name in names
    }
