# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import json
import pathlib
import unittest
from unittest import mock

from ossp_router import aggressive_v5, competition, heuristic, submission
from ossp_router.protocol import Episode, InputBatch, MODEL_IDS, ProtocolError
from ossp_router.protocol import load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class AggressiveV5RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.artifact = aggressive_v5.load_artifact()

    def route(self, inputs: InputBatch, tier: str):
        return submission.make_submission(inputs, self.policy, self.artifact, tier)

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
                f"v5-audit-{index}",
                prompt=episode.prompt,
                messages=episode.messages,
            )
            for index, episode in enumerate(reversed(self.inputs.episodes), start=1)
        )
        changed = InputBatch(
            self.inputs.schema_version,
            "changed-challenge",
            "changed-split",
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

    def test_ensemble_extracts_features_once_per_episode(self) -> None:
        episode = self.inputs.episodes[0]
        original = competition.raw_feature_vector
        with mock.patch.object(
            competition, "raw_feature_vector", wraps=original
        ) as patched:
            aggressive_v5.predict_episode(episode, self.artifact, "premium")
        self.assertEqual(1, patched.call_count)

    def test_artifact_rejects_a_variable_iteration_count(self) -> None:
        value = json.loads(
            (ROOT / "src/ossp_router/resources/aggressive-router.v5.json").read_text(
                encoding="utf-8"
            )
        )
        value["fixed_bisection_steps"] = 47
        with self.assertRaises(ProtocolError):
            aggressive_v5.parse_artifact(value)

    def test_runtime_source_has_fixed_non_clock_control_flow(self) -> None:
        source = inspect.getsource(aggressive_v5)
        self.assertNotIn("import random", source)
        self.assertNotIn("import time", source)
        self.assertIn("FIXED_BISECTION_STEPS = 48", source)


if __name__ == "__main__":
    unittest.main()
