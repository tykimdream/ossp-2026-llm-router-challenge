# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from ossp_router import competition, hybrid
from ossp_router.protocol import Episode, InputBatch, MODEL_IDS, load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class HybridRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.ridge = competition.load_artifact()
        cls.artifact = hybrid.load_artifact()

    def test_default_hybrid_is_deterministic_and_complete(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            first = hybrid.make_submission(
                self.inputs, self.policy, self.ridge, self.artifact, tier
            )
            second = hybrid.make_submission(
                self.inputs, self.policy, self.ridge, self.artifact, tier
            )
            self.assertEqual(first, second)
            self.assertEqual(len(self.inputs.episodes), len(first.decisions))

    def test_fast_guard_never_selects_k1(self) -> None:
        submission = hybrid.make_submission(
            self.inputs, self.policy, self.ridge, self.artifact, "fast"
        )
        self.assertNotIn(MODEL_IDS[2], {item.model_id for item in submission.decisions})

    def test_id_and_order_changes_preserve_content_choices(self) -> None:
        original = hybrid.make_submission(
            self.inputs, self.policy, self.ridge, self.artifact, "premium"
        )
        expected = {
            episode.prompt: decision.model_id
            for episode, decision in zip(self.inputs.episodes, original.decisions)
        }
        changed_episodes = tuple(
            Episode(
                f"hybrid-audit-{index}",
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
        actual_submission = hybrid.make_submission(
            changed, self.policy, self.ridge, self.artifact, "premium"
        )
        actual = {
            episode.prompt: decision.model_id
            for episode, decision in zip(changed.episodes, actual_submission.decisions)
        }
        self.assertEqual(expected, actual)

    def test_cli_writes_valid_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "hybrid.json"
            result = hybrid.main(
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
