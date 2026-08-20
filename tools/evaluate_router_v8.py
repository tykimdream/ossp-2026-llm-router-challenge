# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Evaluate a cross-fitted score ensemble with quantile cost risk control.

The study selects every hyperparameter on Train nested template-group OOF and
then evaluates the frozen candidate on Dev once.  It does not modify the
submission artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

try:
    import numpy as np
    from sklearn.ensemble import GradientBoostingRegressor
except ImportError:  # pragma: no cover
    np = None
    GradientBoostingRegressor = None

import train_aggressive_router_v5 as v5_train
import train_competition_router as base
from evaluate_adaptive_safety_v7 import _scenarios
from evaluate_router_robustness import (
    _load_episode_ids,
    _mixture_indices,
    content_groups,
)
from ossp_router import aggressive_v5, aggressive_v6, aggressive_v7, submission
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
)
from ossp_router.scoring import score_submissions


REPORT_TYPE = "ossp-router-v8-study-v1"
DEFAULT_QUANTILES = (0.25, 0.5, 0.7, 0.8, 0.9)
DEFAULT_RESIDUAL_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
DEFAULT_RISK_QUANTILES = (0.7, 0.8, 0.9)
DEFAULT_RESERVES = (0.95, 0.975, 1.0)


@dataclass(frozen=True)
class PredictionBundle:
    scores: Mapping[str, Any]
    median_costs: Any
    quantile_costs: Mapping[float, Any]


def _require_training_dependencies() -> None:
    if np is None or GradientBoostingRegressor is None:
        raise RuntimeError("V8 평가에는 NumPy와 scikit-learn이 필요합니다.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _fit_ridge_predictions(
    training_matrix: Any,
    training_targets: Any,
    evaluation_matrix: Any,
    alphas: Iterable[float],
) -> Mapping[float, Any]:
    result = {}
    for alpha in sorted(set(alphas)):
        mean, scale, intercept, coefficients = base._fit(
            training_matrix, training_targets, alpha
        )
        result[alpha] = base._predict(
            evaluation_matrix, mean, scale, intercept, coefficients
        )
    return result


def template_fold_ids(fold_keys: Any, folds: int, salt: int = 0) -> Any:
    """Mix FNV bits before modulo while keeping identical templates together."""

    if folds < 2:
        raise ValueError("template fold 수는 2 이상이어야 합니다.")
    mask = (1 << 64) - 1
    values = []
    for raw in fold_keys:
        value = (int(raw) ^ salt) & mask
        value ^= value >> 30
        value = (value * 0xBF58476D1CE4E5B9) & mask
        value ^= value >> 27
        value = (value * 0x94D049BB133111EB) & mask
        value ^= value >> 31
        values.append(value % folds)
    return np.asarray(values, dtype=np.int64)


def _ridge_oof(
    matrix: Any,
    targets: Any,
    fold_keys: Any,
    folds: int,
    alpha: float,
    *,
    salt: int,
) -> Any:
    predictions = np.empty_like(targets)
    fold_ids = template_fold_ids(fold_keys, folds, salt)
    for fold in range(folds):
        validation = fold_ids == fold
        training = ~validation
        if not validation.any() or not training.any():
            raise ValueError("V8 template-group fold가 비어 있습니다.")
        mean, scale, intercept, coefficients = base._fit(
            matrix[training], targets[training], alpha
        )
        predictions[validation] = base._predict(
            matrix[validation], mean, scale, intercept, coefficients
        )
    return predictions


def _tier_ridge_predictions(predictions: Mapping[float, Any]) -> Mapping[str, Any]:
    return {
        tier: np.mean(
            np.stack([predictions[alpha] for alpha in v5_train.DEFAULT_ENSEMBLES[tier]]),
            axis=0,
        )
        for tier in TIERS
    }


def _gbm(**kwargs: Any) -> Any:
    return GradientBoostingRegressor(
        n_estimators=80,
        learning_rate=0.035,
        max_depth=2,
        min_samples_leaf=24,
        subsample=0.85,
        random_state=20260820,
        **kwargs,
    )


