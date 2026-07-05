from tools import collect_terran_bo


def test_build_tasks_repeats_each_matchup_with_unique_ports(monkeypatch, tmp_path):
    monkeypatch.setattr(collect_terran_bo, "BotDefinitions", lambda: object())
    monkeypatch.setattr(
        collect_terran_bo.GameStarter,
        "installed_maps",
        staticmethod(lambda: ["KairosJunctionLE"]),
    )

    tasks, maps_used = collect_terran_bo.build_tasks(
        str(tmp_path),
        ["marine"],
        "KairosJunctionLE",
        30000,
        difficulties=["medium"],
        repeats=5,
    )

    terran_tasks = [task for task in tasks if task["enemy_race"] == "terran"]
    assert maps_used == ["KairosJunctionLE"]
    assert len(tasks) == 15
    assert [task["repeat_index"] for task in terran_tasks] == [1, 2, 3, 4, 5]
    assert len({task["port"] for task in tasks}) == len(tasks)
