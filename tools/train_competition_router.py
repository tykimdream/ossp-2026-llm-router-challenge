# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Train the content-only competition router with template-group OOF folds."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from ossp_router import competition
from ossp_router.heuristic import episode_text
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    Decision,
    InputBatch,
    Outcome,
    OutcomeBatch,
    ProtocolError,
    RoutingPolicy,
    Submission,
    load_bundled_policy,
    load_input,
    load_outcomes,
    policy_sha256,
)
from ossp_router.scoring import score_submissions


DEFAULT_TARGETS = {"fast": 1.18, "balanced": 1.85, "premium": 3.5}


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("학습에는 NumPy가 필요합니다.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cost(outcome: Outcome, policy: RoutingPolicy) -> float:
    rates = policy.models[outcome.model_id]
    unit = Decimal(policy.token_unit)
    return float(
        rates.fixed_cost
        + Decimal(outcome.input_tokens) * rates.input_token_rate / unit
        + Decimal(outcome.output_tokens) * rates.output_token_rate / unit
    )


def _matrix(
    inputs: InputBatch,
    outcomes: OutcomeBatch,
    policy: RoutingPolicy,
    hash_bins: int,
) -> Tuple[Any, Any, Any]:
    _require_numpy()
    if inputs.challenge_id != outcomes.challenge_id or inputs.split != outcomes.split:
        raise ProtocolError("학습 입력과 outcome 메타데이터가 다릅니다.")
    index = {(item.episode_id, item.model_id): item for item in outcomes.outcomes}
    expected = {
        (episode.episode_id, model_id)
        for episode in inputs.episodes
        for model_id in MODEL_IDS
    }
    if set(index) != expected:
        raise ProtocolError("학습 outcome 행렬이 완전하지 않습니다.")
    matrix = np.asarray(
        [competition.raw_feature_vector(episode, hash_bins) for episode in inputs.episodes],
        dtype=np.float64,
    )
    targets = []
    fold_keys = []
    for episode in inputs.episodes:
        rows = [index[(episode.episode_id, model_id)] for model_id in MODEL_IDS]
        targets.append(
            [float(row.score) for row in rows]
            + [math.log(_cost(row, policy)) for row in rows]
        )
        template = competition.normalized_template(episode_text(episode))
        fold_keys.append(competition.stable_hash(template))
    return matrix, np.asarray(targets, dtype=np.float64), np.asarray(fold_keys)


def _fit(matrix: Any, targets: Any, alpha: float) -> Tuple[Any, Any, Any, Any]:
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)
    standardized = (matrix - mean) / scale
    intercept = targets.mean(axis=0)
    centered = targets - intercept
    rows, columns = standardized.shape
    # Some macOS Accelerate builds emit spurious floating-point warnings for
    # finite matrix products. Verify the result explicitly instead.
    with np.errstate(all="ignore"):
        if rows <= columns:
            system = standardized @ standardized.T + alpha * np.eye(rows)
            coefficients = standardized.T @ np.linalg.solve(system, centered)
        else:
            system = standardized.T @ standardized + alpha * np.eye(columns)
            coefficients = np.linalg.solve(system, standardized.T @ centered)
    if not np.isfinite(coefficients).all():
        raise RuntimeError("ridge 학습 결과에 유한하지 않은 값이 있습니다.")
    return mean, scale, intercept, coefficients


def _predict(
    matrix: Any, mean: Any, scale: Any, intercept: Any, coefficients: Any
) -> Any:
    with np.errstate(all="ignore"):
        result = (matrix - mean) / scale @ coefficients + intercept
    if not np.isfinite(result).all():
        raise RuntimeError("ridge 예측에 유한하지 않은 값이 있습니다.")
    return result


def _oof(
    matrix: Any,
    targets: Any,
    fold_keys: Any,
    folds: int,
    alpha: float,
) -> Any:
    predictions = np.empty_like(targets)
    fold_ids = fold_keys % folds
    for fold in range(folds):
        validation = fold_ids == fold
        training = ~validation
        if not validation.any() or not training.any():
            raise ValueError("template-group fold가 비어 있습니다.")
        mean, scale, intercept, coefficients = _fit(
            matrix[training], targets[training], alpha
        )
        predictions[validation] = _predict(
            matrix[validation], mean, scale, intercept, coefficients
        )
    return predictions


def _prediction_rows(predictions: Any):
    score_rows = []
    cost_rows = []
    count = len(MODEL_IDS)
    for row in predictions:
        scores = {
            model_id: min(1.0, max(0.0, float(row[index])))
            for index, model_id in enumerate(MODEL_IDS)
        }
        costs = {
            model_id: math.exp(
                min(50.0, max(-50.0, float(row[count + index])))
            )
            for index, model_id in enumerate(MODEL_IDS)
        }
        light = costs[MODEL_IDS[0]]
        costs[MODEL_IDS[1]] = max(costs[MODEL_IDS[1]], light * (1.0 + 1e-12))
        costs[MODEL_IDS[2]] = max(
            costs[MODEL_IDS[2]], costs[MODEL_IDS[1]] * (1.0 + 1e-12)
        )
        score_rows.append(scores)
        cost_rows.append(costs)
    return score_rows, cost_rows


