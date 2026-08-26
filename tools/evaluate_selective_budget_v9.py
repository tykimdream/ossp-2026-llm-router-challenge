# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Evaluate selective recovery of V7's reserved budget.

The V7 route remains the baseline.  A second pass may only upgrade its choices
when a template-group out-of-fold uplift head predicts a sufficiently large
quality gain.  Candidate hyperparameters are selected on Train stress only;
the frozen winner is then reported once on Dev.
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
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

import train_competition_router as base
from evaluate_adaptive_safety_v7 import _int_list, _scenarios, signal_rows
from evaluate_router_robustness import _mixture_indices, prepare_evaluation
from ossp_router import aggressive_v5, aggressive_v6, aggressive_v7, competition
from ossp_router.heuristic import episode_text
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
)


REPORT_TYPE = "ossp-selective-budget-study-v1"
DEFAULT_ALPHAS = (3_000.0, 10_000.0, 30_000.0)
DEFAULT_BLEND_WEIGHTS = (0.5, 0.75, 1.0)
DEFAULT_THRESHOLDS = (0.1, 0.2, 0.3)
DEFAULT_RECOVERY_FRACTIONS = (0.25, 0.5, 0.75)


@dataclass(frozen=True)
class Candidate:
    alpha: float
    blend_weight: float
    threshold: float
    recovery_fraction: float

    @property
    def name(self) -> str:
        return (
            f"a{self.alpha:g}-w{self.blend_weight:g}"
            f"-t{self.threshold:g}-r{self.recovery_fraction:g}"
        )


