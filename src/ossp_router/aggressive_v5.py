# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic tier-specific Ridge ensemble for aggressive budget use."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
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


ARTIFACT_TYPE = "ossp-aggressive-router-v5"
DEFAULT_ARTIFACT = "aggressive-router.v5.json"
FIXED_BISECTION_STEPS = 48


@dataclass(frozen=True)
class TierEnsemble:
    model_ids: Tuple[str, ...]
    safety_ratio: float


@dataclass(frozen=True)
class AggressiveArtifact:
    policy_id: str
    policy_digest: str
    hash_bins: int
    models: Mapping[str, competition.CompetitionArtifact]
    tiers: Mapping[str, TierEnsemble]
    premium_fill_safety_ratio: float
    fixed_bisection_steps: int
    training_summary: Mapping[str, Any]


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label}은(는) JSON 객체여야 합니다.")
    return value


def _ratio(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ProtocolError(f"{label}은(는) 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 1:
        raise ProtocolError(f"{label}은(는) 0 초과 1 이하여야 합니다.")
    return result


def parse_artifact(value: Any) -> AggressiveArtifact:
    root = _object(value, "aggressive v5 artifact")
    expected = {
        "artifact_type",
        "schema_version",
        "policy_id",
        "policy_sha256",
        "hash_bins",
        "models",
        "tiers",
        "premium_fill_safety_ratio",
        "fixed_bisection_steps",
        "training_summary",
    }
    if set(root) != expected:
        raise ProtocolError("aggressive v5 artifact 필드가 올바르지 않습니다.")
    if root["artifact_type"] != ARTIFACT_TYPE or root["schema_version"] != 1:
        raise ProtocolError("지원하지 않는 aggressive v5 artifact입니다.")
    policy_id = root["policy_id"]
    digest = root["policy_sha256"]
    if not isinstance(policy_id, str) or not isinstance(digest, str):
        raise ProtocolError("aggressive v5 정책 정보가 올바르지 않습니다.")
    hash_bins = root["hash_bins"]
    if (
        isinstance(hash_bins, bool)
        or not isinstance(hash_bins, int)
        or hash_bins < 16
        or hash_bins & (hash_bins - 1)
    ):
        raise ProtocolError("aggressive v5 hash_bins가 올바르지 않습니다.")
    steps = root["fixed_bisection_steps"]
    if (
        isinstance(steps, bool)
        or not isinstance(steps, int)
        or steps != FIXED_BISECTION_STEPS
    ):
        raise ProtocolError("aggressive v5는 고정 48회 선택기만 지원합니다.")

    models_raw = _object(root["models"], "models")
    if not models_raw:
        raise ProtocolError("aggressive v5 모델이 비어 있습니다.")
    models = {}
    for name, model_raw in models_raw.items():
        if not isinstance(name, str) or not name:
            raise ProtocolError("aggressive v5 모델 이름이 올바르지 않습니다.")
        model = competition.parse_artifact(model_raw)
        if (
            model.hash_bins != hash_bins
            or model.policy_id != policy_id
            or model.policy_digest != digest
        ):
            raise ProtocolError("aggressive v5 내부 모델 구성이 일치하지 않습니다.")
        models[name] = model

    tiers_raw = _object(root["tiers"], "tiers")
    if set(tiers_raw) != set(TIERS):
        raise ProtocolError("aggressive v5 tier 설정이 완전하지 않습니다.")
    tiers = {}
    for tier in TIERS:
        raw = _object(tiers_raw[tier], f"tiers.{tier}")
        if set(raw) != {"model_ids", "safety_ratio"}:
            raise ProtocolError(f"tiers.{tier} 필드가 올바르지 않습니다.")
        model_ids = raw["model_ids"]
        if (
            not isinstance(model_ids, list)
            or not model_ids
            or any(not isinstance(item, str) for item in model_ids)
            or len(set(model_ids)) != len(model_ids)
            or any(item not in models for item in model_ids)
        ):
            raise ProtocolError(f"tiers.{tier}.model_ids가 올바르지 않습니다.")
        tiers[tier] = TierEnsemble(
            tuple(model_ids),
            _ratio(raw["safety_ratio"], f"tiers.{tier}.safety_ratio"),
        )
    return AggressiveArtifact(
        policy_id=policy_id,
        policy_digest=digest,
        hash_bins=hash_bins,
        models=models,
        tiers=tiers,
        premium_fill_safety_ratio=_ratio(
            root["premium_fill_safety_ratio"], "premium_fill_safety_ratio"
        ),
        fixed_bisection_steps=steps,
        training_summary=dict(_object(root["training_summary"], "training_summary")),
    )


def load_artifact(path: Optional[Path] = None) -> AggressiveArtifact:
    if path is not None:
        return parse_artifact(load_json(path))
    text = resources.read_text(
        "ossp_router.resources", DEFAULT_ARTIFACT, encoding="utf-8"
    )
    return parse_artifact(json.loads(text))


def predict_episode(
    episode: Episode, artifact: AggressiveArtifact, tier: str
) -> Tuple[Mapping[str, float], Mapping[str, float]]:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    raw = competition.raw_feature_vector(episode, artifact.hash_bins)
    ensemble = artifact.tiers[tier]
    outputs = [
        competition.predict_linear_features(raw, artifact.models[model_id])
        for model_id in ensemble.model_ids
    ]
    count = float(len(outputs))
    score_values = tuple(
        math.fsum(item[0][index] for item in outputs) / count
        for index in range(len(MODEL_IDS))
    )
    log_cost_values = tuple(
        math.fsum(item[1][index] for item in outputs) / count
        for index in range(len(MODEL_IDS))
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


def _select_models(
    scores: Sequence[Mapping[str, float]],
    costs: Sequence[Mapping[str, float]],
    *,
    budget_multiplier: float,
    safety_ratio: float,
    steps: int,
) -> Tuple[Tuple[str, ...], float]:
    if len(scores) != len(costs) or not scores:
        raise ValueError("예측 배열의 길이가 올바르지 않습니다.")
    light_id = MODEL_IDS[0]
    light_total = math.fsum(row[light_id] for row in costs)
    cap = light_total * max(1.0, budget_multiplier * safety_ratio)

    def choose(penalty: float) -> Tuple[Tuple[str, ...], float]:
        selected = tuple(
            max(
                MODEL_IDS,
                key=lambda model_id: (
                    score_row[model_id] - penalty * cost_row[model_id] / light_total,
                    -MODEL_IDS.index(model_id),
                ),
            )
            for score_row, cost_row in zip(scores, costs)
        )
        total = math.fsum(row[model_id] for row, model_id in zip(costs, selected))
        return selected, total

    selected, total = choose(0.0)
    if total > cap:
        low, high = 0.0, 1.0
        selected, total = choose(high)
        while total > cap and high < 2**60:
            low, high = high, high * 2.0
            selected, total = choose(high)
        for _ in range(steps):
            middle = (low + high) / 2.0
            candidate, candidate_total = choose(middle)
            if candidate_total <= cap:
                high, selected, total = middle, candidate, candidate_total
            else:
                low = middle
    if total > cap:
        selected = tuple(light_id for _ in scores)
        total = light_total
    return selected, total / light_total


def _fill_ax31(
    selected: Sequence[str],
    scores: Sequence[Mapping[str, float]],
    costs: Sequence[Mapping[str, float]],
    *,
    budget_multiplier: float,
    safety_ratio: float,
    steps: int,
) -> Tuple[Tuple[str, ...], float]:
    light_id, ax31_id, _ = MODEL_IDS
    light_total = math.fsum(row[light_id] for row in costs)
    current_total = math.fsum(
        row[model_id] for row, model_id in zip(costs, selected)
    )
    cap = max(current_total, light_total * budget_multiplier * safety_ratio)

    def choose(penalty: float) -> Tuple[Tuple[str, ...], float]:
        result = []
        for current, score_row, cost_row in zip(selected, scores, costs):
            if current != light_id:
                result.append(current)
                continue
            gain = score_row[ax31_id] - score_row[light_id]
            extra = cost_row[ax31_id] - cost_row[light_id]
            result.append(
                ax31_id if gain - penalty * extra / light_total > 0 else light_id
            )
        total = math.fsum(row[model_id] for row, model_id in zip(costs, result))
        return tuple(result), total

    result, total = choose(0.0)
    if total > cap:
        low, high = 0.0, 1.0
        result, total = choose(high)
        while total > cap and high < 2**60:
            low, high = high, high * 2.0
            result, total = choose(high)
        for _ in range(steps):
            middle = (low + high) / 2.0
            candidate, candidate_total = choose(middle)
            if candidate_total <= cap:
                high, result, total = middle, candidate, candidate_total
            else:
                low = middle
    if total > cap:
        return tuple(selected), current_total / light_total
    return result, total / light_total


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    artifact: AggressiveArtifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    if (
        artifact.policy_id != policy.policy_id
        or artifact.policy_digest != policy_sha256(policy)
    ):
        raise ProtocolError("aggressive v5 artifact와 실행 정책이 일치하지 않습니다.")
    predictions = [predict_episode(episode, artifact, tier) for episode in inputs.episodes]
    scores = [dict(item[0]) for item in predictions]
    costs = [item[1] for item in predictions]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, _ratio_value = _select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=artifact.tiers[tier].safety_ratio,
        steps=artifact.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, _ratio_value = _fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=artifact.premium_fill_safety_ratio,
            steps=artifact.fixed_bisection_steps,
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
    parser = argparse.ArgumentParser(prog="aggressive-v5-router-run")
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
            inputs,
            policy,
            load_artifact(args.artifact),
            args.tier,
        )
        write_submission_atomic(args.output, routed)
    except (OSError, ProtocolError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} aggressive v5 결과를 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
