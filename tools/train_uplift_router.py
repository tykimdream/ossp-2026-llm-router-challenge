# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Train direct quality-uplift heads with template-group OOF budget calibration."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from ossp_router import competition, uplift
from ossp_router.heuristic import episode_text
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    Outcome,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
    policy_sha256,
)


DEFAULT_TARGETS = {"fast": 1.18, "balanced": 1.85, "premium": 3.5}


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("uplift 학습에는 NumPy가 필요합니다.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cost(outcome: Outcome, policy) -> float:
    rates = policy.models[outcome.model_id]
    unit = Decimal(policy.token_unit)
    return float(
        rates.fixed_cost
        + Decimal(outcome.input_tokens) * rates.input_token_rate / unit
        + Decimal(outcome.output_tokens) * rates.output_token_rate / unit
    )


def _matrix(inputs, outcomes, policy, hash_bins):
    _require_numpy()
    index = {(item.episode_id, item.model_id): item for item in outcomes.outcomes}
    expected = {
        (episode.episode_id, model_id)
        for episode in inputs.episodes
        for model_id in MODEL_IDS
    }
    if set(index) != expected:
        raise ProtocolError("uplift 학습 outcome 행렬이 완전하지 않습니다.")
    matrix = np.asarray(
        [competition.raw_feature_vector(item, hash_bins) for item in inputs.episodes],
        dtype=np.float64,
    )
    scores = []
    costs = []
    fold_keys = []
    for episode in inputs.episodes:
        rows = [index[(episode.episode_id, model_id)] for model_id in MODEL_IDS]
        score_row = [float(item.score) for item in rows]
        cost_row = [_cost(item, policy) for item in rows]
        scores.append(score_row)
        costs.append(cost_row)
        template = competition.normalized_template(episode_text(episode))
        fold_keys.append(competition.stable_hash(template))
    score_array = np.asarray(scores, dtype=np.float64)
    cost_array = np.asarray(costs, dtype=np.float64)
    targets = np.column_stack(
        (
            score_array[:, 1] - score_array[:, 0],
            score_array[:, 2] - score_array[:, 0],
            np.log(cost_array),
        )
    )
    return matrix, targets, score_array, cost_array, np.asarray(fold_keys)


def _fit(matrix, targets, alpha):
    mean = matrix.mean(axis=0)
    scale = np.where(matrix.std(axis=0) > 1e-12, matrix.std(axis=0), 1.0)
    standardized = (matrix - mean) / scale
    intercept = targets.mean(axis=0)
    centered = targets - intercept
    rows, columns = standardized.shape
    with np.errstate(all="ignore"):
        if rows <= columns:
            system = standardized @ standardized.T + alpha * np.eye(rows)
            coefficients = standardized.T @ np.linalg.solve(system, centered)
        else:
            system = standardized.T @ standardized + alpha * np.eye(columns)
            coefficients = np.linalg.solve(system, standardized.T @ centered)
    if not np.isfinite(coefficients).all():
        raise RuntimeError("uplift ridge 계수가 유한하지 않습니다.")
    return mean, scale, intercept, coefficients


def _predict(matrix, mean, scale, intercept, coefficients):
    with np.errstate(all="ignore"):
        result = (matrix - mean) / scale @ coefficients + intercept
    if not np.isfinite(result).all():
        raise RuntimeError("uplift 예측이 유한하지 않습니다.")
    return result


def _oof(matrix, targets, fold_ids, alpha):
    predictions = np.empty_like(targets)
    for fold in sorted(set(int(item) for item in fold_ids)):
        validation = fold_ids == fold
        training = ~validation
        mean, scale, intercept, coefficients = _fit(
            matrix[training], targets[training], alpha
        )
        predictions[validation] = _predict(
            matrix[validation], mean, scale, intercept, coefficients
        )
    return predictions


def _prediction_rows(predictions):
    score_rows = []
    cost_rows = []
    for row in predictions:
        score_rows.append(
            {
                MODEL_IDS[0]: 0.0,
                MODEL_IDS[1]: min(1.0, max(-1.0, float(row[0]))),
                MODEL_IDS[2]: min(1.0, max(-1.0, float(row[1]))),
            }
        )
        costs = {
            model_id: math.exp(min(50.0, max(-50.0, float(row[index + 2]))))
            for index, model_id in enumerate(MODEL_IDS)
        }
        costs[MODEL_IDS[1]] = max(costs[MODEL_IDS[1]], costs[MODEL_IDS[0]] * (1 + 1e-12))
        costs[MODEL_IDS[2]] = max(costs[MODEL_IDS[2]], costs[MODEL_IDS[1]] * (1 + 1e-12))
        cost_rows.append(costs)
    return score_rows, cost_rows


def _select(score_rows, cost_rows, policy, tier, safety):
    scores = [dict(item) for item in score_rows]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, predicted_ratio = competition.select_models(
        scores,
        cost_rows,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=safety,
    )
    if tier == "premium":
        selected, predicted_ratio = competition.fill_ax31(
            selected,
            scores,
            cost_rows,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=competition.PREMIUM_AX31_FILL_SAFETY,
        )
    return selected, predicted_ratio


def _observed(selected, scores, costs, fold_ids):
    model_index = {model_id: index for index, model_id in enumerate(MODEL_IDS)}
    choices = np.asarray([model_index[item] for item in selected])
    rows = np.arange(len(choices))
    quality = scores[rows, choices]
    selected_cost = costs[rows, choices]
    ratios = {}
    for fold in sorted(set(int(item) for item in fold_ids)):
        mask = fold_ids == fold
        ratios[str(fold)] = float(selected_cost[mask].sum() / costs[mask, 0].sum())
    return (
        float(quality.mean()),
        float(selected_cost.sum() / costs[:, 0].sum()),
        ratios,
        {model_id: selected.count(model_id) for model_id in MODEL_IDS},
    )


def _calibrate(predictions, scores, costs, fold_ids, policy, targets):
    score_rows, cost_rows = _prediction_rows(predictions)
    ratios: Dict[str, float] = {}
    reports: Dict[str, Any] = {}
    weighted = 0.0
    for tier in TIERS:
        best = None
        for step in range(161):
            safety = 0.2 + step / 200
            selected, predicted_ratio = _select(
                score_rows, cost_rows, policy, tier, safety
            )
            quality, actual_ratio, fold_ratios, counts = _observed(
                selected, scores, costs, fold_ids
            )
            worst_fold = max(fold_ratios.values())
            if actual_ratio > targets[tier] or worst_fold > targets[tier]:
                continue
            rank = (quality, -worst_fold, -actual_ratio, -safety)
            if best is None or rank > best[0]:
                best = (
                    rank,
                    safety,
                    predicted_ratio,
                    quality,
                    actual_ratio,
                    fold_ratios,
                    counts,
                )
        if best is None:
            raise RuntimeError(f"{tier}의 OOF-safe uplift 후보가 없습니다.")
        ratios[tier] = best[1]
        reports[tier] = {
            "safety_ratio": best[1],
            "predicted_budget_ratio": best[2],
            "tier_score": best[3],
            "actual_budget_ratio": best[4],
            "fold_budget_ratios": best[5],
            "worst_fold_budget_ratio": max(best[5].values()),
            "model_counts": best[6],
        }
        weighted += float(policy.tiers[tier].weight) * best[3]
    return ratios, reports, weighted


def _head(intercept, coefficients):
    return {
        "intercept": float(intercept),
        "coefficients": [float(item) for item in coefficients],
    }


def train(args):
    _require_numpy()
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    policy = load_bundled_policy()
    matrix, targets, scores, costs, fold_keys = _matrix(
        inputs, outcomes, policy, args.hash_bins
    )
    fold_ids = fold_keys % args.folds
    candidates = {}
    best = None
    for alpha in args.alphas:
        predictions = _oof(matrix, targets, fold_ids, alpha)
        safety, tiers, weighted = _calibrate(
            predictions, scores, costs, fold_ids, policy, args.targets
        )
        uplift_mse = float(np.mean((predictions[:, :2] - targets[:, :2]) ** 2))
        candidates[str(alpha)] = {
            "weighted_score": weighted,
            "uplift_mse": uplift_mse,
            "tier_safety_ratios": safety,
            "tiers": tiers,
        }
        rank = (weighted, -uplift_mse, -alpha)
        if best is None or rank > best[0]:
            best = (rank, alpha, predictions, safety, tiers, weighted)
    assert best is not None
    _, alpha, _predictions, safety, oof_tiers, oof_weighted = best
    mean, scale, intercept, coefficients = _fit(matrix, targets, alpha)
    artifact = {
        "artifact_type": uplift.ARTIFACT_TYPE,
        "schema_version": 1,
        "feature_version": competition.FEATURE_VERSION,
        "hash_bins": args.hash_bins,
        "dense_feature_names": list(competition.DENSE_FEATURE_NAMES),
        "model_ids": list(MODEL_IDS),
        "policy_id": policy.policy_id,
        "policy_sha256": policy_sha256(policy),
        "feature_mean": [float(item) for item in mean],
        "feature_scale": [float(item) for item in scale],
        "uplift_heads": {
            model_id: _head(intercept[index], coefficients[:, index])
            for index, model_id in enumerate(uplift.UPLIFT_MODEL_IDS)
        },
        "log_cost_heads": {
            model_id: _head(intercept[index + 2], coefficients[:, index + 2])
            for index, model_id in enumerate(MODEL_IDS)
        },
        "tier_safety_ratios": safety,
        "training_summary": {
            "num_episodes": len(inputs.episodes),
            "input_sha256": _sha256(args.input),
            "outcomes_sha256": _sha256(args.outcomes),
            "folds": args.folds,
            "fold_strategy": "normalized-template-fnv1a64",
            "ridge_alpha": alpha,
            "target": "quality-uplift-relative-to-light-and-log-cost",
            "budget_ratio_targets": args.targets,
            "budget_calibration": "pooled-and-worst-fold",
        },
    }
    uplift.parse_artifact(artifact)
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = {
        "report_type": "ossp-uplift-training-v1",
        "selected_alpha": alpha,
        "oof_weighted_score": oof_weighted,
        "oof_tiers": oof_tiers,
        "alpha_candidates": candidates,
        "training_summary": artifact["training_summary"],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _float_list(value: str):
    try:
        result = tuple(float(item) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("양수 alpha 목록이 필요합니다.") from exc
    if not result or any(not math.isfinite(item) or item <= 0 for item in result):
        raise argparse.ArgumentTypeError("양수 alpha 목록이 필요합니다.")
    return result


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hash-bins", type=int, default=competition.HASH_BINS)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--alphas", type=_float_list, default=_float_list("100,1000,10000"))
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
        "OK: uplift artifact를 생성했습니다 "
        f"(alpha={report['selected_alpha']}, OOF={report['oof_weighted_score']:.6f})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
