# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from ossp_router import aggressive_v9, heuristic, submission
from ossp_router.protocol import Episode, InputBatch, load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SubmissionRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.artifact = submission.load_submission_artifact()

    def test_default_artifact_is_aggressive_v9(self) -> None:
        self.assertEqual(512, self.artifact.hash_bins)
        self.assertEqual(1.0, self.artifact.base.profile.composition_penalty)
        self.assertEqual(0.25, self.artifact.base.profile.small_batch_penalty)
        self.assertEqual(30_000.0, self.artifact.selective.ridge_alpha)
        self.assertEqual(
            "aggressive-pooled-and-worst-template-fold",
            self.artifact.base.base.base.training_summary["budget_calibration"],
        )
        self.assertEqual(6, len(self.artifact.base.base.base.models))

    def test_public_path_matches_learned_router(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            expected = aggressive_v9.make_submission(
                self.inputs, self.policy, self.artifact, tier
            )
            actual = submission.make_submission(
                self.inputs, self.policy, self.artifact, tier
            )
            expected_by_id = {
                item.episode_id: item.model_id for item in expected.decisions
            }
            actual_by_id = {
                item.episode_id: item.model_id for item in actual.decisions
            }
            self.assertEqual(expected_by_id, actual_by_id)

    def test_order_and_ids_do_not_change_content_choices(self) -> None:
        expected_submission = submission.make_submission(
            self.inputs, self.policy, self.artifact, "balanced"
        )
        expected = {
            episode.prompt: decision.model_id
            for episode, decision in zip(
                self.inputs.episodes, expected_submission.decisions
            )
        }
        changed_episodes = tuple(
            Episode(
                f"submission-audit-{index}",
                prompt=episode.prompt,
                messages=episode.messages,
            )
            for index, episode in enumerate(reversed(self.inputs.episodes), start=1)
        )
        changed = InputBatch(
            self.inputs.schema_version,
            self.inputs.challenge_id,
            self.inputs.split,
            changed_episodes,
        )
        actual_submission = submission.make_submission(
            changed, self.policy, self.artifact, "balanced"
        )
        actual = {
            episode.prompt: decision.model_id
            for episode, decision in zip(changed.episodes, actual_submission.decisions)
        }
        self.assertEqual(expected, actual)

    def test_large_workload_guard_is_deterministic_always_light(self) -> None:
        with mock.patch.object(
            aggressive_v9.aggressive_v7.aggressive_v6, "MAX_LEARNED_EPISODES", 1
        ), mock.patch.object(
            submission, "_canonical_batch", side_effect=AssertionError("sorted too early")
        ):
            actual = submission.make_submission(
                self.inputs, self.policy, self.artifact, "premium"
            )
        expected = heuristic.make_submission(
            self.inputs, self.policy, "premium", strategy="always-light"
        )
        self.assertEqual(expected, actual)

    def test_runtime_source_has_no_clock_or_random_branch(self) -> None:
        source = inspect.getsource(submission)
        self.assertNotIn("import random", source)
        self.assertNotIn("import time", source)
        self.assertNotIn("time.monotonic", source)

    def test_cli_writes_valid_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "submission.json"
            result = submission.main(
                [
                    "--input",
                    str(ROOT / "data/toy/inputs.json"),
                    "--tier",
                    "balanced",
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(0, result)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("balanced", value["tier"])
            self.assertEqual(3, len(value["decisions"]))


if __name__ == "__main__":
    unittest.main()
