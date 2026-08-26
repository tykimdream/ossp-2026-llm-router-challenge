# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from decimal import Decimal

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from ossp_router.protocol import Episode, InputBatch, Outcome, OutcomeBatch


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_binomial_score_v10",
    TOOLS / "evaluate_binomial_score_v10.py",
)
assert SPEC is not None and SPEC.loader is not None
V10 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = V10
SPEC.loader.exec_module(V10)


@unittest.skipIf(np is None, "NumPy가 필요합니다.")
class BinomialScoreV10Test(unittest.TestCase):
    def test_generation_counts_reconstruct_scores(self) -> None:
        models = ("ax31-light", "ax31", "axk1-think")
        inputs = InputBatch(1, "challenge", "train", (Episode("e1", prompt="x"),))
        outcomes = OutcomeBatch(
            1,
            "challenge",
            "train",
            tuple(
                Outcome("e1", model, Decimal(score), 2, 1, 1)
                for model, score in zip(models, ("0", "0.5", "1"))
            ),
        )
        successes, trials = V10.generation_counts(inputs, outcomes)
        np.testing.assert_array_equal(((0, 1, 2),), successes)
        np.testing.assert_array_equal(((2, 2, 2),), trials)

    def test_blend_score_rows_obeys_weight(self) -> None:
        models = ("ax31-light", "ax31", "axk1-think")
        ridge = ({models[0]: 0.2, models[1]: 0.4, models[2]: 0.6},)
        binomial = np.asarray(((0.4, 0.6, 0.8),))
        blended = V10.blend_score_rows(ridge, binomial, 0.25)
        self.assertAlmostEqual(0.25, blended[0][models[0]])
        self.assertAlmostEqual(0.45, blended[0][models[1]])
        self.assertAlmostEqual(0.65, blended[0][models[2]])

    def test_blend_rejects_invalid_weight(self) -> None:
        with self.assertRaises(ValueError):
            V10.blend_score_rows(({},), np.zeros((1, 3)), 1.1)

    def test_blend_weight_parser_accepts_zero(self) -> None:
        self.assertEqual((0.0, 0.25, 1.0), V10._blend_weight_list("0,.25,1"))


if __name__ == "__main__":
    unittest.main()