def _fit_residual_heads(
    matrix: Any,
    residuals: Any,
) -> Tuple[Any, ...]:
    heads = []
    for model_index in range(len(MODEL_IDS)):
        head = _gbm(loss="huber")
        head.fit(matrix, residuals[:, model_index])
        heads.append(head)
    return tuple(heads)


def _predict_heads(matrix: Any, heads: Sequence[Any]) -> Any:
    return np.column_stack([head.predict(matrix) for head in heads])


def _fit_quantile_heads(matrix: Any, log_costs: Any, quantile: float) -> Tuple[Any, ...]:
    heads = []
    for model_index in range(len(MODEL_IDS)):
        head = _gbm(loss="quantile", alpha=quantile)
        head.fit(matrix, log_costs[:, model_index])
        heads.append(head)
    return tuple(heads)


def nearest_quantile(values: Any, probability: float) -> float:
    if len(values) < 1 or not 0.0 <= probability <= 1.0:
        raise ValueError("conformal quantile 입력이 올바르지 않습니다.")
    ordered = np.sort(np.asarray(values, dtype=np.float64))
    rank = max(1, math.ceil(probability * (len(ordered) + 1)))
    return float(ordered[min(len(ordered) - 1, rank - 1)])


def _conformal_corrections(
    matrix: Any,
    log_costs: Any,
    fold_keys: Any,
    quantile: float,
    inner_folds: int,
) -> Tuple[float, ...]:
    fold_ids = template_fold_ids(
        fold_keys,
        max(4, inner_folds),
        0x8EBC6AF09C88C6E3,
    )
    calibration = fold_ids == 0
    fitting = ~calibration
    if calibration.sum() < 20 or fitting.sum() < 20:
        raise ValueError("conformal fit/calibration split이 너무 작습니다.")
    heads = _fit_quantile_heads(matrix[fitting], log_costs[fitting], quantile)
    predicted = _predict_heads(matrix[calibration], heads)
    return tuple(
        nearest_quantile(
            log_costs[calibration, model_index] - predicted[:, model_index],
            quantile,
        )
        for model_index in range(len(MODEL_IDS))
    )


def _ordered_costs(values: Any) -> Any:
    result = np.exp(np.clip(values, -50.0, 50.0))
    result[:, 1] = np.maximum(result[:, 1], result[:, 0] * (1.0 + 1e-12))
    result[:, 2] = np.maximum(result[:, 2], result[:, 1] * (1.0 + 1e-12))
    return result


