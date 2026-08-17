# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Single deterministic entry point used by the submitted container image."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

from . import aggressive_v5, aggressive_v6, competition
from .heuristic import episode_text, make_submission as make_heuristic_submission
from .heuristic import write_submission_atomic
from .protocol import (
    TIERS,
    Decision,
    Episode,
    InputBatch,
    ProtocolError,
    RoutingPolicy,
    Submission,
    load_bundled_policy,
    load_input,
    load_json,
    load_policy,
    parse_submission,
    submission_to_dict,
)


# These are deterministic workload guards, not wall-clock deadlines. The public
# 2,640-episode workload is about 11.8 MiB and remains on the learned path.
MAX_LEARNED_EPISODES = 6_000
MAX_LEARNED_CHARACTERS = 30_000_000
SubmissionArtifact = Union[
    competition.CompetitionArtifact,
    aggressive_v5.AggressiveArtifact,
    aggressive_v6.V6Artifact,
]


def _content_key(episode: Episode) -> Tuple[int, str]:
    if episode.prompt is not None:
        canonical = "prompt\x1f" + episode.prompt
    else:
        assert episode.messages is not None
        canonical = "messages\x1f" + "\x1e".join(
            f"{message.role}\x1f{message.content}" for message in episode.messages
        )
    return competition.stable_hash(canonical), canonical


def _canonical_batch(inputs: InputBatch) -> InputBatch:
    episodes = tuple(sorted(inputs.episodes, key=_content_key))
    return InputBatch(
        inputs.schema_version,
        inputs.challenge_id,
        inputs.split,
        episodes,
    )


def _learned_path_allowed(
    inputs: InputBatch, artifact: Optional[SubmissionArtifact] = None
) -> bool:
    if isinstance(artifact, aggressive_v6.V6Artifact):
        return aggressive_v6.learned_path_allowed(inputs)
    return (
        len(inputs.episodes) <= MAX_LEARNED_EPISODES
        and sum(len(episode_text(episode)) for episode in inputs.episodes)
        <= MAX_LEARNED_CHARACTERS
    )


def _restore_input_order(inputs: InputBatch, routed: Submission) -> Submission:
    by_id = {decision.episode_id: decision.model_id for decision in routed.decisions}
    restored = Submission(
        inputs.schema_version,
        inputs.challenge_id,
        routed.policy_id,
        inputs.split,
        routed.tier,
        tuple(
            Decision(episode.episode_id, by_id[episode.episode_id])
            for episode in inputs.episodes
        ),
    )
    return parse_submission(submission_to_dict(restored))


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    artifact: SubmissionArtifact,
    tier: str,
) -> Submission:
    """Route once with no randomness, clock checks, or input-order features."""

    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    # Check the symmetric workload limits before the O(n log n) canonical sort.
    # The fallback is pointwise, so it is already independent of input order.
    if not _learned_path_allowed(inputs, artifact):
        return make_heuristic_submission(
            inputs,
            policy,
            tier,
            strategy="prompt-heuristic",
        )
    canonical = _canonical_batch(inputs)
    if isinstance(artifact, aggressive_v6.V6Artifact):
        routed = aggressive_v6.make_submission(canonical, policy, artifact, tier)
    elif isinstance(artifact, aggressive_v5.AggressiveArtifact):
        routed = aggressive_v5.make_submission(canonical, policy, artifact, tier)
    else:
        routed = competition.make_submission(canonical, policy, artifact, tier)
    return _restore_input_order(inputs, routed)


def load_submission_artifact(path: Optional[Path] = None) -> SubmissionArtifact:
    if path is None:
        return aggressive_v6.load_artifact()
    value = load_json(path)
    if isinstance(value, dict) and value.get("artifact_type") == aggressive_v5.ARTIFACT_TYPE:
        return aggressive_v5.parse_artifact(value)
    return competition.parse_artifact(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="router-run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--artifact", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs = load_input(args.input)
        policy = load_policy(args.policy) if args.policy else load_bundled_policy()
        artifact = load_submission_artifact(args.artifact)
        submission = make_submission(inputs, policy, artifact, args.tier)
        write_submission_atomic(args.output, submission)
    except (OSError, ProtocolError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} 제출용 라우터 결과를 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