def _submission(
    inputs: InputBatch, policy: RoutingPolicy, tier: str, models: Sequence[str]
) -> Submission:
    return Submission(
        inputs.schema_version,
        inputs.challenge_id,
        policy.policy_id,
        inputs.split,
        tier,
        tuple(
            Decision(episode.episode_id, model_id)
            for episode, model_id in zip(inputs.episodes, models)
        ),
    )


def _tier_report(
    inputs: InputBatch,
    outcomes: OutcomeBatch,
    policy: RoutingPolicy,
    tier: str,
    selected: Sequence[str],
) -> Mapping[str, Any]:
    light = tuple(policy.light_model_id for _ in inputs.episodes)
    submissions = [
        _submission(inputs, policy, candidate, selected if candidate == tier else light)
        for candidate in TIERS
    ]
    return score_submissions(inputs, outcomes, submissions, policy)["tiers"][tier]


def _select(
    scores,
    costs,
    policy: RoutingPolicy,
    tier: str,
    safety: float,
):
    selected, predicted_ratio = competition.select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=safety,
    )
    if tier == "premium":
        selected, predicted_ratio = competition.fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=competition.PREMIUM_AX31_FILL_SAFETY,
        )
    return selected, predicted_ratio


def _calibrate(
    inputs: InputBatch,
    outcomes: OutcomeBatch,
    policy: RoutingPolicy,
    predictions: Any,
    targets: Mapping[str, float],
    *,
    maximum_safety: Optional[Mapping[str, float]] = None,
) -> Tuple[Mapping[str, float], Mapping[str, Any], float]:
    scores, costs = _prediction_rows(predictions)
    ratios: Dict[str, float] = {}
    reports: Dict[str, Any] = {}
    weighted = 0.0
    for tier in TIERS:
        best = None
        maximum = 1.0 if maximum_safety is None else maximum_safety[tier]
        for step in range(121):
            safety = 0.5 + step / 240
            if safety > maximum + 1e-12:
                continue
            selected, predicted_ratio = _select(scores, costs, policy, tier, safety)
            report = _tier_report(inputs, outcomes, policy, tier, selected)
            actual_ratio = float(report["budget_ratio"])
            if actual_ratio > targets[tier]:
                continue
            rank = (
                float(report["tier_score"]),
                -actual_ratio,
                -safety,
            )
            if best is None or rank > best[0]:
                best = (rank, safety, predicted_ratio, report)
        if best is None:
            raise RuntimeError(f"{tier}의 안전한 후보를 찾지 못했습니다.")
        ratios[tier] = best[1]
        reports[tier] = {
            "safety_ratio": best[1],
            "predicted_budget_ratio": best[2],
            "actual_budget_ratio": best[3]["budget_ratio"],
            "tier_score": best[3]["tier_score"],
            "model_counts": best[3]["model_counts"],
        }
        weighted += float(policy.tiers[tier].weight) * float(best[3]["tier_score"])
    return ratios, reports, weighted


def _evaluate_safety_ratios(
    inputs: InputBatch,
    outcomes: OutcomeBatch,
    policy: RoutingPolicy,
    predictions: Any,
    targets: Mapping[str, float],
    safety_ratios: Mapping[str, float],
) -> Tuple[Mapping[str, Any], float]:
    scores, costs = _prediction_rows(predictions)
    reports: Dict[str, Any] = {}
    weighted = 0.0
    for tier in TIERS:
        selected, predicted_ratio = _select(
            scores, costs, policy, tier, safety_ratios[tier]
        )
        report = _tier_report(inputs, outcomes, policy, tier, selected)
        if float(report["budget_ratio"]) > targets[tier]:
            raise RuntimeError(
                f"{tier} 안전계수 override가 비용 목표를 초과합니다."
            )
        reports[tier] = {
            "safety_ratio": safety_ratios[tier],
            "predicted_budget_ratio": predicted_ratio,
            "actual_budget_ratio": report["budget_ratio"],
            "tier_score": report["tier_score"],
            "model_counts": report["model_counts"],
        }
        weighted += float(policy.tiers[tier].weight) * float(report["tier_score"])
    return reports, weighted


def _head(intercept: float, coefficients: Any) -> Mapping[str, Any]:
    return {
        "intercept": float(intercept),
        "coefficients": [float(value) for value in coefficients],
    }