def _fit_bundle(
    training_matrix: Any,
    training_targets: Any,
    training_fold_keys: Any,
    evaluation_matrix: Any,
    *,
    inner_folds: int,
    quantiles: Sequence[float],
) -> Tuple[Mapping[float, PredictionBundle], Mapping[str, Any]]:
    """Fit cross-fitted residual targets, then predict one external split."""

    unique_alphas = tuple(
        sorted({alpha for rows in v5_train.DEFAULT_ENSEMBLES.values() for alpha in rows})
    )
    oof_by_alpha = {
        alpha: _ridge_oof(
            training_matrix,
            training_targets,
            training_fold_keys,
            inner_folds,
            alpha,
            salt=0xA0761D6478BD642F,
        )
        for alpha in unique_alphas
    }
    full_by_alpha = _fit_ridge_predictions(
        training_matrix,
        training_targets,
        evaluation_matrix,
        unique_alphas,
    )
    oof_tiers = _tier_ridge_predictions(oof_by_alpha)
    full_tiers = _tier_ridge_predictions(full_by_alpha)
    score_count = len(MODEL_IDS)
    score_heads = {
        tier: _fit_residual_heads(
            training_matrix,
            training_targets[:, :score_count] - oof_tiers[tier][:, :score_count],
        )
        for tier in TIERS
    }
    residual_predictions = {
        tier: _predict_heads(evaluation_matrix, score_heads[tier])
        for tier in TIERS
    }
    log_costs = training_targets[:, score_count:]
    quantile_heads = {
        quantile: _fit_quantile_heads(training_matrix, log_costs, quantile)
        for quantile in quantiles
    }
    conformal_corrections = {
        quantile: _conformal_corrections(
            training_matrix,
            log_costs,
            training_fold_keys,
            quantile,
            inner_folds,
        )
        for quantile in quantiles
    }
    log_quantile_predictions = {
        quantile: _predict_heads(evaluation_matrix, heads)
        + np.asarray(conformal_corrections[quantile])[None, :]
        for quantile, heads in quantile_heads.items()
    }
    for model_index in range(len(MODEL_IDS)):
        previous = None
        for quantile in sorted(quantiles):
            column = log_quantile_predictions[quantile][:, model_index]
            if previous is not None:
                column = np.maximum(column, previous)
                log_quantile_predictions[quantile][:, model_index] = column
            previous = column
    quantile_predictions = {
        quantile: _ordered_costs(values)
        for quantile, values in log_quantile_predictions.items()
    }
    bundles = {}
    for residual_weight in DEFAULT_RESIDUAL_WEIGHTS:
        scores = {
            tier: np.clip(
                full_tiers[tier][:, :score_count]
                + residual_weight * residual_predictions[tier],
                0.0,
                1.0,
            )
            for tier in TIERS
        }
        bundles[residual_weight] = PredictionBundle(
            scores=scores,
            median_costs=quantile_predictions[0.5],
            quantile_costs=quantile_predictions,
        )
    diagnostics = {
        "residual_train_mae": {
            tier: float(
                np.mean(
                    np.abs(
                        training_targets[:, :score_count]
                        - oof_tiers[tier][:, :score_count]
                    )
                )
            )
            for tier in TIERS
        },
        "conformal_log_cost_corrections": {
            str(quantile): list(values)
            for quantile, values in conformal_corrections.items()
        },
    }
    return bundles, diagnostics


def nested_oof_bundles(
    matrix: Any,
    targets: Any,
    fold_keys: Any,
    *,
    outer_folds: int,
    inner_folds: int,
    quantiles: Sequence[float],
) -> Mapping[float, PredictionBundle]:
    """Produce strict outer-fold predictions with inner-fold residual targets."""

    score_rows = {
        weight: {tier: np.empty((len(matrix), len(MODEL_IDS))) for tier in TIERS}
        for weight in DEFAULT_RESIDUAL_WEIGHTS
    }
    cost_rows = {
        quantile: np.empty((len(matrix), len(MODEL_IDS))) for quantile in quantiles
    }
    outer_ids = template_fold_ids(fold_keys, outer_folds, 0xE7037ED1A0B428DB)
    for outer_fold in range(outer_folds):
        validation = outer_ids == outer_fold
        training = ~validation
        if not validation.any() or not training.any():
            raise ValueError("V8 outer template fold가 비어 있습니다.")
        fold_bundles, _ = _fit_bundle(
            matrix[training],
            targets[training],
            fold_keys[training],
            matrix[validation],
            inner_folds=inner_folds,
            quantiles=quantiles,
        )
        for weight, bundle in fold_bundles.items():
            for tier in TIERS:
                score_rows[weight][tier][validation] = bundle.scores[tier]
        for quantile in quantiles:
            cost_rows[quantile][validation] = fold_bundles[0.0].quantile_costs[quantile]
    return {
        weight: PredictionBundle(
            scores=score_rows[weight],
            median_costs=cost_rows[0.5],
            quantile_costs=cost_rows,
        )
        for weight in DEFAULT_RESIDUAL_WEIGHTS
    }


def _rows(matrix: Any) -> Tuple[Mapping[str, float], ...]:
    return tuple(
        {model_id: float(row[index]) for index, model_id in enumerate(MODEL_IDS)}
        for row in matrix
    )


