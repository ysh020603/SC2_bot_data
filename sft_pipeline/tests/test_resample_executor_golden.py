from __future__ import annotations

import unittest

from sft_pipeline.build_sft.resample_executor_golden import (
    apply_oversample_cap,
    equal_layer_action_targets,
    resample_executor_golden,
)
from sft_pipeline.common.executor_golden_rank import classify_executor_rule_layer_from_prompt


def _record(system: str, user: str, *, tag: int = 1) -> dict:
    return {"system": system, "user": user, "golden_tags": [tag], "record_id": f"r{tag}"}


def _system(ability: str, conflicts: list[str] | None = None) -> str:
    conflict_text = "\n".join(f"  - {c}" for c in (conflicts or [])) or "  (none)"
    return (
        f"[Ability to execute] {ability}\n"
        "[Pending actions not yet executed]   (none)\n"
        "[Actions currently waiting]   (none)\n"
        "[Possible conflicts in pending actions]\n"
        f"{conflict_text}\n\n"
        "Output ONLY a JSON list with exactly one tag.\n"
        "[123]"
    )


def _user(*lines: str) -> str:
    body = "\n".join(f"  - {line}" for line in lines)
    return f"[Candidate Executors]\n{body}"


class ResampleExecutorGoldenTests(unittest.TestCase):
    def test_equal_layer_action_targets(self) -> None:
        targets = equal_layer_action_targets("L1", ["A", "B", "C"], 10)
        self.assertEqual(sum(targets.values()), 10)
        self.assertEqual(set(targets.values()), {3, 4})

    def test_apply_oversample_cap_redistributes_to_marine(self) -> None:
        requested = {"BARRACKSTRAIN_MARINE": 100, "STARPORTTRAIN_VIKINGFIGHTER": 100}
        pool_sizes = {"BARRACKSTRAIN_MARINE": 131, "STARPORTTRAIN_VIKINGFIGHTER": 3}
        effective, events, remaining = apply_oversample_cap(
            layer="L0",
            requested=requested,
            pool_sizes=pool_sizes,
            max_oversample_ratio=10,
        )
        self.assertEqual(effective["STARPORTTRAIN_VIKINGFIGHTER"], 30)
        self.assertEqual(effective["BARRACKSTRAIN_MARINE"], 170)
        self.assertEqual(sum(effective.values()), 200)
        self.assertEqual(remaining, 0)
        self.assertTrue(any("redistributed_in" in event for event in events))

    def test_resample_balanced_total(self) -> None:
        records: list[dict] = []
        tag = 1
        for _ in range(5):
            records.append(
                _record(
                    _system("BARRACKSTRAIN_MARINE", []),
                    _user("tag=100 BARRACKS [idle, has TechLab]", "tag=200 BARRACKS [idle, no add-on]"),
                    tag=tag,
                )
            )
            tag += 1
        for _ in range(3):
            records.append(
                _record(
                    _system("COMMANDCENTERTRAIN_SCV", []),
                    _user("tag=233 ORBITALCOMMAND [idle]", "tag=289 COMMANDCENTER [idle]"),
                    tag=tag,
                )
            )
            tag += 1
        kept, report = resample_executor_golden(records, layer_target=2, seed=1, max_oversample_ratio=3)
        self.assertEqual(report["output_total"], len(kept))
        self.assertEqual(sum(report["by_layer"].values()), len(kept))

    def test_classify_layers(self) -> None:
        ability, layer = classify_executor_rule_layer_from_prompt(
            _system("BARRACKSTRAIN_MARINE", []),
            _user("tag=985 BARRACKS [idle, has TechLab]", "tag=657 BARRACKS [idle, no add-on]"),
        )
        self.assertEqual(ability, "BARRACKSTRAIN_MARINE")
        self.assertEqual(layer, "L2")


if __name__ == "__main__":
    unittest.main()
