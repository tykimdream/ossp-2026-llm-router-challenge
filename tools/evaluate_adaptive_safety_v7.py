# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Calibrate a content-shift and small-batch safety reserve on Train only."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from evaluate_router_robustness import (
    _bootstrap_indices,
    _mixture_indices,
    content_groups,
    evaluate_indices,
    prepare_evaluation,
)
from ossp_router import aggressive_v6
from ossp_router.heuristic import episode_text, extract_features
from ossp_router.protocol import TIERS, Episode, ProtocolError, load_bundled_policy
from ossp_router.protocol import load_input, load_outcomes


REPORT_TYPE = "ossp-adaptive-safety-study-v1"
SIGNAL_NAMES = (
    "korean_hangul_10pct",
    "code_marker",
    "math_or_numeric",
    "long_context_gt_8000",
    "short_context_le_100",
    "messages",
)
DEFAULT_COMPOSITION_PENALTIES = (0.5, 1.0, 1.5, 2.0)
DEFAULT_SMALL_BATCH_PENALTIES = (0.1, 0.2, 0.3)


@dataclass(frozen=True)
class SafetyProfile:
    reference_rates: Mapping[str, float]
    stable_batch_size: int
    composition_penalty: float
    small_batch_penalty: float


def signal_row(episode: Episode) -> Tuple[bool, ...]:
    features = extract_features(episode)
    return (
        features.hangul_ratio >= 0.1,
        features.code_marker_count > 0,
        features.math_marker_count > 0 or features.numeric_density >= 0.08,
        len(episode_text(episode)) > 8_000,
        len(episode_text(episode)) <= 100,
        episode.messages is not None,
    )


def signal_rows(episodes: Sequence[Episode]) -> Tuple[Tuple[bool, ...], ...]:
    return tuple(signal_row(episode) for episode in episodes)


def reference_rates(rows: Sequence[Sequence[bool]]) -> Mapping[str, float]:
    if not rows:
        raise ValueError("reference signal 행이 비어 있습니다.")
    return {
        name: math.fsum(float(row[index]) for row in rows) / len(rows)
        for index, name in enumerate(SIGNAL_NAMES)
    }


def safety_multiplier(
    indices: Sequence[int],
    rows: Sequence[Sequence[bool]],
    profile: SafetyProfile,
) -> float:
    if not indices:
        raise ValueError("adaptive safety batch가 비어 있습니다.")
    rates = _signal_rates(indices, rows)
    maximum_shift = max(
        abs(rates[name] - profile.reference_rates[name]) for name in SIGNAL_NAMES
    )
    size_shift = max(
        0.0,
        math.sqrt(profile.stable_batch_size / len(indices)) - 1.0,
    )
    return math.exp(
        -profile.composition_penalty * maximum_shift
        -profile.small_batch_penalty * size_shift
    )


def _signal_rates(
    indices: Sequence[int], rows: Sequence[Sequence[bool]]
) -> Mapping[str, float]:
    return {
        name: math.fsum(float(rows[row_index][signal_index]) for row_index in indices)
        / len(indices)
        for signal_index, name in enumerate(SIGNAL_NAMES)
    }


