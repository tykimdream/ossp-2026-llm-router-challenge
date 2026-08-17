# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from ossp_router import aggressive_v5, aggressive_v6, competition, submission
from ossp_router.heuristic import write_submission_atomic
from ossp_router.protocol import Episode, InputBatch, Message
from ossp_router.protocol import load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class AggressiveV6RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.v5 = aggressive_v5.load_artifact()
        cls.v6 = aggressive_v6.load_artifact()

    def route(self, inputs: InputBatch, tier: str):
        return submission.make_submission(inputs, self.policy, self.v6, tier)

    def test_streaming_features_match_v5_features(self) -> None:
        for episode in self.inputs.episodes:
            expected = competition.raw_feature_vector(episode, self.v5.hash_bins)
            actual = aggressive_v6.raw_feature_vector(episode, self.v6.hash_bins)
            self.assertEqual(expected, actual)

    def test_compiled_heads_match_v5_predictions(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            for episode in self.inputs.episodes:
                expected = aggressive_v5.predict_episode(episode, self.v5, tier)
                actual = aggressive_v6.predict_episode(episode, self.v6, tier)
                for expected_rows, actual_rows in zip(expected, actual):
                    for model_id in expected_rows:
                        self.assertAlmostEqual(
                            expected_rows[model_id], actual_rows[model_id], places=12
                        )

    def test_duplicate_content_is_predicted_once(self) -> None:
        episode = self.inputs.episodes[0]
        duplicated = InputBatch(
            1,
            "duplicate-test",
            "duplicate-test",
            tuple(
                Episode(f"duplicate-{index}", prompt=episode.prompt)
                for index in range(100)
            ),
        )
        original = aggressive_v6.predict_episode
        with mock.patch.object(
            aggressive_v6, "predict_episode", wraps=original
        ) as patched:
            self.route(duplicated, "premium")
        self.assertEqual(1, patched.call_count)

    def test_work_guard_accounts_for_tokens_and_messages(self) -> None:
        dense = InputBatch(1, "dense", "dense", (Episode("dense", prompt="a " * 50),))
        messages = InputBatch(
            1,
            "messages",
            "messages",
            (
                Episode(
                    "messages",
                    messages=tuple(
                        Message("user", "x")
                        for _ in range(10)
                    ),
                ),
            ),
        )
        with mock.patch.object(aggressive_v6, "MAX_LEARNED_WORK_UNITS", 100):
            self.assertFalse(aggressive_v6.learned_path_allowed(dense))
        with mock.patch.object(aggressive_v6, "MAX_LEARNED_MESSAGES", 9):
            self.assertFalse(aggressive_v6.learned_path_allowed(messages))

    def test_token_guard_counts_non_latin_words(self) -> None:
        text = "alpha βeta 한글 123 _ punctuation!?"
        expected = len(tuple(competition._TOKEN.finditer(text)))
        self.assertGreaterEqual(
            aggressive_v6._token_count_upper_bound(text, 1_000), expected
        )

    def test_order_and_ids_do_not_change_content_choices(self) -> None:
        expected_submission = self.route(self.inputs, "balanced")
        expected = {
            episode.prompt: decision.model_id
            for episode, decision in zip(
                self.inputs.episodes, expected_submission.decisions
            )
        }
        changed_episodes = tuple(
            Episode(
                f"v6-audit-{index}",
                prompt=episode.prompt,
                messages=episode.messages,
            )
            for index, episode in enumerate(reversed(self.inputs.episodes), start=1)
        )
        changed = InputBatch(1, "changed", "changed", changed_episodes)
        actual_submission = self.route(changed, "balanced")
        actual = {
            episode.prompt: decision.model_id
            for episode, decision in zip(changed_episodes, actual_submission.decisions)
        }
        self.assertEqual(expected, actual)

    def test_submission_writer_uses_compact_json(self) -> None:
        routed = self.route(self.inputs, "fast")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "submission.json"
            write_submission_atomic(path, routed)
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("\n  ", text)
        self.assertEqual(1, len(text.splitlines()))
        self.assertEqual("fast", json.loads(text)["tier"])

    def test_runtime_source_has_fixed_non_clock_control_flow(self) -> None:
        source = inspect.getsource(aggressive_v6)
        self.assertNotIn("import random", source)
        self.assertNotIn("import time", source)
        self.assertEqual(0.92, aggressive_v6.TIER_SAFETY_RATIOS["fast"])
        self.assertEqual(0.90, aggressive_v6.TIER_SAFETY_RATIOS["balanced"])
        self.assertEqual(0.75, aggressive_v6.TIER_SAFETY_RATIOS["premium"])


if __name__ == "__main__":
    unittest.main()
