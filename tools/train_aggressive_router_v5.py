# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Train the deterministic tier-specific aggressive Ridge ensemble v5."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

import train_competition_router as base
import train_risk_router_v4 as risk
from ossp_router import aggressive_v5
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
    policy_sha256,
)


DEFAULT_HASH_BINS = 512
DEFAULT_TARGETS = {"fast": 1.23, "balanced": 1.95, "premium": 3.85}
DEFAULT_ENSEMBLES = {
    "fast": (300.0, 500.0, 5000.0),
    "balanced": (10000.0,),
    "premium": (3000.0, 10000.0, 15000.0),
}
PREMIUM_FILL_SAFETY = 0.65


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("v5 학습에는 NumPy가 필요합니다.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ensemble(predictions: Mapping[float, Any], alphas: Sequence[float]) -> Any:
    return np.mean(np.stack([predictions[alpha] for alpha in alphas]), axis=0)


def _calibrate_tier(
    predictions: Any,
    actual_scores: Any,
    actual_costs: Any,
    fold_ids: Any,
    policy: Any,
    tier: str,
    target: float,
) -> Tuple[float, Mapping[str, Any], float]:
    predicted_scores, predicted_costs = risk._prediction_arrays(predictions)
    fold_values = tuple(sorted(set(int(item) for item in fold_ids)))
    best = None
    for step in range(161):
        safety = 0.2 + step / 200.0
        selected, predicted_ratio = risk._select(
            predicted_scores,
            predicted_costs,
            tier,
            safety,
            float(policy.tiers[tier].budget_multiplier),
        )
        quality, actual_ratio = risk._observed(
            selected, actual_scores, actual_costs
        )
        fold_reports = {}
        for fold in fold_values:
            mask = fold_ids == fold
            fold_quality, fold_ratio = risk._observed(
                selected, actual_scores, actual_costs, mask
            )
            fold_reports[str(fold)] = {
                "quality": fold_quality,
                "budget_ratio": fold_ratio,
            }
        worst_ratio = max(item["budget_ratio"] for item in fold_reports.values())
        if actual_ratio > target or worst_ratio > target:
            continue
        rank = (quality, -worst_ratio, -actual_ratio, -safety)
        if best is None or rank > best[0]:
            best = (
                rank,
                safety,
                predicted_ratio,
                quality,
                actual_ratio,
                fold_reports,
                selected,
            )
    if best is None:
        raise RuntimeError(f"{tier}의 aggressive 예산 후보가 없습니다.")
    report = {
        "safety_ratio": best[1],
        "predicted_budget_ratio": best[2],
        "tier_score": best[3],
        "actual_budget_ratio": best[4],
        "folds": best[5],
        "worst_fold_budget_ratio": max(
            item["budget_ratio"] for item in best[5].values()
        ),
        "model_counts": {
            model_id: int((best[6] == index).sum())
            for index, model_id in enumerate(MODEL_IDS)
        },
    }
    return best[1], report, best[3]


def _model_name(alpha: float) -> str:
    return f"ridge-a{alpha:g}".replace(".", "p")


def train(args: argparse.Namespace) -> Mapping[str, Any]:
    _require_numpy()
    policy = load_bundled_policy()
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    matrix, targets, fold_keys = base._matrix(
        inputs, outcomes, policy, args.hash_bins
    )
    fold_ids = fold_keys % args.folds
    actual_scores = targets[:, : len(MODEL_IDS)]
    actual_costs = np.exp(targets[:, len(MODEL_IDS) :])
    unique_alphas = tuple(
        sorted({alpha for values in DEFAULT_ENSEMBLES.values() for alpha in values})
    )
    oof_predictions = {
        alpha: base._oof(matrix, targets, fold_keys, args.folds, alpha)
        for alpha in unique_alphas
    }

    safety_ratios: Dict[str, float] = {}
    tier_reports: Dict[str, Any] = {}
    weighted = 0.0
    for tier in TIERS:
        predictions = _ensemble(oof_predictions, DEFAULT_ENSEMBLES[tier])
        safety, report, quality = _calibrate_tier(
            predictions,
            actual_scores,
            actual_costs,
            fold_ids,
            policy,
            tier,
            args.targets[tier],
        )
        safety_ratios[tier] = safety
        tier_reports[tier] = report
        weighted += float(policy.tiers[tier].weight) * quality

    models = {}
    model_fit_reports = {}
    for alpha in unique_alphas:
        mean, scale, intercept, coefficients = base._fit(matrix, targets, alpha)
        name = _model_name(alpha)
        model_summary = {
            "num_episodes": len(inputs.episodes),
            "ridge_alpha": alpha,
            "hash_bins": args.hash_bins,
            "role": "aggressive-v5-tier-ensemble-member",
        }
        models[name] = base._artifact(
            policy=policy,
            hash_bins=args.hash_bins,
            mean=mean,
            scale=scale,
            intercept=intercept,
            coefficients=coefficients,
            safety_ratios={tier: 1.0 for tier in TIERS},
            summary=model_summary,
        )
        fitted = base._predict(matrix, mean, scale, intercept, coefficients)
        model_fit_reports[name] = {
            "alpha": alpha,
            "score_mse": float(
                np.mean((fitted[:, : len(MODEL_IDS)] - actual_scores) ** 2)
            ),
        }

    ensemble_names = {
        tier: [_model_name(alpha) for alpha in DEFAULT_ENSEMBLES[tier]]
        for tier in TIERS
    }
    summary = {
        "num_episodes": len(inputs.episodes),
        "folds": args.folds,
        "fold_strategy": "normalized-template-fnv1a64",
        "hash_bins": args.hash_bins,
        "tier_ensemble_alphas": {
            tier: list(DEFAULT_ENSEMBLES[tier]) for tier in TIERS
        },
        "budget_ratio_targets": args.targets,
        "budget_calibration": "aggressive-pooled-and-worst-template-fold",
        "input_sha256": _sha256(args.input),
        "outcomes_sha256": _sha256(args.outcomes),
        "selector_bisection_steps": aggressive_v5.FIXED_BISECTION_STEPS,
    }
    artifact = {
        "artifact_type": aggressive_v5.ARTIFACT_TYPE,
        "schema_version": 1,
        "policy_id": policy.policy_id,
        "policy_sha256": policy_sha256(policy),
        "hash_bins": args.hash_bins,
        "models": models,
        "tiers": {
            tier: {
                "model_ids": ensemble_names[tier],
                "safety_ratio": safety_ratios[tier],
            }
            for tier in TIERS
        },
        "premium_fill_safety_ratio": PREMIUM_FILL_SAFETY,
        "fixed_bisection_steps": aggressive_v5.FIXED_BISECTION_STEPS,
        "training_summary": summary,
    }
    aggressive_v5.parse_artifact(artifact)
    report = {
        "report_type": "ossp-aggressive-router-v5-training-v1",
        "training_summary": summary,
        "oof_weighted_score": weighted,
        "oof_tiers": tier_reports,
        "tier_safety_ratios": safety_ratios,
        "ensemble_models": ensemble_names,
        "model_fit_reports": model_fit_reports,
    }
    base._write(args.artifact, artifact)
    base._write(args.report, report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hash-bins", type=int, default=DEFAULT_HASH_BINS)
    parser.add_argument("--folds", type=int, default=5)
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
        "OK: aggressive router v5 artifact를 생성했습니다 "
        f"(OOF={report['oof_weighted_score']:.6f})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
