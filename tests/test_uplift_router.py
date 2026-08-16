# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from ossp_router import uplift
from ossp_router.protocol import Episode, InputBatch, MODEL_IDS, load_bundled_policy, load_input


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "experiments/artifacts/uplift-train-only.v1.json"


class UpliftRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_input(ROOT / "data/toy/inputs.json")
        cls.policy = load_bundled_policy()
        cls.artifact = uplift.load_artifact(ARTIFACT)

    def test_is_deterministic_and_complete(self) -> None:
        for tier in ("fast", "balanced", "premium"):
            first = uplift.make_submission(
                self.inputs, self.policy, self.artifact, tier
            )
            second = uplift.make_submission(
                self.inputs, self.policy, self.artifact, tier
            )
            self.assertEqual(first, second)
            self.assertEqual(len(self.inputs.episodes), len(first.decisions))

    def test_fast_never_selects_k1(self) -> None:
        submission = uplift.make_submission(
            self.inputs, self.policy, self.artifact, "fast"
        )
        self.assertNotIn(MODEL_IDS[2], {item.model_id for item in submission.decisions})

    def test_id_and_order_changes_preserve_content_choices(self) -> None:
        original = uplift.make_submission(
            self.inputs, self.policy, self.artifact, "premium"
        )
        expected = {
            episode.prompt: decision.model_id
            for episode, decision in zip(self.inputs.episodes, original.decisions)
        }
        changed_episodes = tuple(
            Episode(
                f"uplift-audit-{index}",
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
        actual_submission = uplift.make_submission(
            changed, self.policy, self.artifact, "premium"
        )
        actual = {
            episode.prompt: decision.model_id
            for episode, decision in zip(changed.episodes, actual_submission.decisions)
        }
        self.assertEqual(expected, actual)

    def test_cli_writes_valid_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "uplift.json"
            result = uplift.main(
                [
                    "--input",
                    str(ROOT / "data/toy/inputs.json"),
                    "--tier",
                    "balanced",
                    "--artifact",
                    str(ARTIFACT),
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
