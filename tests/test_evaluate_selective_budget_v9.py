# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from ossp_router.protocol import Episode, InputBatch


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_selective_budget_v9",
    TOOLS / "evaluate_selective_budget_v9.py",
)
assert SPEC is not None and SPEC.loader is not None
V9 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = V9
SPEC.loader.exec_module(V9)


@unittest.skipIf(np is None, "NumPy가 필요합니다.")
class SelectiveBudgetV9Test(unittest.TestCase):
    def test_uplift_targets_are_relative_to_light(self) -> None:
        scores = np.asarray(((0.2, 0.5, 0.8), (0.7, 0.6, 0.9)))
        np.testing.assert_allclose(
            np.asarray(((0.3, 0.6), (-0.1, 0.2))),
            V9.uplift_targets(scores),
        )

    def test_candidate_name_is_stable(self) -> None:
        candidate = V9.Candidate(3000.0, 0.75, 0.2, 0.5)
        self.assertEqual("a3000-w0.75-t0.2-r0.5", candidate.name)

    def test_selective_upgrade_respects_recovered_cap(self) -> None:
        models = ("ax31-light", "ax31", "axk1-think")
        selected = (models[0], models[0])
        scores = (
            {models[0]: 0.2, models[1]: 0.8, models[2]: 0.9},
            {models[0]: 0.2, models[1]: 0.7, models[2]: 0.9},
        )
        costs = (
            {models[0]: 1.0, models[1]: 1.4, models[2]: 3.0},
            {models[0]: 1.0, models[1]: 1.4, models[2]: 3.0},
        )
        uplift = np.asarray(((0.6, 0.7), (0.5, 0.7)))
        result, ratio, upgrades = V9.selective_upgrades(
            selected,
            scores,
            costs,
            uplift,
            (0, 1),
            tier="fast",
            budget_multiplier=1.25,
            base_safety_ratio=0.92,
            safety_multiplier=1.0,
            candidate=V9.Candidate(3000.0, 1.0, 0.1, 0.5),
        )
        self.assertEqual((models[1], models[0]), result)
        self.assertAlmostEqual(1.2, ratio)
        self.assertEqual(1, upgrades)

    def test_zero_recovery_does_not_exceed_base_cap(self) -> None:
        models = ("ax31-light", "ax31", "axk1-think")
        result, ratio, upgrades = V9.selective_upgrades(
            (models[0],),
            ({models[0]: 0.2, models[1]: 0.9, models[2]: 1.0},),
            ({models[0]: 1.0, models[1]: 1.2, models[2]: 2.0},),
            np.asarray(((0.7, 0.8),)),
            (0,),
            tier="fast",
            budget_multiplier=1.25,
            base_safety_ratio=0.9,
            safety_multiplier=1.0,
            candidate=V9.Candidate(3000.0, 1.0, 0.1, 0.0),
        )
        self.assertEqual((models[0],), result)
        self.assertAlmostEqual(1.0, ratio)
        self.assertEqual(0, upgrades)

    def test_recovery_preserves_adaptive_shift_penalty(self) -> None:
        models = ("ax31-light", "ax31", "axk1-think")
        result, ratio, upgrades = V9.selective_upgrades(
            (models[0],),
            ({models[0]: 0.2, models[1]: 0.9, models[2]: 1.0},),
            ({models[0]: 1.0, models[1]: 1.2, models[2]: 2.0},),
            np.asarray(((0.7, 0.8),)),
            (0,),
            tier="fast",
            budget_multiplier=1.25,
            base_safety_ratio=0.9,
            safety_multiplier=0.5,
            candidate=V9.Candidate(3000.0, 1.0, 0.1, 1.0),
        )
        self.assertEqual((models[0],), result)
        self.assertAlmostEqual(1.0, ratio)
        self.assertEqual(0, upgrades)

    def test_study_scenarios_add_source_groups(self) -> None:
        words = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")
        inputs = InputBatch(
            1,
            "challenge",
            "train",
            tuple(
                Episode(
                    f"e{index}",
                    prompt=(words[index] if index < 4 else words[index] * 30),
                )
                for index in range(8)
            ),
        )
        scenarios, groups = V9.study_scenarios(
            inputs,
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
