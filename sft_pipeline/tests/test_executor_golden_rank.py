from __future__ import annotations

import unittest

from sft_pipeline.common.executor_golden_rank import parse_executor_prompt, rank_executor_prompt


def _system(ability: str, conflicts: list[str]) -> str:
    conflict_text = "\n".join(f"  - {c}" for c in conflicts) if conflicts else "  (none)"
    return (
        "You are a StarCraft II Terran executor selector.\n\n"
        f"[Ability to execute] {ability}\n"
        "[Pending actions not yet executed]   (none)\n"
        "[Actions currently waiting]   (none)\n"
        "[Possible conflicts in pending actions]\n"
        f"{conflict_text}\n\n"
        "Output ONLY a JSON list with exactly one tag, no prose, no markdown fences:\n"
        "[12345]"
    )


def _user(*lines: str) -> str:
    body = "\n".join(f"  - {line}" for line in lines)
    return f"[Candidate Executors]\n{body}"


class ExecutorGoldenRankTests(unittest.TestCase):
    def test_parse_ability_and_candidates(self) -> None:
        ctx = parse_executor_prompt(
            _system("BARRACKSTRAIN_MARINE", []),
            _user("tag=985 BARRACKS [idle, has TechLab]", "tag=657 BARRACKS [idle, no add-on]"),
        )
        self.assertEqual(ctx.ability, "BARRACKSTRAIN_MARINE")
        self.assertEqual(len(ctx.candidates), 2)
        self.assertEqual(ctx.candidates[0].addon, "techlab")

    def test_no_reservation_prefers_techlab_over_bare(self) -> None:
        result = rank_executor_prompt(
            _system("BARRACKSTRAIN_MARINE", ["BARRACKSTRAIN_MARINE"]),
            _user("tag=985 BARRACKS [idle, has TechLab]", "tag=657 BARRACKS [idle, no add-on]"),
        )
        self.assertEqual(result.golden_tags, [985])

    def test_addon_conflict_filters_bare(self) -> None:
        result = rank_executor_prompt(
            _system("BARRACKSTRAIN_MARINE", ["BUILD_TECHLAB_BARRACKS"]),
            _user("tag=985 BARRACKS [idle, has TechLab]", "tag=657 BARRACKS [idle, no add-on]"),
        )
        self.assertEqual(result.golden_tags, [985])

    def test_reactor_beats_techlab_when_idle(self) -> None:
        result = rank_executor_prompt(
            _system("BARRACKSTRAIN_MARINE", ["BUILD_REACTOR_BARRACKS"]),
            _user(
                "tag=234 BARRACKS [idle, has Reactor]",
                "tag=251 BARRACKS [idle, has TechLab]",
                "tag=137 BARRACKS [idle, no add-on]",
            ),
        )
        self.assertEqual(result.golden_tags, [234])

    def test_busy_progress_prefers_higher_completion(self) -> None:
        result = rank_executor_prompt(
            _system("COMMANDCENTERTRAIN_SCV", ["COMMANDCENTERTRAIN_SCV"]),
            _user(
                "tag=233 ORBITALCOMMAND [busy: Train SCV (97%)]",
                "tag=857 ORBITALCOMMAND [busy: Train SCV (23%)]",
            ),
        )
        self.assertEqual(result.golden_tags, [233])

    def test_idle_oc_beats_idle_cc(self) -> None:
        result = rank_executor_prompt(
            _system("COMMANDCENTERTRAIN_SCV", ["COMMANDCENTERTRAIN_SCV"]),
            _user("tag=233 ORBITALCOMMAND [idle]", "tag=289 COMMANDCENTER [idle]"),
        )
        self.assertEqual(result.golden_tags, [233])

    def test_upgrade_conflict_filters_unupgraded_cc(self) -> None:
        result = rank_executor_prompt(
            _system("COMMANDCENTERTRAIN_SCV", ["UPGRADETOORBITAL_ORBITALCOMMAND"]),
            _user("tag=233 ORBITALCOMMAND [idle]", "tag=289 COMMANDCENTER [idle]"),
        )
        self.assertEqual(result.golden_tags, [233])

    def test_multiple_idle_reactors_kept(self) -> None:
        result = rank_executor_prompt(
            _system("BARRACKSTRAIN_MARINE", []),
            _user(
                "tag=100 BARRACKS [idle, has Reactor]",
                "tag=200 BARRACKS [idle, has Reactor]",
            ),
        )
        self.assertEqual(set(result.golden_tags), {100, 200})


if __name__ == "__main__":
    unittest.main()
