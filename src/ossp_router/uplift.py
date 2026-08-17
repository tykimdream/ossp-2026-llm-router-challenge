# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic router trained on quality uplift relative to Light."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from . import competition
from .heuristic import write_submission_atomic
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
    load_json,
    load_policy,
    parse_submission,
    policy_sha256,
    submission_to_dict,
)


ARTIFACT_TYPE = "ossp-uplift-linear-v1"
UPLIFT_MODEL_IDS = MODEL_IDS[1:]


@dataclass(frozen=True)
class LinearHead:
    intercept: float
    coefficients: Tuple[float, ...]


@dataclass(frozen=True)
class UpliftArtifact:
    hash_bins: int
    feature_mean: Tuple[float, ...]
    feature_scale: Tuple[float, ...]
    uplift_heads: Mapping[str, LinearHead]
    log_cost_heads: Mapping[str, LinearHead]
    tier_safety_ratios: Mapping[str, float]
    policy_id: str
    policy_digest: str
    training_summary: Mapping[str, Any]


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label}은(는) JSON 객체여야 합니다.")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ProtocolError(f"{label}은(는) 유한한 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError(f"{label}은(는) 유한한 숫자여야 합니다.")
    return result


def _vector(value: Any, length: int, label: str) -> Tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ProtocolError(f"{label} 길이가 올바르지 않습니다.")
    return tuple(_number(item, f"{label}[{index}]") for index, item in enumerate(value))


def _head(value: Any, length: int, label: str) -> LinearHead:
    raw = _object(value, label)
    if set(raw) != {"intercept", "coefficients"}:
        raise ProtocolError(f"{label} 필드가 올바르지 않습니다.")
    return LinearHead(
        _number(raw["intercept"], f"{label}.intercept"),
        _vector(raw["coefficients"], length, f"{label}.coefficients"),
    )


def parse_artifact(value: Any) -> UpliftArtifact:
    root = _object(value, "uplift artifact")
    expected = {
        "artifact_type",
        "schema_version",
        "feature_version",
        "hash_bins",
        "dense_feature_names",
        "model_ids",
        "policy_id",
        "policy_sha256",
        "feature_mean",
        "feature_scale",
        "uplift_heads",
        "log_cost_heads",
        "tier_safety_ratios",
        "training_summary",
    }
    if set(root) != expected:
        raise ProtocolError("uplift artifact 필드가 올바르지 않습니다.")
    if root["artifact_type"] != ARTIFACT_TYPE or root["schema_version"] != 1:
        raise ProtocolError("지원하지 않는 uplift artifact입니다.")
    if root["feature_version"] != competition.FEATURE_VERSION:
        raise ProtocolError("uplift feature 버전이 올바르지 않습니다.")
    if root["dense_feature_names"] != list(competition.DENSE_FEATURE_NAMES):
        raise ProtocolError("uplift dense feature 정의가 다릅니다.")
    if root["model_ids"] != list(MODEL_IDS):
        raise ProtocolError("uplift model_ids가 올바르지 않습니다.")
    hash_bins = root["hash_bins"]
    if (
        isinstance(hash_bins, bool)
        or not isinstance(hash_bins, int)
        or hash_bins < 16
        or hash_bins & (hash_bins - 1)
    ):
        raise ProtocolError("uplift hash_bins가 올바르지 않습니다.")
    length = len(competition.DENSE_FEATURE_NAMES) + hash_bins
    mean = _vector(root["feature_mean"], length, "feature_mean")
    scale = _vector(root["feature_scale"], length, "feature_scale")
    if any(item <= 0 for item in scale):
        raise ProtocolError("uplift feature_scale은 양수여야 합니다.")
    uplift_raw = _object(root["uplift_heads"], "uplift_heads")
    cost_raw = _object(root["log_cost_heads"], "log_cost_heads")
    if set(uplift_raw) != set(UPLIFT_MODEL_IDS) or set(cost_raw) != set(MODEL_IDS):
        raise ProtocolError("uplift head의 모델 집합이 올바르지 않습니다.")
    safety_raw = _object(root["tier_safety_ratios"], "tier_safety_ratios")
    if set(safety_raw) != set(TIERS):
        raise ProtocolError("uplift 안전계수가 완전하지 않습니다.")
    safety = {
        tier: _number(safety_raw[tier], f"tier_safety_ratios.{tier}")
        for tier in TIERS
    }
    if any(not 0 < item <= 1 for item in safety.values()):
        raise ProtocolError("uplift 안전계수 범위가 올바르지 않습니다.")
    policy_id = root["policy_id"]
    policy_digest = root["policy_sha256"]
    if not isinstance(policy_id, str) or not isinstance(policy_digest, str):
        raise ProtocolError("uplift 정책 정보가 올바르지 않습니다.")
    return UpliftArtifact(
        hash_bins,
        mean,
        scale,
        {
            model_id: _head(uplift_raw[model_id], length, f"uplift_heads.{model_id}")
            for model_id in UPLIFT_MODEL_IDS
        },
        {
            model_id: _head(cost_raw[model_id], length, f"log_cost_heads.{model_id}")
            for model_id in MODEL_IDS
        },
        safety,
        policy_id,
        policy_digest,
        dict(_object(root["training_summary"], "training_summary")),
    )


