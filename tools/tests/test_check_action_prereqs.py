from SC2_Agent.data_tools.check_action_prereqs import tech_chain_relations


def _relation_keys(relations):
    return {
        (rel["action"], rel["depends_on"], rel["via_entity"])
        for rel in relations
    }


def test_tech_chain_relations_suppresses_prereqs_satisfied_by_observation():
    actions = [
        "TERRANBUILD_SUPPLYDEPOT",
        "TERRANBUILD_BARRACKS",
        "BARRACKSTRAIN_MARINE",
    ]

    baseline = _relation_keys(tech_chain_relations(actions))
    assert (
        "TERRANBUILD_BARRACKS",
        "TERRANBUILD_SUPPLYDEPOT",
        "SupplyDepot",
    ) in baseline
    assert (
        "BARRACKSTRAIN_MARINE",
        "TERRANBUILD_BARRACKS",
        "Barracks",
    ) in baseline

    with_supply_depot = _relation_keys(
        tech_chain_relations(actions, available_entities=["SupplyDepotLowered"])
    )
    assert (
        "TERRANBUILD_BARRACKS",
        "TERRANBUILD_SUPPLYDEPOT",
        "SupplyDepot",
    ) not in with_supply_depot
    assert (
        "BARRACKSTRAIN_MARINE",
        "TERRANBUILD_BARRACKS",
        "Barracks",
    ) in with_supply_depot

    with_supply_and_barracks = _relation_keys(
        tech_chain_relations(
            actions,
            available_entities=["SupplyDepotLowered", "Barracks"],
        )
    )
    assert (
        "BARRACKSTRAIN_MARINE",
        "TERRANBUILD_BARRACKS",
        "Barracks",
    ) not in with_supply_and_barracks