def _risk_prune(
    selected: Sequence[str],
    score_rows: Sequence[Mapping[str, float]],
    risk_rows: Sequence[Mapping[str, float]],
    baseline_rows: Sequence[Mapping[str, float]],
    *,
    budget_multiplier: float,
    reserve: float,
) -> Tuple[str, ...]:
    """Downgrade the least valuable risk-cost purchases until the bill passes."""

    result = list(selected)
    light_id = MODEL_IDS[0]
    baseline_total = math.fsum(row[light_id] for row in baseline_rows)
    cap = (
        baseline_total * budget_multiplier * reserve
    )

    def total() -> float:
        return baseline_total + math.fsum(
            max(0.0, row[model_id] - row[light_id])
            for row, model_id in zip(risk_rows, result)
        )

    while total() > cap:
        candidates = []
        for row_index, current in enumerate(result):
            current_index = MODEL_IDS.index(current)
            if current_index == 0:
                continue
            for lower_index in range(current_index):
                lower = MODEL_IDS[lower_index]
                current_excess = max(
                    0.0,
                    risk_rows[row_index][current] - risk_rows[row_index][light_id],
                )
                lower_excess = max(
                    0.0,
                    risk_rows[row_index][lower] - risk_rows[row_index][light_id],
                )
                saved = current_excess - lower_excess
                if saved <= 0.0:
                    continue
                loss = max(
                    0.0,
                    score_rows[row_index][current] - score_rows[row_index][lower],
                )
                candidates.append(
                    (loss / saved, loss, -saved, row_index, lower_index, lower)
                )
        if not candidates:
            return tuple(light_id for _ in result)
        _ratio, _loss, _saved, row_index, _lower_index, lower = min(candidates)
        result[row_index] = lower
    return tuple(result)


def route_indices(
    indices: Sequence[int],
    bundle: PredictionBundle,
    actual_scores: Any,
    actual_costs: Any,
    tier: str,
    policy: Any,
    *,
    risk_quantile: float,
    reserve: float,
) -> Mapping[str, Any]:
    score_matrix = bundle.scores[tier][list(indices)]
    median_matrix = bundle.median_costs[list(indices)]
    risk_matrix = bundle.quantile_costs[risk_quantile][list(indices)]
    baseline_matrix = bundle.median_costs[list(indices)]
    score_rows = list(_rows(score_matrix))
    if tier == "fast":
        for row in score_rows:
            row[MODEL_IDS[2]] = -2.0
    median_rows = _rows(median_matrix)
    risk_rows = _rows(risk_matrix)
    baseline_rows = _rows(baseline_matrix)
    selected, predicted_ratio = aggressive_v5._select_models(
        score_rows,
        median_rows,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=1.0,
        steps=aggressive_v5.FIXED_BISECTION_STEPS,
    )
    selected = _risk_prune(
        selected,
        score_rows,
        risk_rows,
        baseline_rows,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        reserve=reserve,
    )
    model_indices = np.asarray([MODEL_IDS.index(model_id) for model_id in selected])
    row_indices = np.asarray(indices)
    quality = float(actual_scores[row_indices, model_indices].mean())
    ratio = float(
        actual_costs[row_indices, model_indices].sum()
        / actual_costs[row_indices, 0].sum()
    )
    limit = float(policy.tiers[tier].budget_multiplier)
    passed = ratio <= limit
    return {
        "quality_score": quality,
        "tier_score": quality if passed else 0.0,
        "budget_ratio": ratio,
        "budget_limit": limit,
        "budget_passed": passed,
        "median_predicted_budget_ratio": predicted_ratio,
        "model_counts": {
            model_id: selected.count(model_id) for model_id in MODEL_IDS
        },
    }


def evaluate_indices(
    indices: Sequence[int],
    bundle: PredictionBundle,
    actual_scores: Any,
    actual_costs: Any,
    policy: Any,
    *,
    risk_quantile: float,
    reserve: float,
) -> Mapping[str, Any]:
    tiers = {
        tier: route_indices(
            indices,
            bundle,
            actual_scores,
            actual_costs,
            tier,
            policy,
            risk_quantile=risk_quantile,
            reserve=reserve,
        )
        for tier in TIERS
    }
    return {
        "num_episodes": len(indices),
        "final_score": math.fsum(
            float(policy.tiers[tier].weight) * tiers[tier]["tier_score"]
            for tier in TIERS
        ),
        "all_tiers_passed": all(row["budget_passed"] for row in tiers.values()),
        "tiers": tiers,
    }


