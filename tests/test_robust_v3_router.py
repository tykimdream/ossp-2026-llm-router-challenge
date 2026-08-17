# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import pathlib
import unittest
from unittest import mock

from ossp_router import competition, heuristic, robust_v3, submission
from ossp_router.protocol import (
    Episode,
    InputBatch,
    MODEL_IDS,
    ProtocolError,
    load_bundled_policy,
    load_input,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RobustV3RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.uplift = robust_v3.load_uplift_artifact()
        cls.robust = robust_v3.load_artifact()

    def route(self, inputs: InputBatch, tier: str):
        return robust_v3.make_submission(
            inputs, self.policy, self.uplift, self.robust, tier
        )

    def test_is_deterministic_complete_and_fast_blocks_k1(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            first = self.route(self.inputs, tier)
            self.assertEqual(first, self.route(self.inputs, tier))
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
                f"v3-audit-{index}",
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

    def test_large_guard_runs_before_canonical_sort(self) -> None:
        with mock.patch.object(submission, "MAX_LEARNED_EPISODES", 1), mock.patch.object(
            submission, "_canonical_batch", side_effect=AssertionError("sorted too early")
        ):
            actual = self.route(self.inputs, "premium")
        expected = heuristic.make_submission(
            self.inputs, self.policy, "premium", strategy="prompt-heuristic"
        )
        self.assertEqual(expected, actual)

    def test_fixed_48_steps_matches_original_80_on_public_toy(self) -> None:
        predictions = [
            robust_v3.uplift.predict_episode(episode, self.uplift)
            for episode in self.inputs.episodes
        ]
        scores = [dict(item[0]) for item in predictions]
        costs = [item[1] for item in predictions]
        for tier in ("fast", "balanced", "premium"):
            if tier == "fast":
                for row in scores:
                    row[MODEL_IDS[2]] = -2.0
            expected = competition.select_models(
                scores,
                costs,
                budget_multiplier=float(
                    self.policy.tiers[tier].budget_multiplier
                ),
                safety_ratio=self.robust.tier_safety_ratios[tier],
            )[0]
            actual = robust_v3._select_models(
                scores,
                costs,
                budget_multiplier=float(
                    self.policy.tiers[tier].budget_multiplier
                ),
                safety_ratio=self.robust.tier_safety_ratios[tier],
            )[0]
            self.assertEqual(expected, actual)

    def test_artifact_rejects_a_variable_iteration_count(self) -> None:
        import json

        value = json.loads(
            (ROOT / "src/ossp_router/resources/robust-router.v3.json").read_text(
                encoding="utf-8"
            )
        )
        value["fixed_bisection_steps"] = 47
        with self.assertRaises(ProtocolError):
            robust_v3.parse_artifact(value)

    def test_runtime_source_has_fixed_non_clock_control_flow(self) -> None:
        source = inspect.getsource(robust_v3)
        self.assertNotIn("import random", source)
        self.assertNotIn("import time", source)
        self.assertIn("FIXED_BISECTION_STEPS = 48", source)


if __name__ == "__main__":
    unittest.main()
