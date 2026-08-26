# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Compare V9 with template-OOF binomial logistic score heads.

Each public score is k/n successful generations.  The study fits one
weighted Bernoulli logistic head per model, blends its probability with V9's
Ridge score, selects tier-specific blend parameters on Train only, and then
evaluates the frozen combination once on Dev.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

try:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
except ImportError:  # pragma: no cover
    np = None
    LogisticRegression = None

import evaluate_selective_budget_v9 as v9
import train_competition_router as base
from evaluate_adaptive_safety_v7 import _int_list, signal_rows
from evaluate_router_robustness import PreparedEvaluation, prepare_evaluation
from ossp_router import aggressive_v6, aggressive_v7
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
)


REPORT_TYPE = "ossp-binomial-score-study-v1"
DEFAULT_CS = (0.0003, 0.001, 0.003, 0.01, 0.03)
DEFAULT_BLEND_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
V9_CANDIDATE = v9.Candidate(30_000.0, 0.5, 0.1, 0.75)


def _require_dependencies() -> None:
    if np is None or LogisticRegression is None:
        raise RuntimeError("V10 평가에는 NumPy와 scikit-learn이 필요합니다.")


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


def _blend_weight_list(value: str) -> Tuple[float, ...]:
    result = tuple(float(item) for item in value.split(","))
    if not result or any(
        not math.isfinite(item) or not 0.0 <= item <= 1.0 for item in result
    ):
        raise argparse.ArgumentTypeError("0~1 score blend 목록이 필요합니다.")
    return result


def generation_counts(inputs: Any, outcomes: Any) -> Tuple[Any, Any]:
    """Return integer successes and trials in input/model matrix order."""

    _require_dependencies()
    index = {(row.episode_id, row.model_id): row for row in outcomes.outcomes}
    successes = np.empty((len(inputs.episodes), len(MODEL_IDS)), dtype=np.int64)
    trials = np.empty_like(successes)
    for row_index, episode in enumerate(inputs.episodes):
        for model_index, model_id in enumerate(MODEL_IDS):
            outcome = index[(episode.episode_id, model_id)]
            n = int(outcome.num_generations)
            k = int(round(float(outcome.score) * n))
            if n < 1 or not 0 <= k <= n or abs(float(outcome.score) * n - k) > 1e-9:
                raise ValueError("score가 성공 횟수 k/n으로 복원되지 않습니다.")
            successes[row_index, model_index] = k
            trials[row_index, model_index] = n
    return successes, trials


def _fit_predict_one(
    training_matrix: Any,
    successes: Any,
    trials: Any,
    evaluation_matrix: Any,
    c_value: float,
) -> Any:
    mean = training_matrix.mean(axis=0)
    scale = training_matrix.std(axis=0)
    scale = np.where(scale > 1e-8, scale, 1.0)
    standardized = (training_matrix - mean) / scale
    evaluation = (evaluation_matrix - mean) / scale
    expanded_matrix = np.concatenate((standardized, standardized), axis=0)
    labels = np.concatenate((np.ones(len(standardized)), np.zeros(len(standardized))))
    weights = np.concatenate((successes, trials - successes)).astype(np.float64)
    keep = weights > 0.0
    model = LogisticRegression(
        C=c_value,
        solver="liblinear",
        intercept_scaling=100.0,
        max_iter=2_000,
        tol=1e-7,
    )
    model.fit(expanded_matrix[keep], labels[keep], sample_weight=weights[keep])
    return model.predict_proba(evaluation)[:, 1]


def oof_binomial_predictions(
    matrix: Any,
    successes: Any,
    trials: Any,
    fold_ids: Any,
    c_values: Iterable[float],
) -> Mapping[float, Any]:
    _require_dependencies()
    result = {
        c_value: np.empty((len(matrix), len(MODEL_IDS)), dtype=np.float64)
        for c_value in sorted(set(c_values))
    }
    for fold in sorted(int(value) for value in np.unique(fold_ids)):
        validation = fold_ids == fold
        training = ~validation
        if not training.any() or not validation.any():
            raise ValueError("V10 template-group fold가 비어 있습니다.")
        for c_value in result:
            for model_index in range(len(MODEL_IDS)):
                result[c_value][validation, model_index] = _fit_predict_one(
                    matrix[training],
                    successes[training, model_index],
                    trials[training, model_index],
                    matrix[validation],
                    c_value,
                )
    return result


