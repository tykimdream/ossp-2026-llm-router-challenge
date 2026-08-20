# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

from ossp_router.protocol import Episode

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_router_v8",
    TOOLS / "evaluate_router_v8.py",
)
assert SPEC is not None and SPEC.loader is not None
V8 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = V8
SPEC.loader.exec_module(V8)


@unittest.skipIf(np is None, "NumPy가 필요합니다.")
class RouterV8StudyTest(unittest.TestCase):
    def test_template_fold_mixes_bits_and_keeps_groups_together(self) -> None:
        keys = np.asarray((2, 2, 4, 6, 8, 10, 12, 14), dtype=np.uint64)
        folds = V8.template_fold_ids(keys, 3, salt=1234)
        self.assertEqual(folds[0], folds[1])
        self.assertGreater(len(set(int(value) for value in folds)), 1)

    def test_risk_prune_removes_low_value_upgrade(self) -> None:
        selected = ("ax31", "ax31")
        scores = (
            {"ax31-light": 0.5, "ax31": 0.51, "axk1-think": 0.51},
            {"ax31-light": 0.5, "ax31": 0.9, "axk1-think": 0.9},
        )
        risk = (
            {"ax31-light": 1.0, "ax31": 2.0, "axk1-think": 3.0},
            {"ax31-light": 1.0, "ax31": 2.0, "axk1-think": 3.0},
        )
        baseline = (
            {"ax31-light": 1.0, "ax31": 1.5, "axk1-think": 2.0},
            {"ax31-light": 1.0, "ax31": 1.5, "axk1-think": 2.0},
        )
        self.assertEqual(
            ("ax31-light", "ax31"),
            V8._risk_prune(
                selected,
                scores,
                risk,
                baseline,
                budget_multiplier=1.5,
                reserve=1.0,
            ),
        )

    def test_candidate_name_round_trip(self) -> None:
        self.assertEqual((0.5, 0.8, 0.975), V8._parse_candidate("rw0.5-q0.8-r0.975"))

    def test_nearest_quantile_uses_finite_sample_upper_rank(self) -> None:
        self.assertEqual(4.0, V8.nearest_quantile(np.asarray((1, 2, 3, 4)), 0.8))
        with self.assertRaises(ValueError):
            V8.nearest_quantile(np.asarray(()), 0.8)

    def test_candidate_rank_prioritizes_fewer_budget_failures(self) -> None:
        unsafe_high_score = {
            "tier_budget_failures": 2,
            "whole": {"final_score": 0.9},
            "stress_final_score_mean": 0.9,
        }
        safer_low_score = {
            "tier_budget_failures": 1,
            "whole": {"final_score": 0.5},
            "stress_final_score_mean": 0.5,
        }
        self.assertGreater(
            V8.candidate_rank(safer_low_score, "safe"),
            V8.candidate_rank(unsafe_high_score, "unsafe"),
        )

    def test_study_scenarios_adds_validation_only_source_groups(self) -> None:
        words = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
        episodes = tuple(
            Episode(
                f"e{index}",
                prompt=word if index < 3 else (word + " text") * 30,
            )
            for index, word in enumerate(words)
        )
        scenarios, groups = V8.study_scenarios(
            episodes,
            seed=7,
            bootstrap_sizes=(3,),
            bootstrap_trials=1,
            mixture_size=4,
            mixture_trials=1,
            mixture_proportions=(0.5,),
            deepmind_ids=("e0", "e1"),
            aime_ids=("e2",),
        )
        self.assertEqual({"deepmind_math": 2, "aime": 1}, groups)
        self.assertGreaterEqual(len(scenarios["standalone"]), 2)
        self.assertGreaterEqual(len(scenarios["mixtures"]), 2)


if __name__ == "__main__":
    unittest.main()
