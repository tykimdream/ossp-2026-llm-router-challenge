# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import math
import pathlib
import unittest

from ossp_router import aggressive_v6, aggressive_v7, submission
from ossp_router.protocol import Episode, InputBatch, Message, load_bundled_policy
from ossp_router.protocol import load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class AggressiveV7RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.artifact = aggressive_v7.load_artifact()

    def test_profile_is_train_calibrated(self) -> None:
        profile = self.artifact.profile
        self.assertEqual(1_000, profile.stable_batch_size)
        self.assertEqual(1.0, profile.composition_penalty)
        self.assertEqual(0.25, profile.small_batch_penalty)
        self.assertEqual(1_760, profile.calibration["train_episodes"])
        self.assertEqual(0, profile.calibration["train_stress_tier_budget_failures"])

    def test_signal_row_uses_content_only(self) -> None:
        episode = Episode(
            "ignored-id",
            messages=(Message("user", "한글 123 + ```python```"),),
        )
        raw = aggressive_v6.raw_feature_vector(episode, self.artifact.hash_bins)
        self.assertEqual(
            (True, True, True, False, True, True),
            aggressive_v7.signal_row(episode, raw),
        )

    def test_multiplier_is_one_at_reference_large_batch(self) -> None:
        profile = self.artifact.profile
        count = 8_800
        rows = []
        target_counts = {
            name: round(profile.reference_rates[name] * count)
            for name in aggressive_v7.SIGNAL_NAMES
        }
        for index in range(count):
            rows.append(
                tuple(
                    index < target_counts[name]
                    for name in aggressive_v7.SIGNAL_NAMES
                )
            )
        self.assertAlmostEqual(
            1.0,
            aggressive_v7.safety_multiplier(rows, profile),
            places=3,
        )

    def test_small_or_shifted_batch_gets_stricter(self) -> None:
        profile = self.artifact.profile
        reference_like = tuple(
            tuple(False for _ in aggressive_v7.SIGNAL_NAMES) for _ in range(1_000)
        )
        small = reference_like[:10]
        shifted = tuple(
            tuple(True for _ in aggressive_v7.SIGNAL_NAMES) for _ in range(1_000)
        )
        baseline = aggressive_v7.safety_multiplier(reference_like, profile)
        self.assertLess(aggressive_v7.safety_multiplier(small, profile), baseline)
        self.assertLess(aggressive_v7.safety_multiplier(shifted, profile), baseline)
        self.assertTrue(math.isfinite(baseline))

    def test_order_and_ids_do_not_change_content_choices(self) -> None:
        expected = submission.make_submission(
            self.inputs, self.policy, self.artifact, "premium"
        )
        changed_episodes = tuple(
            Episode(
                f"changed-{index}",
                prompt=episode.prompt,
                messages=episode.messages,
            )
            for index, episode in enumerate(reversed(self.inputs.episodes))
        )
        changed = InputBatch(1, "changed", "changed", changed_episodes)
        actual = submission.make_submission(
            changed, self.policy, self.artifact, "premium"
        )
        expected_by_content = {
            episode.prompt: decision.model_id
            for episode, decision in zip(self.inputs.episodes, expected.decisions)
        }
        actual_by_content = {
            episode.prompt: decision.model_id
            for episode, decision in zip(changed_episodes, actual.decisions)
        }
        self.assertEqual(expected_by_content, actual_by_content)

    def test_runtime_source_has_no_clock_or_random_branch(self) -> None:
        source = inspect.getsource(aggressive_v7)
        self.assertNotIn("import random", source)
        self.assertNotIn("import time", source)
        self.assertEqual("7.0-adaptive-budget", aggressive_v7.ROUTER_VERSION)


if __name__ == "__main__":
    unittest.main()