def fit_eval_binomial_predictions(
    training_matrix: Any,
    successes: Any,
    trials: Any,
    evaluation_matrix: Any,
    c_values: Iterable[float],
) -> Mapping[float, Any]:
    _require_dependencies()
    result = {}
    for c_value in sorted(set(c_values)):
        columns = [
            _fit_predict_one(
                training_matrix,
                successes[:, model_index],
                trials[:, model_index],
                evaluation_matrix,
                c_value,
            )
            for model_index in range(len(MODEL_IDS))
        ]
        result[c_value] = np.column_stack(columns)
    return result


def blend_score_rows(
    ridge_rows: Sequence[Mapping[str, float]],
    binomial: Any,
    weight: float,
) -> Tuple[Mapping[str, float], ...]:
    if not 0.0 <= weight <= 1.0 or len(ridge_rows) != len(binomial):
        raise ValueError("score blend 입력이 올바르지 않습니다.")
    return tuple(
        {
            model_id: (
                (1.0 - weight) * ridge_rows[row_index][model_id]
                + weight * float(binomial[row_index, model_index])
            )
            for model_index, model_id in enumerate(MODEL_IDS)
        }
        for row_index in range(len(ridge_rows))
    )


def _with_tier_blend(
    data: v9.StudyData,
    tier: str,
    binomial: Any,
    weight: float,
) -> v9.StudyData:
    predicted_scores = dict(data.prepared.predicted_scores)
    predicted_scores[tier] = blend_score_rows(predicted_scores[tier], binomial, weight)
    prepared = PreparedEvaluation(
        data.prepared.actual_scores,
        data.prepared.actual_costs,
        predicted_scores,
        data.prepared.predicted_costs,
    )
    return replace(data, prepared=prepared)


def _with_selected_blends(
    data: v9.StudyData,
    predictions: Mapping[float, Any],
    selected: Mapping[str, Mapping[str, float]],
) -> v9.StudyData:
    predicted_scores = dict(data.prepared.predicted_scores)
    for tier in TIERS:
        row = selected[tier]
        predicted_scores[tier] = blend_score_rows(
            predicted_scores[tier], predictions[row["c"]], row["weight"]
        )
    prepared = PreparedEvaluation(
        data.prepared.actual_scores,
        data.prepared.actual_costs,
        predicted_scores,
        data.prepared.predicted_costs,
    )
    return replace(data, prepared=prepared)


def _tier_stress_report(
    scenarios: Mapping[str, Sequence[Sequence[int]]],
    data: v9.StudyData,
    artifact: Any,
    profile: Any,
    policy: Any,
    tier: str,
) -> Mapping[str, Any]:
    cache: Dict[Any, Any] = {}
    categories = {}
    for category, samples in scenarios.items():
        reports = [
            v9._route(
                indices,
                data,
                artifact,
                profile,
                policy,
                tier,
                V9_CANDIDATE,
                cache,
            )
            for indices in samples
        ]
        categories[category] = {
            "scenarios": len(reports),
            "budget_failures": sum(not row["budget_passed"] for row in reports),
            "quality_mean": math.fsum(row["quality_score"] for row in reports)
            / len(reports),
            "tier_score_mean": math.fsum(row["tier_score"] for row in reports)
            / len(reports),
            "budget_ratio_max": max(row["budget_ratio"] for row in reports),
        }
    stress = [row for name, row in categories.items() if name != "whole"]
    return {
        "budget_failures": sum(row["budget_failures"] for row in stress),
        "stress_quality_mean": math.fsum(
            row["quality_mean"] * row["scenarios"] for row in stress
        )
        / sum(row["scenarios"] for row in stress),
        "whole_quality": categories["whole"]["quality_mean"],
        "whole_budget_ratio": categories["whole"]["budget_ratio_max"],
        "categories": categories,
    }


def _prepare_data(
    inputs: Any,
    outcomes: Any,
    artifact: Any,
    uplift_predictions: Mapping[float, Any],
) -> v9.StudyData:
    return v9.StudyData(
        inputs,
        prepare_evaluation(inputs, outcomes, artifact),
        signal_rows(inputs.episodes),
        uplift_predictions,
    )


