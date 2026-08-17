# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest

from ossp_router import submission
from ossp_router.protocol import load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/generate_edge_cases.py"
SPEC = importlib.util.spec_from_file_location("generate_edge_cases", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
edge_cases = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = edge_cases
SPEC.loader.exec_module(edge_cases)


class GenerateEdgeCasesTest(unittest.TestCase):
    def test_guard_constants_stay_in_sync_with_submission_router(self) -> None:
        self.assertEqual(
            submission.MAX_LEARNED_EPISODES,
            edge_cases.LEARNED_EPISODE_LIMIT,
        )
        self.assertEqual(
            submission.MAX_LEARNED_CHARACTERS,
            edge_cases.LEARNED_CHARACTER_LIMIT,
        )

    def test_k1_pressure_is_valid_and_has_5000_unique_episodes(self) -> None:
        spec = next(
            item for item in edge_cases.CASES if item.name == "k1-pressure-5000"
        )
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            manifest = edge_cases.generate_cases(output, (spec,))
            inputs = load_input(output / "k1-pressure-5000.json")
        self.assertEqual(5_000, len(inputs.episodes))
        self.assertEqual(5_000, len({item.episode_id for item in inputs.episodes}))
        self.assertEqual("learned", manifest["cases"][0]["expected_router_path"])

    def test_order_audit_pair_has_same_content_in_reverse_with_new_ids(self) -> None:
        original = edge_cases._build_order_audit(False)
        changed = edge_cases._build_order_audit(True)
        self.assertEqual(
            [item["prompt"] for item in original],
            list(reversed([item["prompt"] for item in changed])),
        )
        self.assertTrue(
            set(item["episode_id"] for item in original).isdisjoint(
                item["episode_id"] for item in changed
            )
        )

    def test_output_boundary_differs_by_exactly_one_episode(self) -> None:
        under, over = edge_cases._output_boundary_counts()
        self.assertEqual(under + 1, over)
        self.assertLessEqual(
            edge_cases._submission_bytes_for_output_case(under),
            edge_cases.OUTPUT_LIMIT_BYTES,
        )
        self.assertGreater(
            edge_cases._submission_bytes_for_output_case(over),
            edge_cases.OUTPUT_LIMIT_BYTES,
        )

    def test_repeat_to_length_hits_exact_boundary(self) -> None:
        self.assertEqual("abcab", edge_cases._repeat_to_length("abc", 5))
        self.assertEqual(5, len(edge_cases._repeat_to_length("한글", 5)))

    def test_token_density_has_half_a_million_tokens(self) -> None:
        episodes = edge_cases._build_token_density()
        prompt = episodes[0]["prompt"]
        self.assertEqual(1_000_000, len(prompt))
        self.assertEqual(500_000, len(prompt.split()))


if __name__ == "__main__":
    unittest.main()
