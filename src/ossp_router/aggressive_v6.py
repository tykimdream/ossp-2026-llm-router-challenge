# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Operationally hardened V6 router compiled from the frozen V5 ensemble."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from . import aggressive_v5, competition
from .heuristic import episode_text, write_submission_atomic
from .protocol import (
    MODEL_IDS,
    TIERS,
    Decision,
    Episode,
    InputBatch,
    ProtocolError,
    RoutingPolicy,
    Submission,
    load_bundled_policy,
    load_input,
    load_policy,
    parse_submission,
    policy_sha256,
    submission_to_dict,
)


ARTIFACT_TYPE = "ossp-aggressive-router-v6-runtime"
MAX_LEARNED_EPISODES = 6_000
MAX_LEARNED_CHARACTERS = 30_000_000
MAX_LEARNED_MESSAGES = 100_000
MAX_LEARNED_WORK_UNITS = 40_000_000

# Operational targets: 1.15 / 1.80 / 3.00 predicted cost ratios. These are
# deliberately below V5's 1.20 / 2.00 / 3.42 selection caps.
TIER_SAFETY_RATIOS = {
    "fast": 0.92,
    "balanced": 0.90,
    "premium": 0.75,
}
PREMIUM_FILL_SAFETY_RATIO = 0.60


@dataclass(frozen=True)
class RawLinearHead:
    intercept: float
    coefficients: Tuple[float, ...]


@dataclass(frozen=True)
class CompiledTier:
    score_heads: Mapping[str, RawLinearHead]
    log_cost_heads: Mapping[str, RawLinearHead]


@dataclass(frozen=True)
class V6Artifact:
    base: aggressive_v5.AggressiveArtifact
    compiled_tiers: Mapping[str, CompiledTier]

    @property
    def policy_id(self) -> str:
        return self.base.policy_id

    @property
    def policy_digest(self) -> str:
        return self.base.policy_digest

    @property
    def hash_bins(self) -> int:
        return self.base.hash_bins


def _compile_head(
    artifacts: Sequence[competition.CompetitionArtifact],
    *,
    model_id: str,
    score: bool,
) -> RawLinearHead:
    heads = [
        artifact.score_heads[model_id] if score else artifact.log_cost_heads[model_id]
        for artifact in artifacts
    ]
    count = float(len(artifacts))
    coefficients = tuple(
        math.fsum(
            head.coefficients[index] / artifact.feature_scale[index]
            for head, artifact in zip(heads, artifacts)
        )
        / count
        for index in range(len(artifacts[0].feature_mean))
    )
    intercept = math.fsum(
        head.intercept
        - math.fsum(
            mean * coefficient / scale
            for mean, coefficient, scale in zip(
                artifact.feature_mean,
                head.coefficients,
                artifact.feature_scale,
            )
        )
        for head, artifact in zip(heads, artifacts)
    ) / count
    return RawLinearHead(intercept, coefficients)


def compile_artifact(base: aggressive_v5.AggressiveArtifact) -> V6Artifact:
    compiled = {}
    for tier in TIERS:
        models = [base.models[name] for name in base.tiers[tier].model_ids]
        compiled[tier] = CompiledTier(
            score_heads={
                model_id: _compile_head(models, model_id=model_id, score=True)
                for model_id in MODEL_IDS
            },
            log_cost_heads={
                model_id: _compile_head(models, model_id=model_id, score=False)
                for model_id in MODEL_IDS
            },
        )
    return V6Artifact(base=base, compiled_tiers=compiled)


def load_artifact(path: Optional[Path] = None) -> V6Artifact:
    return compile_artifact(aggressive_v5.load_artifact(path))


def _add_hash(bins: list, value: str) -> None:
    digest = competition.stable_hash(value)
    index = digest & (len(bins) - 1)
    bins[index] += -1.0 if digest & (1 << 63) else 1.0


def _streaming_hashed_features(text: str, hash_bins: int) -> Tuple[float, ...]:
    bins = [0.0] * hash_bins
    previous = deque(maxlen=2)
    for match in competition._TOKEN.finditer(text):
        token = match.group(0).casefold()
        if token.isdecimal():
            token = "<number>"
        _add_hash(bins, f"w1:{token}")
        if previous:
            _add_hash(bins, f"w2:{previous[-1]}\x1f{token}")
        if len(previous) == 2:
            _add_hash(bins, f"w3:{previous[0]}\x1f{previous[1]}\x1f{token}")
        previous.append(token)

    characters = competition.normalized_template(text)
    if len(characters) > 4_000:
        characters = characters[:3_000] + characters[-1_000:]
    for size in (3, 4):
        for index in range(0, max(0, len(characters) - size + 1), 2):
            _add_hash(bins, f"c{size}:{characters[index:index + size]}")
    norm = math.sqrt(math.fsum(value * value for value in bins))
    if norm:
        bins = [value / norm for value in bins]
    return tuple(bins)


