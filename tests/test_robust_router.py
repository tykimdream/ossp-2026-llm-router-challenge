# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import pathlib
import unittest

from ossp_router import competition, hybrid, robust
from ossp_router.protocol import Episode, InputBatch, MODEL_IDS, load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RobustRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.ridge = competition.load_artifact()
        cls.uplift = robust.load_uplift_artifact()
        cls.hybrid = hybrid.load_artifact()
        cls.robust = robust.load_artifact()

    def route(self, inputs: InputBatch, tier: str):
        return robust.make_submission(
            inputs,
            self.policy,
            self.ridge,
            self.uplift,
            self.hybrid,
            self.robust,
            tier,
        )

    def test_is_deterministic_complete_and_fast_blocks_k1(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            first = self.route(self.inputs, tier)
            second = self.route(self.inputs, tier)
            self.assertEqual(first, second)
            self.assertEqual(len(self.inputs.episodes), len(first.decisions))
            if tier == "fast":
                self.assertNotIn(
                    MODEL_IDS[2], {item.model_id for item in first.decisions}
                )

    def test_order_and_ids_do_not_change_content_choices(self) -> None:
        original = self.route(self.inputs, "premium")
        expected = {
            episode.prompt: decision.model_id
            for episode, decision in zip(self.inputs.episodes, original.decisions)
        }
        episodes = tuple(
            Episode(
                f"robust-audit-{index}",
                prompt=episode.prompt,
                messages=episode.messages,
            )
            for index, episode in enumerate(reversed(self.inputs.episodes), start=1)
        )
        changed = InputBatch(
            self.inputs.schema_version,
            self.inputs.challenge_id,
            self.inputs.split,
            episodes,
        )
        actual_submission = self.route(changed, "premium")
        actual = {
            episode.prompt: decision.model_id
            for episode, decision in zip(episodes, actual_submission.decisions)
        }
        self.assertEqual(expected, actual)

    def test_runtime_source_has_fixed_non_clock_control_flow(self) -> None:
        source = inspect.getsource(robust)
        self.assertNotIn("import random", source)
        self.assertNotIn("import time", source)
        self.assertIn('steps != 80', source)


if __name__ == "__main__":
    unittest.main()
