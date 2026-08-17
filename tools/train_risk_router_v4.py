# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Train the 512-bin Ridge router with worst-fold budget constraints."""

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
from ossp_router import competition
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
)


DEFAULT_TARGETS = {"fast": 1.18, "balanced": 1.85, "premium": 3.5}
DEFAULT_HASH_BINS = 512
BISECTION_STEPS = 48


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("v4 학습에는 NumPy가 필요합니다.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _prediction_arrays(predictions: Any) -> Tuple[Any, Any]:
    scores = np.clip(predictions[:, : len(MODEL_IDS)], 0.0, 1.0).copy()
    costs = np.exp(np.clip(predictions[:, len(MODEL_IDS) :], -50.0, 50.0))
    costs[:, 1] = np.maximum(costs[:, 1], costs[:, 0] * (1.0 + 1e-12))
    costs[:, 2] = np.maximum(costs[:, 2], costs[:, 1] * (1.0 + 1e-12))
    return scores, costs


def _select(
    scores: Any,
    costs: Any,
    tier: str,
    safety_ratio: float,
    budget_multiplier: float,
) -> Tuple[Any, float]:
    """Vectorized equivalent of the deterministic runtime selector."""

    light_total = float(costs[:, 0].sum())
    cap = light_total * max(1.0, budget_multiplier * safety_ratio)
    adjusted_scores = scores.copy()
    if tier == "fast":
        adjusted_scores[:, 2] = -2.0

    def choose(penalty: float) -> Tuple[Any, float]:
        # np.argmax returns the first maximum, preserving Light -> AX31 -> K1 ties.
        selected = np.argmax(
            adjusted_scores - penalty * costs / light_total,
            axis=1,
        )
        total = float(costs[np.arange(len(selected)), selected].sum())
        return selected, total

    selected, total = choose(0.0)
    if total > cap:
        low, high = 0.0, 1.0
        selected, total = choose(high)
        while total > cap and high < 2**60:
            low, high = high, high * 2.0
            selected, total = choose(high)
        for _ in range(BISECTION_STEPS):
            middle = (low + high) / 2.0
            candidate, candidate_total = choose(middle)
            if candidate_total <= cap:
                high, selected, total = middle, candidate, candidate_total
            else:
                low = middle

    if tier == "premium":
        fill_cap = max(
            total,
            light_total
            * budget_multiplier
            * competition.PREMIUM_AX31_FILL_SAFETY,
        )
        base_selected = selected.copy()
        eligible = base_selected == 0
        gain = adjusted_scores[:, 1] - adjusted_scores[:, 0]
        extra = costs[:, 1] - costs[:, 0]

        def fill(penalty: float) -> Tuple[Any, float]:
            candidate = base_selected.copy()
            promote = eligible & ((gain - penalty * extra / light_total) > 0.0)
            candidate[promote] = 1
            candidate_total = float(
                costs[np.arange(len(candidate)), candidate].sum()
            )
            return candidate, candidate_total

        candidate, candidate_total = fill(0.0)
        if candidate_total > fill_cap:
            low, high = 0.0, 1.0
            candidate, candidate_total = fill(high)
            while candidate_total > fill_cap and high < 2**60:
                low, high = high, high * 2.0
                candidate, candidate_total = fill(high)
            for _ in range(BISECTION_STEPS):
                middle = (low + high) / 2.0
                filled, filled_total = fill(middle)
                if filled_total <= fill_cap:
                    high, candidate, candidate_total = middle, filled, filled_total
                else:
                    low = middle
        if candidate_total <= fill_cap:
            selected, total = candidate, candidate_total

    return selected, total / light_total


def _observed(
    selected: Any,
    scores: Any,
    costs: Any,
    mask: Optional[Any] = None,
) -> Tuple[float, float]:
    if mask is None:
        mask = np.ones(len(selected), dtype=bool)
    rows = np.flatnonzero(mask)
    quality = float(scores[rows, selected[rows]].mean())
    ratio = float(
        costs[rows, selected[rows]].sum() / costs[rows, 0].sum()
    )
    return quality, ratio


def _calibrate(
    predictions: Any,
    actual_scores: Any,
    actual_costs: Any,
    fold_ids: Any,
    policy: Any,
    targets: Mapping[str, float],
    maximum_safety: Optional[Mapping[str, float]] = None,
) -> Tuple[Mapping[str, float], Mapping[str, Any], float]:
    predicted_scores, predicted_costs = _prediction_arrays(predictions)
    ratios: Dict[str, float] = {}
    reports: Dict[str, Any] = {}
    weighted = 0.0
    fold_values = tuple(sorted(set(int(item) for item in fold_ids)))
    for tier in TIERS:
        maximum = 1.0 if maximum_safety is None else maximum_safety[tier]
        best = None
        for step in range(161):
            safety = 0.2 + step / 200.0
            if safety > maximum + 1e-12:
                continue
            selected, predicted_ratio = _select(
                predicted_scores,
                predicted_costs,
                tier,
                safety,
                float(policy.tiers[tier].budget_multiplier),
            )
            quality, actual_ratio = _observed(
                selected, actual_scores, actual_costs
            )
            fold_ratios = {
                str(fold): _observed(
                    selected,
                    actual_scores,
                    actual_costs,
                    fold_ids == fold,
                )[1]
                for fold in fold_values
            }
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
                    selected,
                )
        if best is None:
            raise RuntimeError(f"{tier}의 worst-fold-safe 후보가 없습니다.")
        ratios[tier] = best[1]
        reports[tier] = {
            "safety_ratio": best[1],
            "predicted_budget_ratio": best[2],
            "tier_score": best[3],
            "actual_budget_ratio": best[4],
            "fold_budget_ratios": best[5],
            "worst_fold_budget_ratio": max(best[5].values()),
            "model_counts": {
                model_id: int((best[6] == index).sum())
                for index, model_id in enumerate(MODEL_IDS)
            },
        }
        weighted += float(policy.tiers[tier].weight) * best[3]
    return ratios, reports, weighted


