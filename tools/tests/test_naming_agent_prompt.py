from SC2_Agent.data_tools import is_known_terran_entity
from SC2_Agent.naming_agent import build_naming_messages


def test_naming_prompt_includes_jargon_and_upgrade_categories():
    messages = build_naming_messages(
        race="terran",
        plan_text="Open with rax, then get stim and blue flame later.",
        terran_unit_names=["Barracks", "Marine"],
        terran_upgrade_names=["Stimpack", "HighCapacityBarrels"],
        obs_text="CommandCenter: 1 completed",
        strategy_summary="Bio pressure into mech follow-up.",
    )

    system_msg = messages[0]["content"]

    assert "[Name Hints: Jargon and Upgrade Categories]" in system_msg
    assert "rax -> Barracks" in system_msg
    assert "blue flame -> HighCapacityBarrels" in system_msg
    assert "Every output name must still exactly match" in system_msg
    assert "Infantry upgrades:" in system_msg
    assert "Vehicle/mech upgrades:" in system_msg
    assert "Do not output a whole composition from a general term alone" in system_msg


def test_terran_entity_validation_is_exact_canonical_name_only():
    assert is_known_terran_entity("Stimpack")
    assert is_known_terran_entity("BarracksTechLab")
    assert not is_known_terran_entity("stim")
    assert not is_known_terran_entity("rax")
    assert not is_known_terran_entity("TechLab")