def _candidate_report(
    scenarios: Mapping[str, Sequence[Sequence[int]]],
    bundle: PredictionBundle,
    actual_scores: Any,
    actual_costs: Any,
    policy: Any,
    *,
    risk_quantile: float,
    reserve: float,
) -> Mapping[str, Any]:
    categories = {}
    for name, samples in scenarios.items():
        reports = [
            evaluate_indices(
                indices,
                bundle,
                actual_scores,
                actual_costs,
                policy,
                risk_quantile=risk_quantile,
                reserve=reserve,
            )
            for indices in samples
        ]
        categories[name] = {
            "scenarios": len(reports),
            "tier_budget_failures": sum(
                not row["tiers"][tier]["budget_passed"]
                for row in reports
                for tier in TIERS
            ),
            "final_score_mean": math.fsum(row["final_score"] for row in reports)
            / len(reports),
            "final_score_min": min(row["final_score"] for row in reports),
            "whole": reports[0] if name == "whole" else None,
        }
    stress = [row for name, row in categories.items() if name != "whole"]
    total_stress = sum(row["scenarios"] for row in stress)
    return {
        "tier_budget_failures": sum(row["tier_budget_failures"] for row in stress),
        "stress_final_score_mean": math.fsum(
            row["final_score_mean"] * row["scenarios"] for row in stress
        )
        / total_stress,
        "whole": categories["whole"]["whole"],
        "categories": categories,
    }


def study_scenarios(
    episodes: Sequence[Any],
    *,
    seed: int,
    bootstrap_sizes: Sequence[int],
    bootstrap_trials: int,
    mixture_size: int,
    mixture_trials: int,
    mixture_proportions: Sequence[float],
    deepmind_ids: Iterable[str] = (),
    aime_ids: Iterable[str] = (),
) -> Tuple[Mapping[str, Tuple[Tuple[int, ...], ...]], Mapping[str, int]]:
    """Extend content stress with source-family validation-only groups."""

    scenarios = dict(
        _scenarios(
            episodes,
            seed=seed,
            bootstrap_sizes=bootstrap_sizes,
            bootstrap_trials=bootstrap_trials,
            mixture_size=mixture_size,
            mixture_trials=mixture_trials,
            mixture_proportions=mixture_proportions,
        )
    )
    groups = content_groups(
        episodes,
        deepmind_ids=deepmind_ids,
        aime_ids=aime_ids,
    )
    source_groups = {
        name: groups[name] for name in ("deepmind_math", "aime") if name in groups
    }
    if source_groups:
        scenarios["standalone"] = tuple(scenarios["standalone"]) + tuple(
            source_groups.values()
        )
        population = tuple(range(len(episodes)))
        population_set = set(population)
        rng = random.Random(seed ^ 0xD1B54A32D192ED03)
        source_mixtures = tuple(
            sample
            for group in source_groups.values()
            for proportion in mixture_proportions
            for sample in _mixture_indices(
                group,
                tuple(sorted(population_set - set(group))),
                mixture_size,
                proportion,
                mixture_trials,
                rng,
            )
        )
        scenarios["mixtures"] = tuple(scenarios["mixtures"]) + source_mixtures
    return scenarios, {name: len(indices) for name, indices in source_groups.items()}


def _select_candidate(
    scenarios: Mapping[str, Sequence[Sequence[int]]],
    bundles: Mapping[float, PredictionBundle],
    actual_scores: Any,
    actual_costs: Any,
    policy: Any,
    risk_quantiles: Sequence[float],
    reserves: Sequence[float],
) -> Tuple[str, Mapping[str, Any], Mapping[str, Mapping[str, Any]]]:
    candidates = {}
    for residual_weight, bundle in bundles.items():
        for risk_quantile in risk_quantiles:
            for reserve in reserves:
                name = f"rw{residual_weight:g}-q{risk_quantile:g}-r{reserve:g}"
                candidates[name] = _candidate_report(
                    scenarios,
                    bundle,
                    actual_scores,
                    actual_costs,
                    policy,
                    risk_quantile=risk_quantile,
                    reserve=reserve,
                )
    safe = [name for name, row in candidates.items() if row["tier_budget_failures"] == 0]
    pool = safe or list(candidates)
    selected = max(
        pool,
        key=lambda name: candidate_rank(candidates[name], name),
    )
    return selected, candidates[selected], candidates


