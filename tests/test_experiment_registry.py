# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ExperimentRegistryTest(unittest.TestCase):
    def test_registry_preserves_reference_candidate_and_failed_runs(self) -> None:
        registry = json.loads(
            (ROOT / "experiments/registry.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, registry["schema_version"])
        experiments = {item["id"]: item for item in registry["experiments"]}
        self.assertIn("ridge-full-public-v1", experiments)
        self.assertIn("hybrid-safe-v1", experiments)
        self.assertIn("hybrid-train-calibrated-v1", experiments)
        self.assertEqual(
            "rejected", experiments["hybrid-train-calibrated-v1"]["status"]
        )
        self.assertFalse(
            experiments["hybrid-train-calibrated-v1"]["metrics"]["tiers"]
            ["balanced"]["budget_passed"]
        )

    def test_generated_report_links_both_svg_charts(self) -> None:
        markdown = (ROOT / "docs/EXPERIMENT_COMPARISON.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("experiment-score.svg", markdown)
        self.assertIn("experiment-budget.svg", markdown)
        self.assertIn("Hybrid Safe v1", markdown)
        for name in ("experiment-score.svg", "experiment-budget.svg"):
            svg = (ROOT / "docs" / name).read_text(encoding="utf-8")
            self.assertIn("<svg", svg)
            self.assertIn("SPDX-License-Identifier", svg)


if __name__ == "__main__":
    unittest.main()
