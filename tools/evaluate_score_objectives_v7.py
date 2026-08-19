# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Compare direct-uplift and generation-weighted score objectives for V7."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

import train_competition_router as base
from evaluate_router_robustness import content_groups
from ossp_router import aggressive_v5, aggressive_v6
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
)


REPORT_TYPE = "ossp-score-objective-study-v1"
DEFAULT_ALPHAS = (300.0, 1_000.0, 3_000.0, 10_000.0, 30_000.0, 100_000.0)


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("score objective 평가에는 NumPy가 필요합니다.")


def uplift_targets(scores: Any) -> Any:
    """Represent scores as Light plus each strong model's uplift over Light."""

    _require_numpy()
    result = scores.copy()
    result[:, 1] = scores[:, 1] - scores[:, 0]
    result[:, 2] = scores[:, 2] - scores[:, 0]
    return result


def reconstruct_uplift_scores(predictions: Any) -> Any:
    _require_numpy()
    result = predictions.copy()
    result[:, 1] = predictions[:, 0] + predictions[:, 1]
    result[:, 2] = predictions[:, 0] + predictions[:, 2]
    return np.clip(result, 0.0, 1.0)


def _weighted_score_fit(
    matrix: Any, scores: Any, weights: Any, alpha: float
) -> Sequence[Mapping[str, Any]]:
    """Fit one deterministic weighted Ridge head per model score."""

    _require_numpy()
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale = np.where(scale > 1e-8, scale, 1.0)
    standardized = (matrix - mean) / scale
    heads = []
    for model_index in range(len(MODEL_IDS)):
        weight = weights[:, model_index]
        weighted_mean = np.average(standardized, axis=0, weights=weight)
        target_mean = float(np.average(scores[:, model_index], weights=weight))
        centered_matrix = standardized - weighted_mean
        centered_target = scores[:, model_index] - target_mean
        root_weight = np.sqrt(weight)
        weighted_matrix = centered_matrix * root_weight[:, None]
        weighted_target = centered_target * root_weight
        # Some macOS Accelerate builds emit spurious floating-point warnings
        # for finite matrix products. Match the production trainer: suppress
        # those warnings locally, then reject any genuinely non-finite result.
        with np.errstate(all="ignore"):
            system = (
                weighted_matrix.T @ weighted_matrix
                + alpha * np.eye(weighted_matrix.shape[1])
            )
            coefficients = np.linalg.solve(
                system, weighted_matrix.T @ weighted_target
            )
        if not np.isfinite(coefficients).all():
            raise RuntimeError("가중 Ridge 학습 결과에 유한하지 않은 값이 있습니다.")
        heads.append(
            {
                "mean": mean,
                "scale": scale,
                "weighted_mean": weighted_mean,
                "target_mean": target_mean,
                "coefficients": coefficients,
            }
        )
    return heads


def _weighted_score_predict(matrix: Any, heads: Sequence[Mapping[str, Any]]) -> Any:
    columns = []
    for head in heads:
        standardized = (matrix - head["mean"]) / head["scale"]
        with np.errstate(all="ignore"):
            prediction = (
                (standardized - head["weighted_mean"]) @ head["coefficients"]
                + head["target_mean"]
            )
        if not np.isfinite(prediction).all():
            raise RuntimeError("가중 Ridge 예측에 유한하지 않은 값이 있습니다.")
        columns.append(prediction)
    return np.clip(np.column_stack(columns), 0.0, 1.0)


def _route(
    indices: Sequence[int],
    predicted_scores: Any,
    predicted_costs: Mapping[str, Sequence[Mapping[str, float]]],
    actual_scores: Any,
    actual_costs: Any,
    tier: str,
    artifact: Any,
    policy: Any,
) -> Mapping[str, Any]:
    scores = [
        {
            model_id: float(predicted_scores[index, model_index])
            for model_index, model_id in enumerate(MODEL_IDS)
        }
        for index in indices
    ]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    costs = [predicted_costs[tier][index] for index in indices]
    selected, predicted_ratio = aggressive_v5._select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=artifact.base.tiers[tier].safety_ratio,
        steps=artifact.base.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, predicted_ratio = aggressive_v5._fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=artifact.base.premium_fill_safety_ratio,
            steps=artifact.base.fixed_bisection_steps,
        )
    selected_indices = np.asarray([MODEL_IDS.index(model_id) for model_id in selected])
    rows = np.asarray(indices)
    quality = float(actual_scores[rows, selected_indices].mean())
    ratio = float(
        actual_costs[rows, selected_indices].sum() / actual_costs[rows, 0].sum()
    )
    limit = float(policy.tiers[tier].budget_multiplier)
    return {
        "quality_score": quality,
        "tier_score": quality if ratio <= limit else 0.0,
        "budget_ratio": ratio,
        "budget_limit": limit,
        "budget_passed": ratio <= limit,
        "predicted_budget_ratio": predicted_ratio,
        "model_counts": {
            model_id: selected.count(model_id) for model_id in MODEL_IDS
        },
    }


