# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import pathlib
import unittest

from ossp_router import aggressive_v9
from ossp_router.protocol import load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class AggressiveV9RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.artifact = aggressive_v9.load_artifact()

    def test_frozen_candidate_parameters(self) -> None:
        profile = self.artifact.selective
        self.assertEqual(30_000.0, profile.ridge_alpha)
        self.assertEqual(0.5, profile.blend_weight)
        self.assertEqual(0.1, profile.threshold)
        self.assertEqual(0.75, profile.recovery_fraction)

    def test_router_is_deterministic(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            first = aggressive_v9.make_submission(
                self.inputs, self.policy, self.artifact, tier
            )
            second = aggressive_v9.make_submission(
                self.inputs, self.policy, self.artifact, tier
            )
            self.assertEqual(first, second)

    def test_runtime_source_has_no_clock_or_random_branch(self) -> None:
        source = inspect.getsource(aggressive_v9)
        self.assertNotIn("import random", source)
        self.assertNotIn("import time", source)
        self.assertEqual("9.0-selective-budget", aggressive_v9.ROUTER_VERSION)


if __name__ == "__main__":
    unittest.main()