def candidate_rank(report: Mapping[str, Any], name: str) -> Tuple[Any, ...]:
    return (
        -report["tier_budget_failures"],
        report["whole"]["final_score"],
        report["stress_final_score_mean"],
        name,
    )


def _parse_candidate(name: str) -> Tuple[float, float, float]:
    pieces = name.split("-")
    return (
        float(pieces[0][2:]),
        float(pieces[1][1:]),
        float(pieces[2][1:]),
    )


def _actual_arrays(targets: Any) -> Tuple[Any, Any]:
    count = len(MODEL_IDS)
    return targets[:, :count], np.exp(targets[:, count:])


def _baseline_report(inputs: Any, outcomes: Any, artifact_path: Path) -> Mapping[str, Any]:
    policy = load_bundled_policy()
    v6 = aggressive_v6.load_artifact(artifact_path)
    v7 = aggressive_v7.V7Artifact(v6, aggressive_v7.load_profile())
    result = {}
    for name, artifact in (("v6.1", v6), ("v7", v7)):
        routed = [
            submission.make_submission(inputs, policy, artifact, tier) for tier in TIERS
        ]
        result[name] = score_submissions(inputs, outcomes, routed, policy)
    v6_score = float(result["v6.1"]["final_score"])
    v7_score = float(result["v7"]["final_score"])
    insurance = v6_score - v7_score
    result["break_even_failure_probability"] = {
        tier: insurance
        / (
            float(policy.tiers[tier].weight)
            * float(result["v6.1"]["tiers"][tier]["tier_score"])
        )
        for tier in TIERS
    }
    return result


def evaluate(args: argparse.Namespace) -> Mapping[str, Any]:
    _require_training_dependencies()
    policy = load_bundled_policy()
    train_inputs = load_input(args.train_input)
    train_outcomes = load_outcomes(args.train_outcomes)
    eval_inputs = load_input(args.eval_input)
    eval_outcomes = load_outcomes(args.eval_outcomes)
    train_matrix, train_targets, train_fold_keys = base._matrix(
        train_inputs, train_outcomes, policy, args.hash_bins
    )
    eval_matrix, eval_targets, _ = base._matrix(
        eval_inputs, eval_outcomes, policy, args.hash_bins
    )
    train_scores, train_costs = _actual_arrays(train_targets)
    eval_scores, eval_costs = _actual_arrays(eval_targets)
    nested_bundles = nested_oof_bundles(
        train_matrix,
        train_targets,
        train_fold_keys,
        outer_folds=args.outer_folds,
        inner_folds=args.inner_folds,
        quantiles=args.quantiles,
    )
    train_scenarios, train_source_groups = study_scenarios(
        train_inputs.episodes,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
        deepmind_ids=_load_episode_ids(args.deepmind_selection, train_inputs.split),
        aime_ids=_load_episode_ids(args.train_aime_selection, train_inputs.split),
    )
    selected, selected_train, candidates = _select_candidate(
        train_scenarios,
        nested_bundles,
        train_scores,
        train_costs,
        policy,
        args.risk_quantiles,
        args.reserves,
    )
    residual_weight, risk_quantile, reserve = _parse_candidate(selected)
    eval_bundles, fit_diagnostics = _fit_bundle(
        train_matrix,
        train_targets,
        train_fold_keys,
        eval_matrix,
        inner_folds=args.inner_folds,
        quantiles=args.quantiles,
    )
    eval_scenarios, eval_source_groups = study_scenarios(
        eval_inputs.episodes,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
        deepmind_ids=_load_episode_ids(args.deepmind_selection, eval_inputs.split),
        aime_ids=_load_episode_ids(args.eval_aime_selection, eval_inputs.split),
    )
    fixed_eval = _candidate_report(
        eval_scenarios,
        eval_bundles[residual_weight],
        eval_scores,
        eval_costs,
        policy,
        risk_quantile=risk_quantile,
        reserve=reserve,
    )
    baselines = _baseline_report(eval_inputs, eval_outcomes, args.base_artifact)
    v7_score = float(baselines["v7"]["final_score"])
    promoted = (
        fixed_eval["tier_budget_failures"] == 0
        and fixed_eval["whole"]["final_score"] > v7_score
    )
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "protocol": "nested-template-train-select-then-fixed-dev-eval",
        "seed": args.seed,
        "hash_bins": args.hash_bins,
        "outer_folds": args.outer_folds,
        "inner_folds": args.inner_folds,
        "quantiles": list(args.quantiles),
        "risk_quantiles": list(args.risk_quantiles),
        "reserves": list(args.reserves),
        "residual_weights": list(DEFAULT_RESIDUAL_WEIGHTS),
        "train": {
            "input_sha256": _sha256(args.train_input),
            "outcomes_sha256": _sha256(args.train_outcomes),
            "selected_candidate": selected,
            "selected_report": selected_train,
            "candidates": candidates,
            "source_groups": train_source_groups,
        },
        "fixed_eval": fixed_eval,
        "fixed_eval_source_groups": eval_source_groups,
        "fit_diagnostics": fit_diagnostics,
        "baselines": baselines,
        "promotion": {
            "eligible": promoted,
            "rule": "fixed Dev score above V7 and zero fixed Dev stress tier failures",
            "decision": "promote-v8" if promoted else "retain-v7",
        },
    }