def _artifact(
    *,
    policy: RoutingPolicy,
    hash_bins: int,
    mean: Any,
    scale: Any,
    intercept: Any,
    coefficients: Any,
    safety_ratios: Mapping[str, float],
    summary: Mapping[str, Any],
) -> Mapping[str, Any]:
    count = len(MODEL_IDS)
    return {
        "artifact_type": competition.ARTIFACT_TYPE,
        "schema_version": 1,
        "feature_version": competition.FEATURE_VERSION,
        "hash_algorithm": "fnv1a64-signed-word-char",
        "hash_bins": hash_bins,
        "dense_feature_names": list(competition.DENSE_FEATURE_NAMES),
        "model_ids": list(MODEL_IDS),
        "policy_id": policy.policy_id,
        "policy_sha256": policy_sha256(policy),
        "feature_mean": [float(value) for value in mean],
        "feature_scale": [float(value) for value in scale],
        "score_heads": {
            model_id: _head(intercept[index], coefficients[:, index])
            for index, model_id in enumerate(MODEL_IDS)
        },
        "log_cost_heads": {
            model_id: _head(
                intercept[count + index], coefficients[:, count + index]
            )
            for index, model_id in enumerate(MODEL_IDS)
        },
        "tier_safety_ratios": dict(safety_ratios),
        "training_summary": dict(summary),
    }


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


def train(args: argparse.Namespace) -> Mapping[str, Any]:
    _require_numpy()
    policy = load_bundled_policy()
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    matrix, targets, fold_keys = _matrix(inputs, outcomes, policy, args.hash_bins)
    alpha_reports = {}
    best = None
    for alpha in args.alphas:
        predictions = _oof(matrix, targets, fold_keys, args.folds, alpha)
        safety, reports, weighted = _calibrate(
            inputs, outcomes, policy, predictions, args.targets
        )
        score_mse = float(np.mean((predictions[:, :3] - targets[:, :3]) ** 2))
        alpha_reports[str(alpha)] = {
            "weighted_score": weighted,
            "score_mse": score_mse,
            "safety_ratios": safety,
            "tiers": reports,
        }
        rank = (weighted, -score_mse, -alpha)
        if best is None or rank > best[0]:
            best = (rank, alpha, safety, predictions, reports)
    assert best is not None
    _, alpha, oof_safety, _predictions, oof_reports = best
    mean, scale, intercept, coefficients = _fit(matrix, targets, alpha)
    fitted = _predict(matrix, mean, scale, intercept, coefficients)
    safety, fitted_reports, fitted_weighted = _calibrate(
        inputs,
        outcomes,
        policy,
        fitted,
        args.targets,
        maximum_safety=oof_safety,
    )
    if args.safety_overrides is not None:
        safety = args.safety_overrides
        fitted_reports, fitted_weighted = _evaluate_safety_ratios(
            inputs,
            outcomes,
            policy,
            fitted,
            args.targets,
            safety,
        )
    summary = {
        "num_episodes": len(inputs.episodes),
        "folds": args.folds,
        "fold_strategy": "normalized-template-fnv1a64",
        "ridge_alpha": alpha,
        "hash_bins": args.hash_bins,
        "input_sha256": _sha256(args.input),
        "outcomes_sha256": _sha256(args.outcomes),
        "budget_ratio_targets": args.targets,
    }
    artifact_value = _artifact(
        policy=policy,
        hash_bins=args.hash_bins,
        mean=mean,
        scale=scale,
        intercept=intercept,
        coefficients=coefficients,
        safety_ratios=safety,
        summary=summary,
    )
    competition.parse_artifact(artifact_value)
    report = {
        "report_type": "ossp-competition-training-v1",
        "training_summary": summary,
        "feature_dimension": matrix.shape[1],
        "selected_alpha": alpha,
        "oof_weighted_score": best[0][0],
        "oof_tiers": oof_reports,
        "fitted_weighted_score": fitted_weighted,
        "fitted_tiers": fitted_reports,
        "alpha_candidates": alpha_reports,
    }
    _write(args.artifact, artifact_value)
    _write(args.report, report)
    return report


def _float_list(value: str) -> Tuple[float, ...]:
    result = tuple(float(item) for item in value.split(","))
    if not result or any(not math.isfinite(item) or item <= 0 for item in result):
        raise argparse.ArgumentTypeError("양의 alpha 목록이 필요합니다.")
    return result


def _safety_ratios(value: str) -> Mapping[str, float]:
    try:
        pairs = [item.split("=", 1) for item in value.split(",")]
        result = {tier: float(number) for tier, number in pairs}
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("안전계수 형식이 올바르지 않습니다.") from exc
    if set(result) != set(TIERS) or any(
        not math.isfinite(item) or not 0 < item <= 1 for item in result.values()
    ):
        raise argparse.ArgumentTypeError("세 등급의 0~1 안전계수가 필요합니다.")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hash-bins", type=int, default=competition.HASH_BINS)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument(
        "--alphas", type=_float_list, default=_float_list("1,10,100,1000")
    )
    parser.add_argument("--safety-overrides", type=_safety_ratios)
    parser.set_defaults(targets=dict(DEFAULT_TARGETS))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = train(args)
    except (OSError, ProtocolError, RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: competition artifact를 생성했습니다 "
        f"(OOF {report['oof_weighted_score']:.6f}, fitted {report['fitted_weighted_score']:.6f})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
