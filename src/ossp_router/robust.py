# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Experimental deterministic router with fixed cost-stress scenarios."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from . import competition, hybrid, submission, uplift
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


ARTIFACT_TYPE = "ossp-robust-router-policy-v2"
DEFAULT_ARTIFACT = "robust-router.v2.json"
DEFAULT_UPLIFT_ARTIFACT = "submission-uplift.v1.json"


@dataclass(frozen=True)
class CostScenario:
    name: str
    multipliers: Mapping[str, float]


@dataclass(frozen=True)
class TierStrategy:
    predictor: str
    scenarios: Tuple[CostScenario, ...]


@dataclass(frozen=True)
class RobustArtifact:
    policy_id: str
    policy_digest: str
    tiers: Mapping[str, TierStrategy]
    fixed_bisection_steps: int
    training_summary: Mapping[str, Any]


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label}은(는) JSON 객체여야 합니다.")
    return value


def _finite_multiplier(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{label}은(는) 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result) or result < 1:
        raise ProtocolError(f"{label}은(는) 1 이상의 유한한 수여야 합니다.")
    return result


def parse_artifact(value: Any) -> RobustArtifact:
    root = _object(value, "robust artifact")
    expected = {
        "artifact_type",
        "schema_version",
        "policy_id",
        "policy_sha256",
        "fixed_bisection_steps",
        "tiers",
        "training_summary",
    }
    if set(root) != expected:
        raise ProtocolError("robust artifact 필드가 올바르지 않습니다.")
    if root["artifact_type"] != ARTIFACT_TYPE or root["schema_version"] != 1:
        raise ProtocolError("지원하지 않는 robust artifact입니다.")
    steps = root["fixed_bisection_steps"]
    if isinstance(steps, bool) or not isinstance(steps, int) or steps != 80:
        raise ProtocolError("robust artifact는 고정 80회 선택기만 지원합니다.")
    tiers_raw = _object(root["tiers"], "tiers")
    if set(tiers_raw) != set(TIERS):
        raise ProtocolError("robust tier 설정이 완전하지 않습니다.")
    tiers = {}
    for tier in TIERS:
        raw = _object(tiers_raw[tier], f"tiers.{tier}")
        if set(raw) != {"predictor", "cost_scenarios"}:
            raise ProtocolError(f"tiers.{tier} 필드가 올바르지 않습니다.")
        predictor = raw["predictor"]
        if predictor not in {"uplift", "hybrid"}:
            raise ProtocolError(f"tiers.{tier}.predictor가 올바르지 않습니다.")
        scenarios_raw = raw["cost_scenarios"]
        if not isinstance(scenarios_raw, list) or not scenarios_raw:
            raise ProtocolError(f"tiers.{tier}.cost_scenarios가 비어 있습니다.")
        scenarios = []
        names = set()
        for index, item in enumerate(scenarios_raw):
            scenario = _object(item, f"tiers.{tier}.cost_scenarios[{index}]")
            if set(scenario) != {"name", "multipliers"}:
                raise ProtocolError("robust cost scenario 필드가 올바르지 않습니다.")
            name = scenario["name"]
            if not isinstance(name, str) or not name or name in names:
                raise ProtocolError("robust cost scenario 이름이 올바르지 않습니다.")
            names.add(name)
            multipliers_raw = _object(scenario["multipliers"], "multipliers")
            if set(multipliers_raw) != set(MODEL_IDS):
                raise ProtocolError("robust cost scenario 모델 집합이 다릅니다.")
            multipliers = {
                model_id: _finite_multiplier(
                    multipliers_raw[model_id],
                    f"{tier}.{name}.{model_id}",
                )
                for model_id in MODEL_IDS
            }
            if multipliers[MODEL_IDS[0]] != 1:
                raise ProtocolError("Light 비용 배수는 항상 1이어야 합니다.")
            scenarios.append(CostScenario(name, multipliers))
        tiers[tier] = TierStrategy(predictor, tuple(scenarios))
    policy_id = root["policy_id"]
    digest = root["policy_sha256"]
    if not isinstance(policy_id, str) or not isinstance(digest, str):
        raise ProtocolError("robust 정책 정보가 올바르지 않습니다.")
    return RobustArtifact(
        policy_id,
        digest,
        tiers,
        steps,
        dict(_object(root["training_summary"], "training_summary")),
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


def _stress_costs(
    costs: Sequence[Mapping[str, float]], scenarios: Sequence[CostScenario]
) -> Sequence[Mapping[str, float]]:
    return [
        {
            model_id: max(
                row[model_id] * scenario.multipliers[model_id]
                for scenario in scenarios
            )
            for model_id in MODEL_IDS
        }
        for row in costs
    ]


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    ridge_artifact: competition.CompetitionArtifact,
    uplift_artifact: uplift.UpliftArtifact,
    hybrid_artifact: hybrid.HybridArtifact,
    robust_artifact: RobustArtifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    digest = policy_sha256(policy)
    artifacts = (ridge_artifact, uplift_artifact, hybrid_artifact, robust_artifact)
    if any(
        item.policy_id != policy.policy_id or item.policy_digest != digest
        for item in artifacts
    ):
        raise ProtocolError("robust 구성과 실행 정책이 일치하지 않습니다.")
    canonical = submission._canonical_batch(inputs)
    if not submission._learned_path_allowed(canonical):
        routed = make_heuristic_submission(
            canonical, policy, tier, strategy="prompt-heuristic"
        )
        return submission._restore_input_order(inputs, routed)
    strategy = robust_artifact.tiers[tier]
    if strategy.predictor == "uplift":
        predictions = [
            uplift.predict_episode(episode, uplift_artifact)
            for episode in canonical.episodes
        ]
        scores = [dict(item[0]) for item in predictions]
        costs = [item[1] for item in predictions]
        safety_ratio = uplift_artifact.tier_safety_ratios[tier]
        fill_safety = competition.PREMIUM_AX31_FILL_SAFETY
    else:
        predictions = [
            competition.predict_episode(episode, ridge_artifact)
            for episode in canonical.episodes
        ]
        scores = [
            hybrid.guarded_scores(episode, item[0], tier, hybrid_artifact)
            for episode, item in zip(canonical.episodes, predictions)
        ]
        costs = [item[1] for item in predictions]
        safety_ratio = hybrid_artifact.tiers[tier].safety_ratio
        fill_safety = hybrid_artifact.premium_ax31_fill_safety
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    stressed = _stress_costs(costs, strategy.scenarios)
    selected, _ratio = competition.select_models(
        scores,
        stressed,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=safety_ratio,
    )
    if tier == "premium":
        selected, _ratio = competition.fill_ax31(
            selected,
            scores,
            stressed,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=fill_safety,
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
    routed = parse_submission(submission_to_dict(routed))
    return submission._restore_input_order(inputs, routed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="robust-router-run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--ridge-artifact", type=Path)
    parser.add_argument("--uplift-artifact", type=Path)
    parser.add_argument("--hybrid-artifact", type=Path)
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
            competition.load_artifact(args.ridge_artifact),
            load_uplift_artifact(args.uplift_artifact),
            hybrid.load_artifact(args.hybrid_artifact),
            load_artifact(args.robust_artifact),
            args.tier,
        )
        write_submission_atomic(args.output, routed)
    except (OSError, ProtocolError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} robust 후보 결과를 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
