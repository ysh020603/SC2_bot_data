from SC2_Agent.ordering_agent import build_ordering_messages


def test_ordering_prompt_includes_strategy_step_without_changing_multiset_rule():
    messages = build_ordering_messages(
        race="terran",
        actions=["TERRANBUILD_BARRACKS", "BARRACKSTRAIN_MARINE", "BARRACKSTRAIN_MARINE"],
        obs_text="Barracks: 1 completed",
        prereq_hints="Barracks before Marine",
        conflict_hints="Barracks trains Marines sequentially",
        cost_hints="Marine: minerals 50",
        strategy_step_text="Build one Barracks first, then start Marine production.",
    )

    system_msg = messages[0]["content"]
    user_msg = messages[1]["content"]

    assert "Use the Strategy Step to understand strategic priority" in system_msg
    assert "negative supply" in system_msg
    assert "Do not add or remove actions" in system_msg
    assert "[Strategy Step]" in user_msg
    assert "Build one Barracks first, then start Marine production." in user_msg
    assert "BARRACKSTRAIN_MARINE x 2" in user_msg
