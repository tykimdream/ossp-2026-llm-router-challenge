# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from ossp_router.competition import (
    load_artifact,
    main,
    make_submission,
    raw_feature_vector,
)
from ossp_router.protocol import Episode, InputBatch, load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CompetitionRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.artifact = load_artifact()

    def test_default_artifact_generates_every_tier_deterministically(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            first = make_submission(self.inputs, self.policy, self.artifact, tier)
            second = make_submission(self.inputs, self.policy, self.artifact, tier)
            self.assertEqual(first, second)
            self.assertEqual(len(self.inputs.episodes), len(first.decisions))
            self.assertEqual(tier, first.tier)

    def test_features_do_not_use_episode_id(self) -> None:
        original = self.inputs.episodes[0]
        renamed = Episode("completely-different-id", prompt=original.prompt)
        self.assertEqual(
            raw_feature_vector(original),
            raw_feature_vector(renamed),
        )

    def test_id_and_order_changes_preserve_content_choices(self) -> None:
        tier = "premium"
        original = make_submission(self.inputs, self.policy, self.artifact, tier)
        original_by_text = {
            episode.prompt
            or tuple((message.role, message.content) for message in episode.messages or ()):
            decision.model_id
            for episode, decision in zip(self.inputs.episodes, original.decisions)
        }
        changed_episodes = tuple(
            Episode(
                episode_id=f"audit-{index}",
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
        audited = make_submission(changed, self.policy, self.artifact, tier)
        audited_by_text = {
            episode.prompt
            or tuple((message.role, message.content) for message in episode.messages or ()):
            decision.model_id
            for episode, decision in zip(changed.episodes, audited.decisions)
        }
        self.assertEqual(original_by_text, audited_by_text)

    def test_cli_writes_one_valid_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "submission.json"
            result = main(
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
