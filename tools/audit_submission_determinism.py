# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Audit the active submission against order and public metadata changes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from ossp_router import submission
from ossp_router.protocol import Episode, InputBatch, TIERS, load_bundled_policy, load_input


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    inputs = load_input(args.input)
    policy = load_bundled_policy()
    artifact = submission.load_submission_artifact(args.artifact)
    changed_to_original = {}
    changed_episodes = []
    for index, episode in enumerate(reversed(inputs.episodes), start=1):
        changed_id = f"determinism-audit-{index:06d}"
        changed_to_original[changed_id] = episode.episode_id
        changed_episodes.append(
            Episode(changed_id, prompt=episode.prompt, messages=episode.messages)
        )
    changed = InputBatch(
        inputs.schema_version,
        "determinism-audit-challenge",
        "determinism-audit-split",
        tuple(changed_episodes),
    )

    tiers = {}
    for tier in TIERS:
        first = submission.make_submission(inputs, policy, artifact, tier)
        repeated = submission.make_submission(inputs, policy, artifact, tier)
        altered = submission.make_submission(changed, policy, artifact, tier)
        expected = {item.episode_id: item.model_id for item in first.decisions}
        mismatches = sum(
            expected[changed_to_original[item.episode_id]] != item.model_id
            for item in altered.decisions
        )
        tiers[tier] = {
            "num_episodes": len(inputs.episodes),
            "repeat_identical": first == repeated,
            "order_id_split_challenge_mismatches": mismatches,
        }
    report = {
        "report_type": "ossp-submission-determinism-audit-v1",
        "input": str(args.input),
        "artifact": str(args.artifact) if args.artifact else "bundled-default",
        "tiers": tiers,
        "passed": all(
            item["repeat_identical"]
            and item["order_id_split_challenge_mismatches"] == 0
            for item in tiers.values()
        ),
    }
    _write(args.report, report)
    print(f"OK: determinism audit passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
