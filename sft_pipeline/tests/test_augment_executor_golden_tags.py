from __future__ import annotations

import unittest

from sft_pipeline.build_sft.augment_executor_golden_tags import (
    augment_executor_golden_records,
    augment_executor_record,
    apply_tag_remap_to_user,
    build_tag_remap,
    non_identity_remap,
    validate_augmented_record,
)
from sft_pipeline.common.executor_golden_rank import parse_candidates, rank_executor_prompt


def _record(system: str, user: str, *, golden_tags: list[int], record_id: str) -> dict:
    result = rank_executor_prompt(system, user)
    return {
        "record_id": record_id,
        "system": system,
        "user": user,
        "ability": result.ability,
        "golden_tags": golden_tags,
        "golden_rank": result.to_dict(),
    }


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


class AugmentExecutorGoldenTagsTests(unittest.TestCase):
    def test_build_tag_remap_shuffle_is_bijection(self) -> None:
        mapping = build_tag_remap([417, 985, 62], rng=__import__("random").Random(1), strategy="shuffle")
        self.assertEqual(set(mapping.keys()), {417, 985, 62})
        self.assertEqual(len(set(mapping.values())), 3)

    def test_apply_tag_remap_to_user(self) -> None:
        user = _user("tag=417 BARRACKS [idle, has TechLab]", "tag=985 BARRACKS [idle, no add-on]")
        remapped = apply_tag_remap_to_user(user, {417: 731, 985: 156})
        self.assertIn("tag=731 BARRACKS [idle, has TechLab]", remapped)
        self.assertIn("tag=156 BARRACKS [idle, no add-on]", remapped)
        self.assertNotIn("tag=417", remapped)
        self.assertNotIn("tag=985", remapped)

    def test_augment_record_preserves_golden_semantics(self) -> None:
        system = _system("BARRACKSTRAIN_MARINE", ["BARRACKSTRAIN_MARINE"])
        user = _user("tag=417 BARRACKS [idle, has TechLab]", "tag=985 BARRACKS [idle, no add-on]")
        ranked = rank_executor_prompt(system, user)
        original = _record(system, user, golden_tags=ranked.golden_tags, record_id="sample-1")
        augmented = augment_executor_record(original, rng=__import__("random").Random(7))
        self.assertTrue(augmented["record_id"].endswith("__tagaug"))
        self.assertEqual(validate_augmented_record(original, augmented), [])
        self.assertNotEqual(augmented["user"], original["user"])

        old_tags = [candidate.tag for candidate in parse_candidates(str(original["user"]))]
        new_tags = [candidate.tag for candidate in parse_candidates(str(augmented["user"]))]
        self.assertEqual(set(old_tags), set(new_tags))

    def test_non_identity_remap_for_two_tags(self) -> None:
        mapping = non_identity_remap([10, 20], rng=__import__("random").Random(0), strategy="shuffle")
        self.assertNotEqual(mapping[10], 10)
        self.assertNotEqual(mapping[20], 20)

    def test_augment_executor_golden_records_doubles_and_shuffles(self) -> None:
        records = [
            _record(
                _system("BARRACKSTRAIN_MARINE"),
                _user("tag=100 BARRACKS [idle, has TechLab]", "tag=200 BARRACKS [idle, no add-on]"),
                golden_tags=[100],
                record_id="a",
            ),
            _record(
                _system("COMMANDCENTERTRAIN_SCV"),
                _user("tag=233 ORBITALCOMMAND [idle]", "tag=289 COMMANDCENTER [idle]"),
                golden_tags=[233],
                record_id="b",
            ),
        ]
        merged, report = augment_executor_golden_records(records, seed=99, shuffle_output=True)
        self.assertEqual(len(merged), 4)
        self.assertEqual(report["output_total"], 4)
        self.assertEqual(report["validation_error_count"], 0)


if __name__ == "__main__":
    unittest.main()
