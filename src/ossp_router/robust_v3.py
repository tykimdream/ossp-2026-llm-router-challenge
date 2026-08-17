# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Train-OOF-calibrated deterministic robust router v3."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from . import submission, uplift
from .heuristic import make_submission as make_heuristic_submission
from .heuristic import write_submission_atomic
from .protocol import (
    MODEL_IDS,
    TIERS,
    Decision,
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


ARTIFACT_TYPE = "ossp-robust-router-policy-v3"
DEFAULT_ARTIFACT = "robust-router.v3.json"
DEFAULT_UPLIFT_ARTIFACT = "submission-uplift.v1.json"
FIXED_BISECTION_STEPS = 48


@dataclass(frozen=True)
class RobustArtifact:
    policy_id: str
    policy_digest: str
    predictor: str
    tier_safety_ratios: Mapping[str, float]
    premium_fill_safety_ratio: float
    fixed_bisection_steps: int
    training_summary: Mapping[str, Any]


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label}은(는) JSON 객체여야 합니다.")
    return value


def _ratio(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{label}은(는) 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 1:
        raise ProtocolError(f"{label}은(는) 0 초과 1 이하여야 합니다.")
    return result


def parse_artifact(value: Any) -> RobustArtifact:
    root = _object(value, "robust v3 artifact")
    expected = {
        "artifact_type",
        "schema_version",
        "policy_id",
        "policy_sha256",
        "predictor",
        "tier_safety_ratios",
        "premium_fill_safety_ratio",
        "fixed_bisection_steps",
        "training_summary",
    }
    if set(root) != expected:
        raise ProtocolError("robust v3 artifact 필드가 올바르지 않습니다.")
    if root["artifact_type"] != ARTIFACT_TYPE or root["schema_version"] != 1:
        raise ProtocolError("지원하지 않는 robust v3 artifact입니다.")
    if root["predictor"] != "quality-uplift-relative-to-light":
        raise ProtocolError("robust v3 predictor가 올바르지 않습니다.")
    steps = root["fixed_bisection_steps"]
    if (
        isinstance(steps, bool)
        or not isinstance(steps, int)
        or steps != FIXED_BISECTION_STEPS
    ):
        raise ProtocolError("robust v3는 고정 48회 선택기만 지원합니다.")
    safety_raw = _object(root["tier_safety_ratios"], "tier_safety_ratios")
    if set(safety_raw) != set(TIERS):
        raise ProtocolError("robust v3 안전계수가 완전하지 않습니다.")
    policy_id = root["policy_id"]
    digest = root["policy_sha256"]
    if not isinstance(policy_id, str) or not isinstance(digest, str):
        raise ProtocolError("robust v3 정책 정보가 올바르지 않습니다.")
    return RobustArtifact(
        policy_id=policy_id,
        policy_digest=digest,
        predictor=root["predictor"],
        tier_safety_ratios={
            tier: _ratio(safety_raw[tier], f"tier_safety_ratios.{tier}")
            for tier in TIERS
        },
        premium_fill_safety_ratio=_ratio(
            root["premium_fill_safety_ratio"], "premium_fill_safety_ratio"
        ),
        fixed_bisection_steps=steps,
        training_summary=dict(_object(root["training_summary"], "training_summary")),
    )


def load_artifact(path: Optional[Path] = None) -> RobustArtifact:
    if path is not None:
        return parse_artifact(load_json(path))
    text = resources.read_text(
        "ossp_router.resources", DEFAULT_ARTIFACT, encoding="utf-8"
    )
    return parse_artifact(json.loads(text))


def load_uplift_artifact(path: Optional[Path] = None) -> uplift.UpliftArtifact:
    if path is not None:
        return uplift.load_artifact(path)
    text = resources.read_text(
        "ossp_router.resources", DEFAULT_UPLIFT_ARTIFACT, encoding="utf-8"
    )
    return uplift.parse_artifact(json.loads(text))


def _select_models(
    predicted_scores: Sequence[Mapping[str, float]],
    predicted_costs: Sequence[Mapping[str, float]],
    *,
    budget_multiplier: float,
    safety_ratio: float,
    steps: int = FIXED_BISECTION_STEPS,
) -> Tuple[Tuple[str, ...], float]:
    """Select models using a fixed, validated number of bisection steps."""

    if len(predicted_scores) != len(predicted_costs) or not predicted_scores:
        raise ValueError("예측 배열의 길이가 올바르지 않습니다.")
    light_id = MODEL_IDS[0]
    light_total = math.fsum(row[light_id] for row in predicted_costs)
    cap = light_total * max(1.0, budget_multiplier * safety_ratio)

    def choose(penalty: float) -> Tuple[Tuple[str, ...], float]:
        selected = tuple(
            max(
                MODEL_IDS,
                key=lambda model_id: (
                    scores[model_id] - penalty * costs[model_id] / light_total,
                    -MODEL_IDS.index(model_id),
                ),
            )
            for scores, costs in zip(predicted_scores, predicted_costs)
        )
        total = math.fsum(
            costs[model_id]
            for costs, model_id in zip(predicted_costs, selected)
        )
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
        selected = tuple(light_id for _ in predicted_scores)
        total = light_total
    return selected, total / light_total


def _fill_ax31(
    selected: Sequence[str],
    scores: Sequence[Mapping[str, float]],
    costs: Sequence[Mapping[str, float]],
    *,
    budget_multiplier: float,
    safety_ratio: float,
    steps: int = FIXED_BISECTION_STEPS,
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
        total = math.fsum(row[model] for row, model in zip(costs, result))
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
    uplift_artifact: uplift.UpliftArtifact,
    robust_artifact: RobustArtifact,
    tier: str,
) -> Submission:
    """Route deterministically using Train-only OOF-calibrated controls."""

    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    digest = policy_sha256(policy)
    if any(
        item.policy_id != policy.policy_id or item.policy_digest != digest
        for item in (uplift_artifact, robust_artifact)
    ):
        raise ProtocolError("robust v3 구성과 실행 정책이 일치하지 않습니다.")

    # Adversarial large-batch defense: do not sort or allocate prediction matrices
    # before deciding that the learned path is safe for this symmetric workload.
    if not submission._learned_path_allowed(inputs):
        return make_heuristic_submission(
            inputs, policy, tier, strategy="prompt-heuristic"
        )

    canonical = submission._canonical_batch(inputs)
    predictions = [
        uplift.predict_episode(episode, uplift_artifact)
        for episode in canonical.episodes
    ]
    scores = [dict(item[0]) for item in predictions]
    costs = [item[1] for item in predictions]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0

    selected, _ratio_value = _select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=robust_artifact.tier_safety_ratios[tier],
        steps=robust_artifact.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, _ratio_value = _fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=robust_artifact.premium_fill_safety_ratio,
            steps=robust_artifact.fixed_bisection_steps,
        )
    routed = Submission(
        canonical.schema_version,
        canonical.challenge_id,
        policy.policy_id,
        canonical.split,
        tier,
        tuple(
            Decision(episode.episode_id, model_id)
            for episode, model_id in zip(canonical.episodes, selected)
        ),
    )
    validated = parse_submission(submission_to_dict(routed))
    return submission._restore_input_order(inputs, validated)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="robust-v3-router-run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--uplift-artifact", type=Path)
    parser.add_argument("--robust-artifact", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs = load_input(args.input)
        policy = load_policy(args.policy) if args.policy else load_bundled_policy()
        routed = make_submission(
            inputs,
            policy,
            load_uplift_artifact(args.uplift_artifact),
            load_artifact(args.robust_artifact),
            args.tier,
        )
        write_submission_atomic(args.output, routed)
    except (OSError, ProtocolError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} robust v3 후보 결과를 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