def raw_feature_vector(episode: Episode, hash_bins: int) -> Tuple[float, ...]:
    return competition.dense_feature_vector(episode) + _streaming_hashed_features(
        episode_text(episode), hash_bins
    )


def _linear(head: RawLinearHead, raw: Sequence[float]) -> float:
    return head.intercept + math.fsum(
        coefficient * value for coefficient, value in zip(head.coefficients, raw)
    )


def predict_episode(
    episode: Episode, artifact: V6Artifact, tier: str
) -> Tuple[Mapping[str, float], Mapping[str, float]]:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    raw = raw_feature_vector(episode, artifact.hash_bins)
    compiled = artifact.compiled_tiers[tier]
    score_values = tuple(_linear(compiled.score_heads[model_id], raw) for model_id in MODEL_IDS)
    log_cost_values = tuple(
        _linear(compiled.log_cost_heads[model_id], raw) for model_id in MODEL_IDS
    )
    scores = {
        model_id: min(1.0, max(0.0, score_values[index]))
        for index, model_id in enumerate(MODEL_IDS)
    }
    costs = {
        model_id: math.exp(min(50.0, max(-50.0, log_cost_values[index])))
        for index, model_id in enumerate(MODEL_IDS)
    }
    light = costs[MODEL_IDS[0]]
    costs[MODEL_IDS[1]] = max(costs[MODEL_IDS[1]], light * (1.0 + 1e-12))
    costs[MODEL_IDS[2]] = max(
        costs[MODEL_IDS[2]], costs[MODEL_IDS[1]] * (1.0 + 1e-12)
    )
    return scores, costs


def _content_key(episode: Episode) -> Tuple[object, ...]:
    if episode.prompt is not None:
        return ("prompt", episode.prompt)
    assert episode.messages is not None
    return ("messages", episode.messages)


def _token_count_upper_bound(text: str, remaining: int) -> int:
    count = 0
    in_word = False
    for character in text:
        if character.isalnum():
            if not in_word:
                count += 1
                if count > remaining:
                    return count
            in_word = True
        elif character.isspace() or character == "_":
            in_word = False
            continue
        else:
            count += 1
            in_word = False
            if count > remaining:
                return count
    return count


def learned_path_allowed(inputs: InputBatch) -> bool:
    if len(inputs.episodes) > MAX_LEARNED_EPISODES:
        return False
    characters = 0
    messages = 0
    work_units = 0
    for episode in inputs.episodes:
        text = episode_text(episode)
        characters += len(text)
        messages += 1 if episode.prompt is not None else len(episode.messages or ())
        if characters > MAX_LEARNED_CHARACTERS or messages > MAX_LEARNED_MESSAGES:
            return False
        remaining_tokens = max(0, (MAX_LEARNED_WORK_UNITS - work_units) // 3)
        tokens = _token_count_upper_bound(text, remaining_tokens)
        work_units += len(text) + 3 * tokens
        if work_units > MAX_LEARNED_WORK_UNITS:
            return False
    return True


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    artifact: V6Artifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    if (
        artifact.policy_id != policy.policy_id
        or artifact.policy_digest != policy_sha256(policy)
    ):
        raise ProtocolError("aggressive v6 artifact와 실행 정책이 일치하지 않습니다.")

    cache: Dict[
        Tuple[object, ...], Tuple[Mapping[str, float], Mapping[str, float]]
    ] = {}
    predictions = []
    for episode in inputs.episodes:
        key = _content_key(episode)
        prediction = cache.get(key)
        if prediction is None:
            prediction = predict_episode(episode, artifact, tier)
            cache[key] = prediction
        predictions.append(prediction)
    scores = [dict(item[0]) for item in predictions]
    costs = [item[1] for item in predictions]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, _ratio = aggressive_v5._select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=TIER_SAFETY_RATIOS[tier],
        steps=artifact.base.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, _ratio = aggressive_v5._fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=PREMIUM_FILL_SAFETY_RATIO,
            steps=artifact.base.fixed_bisection_steps,
        )
    routed = Submission(
        inputs.schema_version,
        inputs.challenge_id,
        policy.policy_id,
        inputs.split,
        tier,
        tuple(
            Decision(episode.episode_id, model_id)
            for episode, model_id in zip(inputs.episodes, selected)
        ),
    )
    return parse_submission(submission_to_dict(routed))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aggressive-v6-router-run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--artifact", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        from . import submission

        inputs = load_input(args.input)
        policy = load_policy(args.policy) if args.policy else load_bundled_policy()
        routed = submission.make_submission(
            inputs, policy, load_artifact(args.artifact), args.tier
        )
        write_submission_atomic(args.output, routed)
    except (OSError, ProtocolError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} aggressive v6 결과를 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