@dataclass(frozen=True)
class StudyData:
    inputs: Any
    prepared: Any
    signal_rows: Tuple[Tuple[bool, ...], ...]
    uplift_predictions: Mapping[float, Any]


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("V9 평가에는 NumPy가 필요합니다.")


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def template_fold_ids(inputs: Any, folds: int) -> Any:
    """Assign normalized prompt templates to deterministic non-leaking folds."""

    _require_numpy()
    if folds < 2:
        raise ValueError("template fold 수는 2 이상이어야 합니다.")
    values = []
    for episode in inputs.episodes:
        template = competition.normalized_template(episode_text(episode))
        value = competition.stable_hash(template)
        value ^= value >> 30
        value = (value * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
        value ^= value >> 27
        values.append(value % folds)
    return np.asarray(values, dtype=np.int64)


def load_selection_ids(path: Optional[Path], split: str) -> Tuple[str, ...]:
    if path is None or not path.is_file():
        return ()
    root = json.loads(path.read_text(encoding="utf-8"))
    if "splits" in root:
        rows = root["splits"].get(split, [])
    else:
        rows = root.get("episodes", [])
    return tuple(row["episode_id"] for row in rows)


def study_scenarios(
    inputs: Any,
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
    scenarios = dict(
        _scenarios(
            inputs.episodes,
            seed=seed,
            bootstrap_sizes=bootstrap_sizes,
            bootstrap_trials=bootstrap_trials,
            mixture_size=mixture_size,
            mixture_trials=mixture_trials,
            mixture_proportions=mixture_proportions,
        )
    )
    id_to_index = {
        episode.episode_id: index for index, episode in enumerate(inputs.episodes)
    }
    source_groups = {
        "deepmind_math": tuple(
            id_to_index[episode_id]
            for episode_id in deepmind_ids
            if episode_id in id_to_index
        ),
        "aime": tuple(
            id_to_index[episode_id]
            for episode_id in aime_ids
            if episode_id in id_to_index
        ),
    }
    source_groups = {
        name: indices for name, indices in source_groups.items() if indices
    }
    scenarios["standalone"] = scenarios["standalone"] + tuple(source_groups.values())
    population = tuple(range(len(inputs.episodes)))
    population_set = set(population)
    rng = random.Random(seed ^ 0x5A17CE)
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
    scenarios["mixtures"] = scenarios["mixtures"] + source_mixtures
    return scenarios, {name: len(indices) for name, indices in source_groups.items()}


def uplift_targets(scores: Any) -> Any:
    _require_numpy()
    return scores[:, 1:] - scores[:, :1]


def _oof_uplift_predictions(
    matrix: Any,
    targets: Any,
    fold_ids: Any,
    alphas: Iterable[float],
) -> Mapping[float, Any]:
    result = {
        alpha: np.empty((matrix.shape[0], len(MODEL_IDS) - 1), dtype=np.float64)
        for alpha in sorted(set(alphas))
    }
    for fold in sorted(int(value) for value in np.unique(fold_ids)):
        validation = fold_ids == fold
        training = ~validation
        if not validation.any() or not training.any():
            raise ValueError("V9 template-group fold가 비어 있습니다.")
        for alpha in result:
            mean, scale, intercept, coefficients = base._fit(
                matrix[training], targets[training], alpha
            )
            result[alpha][validation] = base._predict(
                matrix[validation], mean, scale, intercept, coefficients
            )
    return result


def _fit_eval_uplift_predictions(
    train_matrix: Any,
    train_targets: Any,
    eval_matrix: Any,
    alphas: Iterable[float],
) -> Mapping[float, Any]:
    result = {}
    for alpha in sorted(set(alphas)):
        mean, scale, intercept, coefficients = base._fit(
            train_matrix, train_targets, alpha
        )
        result[alpha] = base._predict(eval_matrix, mean, scale, intercept, coefficients)
    return result


def _base_route(
    indices: Sequence[int],
    data: StudyData,
    artifact: Any,
    profile: Any,
    policy: Any,
    tier: str,
) -> Tuple[Tuple[str, ...], Sequence[Mapping[str, float]], float, float]:
    scores = [dict(data.prepared.predicted_scores[tier][index]) for index in indices]
    costs = [data.prepared.predicted_costs[tier][index] for index in indices]
    multiplier = aggressive_v7.safety_multiplier(
        [data.signal_rows[index] for index in indices], profile
    )
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, ratio = aggressive_v5._select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=artifact.base.tiers[tier].safety_ratio * multiplier,
        steps=artifact.base.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, ratio = aggressive_v5._fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=artifact.base.premium_fill_safety_ratio * multiplier,
            steps=artifact.base.fixed_bisection_steps,
        )
    return selected, costs, ratio, multiplier


def selective_upgrades(
    selected: Sequence[str],
    scores: Sequence[Mapping[str, float]],
    costs: Sequence[Mapping[str, float]],
    uplift_predictions: Any,
    row_indices: Sequence[int],
    *,
    tier: str,
    budget_multiplier: float,
    base_safety_ratio: float,
    safety_multiplier: float,
    candidate: Candidate,
) -> Tuple[Tuple[str, ...], float, int]:
    """Upgrade V7 decisions within a partially recovered predicted-cost cap."""

    if not selected or len(selected) != len(costs) or len(selected) != len(row_indices):
        raise ValueError("V9 선택 배열의 길이가 올바르지 않습니다.")
    if not 0.0 <= candidate.recovery_fraction <= 1.0:
        raise ValueError("recovery fraction은 0~1 사이여야 합니다.")
    light_id, ax31_id, k1_id = MODEL_IDS
    light_total = math.fsum(row[light_id] for row in costs)
    current_total = math.fsum(row[model_id] for row, model_id in zip(costs, selected))
    recovered_safety_ratio = base_safety_ratio + candidate.recovery_fraction * (
        1.0 - base_safety_ratio
    )
    recovered_cap_ratio = max(
        1.0, budget_multiplier * recovered_safety_ratio * safety_multiplier
    )
    cap = max(current_total, light_total * recovered_cap_ratio)
    choices = []
    for local_index, (current, score_row, cost_row, source_index) in enumerate(
        zip(selected, scores, costs, row_indices)
    ):
        if current == light_id:
            target = ax31_id
            direct_gain = float(uplift_predictions[source_index, 0])
        elif current == ax31_id and tier != "fast":
            target = k1_id
            direct_gain = float(
                uplift_predictions[source_index, 1]
                - uplift_predictions[source_index, 0]
            )
        else:
            continue
        baseline_gain = score_row[target] - score_row[current]
        blended_gain = (
            candidate.blend_weight * direct_gain
            + (1.0 - candidate.blend_weight) * baseline_gain
        )
        extra_cost = cost_row[target] - cost_row[current]
        if blended_gain < candidate.threshold or extra_cost <= 0.0:
            continue
        choices.append(
            (
                blended_gain / extra_cost,
                blended_gain,
                -local_index,
                local_index,
                target,
                extra_cost,
            )
        )
    result = list(selected)
    upgrades = 0
    for _utility, _gain, _tie, index, target, extra_cost in sorted(
        choices, reverse=True
    ):
        if current_total + extra_cost <= cap:
            result[index] = target
            current_total += extra_cost
            upgrades += 1
    return tuple(result), current_total / light_total, upgrades


def _route(
    indices: Sequence[int],
    data: StudyData,
    artifact: Any,
    profile: Any,
    policy: Any,
    tier: str,
    candidate: Optional[Candidate],
    base_cache: Dict[
        Tuple[str, Tuple[int, ...]],
        Tuple[Tuple[str, ...], Sequence[Mapping[str, float]], float, float],
    ],
) -> Mapping[str, Any]:
    cache_key = (tier, tuple(indices))
    base_result = base_cache.get(cache_key)
    if base_result is None:
        base_result = _base_route(indices, data, artifact, profile, policy, tier)
        base_cache[cache_key] = base_result
    selected, costs, predicted_ratio, multiplier = base_result
    upgrades = 0
    if candidate is not None:
        scores = [data.prepared.predicted_scores[tier][index] for index in indices]
        selected, predicted_ratio, upgrades = selective_upgrades(
            selected,
            scores,
            costs,
            data.uplift_predictions[candidate.alpha],
            indices,
            tier=tier,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            base_safety_ratio=artifact.base.tiers[tier].safety_ratio,
            safety_multiplier=multiplier,
            candidate=candidate,
        )
    light_total = math.fsum(
        data.prepared.actual_costs[index][MODEL_IDS[0]] for index in indices
    )
    actual_total = math.fsum(
        data.prepared.actual_costs[index][model_id]
        for index, model_id in zip(indices, selected)
    )
    quality = math.fsum(
        data.prepared.actual_scores[index][model_id]
        for index, model_id in zip(indices, selected)
    ) / len(indices)
    ratio = actual_total / light_total
    limit = float(policy.tiers[tier].budget_multiplier)
    passed = ratio <= limit
    return {
        "quality_score": quality,
        "tier_score": quality if passed else 0.0,
        "budget_ratio": ratio,
        "budget_limit": limit,
        "budget_passed": passed,
        "predicted_budget_ratio": predicted_ratio,
        "safety_multiplier": multiplier,
        "upgrades": upgrades,
        "model_counts": {model_id: selected.count(model_id) for model_id in MODEL_IDS},
    }


def _score_scenarios(
    scenarios: Mapping[str, Sequence[Sequence[int]]],
    data: StudyData,
    artifact: Any,
    profile: Any,
    policy: Any,
    candidate: Optional[Candidate],
    base_cache: Dict[
        Tuple[str, Tuple[int, ...]],
        Tuple[Tuple[str, ...], Sequence[Mapping[str, float]], float, float],
    ],
) -> Mapping[str, Any]:
    categories: Dict[str, Mapping[str, Any]] = {}
    for category, samples in scenarios.items():
        reports = []
        for indices in samples:
            tiers = {
                tier: _route(
                    indices,
                    data,
                    artifact,
                    profile,
                    policy,
                    tier,
                    candidate,
                    base_cache,
                )
                for tier in TIERS
            }
            reports.append(
                {
                    "final_score": math.fsum(
                        float(policy.tiers[tier].weight) * tiers[tier]["tier_score"]
                        for tier in TIERS
                    ),
                    "tiers": tiers,
                }
            )
        failures = sum(
            not report["tiers"][tier]["budget_passed"]
            for report in reports
            for tier in TIERS
        )
        categories[category] = {
            "scenarios": len(reports),
            "tier_budget_failures": failures,
            "final_score_mean": math.fsum(row["final_score"] for row in reports)
            / len(reports),
            "tiers": {
                tier: {
                    "budget_failures": sum(
                        not row["tiers"][tier]["budget_passed"] for row in reports
                    ),
                    "budget_ratio_mean": math.fsum(
                        row["tiers"][tier]["budget_ratio"] for row in reports
                    )
                    / len(reports),
                    "budget_ratio_max": max(
                        row["tiers"][tier]["budget_ratio"] for row in reports
                    ),
                    "quality_score_mean": math.fsum(
                        row["tiers"][tier]["quality_score"] for row in reports
                    )
                    / len(reports),
                    "upgrades_mean": math.fsum(
                        row["tiers"][tier]["upgrades"] for row in reports
                    )
                    / len(reports),
                }
                for tier in TIERS
            },
        }
    stress = [row for name, row in categories.items() if name != "whole"]
    return {
        "tier_budget_failures": sum(row["tier_budget_failures"] for row in stress),
        "stress_final_score_mean": math.fsum(
            row["final_score_mean"] * row["scenarios"] for row in stress
        )
        / sum(row["scenarios"] for row in stress),
        "whole": categories["whole"],
        "categories": categories,
    }


def _candidate_grid(args: argparse.Namespace) -> Tuple[Candidate, ...]:
    return tuple(
        Candidate(alpha, weight, threshold, recovery)
        for alpha in args.alphas
        for weight in args.blend_weights
        for threshold in args.thresholds
        for recovery in args.recovery_fractions
    )


def evaluate(args: argparse.Namespace) -> Mapping[str, Any]:
    _require_numpy()
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
    targets = uplift_targets(train_targets[:, : len(MODEL_IDS)])
    fold_ids = template_fold_ids(train_inputs, args.folds)
    train_uplift = _oof_uplift_predictions(train_matrix, targets, fold_ids, args.alphas)
    eval_uplift = _fit_eval_uplift_predictions(
        train_matrix, targets, eval_matrix, args.alphas
    )
    train_data = StudyData(
        train_inputs,
        prepare_evaluation(train_inputs, train_outcomes, artifact),
        signal_rows(train_inputs.episodes),
        train_uplift,
    )
    eval_data = StudyData(
        eval_inputs,
        prepare_evaluation(eval_inputs, eval_outcomes, artifact),
        signal_rows(eval_inputs.episodes),
        eval_uplift,
    )
    train_scenarios, train_source_groups = study_scenarios(
        train_inputs,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
        deepmind_ids=load_selection_ids(args.deepmind_selection, "train"),
        aime_ids=load_selection_ids(args.train_aime_selection, "train"),
    )
    train_base_cache: Dict[
        Tuple[str, Tuple[int, ...]],
        Tuple[Tuple[str, ...], Sequence[Mapping[str, float]], float, float],
    ] = {}
    baseline_train = _score_scenarios(
        train_scenarios,
        train_data,
        artifact,
        profile,
        policy,
        None,
        train_base_cache,
    )
    candidate_summaries = {}
    candidate_objects = {
        candidate.name: candidate for candidate in _candidate_grid(args)
    }
    for name, candidate in candidate_objects.items():
        row = _score_scenarios(
            train_scenarios,
            train_data,
            artifact,
            profile,
            policy,
            candidate,
            train_base_cache,
        )
        candidate_summaries[name] = {
            "tier_budget_failures": row["tier_budget_failures"],
            "stress_final_score_mean": row["stress_final_score_mean"],
            "whole_final_score": row["whole"]["final_score_mean"],
        }
    safe = [
        name
        for name, row in candidate_summaries.items()
        if row["tier_budget_failures"] == 0
    ]
    pool = safe or list(candidate_summaries)
    selected_name = max(
        pool,
        key=lambda name: (
            -candidate_summaries[name]["tier_budget_failures"],
            candidate_summaries[name]["whole_final_score"],
            candidate_summaries[name]["stress_final_score_mean"],
            name,
        ),
    )
    selected = candidate_objects[selected_name]
    selected_train = _score_scenarios(
        train_scenarios,
        train_data,
        artifact,
        profile,
        policy,
        selected,
        train_base_cache,
    )
    eval_scenarios, eval_source_groups = study_scenarios(
        eval_inputs,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
        deepmind_ids=load_selection_ids(args.deepmind_selection, "dev"),
        aime_ids=load_selection_ids(args.eval_aime_selection, "dev"),
    )
    eval_base_cache: Dict[
        Tuple[str, Tuple[int, ...]],
        Tuple[Tuple[str, ...], Sequence[Mapping[str, float]], float, float],
    ] = {}
    baseline_eval = _score_scenarios(
        eval_scenarios,
        eval_data,
        artifact,
        profile,
        policy,
        None,
        eval_base_cache,
    )
    selected_eval = _score_scenarios(
        eval_scenarios,
        eval_data,
        artifact,
        profile,
        policy,
        selected,
        eval_base_cache,
    )
    promoted = (
        selected_eval["tier_budget_failures"] == 0
        and selected_eval["whole"]["final_score_mean"]
        > baseline_eval["whole"]["final_score_mean"]
    )
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "protocol": "train-template-oof-select-then-fixed-dev",
        "selected_candidate": selected_name,
        "safe_train_candidates": len(safe),
        "candidate_count": len(candidate_summaries),
        "source_groups": {
            "train": train_source_groups,
            "dev": eval_source_groups,
        },
        "candidates": candidate_summaries,
        "train": {"baseline": baseline_train, "selected": selected_train},
        "dev": {"baseline": baseline_eval, "selected": selected_eval},
        "promotion_decision": "promote-v9" if promoted else "retain-v7",
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
    parser.add_argument("--alphas", type=base._float_list, default=DEFAULT_ALPHAS)
    parser.add_argument(
        "--blend-weights", type=base._float_list, default=DEFAULT_BLEND_WEIGHTS
    )
    parser.add_argument(
        "--thresholds", type=base._float_list, default=DEFAULT_THRESHOLDS
    )
    parser.add_argument(
        "--recovery-fractions",
        type=base._float_list,
        default=DEFAULT_RECOVERY_FRACTIONS,
    )
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
    except (OSError, ProtocolError, RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: V9 selective budget study를 생성했습니다 "
        f"(selected={report['selected_candidate']}, "
        f"decision={report['promotion_decision']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