def _float_list(value: str) -> Tuple[float, ...]:
    values = tuple(float(item) for item in value.split(",") if item)
    if not values:
        raise argparse.ArgumentTypeError("실수를 하나 이상 입력해야 합니다.")
    return values


def _int_list(value: str) -> Tuple[int, ...]:
    values = tuple(int(item) for item in value.split(",") if item)
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("1 이상의 정수를 입력해야 합니다.")
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-input", type=Path, required=True)
    parser.add_argument("--train-outcomes", type=Path, required=True)
    parser.add_argument("--eval-input", type=Path, required=True)
    parser.add_argument("--eval-outcomes", type=Path, required=True)
    parser.add_argument("--base-artifact", type=Path, required=True)
    parser.add_argument("--deepmind-selection", type=Path)
    parser.add_argument("--train-aime-selection", type=Path)
    parser.add_argument("--eval-aime-selection", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hash-bins", type=int, default=512)
    parser.add_argument("--outer-folds", type=int, default=4)
    parser.add_argument("--inner-folds", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--quantiles", type=_float_list, default=DEFAULT_QUANTILES)
    parser.add_argument(
        "--risk-quantiles", type=_float_list, default=DEFAULT_RISK_QUANTILES
    )
    parser.add_argument("--reserves", type=_float_list, default=DEFAULT_RESERVES)
    parser.add_argument("--bootstrap-sizes", type=_int_list, default=(100, 300, 880))
    parser.add_argument("--bootstrap-trials", type=int, default=10)
    parser.add_argument("--mixture-size", type=int, default=300)
    parser.add_argument("--mixture-trials", type=int, default=5)
    parser.add_argument(
        "--mixture-proportions", type=_float_list, default=(0.25, 0.5, 0.75, 1.0)
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not set(args.risk_quantiles) <= set(args.quantiles):
            raise ValueError("risk quantile은 학습 quantile 목록에 포함되어야 합니다.")
        if 0.25 not in args.quantiles or 0.5 not in args.quantiles:
            raise ValueError("quantile 목록에는 0.25와 0.5가 필요합니다.")
        if any(not 0.0 < item < 1.0 for item in args.quantiles):
            raise ValueError("quantile은 0과 1 사이여야 합니다.")
        if any(not 0.0 < item <= 1.0 for item in args.reserves):
            raise ValueError("reserve는 0 초과 1 이하여야 합니다.")
        report = evaluate(args)
        _write(args.report, report)
    except (OSError, ProtocolError, RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: V8 study를 생성했습니다 "
        f"(selected={report['train']['selected_candidate']}, "
        f"decision={report['promotion']['decision']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
