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
    "evaluate_relative_cost_targets",
    TOOLS / "evaluate_relative_cost_targets.py",
)
assert SPEC is not None and SPEC.loader is not None
RELATIVE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RELATIVE
SPEC.loader.exec_module(RELATIVE)


@unittest.skipIf(np is None, "NumPy가 필요합니다.")
class RelativeCostTargetsTest(unittest.TestCase):
    def test_targets_store_light_and_strong_over_light_logs(self) -> None:
        targets = np.asarray(
            [[0.1, 0.2, 0.3, np.log(2.0), np.log(6.0), np.log(20.0)]]
        )
        relative = RELATIVE.relative_targets(targets)
        self.assertAlmostEqual(np.log(2.0), relative[0, 3])
        self.assertAlmostEqual(np.log(3.0), relative[0, 4])
        self.assertAlmostEqual(np.log(10.0), relative[0, 5])
        np.testing.assert_array_equal(targets[:, :3], relative[:, :3])

    def test_reconstruction_is_positive_and_monotonic(self) -> None:
        predictions = np.asarray(
            [
                [0.0, 0.0, 0.0, np.log(2.0), np.log(3.0), np.log(10.0)],
                [0.0, 0.0, 0.0, np.log(5.0), -100.0, -100.0],
            ]
        )
        costs = RELATIVE.reconstruct_costs(predictions)
        np.testing.assert_allclose((2.0, 6.0, 20.0), costs[0])
        self.assertTrue((costs > 0).all())
        self.assertTrue((costs[:, 1] >= costs[:, 0]).all())
        self.assertTrue((costs[:, 2] > costs[:, 1]).all())


if __name__ == "__main__":
    unittest.main()
