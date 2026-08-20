# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import pathlib
import random
import sys
import unittest

from ossp_router.protocol import Episode, Message, load_bundled_policy


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "evaluate_router_robustness",
    ROOT / "tools/evaluate_router_robustness.py",
)
assert SPEC is not None and SPEC.loader is not None
ROBUSTNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ROBUSTNESS
SPEC.loader.exec_module(ROBUSTNESS)


class RouterRobustnessEvaluationTest(unittest.TestCase):
    def test_content_groups_keep_provenance_separate_from_content(self) -> None:
        episodes = (
            Episode("opaque-a", prompt="한글 질문입니다. 답을 고르세요."),
            Episode("opaque-b", prompt="```python\ndef f(x): return x + 1\n```"),
            Episode("opaque-c", prompt="x" * 8_001),
            Episode(
                "opaque-d",
                messages=(Message("system", "be concise"), Message("user", "hi")),
            ),
        )
        groups = ROBUSTNESS.content_groups(
            episodes,
            deepmind_ids=("opaque-b",),
            aime_ids=("opaque-c",),
        )
        self.assertEqual((0,), groups["korean_hangul_10pct"])
        self.assertEqual((1,), groups["code_marker"])
        self.assertEqual((2,), groups["long_context_gt_8000"])
        self.assertEqual((3,), groups["messages"])
        self.assertEqual((1,), groups["deepmind_math"])
        self.assertEqual((2,), groups["aime"])

    def test_seeded_bootstrap_and_mixture_are_reproducible(self) -> None:
        expected = ROBUSTNESS._bootstrap_indices((0, 1, 2), 4, 3, random.Random(7))
        actual = ROBUSTNESS._bootstrap_indices((0, 1, 2), 4, 3, random.Random(7))
        self.assertEqual(expected, actual)
        expected_mix = ROBUSTNESS._mixture_indices(
            (0, 1), (2, 3), 8, 0.25, 2, random.Random(11)
        )
        actual_mix = ROBUSTNESS._mixture_indices(
            (0, 1), (2, 3), 8, 0.25, 2, random.Random(11)
        )
        self.assertEqual(expected_mix, actual_mix)
        self.assertTrue(
            all(sum(index < 2 for index in row) == 2 for row in actual_mix)
        )

    def test_trial_summary_counts_budget_failures(self) -> None:
        policy = load_bundled_policy()
        reports = []
        for ratio in (1.1, 1.3):
            tiers = {}
            for tier in ("fast", "balanced", "premium"):
                limit = float(policy.tiers[tier].budget_multiplier)
                actual = ratio if tier == "fast" else 1.0
                tiers[tier] = {
                    "quality_score": 0.75,
                    "budget_ratio": actual,
                    "budget_passed": actual <= limit,
                }
            reports.append(
                {
                    "final_score": 0.75 if ratio <= 1.25 else 0.45,
                    "all_tiers_passed": ratio <= 1.25,
                    "tiers": tiers,
                }
            )
        summary = ROBUSTNESS.summarize_trials(reports, policy)
        self.assertEqual(1, summary["tiers"]["fast"]["budget_failures"])
        self.assertEqual(0.5, summary["tiers"]["fast"]["budget_failure_rate"])
        self.assertEqual(1, summary["all_tier_failure_trials"])
        self.assertAlmostEqual(0.6, summary["final_score_mean"])

    def test_nearest_rank_rejects_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            ROBUSTNESS._nearest_rank((), 0.95)


if __name__ == "__main__":
    unittest.main()
