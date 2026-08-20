# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Compare absolute and Light-relative Ridge cost targets without deployment changes."""

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


REPORT_TYPE = "ossp-relative-cost-target-study-v1"
DEFAULT_ALPHAS = (100.0, 300.0, 1_000.0, 3_000.0, 10_000.0, 30_000.0)


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("relative cost target 평가에는 NumPy가 필요합니다.")


def relative_targets(targets: Any) -> Any:
    """Replace strong-model absolute log-costs with log cost ratios to Light."""

    _require_numpy()
    result = targets.copy()
    count = len(MODEL_IDS)
    light = targets[:, count]
    result[:, count + 1] = targets[:, count + 1] - light
    result[:, count + 2] = targets[:, count + 2] - light
    return result


def reconstruct_costs(predictions: Any) -> Any:
    """Reconstruct positive monotonic costs from relative-target predictions."""

    _require_numpy()
    count = len(MODEL_IDS)
    light = np.exp(np.clip(predictions[:, count], -50.0, 50.0))
    ax31 = light * np.exp(np.clip(predictions[:, count + 1], 0.0, 50.0))
    k1 = light * np.exp(np.clip(predictions[:, count + 2], 0.0, 50.0))
    k1 = np.maximum(k1, ax31 * (1.0 + 1e-12))
    return np.column_stack((light, ax31, k1))


def _route(
    indices: Sequence[int],
    score_rows: Sequence[Mapping[str, float]],
    predicted_costs: Any,
    actual_scores: Any,
    actual_costs: Any,
    tier: str,
    score_artifact: Any,
    policy: Any,
) -> Mapping[str, Any]:
    scores = [dict(score_rows[index]) for index in indices]
    costs = [
        {
            model_id: float(predicted_costs[index, model_index])
            for model_index, model_id in enumerate(MODEL_IDS)
        }
        for index in indices
    ]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, predicted_ratio = aggressive_v5._select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=score_artifact.base.tiers[tier].safety_ratio,
        steps=score_artifact.base.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, predicted_ratio = aggressive_v5._fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=score_artifact.base.premium_fill_safety_ratio,
            steps=score_artifact.base.fixed_bisection_steps,
        )
    selected_indices = np.asarray([MODEL_IDS.index(model_id) for model_id in selected])
    rows = np.asarray(indices)
    quality = float(actual_scores[rows, selected_indices].mean())
    actual_ratio = float(
        actual_costs[rows, selected_indices].sum() / actual_costs[rows, 0].sum()
    )
    limit = float(policy.tiers[tier].budget_multiplier)
    return {
        "quality_score": quality,
        "tier_score": quality if actual_ratio <= limit else 0.0,
        "budget_ratio": actual_ratio,
        "budget_limit": limit,
        "budget_passed": actual_ratio <= limit,
        "predicted_budget_ratio": predicted_ratio,
        "model_counts": {
            model_id: selected.count(model_id) for model_id in MODEL_IDS
        },
    }


def _candidate_report(
    predicted_costs: Any,
    score_rows: Mapping[str, Sequence[Mapping[str, float]]],
    actual_scores: Any,
    actual_costs: Any,
    groups: Mapping[str, Sequence[int]],
    score_artifact: Any,
    policy: Any,
) -> Mapping[str, Any]:
    all_indices = groups["all"]
    tiers = {
        tier: _route(
            all_indices,
            score_rows[tier],
            predicted_costs,
            actual_scores,
            actual_costs,
            tier,
            score_artifact,
            policy,
        )
        for tier in TIERS
    }
    standalone = {}
    failures = []
    for name, indices in groups.items():
        if name == "all":
            continue
        rows = {}
        for tier in TIERS:
            rows[tier] = _route(
                indices,
                score_rows[tier],
                predicted_costs,
                actual_scores,
                actual_costs,
                tier,
                score_artifact,
                policy,
            )
            if not rows[tier]["budget_passed"]:
                failures.append({"group": name, "tier": tier})
        standalone[name] = rows
    final_score = math.fsum(
        float(policy.tiers[tier].weight) * tiers[tier]["tier_score"]
        for tier in TIERS
    )
    return {
        "final_score": final_score,
        "all_split_tiers_passed": all(row["budget_passed"] for row in tiers.values()),
        "standalone_budget_failures": failures,
        "tiers": tiers,
        "standalone": standalone,
    }


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
    transformed = relative_targets(train_targets)
    score_artifact = aggressive_v6.compile_artifact(
        aggressive_v5.load_artifact(args.score_artifact)
    )
    score_rows = {
        tier: tuple(
            aggressive_v6.predict_episode(episode, score_artifact, tier)[0]
            for episode in eval_inputs.episodes
        )
        for tier in TIERS
    }
    groups = content_groups(eval_inputs.episodes)
    actual_scores = eval_targets[:, : len(MODEL_IDS)]
    actual_costs = np.exp(eval_targets[:, len(MODEL_IDS) :])
    candidates: Dict[str, Mapping[str, Any]] = {}
    for alpha in args.alphas:
        mean, scale, intercept, coefficients = base._fit(
            train_matrix, transformed, alpha
        )
        predictions = base._predict(
            eval_matrix, mean, scale, intercept, coefficients
        )
        candidates[f"{alpha:g}"] = _candidate_report(
            reconstruct_costs(predictions),
            score_rows,
            actual_scores,
            actual_costs,
            groups,
            score_artifact,
            policy,
        )
    best_score = max(
        candidates,
        key=lambda name: (
            candidates[name]["final_score"],
            -len(candidates[name]["standalone_budget_failures"]),
        ),
    )
    safe = [
        name
        for name, report in candidates.items()
        if report["all_split_tiers_passed"]
        and not report["standalone_budget_failures"]
    ]
    best_safe = max(safe, key=lambda name: candidates[name]["final_score"]) if safe else None
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "protocol": "train-fit-to-dev-report-no-dev-selection",
        "train": str(args.train_input),
        "evaluated_on": str(args.eval_input),
        "score_artifact": str(args.score_artifact),
        "cost_target": "log-light-cost-plus-log-strong-over-light-ratios",
        "hash_bins": args.hash_bins,
        "alphas": list(args.alphas),
        "groups": {name: len(indices) for name, indices in groups.items()},
        "best_score_alpha": best_score,
        "best_standalone_safe_alpha": best_safe,
        "candidates": candidates,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-input", type=Path, required=True)
    parser.add_argument("--train-outcomes", type=Path, required=True)
    parser.add_argument("--eval-input", type=Path, required=True)
    parser.add_argument("--eval-outcomes", type=Path, required=True)
    parser.add_argument("--score-artifact", type=Path, required=True)
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
        "OK: relative cost target study를 생성했습니다 "
        f"(best_score_alpha={report['best_score_alpha']}, "
        f"best_safe_alpha={report['best_standalone_safe_alpha']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