def _scenarios(
    episodes: Sequence[Episode],
    *,
    seed: int,
    bootstrap_sizes: Sequence[int],
    bootstrap_trials: int,
    mixture_size: int,
    mixture_trials: int,
    mixture_proportions: Sequence[float],
) -> Mapping[str, Tuple[Tuple[int, ...], ...]]:
    groups = content_groups(episodes)
    rng = random.Random(seed)
    result: Dict[str, Tuple[Tuple[int, ...], ...]] = {
        "whole": (tuple(range(len(episodes))),),
        "standalone": tuple(
            indices
            for name, indices in groups.items()
            if name in SIGNAL_NAMES
        ),
    }
    population = tuple(range(len(episodes)))
    result["bootstrap"] = tuple(
        sample
        for size in bootstrap_sizes
        for sample in _bootstrap_indices(population, size, bootstrap_trials, rng)
    )
    population_set = set(population)
    result["mixtures"] = tuple(
        sample
        for name, group in groups.items()
        if name in SIGNAL_NAMES
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
    return result


def _score_profile(
    scenarios: Mapping[str, Sequence[Sequence[int]]],
    rows: Sequence[Sequence[bool]],
    prepared: Any,
    artifact: Any,
    policy: Any,
    profile: SafetyProfile,
) -> Mapping[str, Any]:
    categories = {}
    for category, samples in scenarios.items():
        reports = []
        multipliers = []
        failure_details = []
        for scenario_index, indices in enumerate(samples):
            multiplier = safety_multiplier(indices, rows, profile)
            multipliers.append(multiplier)
            reports.append(
                evaluate_indices(
                    indices,
                    prepared,
                    artifact,
                    policy,
                    multiplier,
                )
            )
            failed_tiers = {
                tier: reports[-1]["tiers"][tier]["budget_ratio"]
                for tier in TIERS
                if not reports[-1]["tiers"][tier]["budget_passed"]
            }
            if failed_tiers:
                failure_details.append(
                    {
                        "scenario_index": scenario_index,
                        "num_episodes": len(indices),
                        "safety_multiplier": multiplier,
                        "signal_rates": _signal_rates(indices, rows),
                        "failed_tiers": failed_tiers,
                    }
                )
        failures = sum(
            not report["tiers"][tier]["budget_passed"]
            for report in reports
            for tier in TIERS
        )
        tiers = {}
        for tier in TIERS:
            tier_rows = [report["tiers"][tier] for report in reports]
            tiers[tier] = {
                "budget_limit": float(policy.tiers[tier].budget_multiplier),
                "budget_failures": sum(
                    not row["budget_passed"] for row in tier_rows
                ),
                "budget_ratio_mean": math.fsum(
                    row["budget_ratio"] for row in tier_rows
                )
                / len(tier_rows),
                "budget_ratio_max": max(row["budget_ratio"] for row in tier_rows),
                "quality_score_mean": math.fsum(
                    row["quality_score"] for row in tier_rows
                )
                / len(tier_rows),
            }
        categories[category] = {
            "scenarios": len(reports),
            "tier_budget_failures": failures,
            "all_tier_failure_scenarios": sum(
                not report["all_tiers_passed"] for report in reports
            ),
            "final_score_mean": math.fsum(
                report["final_score"] for report in reports
            )
            / len(reports),
            "safety_multiplier_mean": math.fsum(multipliers) / len(multipliers),
            "safety_multiplier_min": min(multipliers),
            "failure_details": failure_details,
            "tiers": tiers,
        }
    stress = tuple(
        row
        for category, row in categories.items()
        if category != "whole"
    )
    return {
        "tier_budget_failures": sum(row["tier_budget_failures"] for row in stress),
        "all_tier_failure_scenarios": sum(
            row["all_tier_failure_scenarios"] for row in stress
        ),
        "stress_final_score_mean": math.fsum(
            row["final_score_mean"] * row["scenarios"] for row in stress
        )
        / sum(row["scenarios"] for row in stress),
        "categories": categories,
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


def evaluate(args: argparse.Namespace) -> Mapping[str, Any]:
    train_inputs = load_input(args.train_input)
    train_outcomes = load_outcomes(args.train_outcomes)
    eval_inputs = load_input(args.eval_input)
    eval_outcomes = load_outcomes(args.eval_outcomes)
    policy = load_bundled_policy()
    artifact = aggressive_v6.load_artifact(args.artifact)
    train_prepared = prepare_evaluation(train_inputs, train_outcomes, artifact)
    eval_prepared = prepare_evaluation(eval_inputs, eval_outcomes, artifact)
    train_rows = signal_rows(train_inputs.episodes)
    eval_rows = signal_rows(eval_inputs.episodes)
    rates = reference_rates(train_rows)
    train_scenarios = _scenarios(
        train_inputs.episodes,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
    )
    candidates = {}
    for composition_penalty in args.composition_penalties:
        for small_batch_penalty in args.small_batch_penalties:
            name = f"composition-{composition_penalty:g}-small-{small_batch_penalty:g}"
            profile = SafetyProfile(
                rates,
                args.stable_batch_size,
                composition_penalty,
                small_batch_penalty,
            )
            candidates[name] = _score_profile(
                train_scenarios,
                train_rows,
                train_prepared,
                artifact,
                policy,
                profile,
            )
    passing = [
        name for name, row in candidates.items() if row["tier_budget_failures"] == 0
    ]
    pool = passing or list(candidates)
    selected = max(
        pool,
        key=lambda name: (
            -candidates[name]["tier_budget_failures"],
            candidates[name]["stress_final_score_mean"],
            name,
        ),
    )
    pieces = selected.split("-")
    selected_profile = SafetyProfile(
        rates,
        args.stable_batch_size,
        float(pieces[1]),
        float(pieces[3]),
    )
    eval_scenarios = _scenarios(
        eval_inputs.episodes,
        seed=args.seed,
        bootstrap_sizes=args.bootstrap_sizes,
        bootstrap_trials=args.bootstrap_trials,
        mixture_size=args.mixture_size,
        mixture_trials=args.mixture_trials,
        mixture_proportions=args.mixture_proportions,
    )
    eval_report = _score_profile(
        eval_scenarios,
        eval_rows,
        eval_prepared,
        artifact,
        policy,
        selected_profile,
    )
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "protocol": "train-calibrate-then-fixed-eval",
        "seed": args.seed,
        "reference_rates": rates,
        "stable_batch_size": args.stable_batch_size,
        "composition_penalties": list(args.composition_penalties),
        "small_batch_penalties": list(args.small_batch_penalties),
        "selected_profile": {
            "name": selected,
            "composition_penalty": selected_profile.composition_penalty,
            "small_batch_penalty": selected_profile.small_batch_penalty,
        },
        "train_candidates": candidates,
        "fixed_eval": eval_report,
    }


def _float_list(value: str) -> Tuple[float, ...]:
    values = tuple(float(item) for item in value.split(",") if item)
    if not values or any(item < 0.0 for item in values):
        raise argparse.ArgumentTypeError("0 이상의 실수를 쉼표로 구분해야 합니다.")
    return values


def _int_list(value: str) -> Tuple[int, ...]:
    values = tuple(int(item) for item in value.split(",") if item)
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("1 이상의 정수를 쉼표로 구분해야 합니다.")
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-input", type=Path, required=True)
    parser.add_argument("--train-outcomes", type=Path, required=True)
    parser.add_argument("--eval-input", type=Path, required=True)
    parser.add_argument("--eval-outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument("--stable-batch-size", type=int, default=300)
    parser.add_argument("--bootstrap-sizes", type=_int_list, default=(100, 300, 880))
    parser.add_argument("--bootstrap-trials", type=int, default=10)
    parser.add_argument("--mixture-size", type=int, default=300)
    parser.add_argument("--mixture-trials", type=int, default=5)
    parser.add_argument(
        "--mixture-proportions", type=_float_list, default=(0.25, 0.5, 0.75, 1.0)
    )
    parser.add_argument(
        "--composition-penalties",
        type=_float_list,
        default=DEFAULT_COMPOSITION_PENALTIES,
    )
    parser.add_argument(
        "--small-batch-penalties",
        type=_float_list,
        default=DEFAULT_SMALL_BATCH_PENALTIES,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.stable_batch_size < 1:
            raise ValueError("stable batch size는 1 이상이어야 합니다.")
        report = evaluate(args)
        _write(args.report, report)
    except (OSError, ProtocolError, RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: adaptive safety study를 생성했습니다 "
        f"(selected={report['selected_profile']['name']}, "
        f"eval_failures={report['fixed_eval']['tier_budget_failures']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
