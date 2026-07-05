from types import SimpleNamespace

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

import sharpy.managers.extensions.ability_recorder as recorder_module
from sharpy.managers.extensions.ability_recorder import AbilityRecorderManager


class FakeUnits(list):
    def find_by_tag(self, tag):
        return next((unit for unit in self if unit.tag == tag), None)


class FakeCache:
    def __init__(self, ai):
        self.ai = ai

    def by_tag(self, tag):
        return next(
            (unit for unit in self.ai.units + self.ai.structures if unit.tag == tag),
            None,
        )


def make_order(ability):
    return SimpleNamespace(ability=SimpleNamespace(id=ability))


def make_unit(tag, type_id, position=(0, 0), orders=None, build_progress=1.0):
    return SimpleNamespace(
        tag=tag,
        type_id=type_id,
        position=Point2(position),
        orders=list(orders or []),
        build_progress=build_progress,
        add_on_tag=0,
    )


def make_action(actor, ability, target=None):
    return SimpleNamespace(unit=actor, ability=ability, target=target)


def make_recorder(monkeypatch, units):
    ai = SimpleNamespace(time=0.0, units=FakeUnits(units), structures=FakeUnits())
    ai.unit_cache = FakeCache(ai)

    recorder = AbilityRecorderManager()
    recorder.ai = ai
    recorder.knowledge = SimpleNamespace(iteration=1)
    recorder.enabled = True
    monkeypatch.setattr(recorder_module, "collect_entities", lambda *_args: {})
    return recorder, ai


def test_build_order_is_not_committed_until_structure_exists(monkeypatch):
    worker = make_unit(1, UnitTypeId.SCV, position=(10, 10))
    recorder, ai = make_recorder(monkeypatch, [worker])
    target = Point2((20, 20))

    recorder.record(make_action(worker, AbilityId.TERRANBUILD_SUPPLYDEPOT, target))
    worker.orders = [make_order(AbilityId.TERRANBUILD_SUPPLYDEPOT)]
    recorder._resolve_pending()

    assert recorder.sequence == []
    assert len(recorder._pending) == 1

    depot = make_unit(
        100,
        UnitTypeId.SUPPLYDEPOT,
        position=(20, 20),
        build_progress=0.05,
    )
    ai.structures.append(depot)
    ai.time = 1.0
    recorder._resolve_pending()

    assert len(recorder.sequence) == 1
    assert recorder.sequence[0]["ability"] == "TERRANBUILD_SUPPLYDEPOT"
    assert recorder.sequence[0]["confirmation"]["kind"] == "structure_started"
    assert recorder.sequence[0]["confirmation"]["entity_tag"] == 100
    assert recorder.sequence[0]["confirmation"]["actor_tag"] == 1


def test_one_structure_confirms_only_one_of_many_same_place_commands(monkeypatch):
    workers = [make_unit(tag, UnitTypeId.SCV) for tag in range(1, 5)]
    recorder, ai = make_recorder(monkeypatch, workers)
    target = Point2((30, 30))

    for worker in workers:
        recorder.record(make_action(worker, AbilityId.TERRANBUILD_SUPPLYDEPOT, target))
        worker.orders = [make_order(AbilityId.TERRANBUILD_SUPPLYDEPOT)]

    ai.structures.append(
        make_unit(200, UnitTypeId.SUPPLYDEPOT, position=(30, 30), build_progress=0.01)
    )
    ai.time = 1.0
    recorder._resolve_pending()

    assert len(recorder.sequence) == 1
    assert recorder._pending == {}
    assert recorder._superseded_unconfirmed_count == 3


