# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

from ossp_router import aggressive_v7


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_adaptive_safety_v7",
    TOOLS / "evaluate_adaptive_safety_v7.py",
)
assert SPEC is not None and SPEC.loader is not None
ADAPTIVE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ADAPTIVE
SPEC.loader.exec_module(ADAPTIVE)


class AdaptiveSafetyEvaluationTest(unittest.TestCase):
    def test_reference_rates_are_column_means(self) -> None:
        rows = (
            (True, False, True, False, False, False),
            (False, False, True, True, False, False),
        )
        self.assertEqual(
            {
                "korean_hangul_10pct": 0.5,
                "code_marker": 0.0,
                "math_or_numeric": 1.0,
                "long_context_gt_8000": 0.5,
                "short_context_le_100": 0.0,
                "messages": 0.0,
            },
            ADAPTIVE.reference_rates(rows),
        )

    def test_study_multiplier_matches_runtime_formula(self) -> None:
        rows = (
            (True, False, True, False, False, False),
            (False, True, False, False, True, True),
        )
        rates = ADAPTIVE.reference_rates(rows)
        study_profile = ADAPTIVE.SafetyProfile(rates, 1_000, 1.0, 0.25)
        runtime_profile = aggressive_v7.AdaptiveSafetyProfile(
            rates, 1_000, 1.0, 0.25, {}
        )
        self.assertAlmostEqual(
            aggressive_v7.safety_multiplier(rows, runtime_profile),
            ADAPTIVE.safety_multiplier((0, 1), rows, study_profile),
        )

    def test_empty_reference_and_batch_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ADAPTIVE.reference_rates(())
        profile = ADAPTIVE.SafetyProfile(
            {name: 0.0 for name in ADAPTIVE.SIGNAL_NAMES}, 1_000, 1.0, 0.25
        )
        with self.assertRaises(ValueError):
            ADAPTIVE.safety_multiplier((), (), profile)


if __name__ == "__main__":
    unittest.main()