def _evaluate_fixed(
    predictions: Any,
    actual_scores: Any,
    actual_costs: Any,
    fold_ids: Any,
    policy: Any,
    safety_ratios: Mapping[str, float],
) -> Tuple[Mapping[str, Any], float]:
    """Verify fixed OOF controls against every fitted public-data fold."""

    predicted_scores, predicted_costs = _prediction_arrays(predictions)
    fold_values = tuple(sorted(set(int(item) for item in fold_ids)))
    reports: Dict[str, Any] = {}
    weighted = 0.0
    for tier in TIERS:
        budget_limit = float(policy.tiers[tier].budget_multiplier)
        selected, predicted_ratio = _select(
            predicted_scores,
            predicted_costs,
            tier,
            safety_ratios[tier],
            budget_limit,
        )
        quality, actual_ratio = _observed(selected, actual_scores, actual_costs)
        fold_ratios = {
            str(fold): _observed(
                selected,
                actual_scores,
                actual_costs,
                fold_ids == fold,
            )[1]
            for fold in fold_values
        }
        worst_fold = max(fold_ratios.values())
        if actual_ratio > budget_limit or worst_fold > budget_limit:
            raise RuntimeError(
                f"{tier}의 고정 OOF 안전계수가 fitted 공식 예산을 초과합니다."
            )
        reports[tier] = {
            "safety_ratio": safety_ratios[tier],
            "predicted_budget_ratio": predicted_ratio,
            "tier_score": quality,
            "actual_budget_ratio": actual_ratio,
            "fold_budget_ratios": fold_ratios,
            "worst_fold_budget_ratio": worst_fold,
            "model_counts": {
                model_id: int((selected == index).sum())
                for index, model_id in enumerate(MODEL_IDS)
            },
        }
        weighted += float(policy.tiers[tier].weight) * quality
    return reports, weighted


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

    candidates = {}
    best = None
    for alpha in args.alphas:
        predictions = base._oof(matrix, targets, fold_keys, args.folds, alpha)
        safety, tiers, weighted = _calibrate(
            predictions,
            actual_scores,
            actual_costs,
            fold_ids,
            policy,
            args.targets,
        )
        score_mse = float(
            np.mean((predictions[:, : len(MODEL_IDS)] - actual_scores) ** 2)
        )
        candidates[str(alpha)] = {
            "weighted_score": weighted,
            "score_mse": score_mse,
            "tier_safety_ratios": safety,
            "tiers": tiers,
        }
        rank = (weighted, -score_mse, -alpha)
        if best is None or rank > best[0]:
            best = (rank, alpha, safety, tiers, weighted)
    assert best is not None
    _, alpha, oof_safety, oof_tiers, oof_weighted = best

    mean, scale, intercept, coefficients = base._fit(matrix, targets, alpha)
    fitted = base._predict(matrix, mean, scale, intercept, coefficients)
    fitted_tiers, fitted_weighted = _evaluate_fixed(
        fitted,
        actual_scores,
        actual_costs,
        fold_ids,
        policy,
        oof_safety,
    )
    safety = oof_safety
    summary = {
        "num_episodes": len(inputs.episodes),
        "folds": args.folds,
        "fold_strategy": "normalized-template-fnv1a64",
        "ridge_alpha": alpha,
        "hash_bins": args.hash_bins,
        "input_sha256": _sha256(args.input),
        "outcomes_sha256": _sha256(args.outcomes),
        "budget_ratio_targets": args.targets,
        "budget_calibration": "pooled-and-worst-template-fold",
        "selector_bisection_steps": BISECTION_STEPS,
    }
    artifact = base._artifact(
        policy=policy,
        hash_bins=args.hash_bins,
        mean=mean,
        scale=scale,
        intercept=intercept,
        coefficients=coefficients,
        safety_ratios=safety,
        summary=summary,
    )
    competition.parse_artifact(artifact)
    report = {
        "report_type": "ossp-risk-router-v4-training-v1",
        "training_summary": summary,
        "selected_alpha": alpha,
        "oof_weighted_score": oof_weighted,
        "oof_tiers": oof_tiers,
        "fitted_weighted_score": fitted_weighted,
        "fitted_tiers": fitted_tiers,
        "alpha_candidates": candidates,
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
    parser.add_argument(
        "--alphas", type=base._float_list, default=base._float_list("10000")
    )
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
        "OK: risk router v4 artifact를 생성했습니다 "
        f"(OOF={report['oof_weighted_score']:.6f}, "
        f"fitted={report['fitted_weighted_score']:.6f})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
