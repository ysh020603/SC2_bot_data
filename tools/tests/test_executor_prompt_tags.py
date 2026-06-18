import sys
import types
from types import SimpleNamespace

from SC2_Agent.executor_agent import build_executor_messages, parse_executor_response

sc2_module = types.ModuleType("sc2")
sc2_ids_module = types.ModuleType("sc2.ids")
sc2_ability_module = types.ModuleType("sc2.ids.ability_id")
sc2_ability_module.AbilityId = object
sys.modules.setdefault("sc2", sc2_module)
sys.modules.setdefault("sc2.ids", sc2_ids_module)
sys.modules.setdefault("sc2.ids.ability_id", sc2_ability_module)

from SC2_Agent.execution.executor_select import (
    candidates_text,
    executor_conflict_hints,
    prompt_tag_aliases,
)


def _unit(tag, name="BARRACKS"):
    return SimpleNamespace(tag=tag, type_id=SimpleNamespace(name=name))


def test_executor_prompt_uses_short_tags_and_parser_maps_back():
    candidates = [
        (_unit(4358144001), "idle, no add-on"),
        (_unit(4357095425), "idle, no add-on"),
        (_unit(4359192577), "idle, no add-on"),
    ]
    aliases = prompt_tag_aliases(candidates)
    reverse = {short: real for real, short in aliases.items()}

    rendered = candidates_text(candidates, tag_aliases=aliases)

    assert "tag=4358144001" not in rendered
    assert "tag=1 BARRACKS" in rendered
    assert "tag=425 BARRACKS" in rendered
    assert "tag=577 BARRACKS" in rendered
    assert parse_executor_response("[577]", legal_tags=set(reverse), tag_map=reverse) == 4359192577


def test_executor_prompt_conflict_section_and_hints_are_action_names_only():
    candidates = [(_unit(4358144001), "idle, no add-on")]

    hints = executor_conflict_hints(candidates, ["BARRACKSTRAIN_MARINE", "BARRACKSTRAIN_MARINE"])
    messages = build_executor_messages(
        ability_name="BARRACKSTRAIN_MARINE",
        candidate_units_text=candidates_text(candidates, tag_aliases=prompt_tag_aliases(candidates)),
        cost_hint="minerals 50, gas 0, supply 1.0, ~18s",
        pending_actions_summary="  - BARRACKSTRAIN_MARINE x4 (0/4 issued) [PENDING]",
        waiting_actions_summary="  (none)",
        executor_conflict_hints=hints,
    )

    assert hints == "  - BARRACKSTRAIN_MARINE"
    assert "Possible conflicts in pending actions" in messages[0]["content"]
    assert "Possible conflicts between candidates and pending actions" not in messages[0]["content"]
    assert "#tag" not in hints