def _whole_candidate(
    data: v9.StudyData,
    artifact: Any,
    profile: Any,
    policy: Any,
    tier: str,
) -> Mapping[str, Any]:
    row = v9._route(
        tuple(range(len(data.inputs.episodes))),
        data,
        artifact,
        profile,
        policy,
        tier,
        V9_CANDIDATE,
        {},
    )
    return {
        "quality": row["quality_score"],
        "budget_ratio": row["budget_ratio"],
        "budget_passed": row["budget_passed"],
    }


def evaluate(args: argparse.Namespace) -> Mapping[str, Any]:
    _require_dependencies()
    policy = load_bundled_policy()
    artifact = aggressive_v6.load_artifact(args.base_artifact)
    profile = aggressive_v7.load_profile(args.profile)
    train_inputs = load_input(args.train_input)
    train_outcomes = load_outcomes(args.train_outcomes)
    eval_inputs = load_input(args.eval_input)
    eval_outcomes = load_outcomes(args.eval_outcomes)
    train_matrix, train_targets, _ = base._matrix(
        train_inputs, train_outcomes, policy, args.hash_bins
    )
    eval_matrix, _, _ = base._matrix(eval_inputs, eval_outcomes, policy, args.hash_bins)
    successes, trials = generation_counts(train_inputs, train_outcomes)
    fold_ids = v9.template_fold_ids(train_inputs, args.folds)
    train_binomial = oof_binomial_predictions(
        train_matrix, successes, trials, fold_ids, args.c_values
    )
    eval_binomial = fit_eval_binomial_predictions(
        train_matrix, successes, trials, eval_matrix, args.c_values
    )
    uplift_targets = v9.uplift_targets(train_targets[:, : len(MODEL_IDS)])
    train_uplift = v9._oof_uplift_predictions(
        train_matrix, uplift_targets, fold_ids, (V9_CANDIDATE.alpha,)
    )
    eval_uplift = v9._fit_eval_uplift_predictions(
        train_matrix,
        uplift_targets,
        eval_matrix,
        (V9_CANDIDATE.alpha,),
    )
    train_data = _prepare_data(train_inputs, train_outcomes, artifact, train_uplift)
    eval_data = _prepare_data(eval_inputs, eval_outcomes, artifact, eval_uplift)
    train_scenarios, train_sources = v9.study_scenarios(
        train_inputs,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
        deepmind_ids=v9.load_selection_ids(args.deepmind_selection, "train"),
        aime_ids=v9.load_selection_ids(args.train_aime_selection, "train"),
    )
    whole_candidates: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    finalist_keys: Dict[str, Tuple[str, ...]] = {}
    for tier in TIERS:
        rows = {}
        for c_value in args.c_values:
            for weight in args.blend_weights:
                name = f"c{c_value:g}-w{weight:g}"
                blended = _with_tier_blend(
                    train_data, tier, train_binomial[c_value], weight
                )
                rows[name] = {
                    "c": c_value,
                    "weight": weight,
                    **_whole_candidate(blended, artifact, profile, policy, tier),
                }
        whole_candidates[tier] = rows
        ranked = sorted(
            rows,
            key=lambda name: (
                rows[name]["budget_passed"],
                rows[name]["quality"],
                -rows[name]["budget_ratio"],
                name,
            ),
            reverse=True,
        )
        baseline_names = [name for name in rows if rows[name]["weight"] == 0.0]
        finalist_keys[tier] = tuple(
            dict.fromkeys(ranked[: args.stress_finalists] + baseline_names[:1])
        )

    stress_candidates: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    selected: Dict[str, Mapping[str, float]] = {}
    for tier in TIERS:
        rows = {}
        for name in finalist_keys[tier]:
            candidate = whole_candidates[tier][name]
            blended = _with_tier_blend(
                train_data,
                tier,
                train_binomial[candidate["c"]],
                candidate["weight"],
            )
            rows[name] = {
                **candidate,
                **_tier_stress_report(
                    train_scenarios,
                    blended,
                    artifact,
                    profile,
                    policy,
                    tier,
                ),
            }
        stress_candidates[tier] = rows
        safe = [name for name, row in rows.items() if row["budget_failures"] == 0]
        pool = safe or list(rows)
        winner = max(
            pool,
            key=lambda name: (
                -rows[name]["budget_failures"],
                rows[name]["whole_quality"],
                rows[name]["stress_quality_mean"],
                name,
            ),
        )
        selected[tier] = {
            "name": winner,
            "c": rows[winner]["c"],
            "weight": rows[winner]["weight"],
        }

    selected_train_data = _with_selected_blends(train_data, train_binomial, selected)
    baseline_train = v9._score_scenarios(
        train_scenarios, train_data, artifact, profile, policy, V9_CANDIDATE, {}
    )
    selected_train = v9._score_scenarios(
        train_scenarios,
        selected_train_data,
        artifact,
        profile,
        policy,
        V9_CANDIDATE,
        {},
    )
    eval_scenarios, eval_sources = v9.study_scenarios(
        eval_inputs,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
        deepmind_ids=v9.load_selection_ids(args.deepmind_selection, "dev"),
        aime_ids=v9.load_selection_ids(args.eval_aime_selection, "dev"),
    )
    selected_eval_data = _with_selected_blends(eval_data, eval_binomial, selected)
    baseline_eval = v9._score_scenarios(
        eval_scenarios, eval_data, artifact, profile, policy, V9_CANDIDATE, {}
    )
    selected_eval = v9._score_scenarios(
        eval_scenarios,
        selected_eval_data,
        artifact,
        profile,
        policy,
        V9_CANDIDATE,
        {},
    )
    baseline_score = baseline_eval["whole"]["final_score_mean"]
    selected_score = selected_eval["whole"]["final_score_mean"]
    promoted = (
        selected_train["tier_budget_failures"] == 0
        and selected_eval["tier_budget_failures"] == 0
        and selected_score >= baseline_score + args.minimum_promotion_gain
    )
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "protocol": "train-template-oof-tier-select-then-fixed-dev",
        "selected": selected,
        "promotion_decision": "promote-v10" if promoted else "retain-v9",
        "minimum_promotion_gain": args.minimum_promotion_gain,
        "source_groups": {"train": train_sources, "dev": eval_sources},
        "whole_candidates": whole_candidates,
        "stress_candidates": stress_candidates,
        "train": {"baseline_v9": baseline_train, "selected": selected_train},
        "dev": {"baseline_v9": baseline_eval, "selected": selected_eval},
        "inputs": {
            "train_sha256": _sha256(args.train_input),
            "train_outcomes_sha256": _sha256(args.train_outcomes),
            "eval_sha256": _sha256(args.eval_input),
            "eval_outcomes_sha256": _sha256(args.eval_outcomes),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-input", type=Path, required=True)
    parser.add_argument("--train-outcomes", type=Path, required=True)
    parser.add_argument("--eval-input", type=Path, required=True)
    parser.add_argument("--eval-outcomes", type=Path, required=True)
    parser.add_argument("--base-artifact", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--deepmind-selection", type=Path)
    parser.add_argument("--train-aime-selection", type=Path)
    parser.add_argument("--eval-aime-selection", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hash-bins", type=int, default=512)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--c-values", type=base._float_list, default=DEFAULT_CS)
    parser.add_argument(
        "--blend-weights", type=_blend_weight_list, default=DEFAULT_BLEND_WEIGHTS
    )
    parser.add_argument("--stress-finalists", type=int, default=8)
    parser.add_argument("--minimum-promotion-gain", type=float, default=0.001)
    parser.add_argument("--bootstrap-sizes", type=_int_list, default=(100, 300, 880))
    parser.add_argument("--bootstrap-trials", type=int, default=10)
    parser.add_argument("--mixture-size", type=int, default=300)
    parser.add_argument("--mixture-trials", type=int, default=5)
    parser.add_argument(
        "--mixture-proportions",
        type=base._float_list,
        default=(0.25, 0.5, 0.75, 1.0),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate(args)
        _write(args.report, report)
    except (OSError, ProtocolError, RuntimeError, ValueError, KeyError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    baseline = report["dev"]["baseline_v9"]["whole"]["final_score_mean"]
    selected = report["dev"]["selected"]["whole"]["final_score_mean"]
    print(
        "OK: V10 binomial score study를 생성했습니다 "
        f"(v9={baseline:.6f}, candidate={selected:.6f}, "
        f"decision={report['promotion_decision']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