def load_artifact(path: Path) -> UpliftArtifact:
    return parse_artifact(load_json(path))


def _linear(head: LinearHead, values: Sequence[float]) -> float:
    return head.intercept + math.fsum(
        coefficient * value for coefficient, value in zip(head.coefficients, values)
    )


def predict_episode(
    episode: Episode, artifact: UpliftArtifact
) -> Tuple[Mapping[str, float], Mapping[str, float]]:
    raw = competition.raw_feature_vector(episode, artifact.hash_bins)
    values = tuple(
        (value - mean) / scale
        for value, mean, scale in zip(
            raw, artifact.feature_mean, artifact.feature_scale
        )
    )
    scores = {MODEL_IDS[0]: 0.0}
    scores.update(
        {
            model_id: min(1.0, max(-1.0, _linear(artifact.uplift_heads[model_id], values)))
            for model_id in UPLIFT_MODEL_IDS
        }
    )
    costs = {
        model_id: math.exp(
            min(50.0, max(-50.0, _linear(artifact.log_cost_heads[model_id], values)))
        )
        for model_id in MODEL_IDS
    }
    costs[MODEL_IDS[1]] = max(costs[MODEL_IDS[1]], costs[MODEL_IDS[0]] * (1 + 1e-12))
    costs[MODEL_IDS[2]] = max(costs[MODEL_IDS[2]], costs[MODEL_IDS[1]] * (1 + 1e-12))
    return scores, costs


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    artifact: UpliftArtifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    if artifact.policy_id != policy.policy_id or artifact.policy_digest != policy_sha256(policy):
        raise ProtocolError("uplift artifact와 정책이 일치하지 않습니다.")
    predictions = [predict_episode(episode, artifact) for episode in inputs.episodes]
    scores = [dict(item[0]) for item in predictions]
    costs = [item[1] for item in predictions]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, _ratio = competition.select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=artifact.tier_safety_ratios[tier],
    )
    if tier == "premium":
        selected, _ratio = competition.fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=competition.PREMIUM_AX31_FILL_SAFETY,
        )
    return parse_submission(
        submission_to_dict(
            Submission(
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
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uplift-router-run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--artifact", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs = load_input(args.input)
        policy = load_policy(args.policy) if args.policy else load_bundled_policy()
        artifact = load_artifact(args.artifact)
        submission = make_submission(inputs, policy, artifact, args.tier)
        write_submission_atomic(args.output, submission)
    except (OSError, ProtocolError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} uplift 제출 파일을 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