def _candidate_report(
    predicted_scores: Any,
    predicted_costs: Mapping[str, Sequence[Mapping[str, float]]],
    actual_scores: Any,
    actual_costs: Any,
    groups: Mapping[str, Sequence[int]],
    artifact: Any,
    policy: Any,
) -> Mapping[str, Any]:
    tiers = {
        tier: _route(
            groups["all"],
            predicted_scores,
            predicted_costs,
            actual_scores,
            actual_costs,
            tier,
            artifact,
            policy,
        )
        for tier in TIERS
    }
    failures = []
    for name, indices in groups.items():
        if name == "all":
            continue
        for tier in TIERS:
            row = _route(
                indices,
                predicted_scores,
                predicted_costs,
                actual_scores,
                actual_costs,
                tier,
                artifact,
                policy,
            )
            if not row["budget_passed"]:
                failures.append(
                    {"group": name, "tier": tier, "budget_ratio": row["budget_ratio"]}
                )
    return {
        "final_score": math.fsum(
            float(policy.tiers[tier].weight) * tiers[tier]["tier_score"]
            for tier in TIERS
        ),
        "standalone_budget_failures": failures,
        "tiers": tiers,
    }


def _generation_weights(inputs: Any, outcomes: Any) -> Any:
    index = {(row.episode_id, row.model_id): row for row in outcomes.outcomes}
    return np.asarray(
        [
            [index[(episode.episode_id, model_id)].num_generations for model_id in MODEL_IDS]
            for episode in inputs.episodes
        ],
        dtype=np.float64,
    )


def evaluate(args: argparse.Namespace) -> Mapping[str, Any]:
    _require_numpy()
    policy = load_bundled_policy()
    train_inputs = load_input(args.train_input)
    train_outcomes = load_outcomes(args.train_outcomes)
    eval_inputs = load_input(args.eval_input)
    eval_outcomes = load_outcomes(args.eval_outcomes)
    train_matrix, train_targets, _ = base._matrix(
        train_inputs, train_outcomes, policy, args.hash_bins
    )
    eval_matrix, eval_targets, _ = base._matrix(
        eval_inputs, eval_outcomes, policy, args.hash_bins
    )
    train_scores = train_targets[:, : len(MODEL_IDS)]
    actual_scores = eval_targets[:, : len(MODEL_IDS)]
    actual_costs = np.exp(eval_targets[:, len(MODEL_IDS) :])
    weights = _generation_weights(train_inputs, train_outcomes)
    artifact = aggressive_v6.compile_artifact(
        aggressive_v5.load_artifact(args.base_artifact)
    )
    predicted_costs = {}
    baseline_scores = {}
    for tier in TIERS:
        rows = tuple(
            aggressive_v6.predict_episode(episode, artifact, tier)
            for episode in eval_inputs.episodes
        )
        baseline_scores[tier] = np.asarray(
            [[row[0][model_id] for model_id in MODEL_IDS] for row in rows]
        )
        predicted_costs[tier] = tuple(row[1] for row in rows)
    groups = content_groups(eval_inputs.episodes)
    candidates: Dict[str, Mapping[str, Any]] = {}
    for alpha in args.alphas:
        transformed = uplift_targets(train_scores)
        mean, scale, intercept, coefficients = base._fit(
            train_matrix, transformed, alpha
        )
        uplift_predictions = reconstruct_uplift_scores(
            base._predict(eval_matrix, mean, scale, intercept, coefficients)
        )
        candidates[f"direct-uplift-a{alpha:g}"] = _candidate_report(
            uplift_predictions,
            predicted_costs,
            actual_scores,
            actual_costs,
            groups,
            artifact,
            policy,
        )
        weighted_predictions = _weighted_score_predict(
            eval_matrix,
            _weighted_score_fit(train_matrix, train_scores, weights, alpha),
        )
        candidates[f"generation-weighted-a{alpha:g}"] = _candidate_report(
            weighted_predictions,
            predicted_costs,
            actual_scores,
            actual_costs,
            groups,
            artifact,
            policy,
        )
    # Tier-specific V5 scores cannot be represented as one matrix, so report its
    # already audited score as an explicit comparison record.
    baseline_tiers = {
        tier: _route(
            groups["all"],
            baseline_scores[tier],
            predicted_costs,
            actual_scores,
            actual_costs,
            tier,
            artifact,
            policy,
        )
        for tier in TIERS
    }
    baseline = {
        "final_score": math.fsum(
            float(policy.tiers[tier].weight) * baseline_tiers[tier]["tier_score"]
            for tier in TIERS
        ),
        "tiers": baseline_tiers,
    }
    best = max(candidates, key=lambda name: candidates[name]["final_score"])
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "protocol": "train-fit-to-dev-report-no-dev-selection",
        "base_artifact": str(args.base_artifact),
        "hash_bins": args.hash_bins,
        "alphas": list(args.alphas),
        "groups": {name: len(indices) for name, indices in groups.items()},
        "baseline": baseline,
        "best_candidate": best,
        "candidates": candidates,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-input", type=Path, required=True)
    parser.add_argument("--train-outcomes", type=Path, required=True)
    parser.add_argument("--eval-input", type=Path, required=True)
    parser.add_argument("--eval-outcomes", type=Path, required=True)
    parser.add_argument("--base-artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hash-bins", type=int, default=512)
    parser.add_argument("--alphas", type=base._float_list, default=DEFAULT_ALPHAS)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate(args)
        base._write(args.report, report)
    except (OSError, ProtocolError, RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: score objective study를 생성했습니다 "
        f"(best={report['best_candidate']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