def test_train_requires_unit_created_event(monkeypatch):
    marine_order = make_order(AbilityId.BARRACKSTRAIN_MARINE)
    barracks = make_unit(
        10,
        UnitTypeId.BARRACKS,
        orders=[marine_order],
    )
    recorder, ai = make_recorder(monkeypatch, [barracks])

    recorder.record(make_action(barracks, AbilityId.BARRACKSTRAIN_MARINE))
    recorder._resolve_pending()
    assert recorder.sequence == []

    marine = make_unit(20, UnitTypeId.MARINE, position=(1, 0))
    ai.time = 18.0
    recorder.on_unit_created(marine)
    recorder._resolve_pending()

    assert len(recorder.sequence) == 1
    assert recorder.sequence[0]["confirmation"]["kind"] == "unit_created"
    assert recorder.sequence[0]["confirmation"]["entity_tag"] == 20


def test_addon_requires_new_addon_entity(monkeypatch):
    barracks = make_unit(10, UnitTypeId.BARRACKS, position=(10, 10))
    recorder, ai = make_recorder(monkeypatch, [barracks])

    recorder.record(make_action(barracks, AbilityId.BUILD_TECHLAB))
    barracks.orders = [make_order(AbilityId.BUILD_TECHLAB)]
    recorder._resolve_pending()
    assert recorder.sequence == []

    addon = make_unit(
        300,
        UnitTypeId.BARRACKSTECHLAB,
        position=(12.5, 9.5),
        build_progress=0.1,
    )
    barracks.add_on_tag = addon.tag
    ai.structures.append(addon)
    # Addons take longer than the generic order timeout to materialize.
    ai.time = 9.0
    recorder._resolve_pending()

    assert len(recorder.sequence) == 1
    assert recorder.sequence[0]["ability"] == "BUILD_TECHLAB_BARRACKS"
    assert recorder.sequence[0]["confirmation"]["entity_tag"] == 300


def test_issue_time_observation_is_kept_until_outcome(monkeypatch):
    barracks = make_unit(10, UnitTypeId.BARRACKS)
    recorder, ai = make_recorder(monkeypatch, [barracks])
    monkeypatch.setattr(
        recorder,
        "_capture_obs",
        lambda _bot: {"structured": {"captured_at": ai.time}, "text": ""},
    )

    ai.time = 3.0
    recorder.record(make_action(barracks, AbilityId.BARRACKSTRAIN_MARINE))
    ai.time = 21.0
    recorder.on_unit_created(make_unit(20, UnitTypeId.MARINE, position=(1, 0)))

    assert recorder.sequence[0]["game_time"] == 3.0
    assert recorder.sequence[0]["confirmed_time"] == 21.0
    assert recorder.sequence[0]["obs"]["structured"]["captured_at"] == 3.0


def test_morph_requires_unit_type_changed_event(monkeypatch):
    command_center = make_unit(10, UnitTypeId.COMMANDCENTER)
    recorder, ai = make_recorder(monkeypatch, [command_center])
    recorder.record(
        make_action(command_center, AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND)
    )
    recorder._resolve_pending()
    assert recorder.sequence == []

    command_center.type_id = UnitTypeId.ORBITALCOMMAND
    ai.time = 25.0
    recorder.on_unit_type_changed(command_center, UnitTypeId.COMMANDCENTER)
    assert recorder.sequence[0]["confirmation"]["kind"] == "unit_type_changed"
    assert recorder.sequence[0]["ability"] == "UPGRADETOORBITAL_ORBITALCOMMAND"


def test_research_requires_upgrade_complete_event(monkeypatch):
    techlab = make_unit(10, UnitTypeId.BARRACKSTECHLAB)
    recorder, ai = make_recorder(monkeypatch, [techlab])
    recorder.record(
        make_action(techlab, AbilityId.BARRACKSTECHLABRESEARCH_STIMPACK)
    )
    recorder._resolve_pending()
    assert recorder.sequence == []

    ai.time = 90.0
    recorder.on_upgrade_complete(UpgradeId.STIMPACK)
    assert recorder.sequence[0]["confirmation"]["kind"] == "upgrade_completed"
    assert recorder.sequence[0]["confirmation"]["upgrade"] == "STIMPACK"
