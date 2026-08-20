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


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_score_objectives_v7",
    TOOLS / "evaluate_score_objectives_v7.py",
)
assert SPEC is not None and SPEC.loader is not None
SCORE_OBJECTIVES = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCORE_OBJECTIVES
SPEC.loader.exec_module(SCORE_OBJECTIVES)


@unittest.skipIf(np is None, "NumPy가 필요합니다.")
class ScoreObjectivesV7Test(unittest.TestCase):
    def test_uplift_target_round_trip(self) -> None:
        scores = np.asarray(((0.25, 0.75, 1.0), (0.5, 0.0, 0.75)))
        transformed = SCORE_OBJECTIVES.uplift_targets(scores)
        expected = np.asarray(((0.25, 0.5, 0.75), (0.5, -0.5, 0.25)))
        np.testing.assert_allclose(expected, transformed)
        np.testing.assert_allclose(
            scores, SCORE_OBJECTIVES.reconstruct_uplift_scores(transformed)
        )

    def test_weighted_ridge_returns_finite_predictions(self) -> None:
        matrix = np.asarray(((0.0, 1.0), (1.0, 0.0), (1.0, 1.0), (2.0, 1.0)))
        scores = np.asarray(
            ((0.0, 0.5, 1.0), (0.5, 0.5, 1.0), (1.0, 0.5, 0.5), (1.0, 1.0, 1.0))
        )
        weights = np.asarray(((2.0, 4.0, 2.0),) * 4)
        heads = SCORE_OBJECTIVES._weighted_score_fit(
            matrix, scores, weights, alpha=10.0
        )
        predictions = SCORE_OBJECTIVES._weighted_score_predict(matrix, heads)
        self.assertEqual(scores.shape, predictions.shape)
        self.assertTrue(np.isfinite(predictions).all())
        self.assertTrue(((0.0 <= predictions) & (predictions <= 1.0)).all())


if __name__ == "__main__":
    unittest.main()
