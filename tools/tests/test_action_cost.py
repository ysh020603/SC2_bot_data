from SC2_Agent.data_tools.action_cost import cost_for_action


def test_supply_provider_cost_keeps_negative_supply_delta():
    cost = cost_for_action("TERRANBUILD_SUPPLYDEPOT")["cost"]

    assert cost["supply"] == -8.0


def test_townhall_morphs_do_not_add_extra_supply_headroom():
    orbital_cost = cost_for_action("UPGRADETOORBITAL_ORBITALCOMMAND")["cost"]
    planetary_cost = cost_for_action("UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS")["cost"]

    assert orbital_cost["supply"] == 0
    assert planetary_cost["supply"] == 0
