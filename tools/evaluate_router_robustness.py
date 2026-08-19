# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Evaluate V6 routing under standalone groups and deterministic batch stress."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ossp_router import aggressive_v5, aggressive_v6
from ossp_router.heuristic import episode_text, extract_features
from ossp_router.protocol import (
    MODEL_IDS,
    TIERS,
    Episode,
    ProtocolError,
    RoutingPolicy,
    load_bundled_policy,
    load_input,
    load_outcomes,
)


REPORT_TYPE = "ossp-router-robustness-v1"
DEFAULT_BOOTSTRAP_SIZES = (100, 300, 880)
DEFAULT_MIXTURE_PROPORTIONS = (0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class PreparedEvaluation:
    actual_scores: Tuple[Mapping[str, float], ...]
    actual_costs: Tuple[Mapping[str, float], ...]
    predicted_scores: Mapping[str, Tuple[Mapping[str, float], ...]]
    predicted_costs: Mapping[str, Tuple[Mapping[str, float], ...]]


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


def _model_cost(outcome: Any, policy: RoutingPolicy) -> float:
    rates = policy.models[outcome.model_id]
    unit = Decimal(policy.token_unit)
    return float(
        rates.fixed_cost
        + Decimal(outcome.input_tokens) * rates.input_token_rate / unit
        + Decimal(outcome.output_tokens) * rates.output_token_rate / unit
    )


def prepare_evaluation(inputs: Any, outcomes: Any, artifact: Any) -> PreparedEvaluation:
    if inputs.challenge_id != outcomes.challenge_id or inputs.split != outcomes.split:
        raise ProtocolError("입력과 outcome 메타데이터가 일치하지 않습니다.")
    policy = load_bundled_policy()
    index = {(row.episode_id, row.model_id): row for row in outcomes.outcomes}
    expected = {
        (episode.episode_id, model_id)
        for episode in inputs.episodes
        for model_id in MODEL_IDS
    }
    if set(index) != expected:
        raise ProtocolError("robustness 평가용 outcome 행렬이 완전하지 않습니다.")
    actual_scores = tuple(
        {
            model_id: float(index[(episode.episode_id, model_id)].score)
            for model_id in MODEL_IDS
        }
        for episode in inputs.episodes
    )
    actual_costs = tuple(
        {
            model_id: _model_cost(index[(episode.episode_id, model_id)], policy)
            for model_id in MODEL_IDS
        }
        for episode in inputs.episodes
    )
    predicted_scores: Dict[str, Tuple[Mapping[str, float], ...]] = {}
    predicted_costs: Dict[str, Tuple[Mapping[str, float], ...]] = {}
    for tier in TIERS:
        rows = tuple(
            aggressive_v6.predict_episode(episode, artifact, tier)
            for episode in inputs.episodes
        )
        predicted_scores[tier] = tuple(dict(row[0]) for row in rows)
        predicted_costs[tier] = tuple(row[1] for row in rows)
    return PreparedEvaluation(
        actual_scores,
        actual_costs,
        predicted_scores,
        predicted_costs,
    )


def _route_indices(
    indices: Sequence[int],
    prepared: PreparedEvaluation,
    artifact: aggressive_v6.V6Artifact,
    policy: RoutingPolicy,
    tier: str,
) -> Mapping[str, Any]:
    if not indices:
        raise ValueError("robustness scenario는 문항을 하나 이상 포함해야 합니다.")
    scores = [dict(prepared.predicted_scores[tier][index]) for index in indices]
    costs = [prepared.predicted_costs[tier][index] for index in indices]
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
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
    light_total = math.fsum(
        prepared.actual_costs[index][MODEL_IDS[0]] for index in indices
    )
    actual_total = math.fsum(
        prepared.actual_costs[index][model_id]
        for index, model_id in zip(indices, selected)
    )
    quality = math.fsum(
        prepared.actual_scores[index][model_id]
        for index, model_id in zip(indices, selected)
    ) / len(indices)
    actual_ratio = actual_total / light_total
    budget_limit = float(policy.tiers[tier].budget_multiplier)
    passed = actual_ratio <= budget_limit
    return {
        "quality_score": quality,
        "tier_score": quality if passed else 0.0,
        "predicted_budget_ratio": predicted_ratio,
        "budget_ratio": actual_ratio,
        "budget_limit": budget_limit,
        "budget_passed": passed,
        "model_counts": {
            model_id: selected.count(model_id) for model_id in MODEL_IDS
        },
    }


def evaluate_indices(
    indices: Sequence[int],
    prepared: PreparedEvaluation,
    artifact: aggressive_v6.V6Artifact,
    policy: RoutingPolicy,
) -> Mapping[str, Any]:
    tiers = {
        tier: _route_indices(indices, prepared, artifact, policy, tier)
        for tier in TIERS
    }
    final_score = math.fsum(
        float(policy.tiers[tier].weight) * tiers[tier]["tier_score"]
        for tier in TIERS
    )
    return {
        "num_episodes": len(indices),
        "final_score": final_score,
        "all_tiers_passed": all(row["budget_passed"] for row in tiers.values()),
        "tiers": tiers,
    }


def _load_episode_ids(path: Optional[Path], split: str) -> Tuple[str, ...]:
    if path is None or not path.is_file():
        return ()
    root = json.loads(path.read_text(encoding="utf-8"))
    if "splits" in root:
        rows = root["splits"].get(split, [])
    else:
        if root.get("split") != split:
            return ()
        rows = root.get("episodes", [])
    return tuple(row["episode_id"] for row in rows)


def content_groups(
    episodes: Sequence[Episode],
    *,
    deepmind_ids: Iterable[str] = (),
    aime_ids: Iterable[str] = (),
) -> Mapping[str, Tuple[int, ...]]:
    deepmind = set(deepmind_ids)
    aime = set(aime_ids)
    features = tuple(extract_features(episode) for episode in episodes)
    groups: Dict[str, Tuple[int, ...]] = {
        "all": tuple(range(len(episodes))),
        "deepmind_math": tuple(
            index for index, episode in enumerate(episodes)
            if episode.episode_id in deepmind
        ),
        "aime": tuple(
            index for index, episode in enumerate(episodes)
            if episode.episode_id in aime
        ),
        "korean_hangul_10pct": tuple(
            index for index, row in enumerate(features) if row.hangul_ratio >= 0.1
        ),
        "code_marker": tuple(
            index for index, row in enumerate(features) if row.code_marker_count > 0
        ),
        "math_or_numeric": tuple(
            index for index, row in enumerate(features)
            if row.math_marker_count > 0 or row.numeric_density >= 0.08
        ),
        "long_context_gt_8000": tuple(
            index for index, episode in enumerate(episodes)
            if len(episode_text(episode)) > 8_000
        ),
        "short_context_le_100": tuple(
            index for index, episode in enumerate(episodes)
            if len(episode_text(episode)) <= 100
        ),
        "messages": tuple(
            index for index, episode in enumerate(episodes)
            if episode.messages is not None
        ),
    }
    return {name: indices for name, indices in groups.items() if indices}


def _nearest_rank(values: Sequence[float], probability: float) -> float:
    if not values or not 0.0 <= probability <= 1.0:
        raise ValueError("quantile 입력이 올바르지 않습니다.")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[min(len(ordered) - 1, rank - 1)]


def summarize_trials(
    reports: Sequence[Mapping[str, Any]], policy: RoutingPolicy
) -> Mapping[str, Any]:
    if not reports:
        raise ValueError("trial report가 비어 있습니다.")
    tiers = {}
    for tier in TIERS:
        ratios = [row["tiers"][tier]["budget_ratio"] for row in reports]
        quality = [row["tiers"][tier]["quality_score"] for row in reports]
        failures = sum(not row["tiers"][tier]["budget_passed"] for row in reports)
        tiers[tier] = {
            "budget_limit": float(policy.tiers[tier].budget_multiplier),
            "budget_failures": failures,
            "budget_failure_rate": failures / len(reports),
            "budget_ratio_p50": _nearest_rank(ratios, 0.50),
            "budget_ratio_p90": _nearest_rank(ratios, 0.90),
            "budget_ratio_p95": _nearest_rank(ratios, 0.95),
            "budget_ratio_p99": _nearest_rank(ratios, 0.99),
            "budget_ratio_max": max(ratios),
            "quality_score_mean": math.fsum(quality) / len(quality),
        }
    scores = [row["final_score"] for row in reports]
    return {
        "trials": len(reports),
        "all_tier_failure_trials": sum(
            not row["all_tiers_passed"] for row in reports
        ),
        "final_score_mean": math.fsum(scores) / len(scores),
        "final_score_p05": _nearest_rank(scores, 0.05),
        "tiers": tiers,
    }


def _bootstrap_indices(
    population: Sequence[int], size: int, trials: int, rng: random.Random
) -> Tuple[Tuple[int, ...], ...]:
    if not population or size < 1 or trials < 1:
        raise ValueError("bootstrap 설정이 올바르지 않습니다.")
    return tuple(
        tuple(rng.choice(population) for _ in range(size)) for _ in range(trials)
    )


def _mixture_indices(
    group: Sequence[int],
    complement: Sequence[int],
    size: int,
    proportion: float,
    trials: int,
    rng: random.Random,
) -> Tuple[Tuple[int, ...], ...]:
    if not group or not complement or not 0.0 <= proportion <= 1.0:
        raise ValueError("mixture 설정이 올바르지 않습니다.")
    group_count = min(size, max(0, round(size * proportion)))
    other_count = size - group_count
    return tuple(
        tuple(rng.choice(group) for _ in range(group_count))
        + tuple(rng.choice(complement) for _ in range(other_count))
        for _ in range(trials)
    )


def _evaluate_samples(
    samples: Sequence[Sequence[int]],
    prepared: PreparedEvaluation,
    artifact: aggressive_v6.V6Artifact,
    policy: RoutingPolicy,
) -> Mapping[str, Any]:
    reports = [
        evaluate_indices(indices, prepared, artifact, policy) for indices in samples
    ]
    return summarize_trials(reports, policy)


def evaluate(args: argparse.Namespace) -> Mapping[str, Any]:
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    policy = load_bundled_policy()
    artifact = aggressive_v6.load_artifact(args.artifact)
    prepared = prepare_evaluation(inputs, outcomes, artifact)
    groups = content_groups(
        inputs.episodes,
        deepmind_ids=_load_episode_ids(args.deepmind_selection, inputs.split),
        aime_ids=_load_episode_ids(args.aime_selection, inputs.split),
    )
    standalone = {
        name: evaluate_indices(indices, prepared, artifact, policy)
        for name, indices in groups.items()
    }
    rng = random.Random(args.seed)
    population = groups["all"]
    bootstrap = {}
    for size in args.bootstrap_sizes:
        samples = _bootstrap_indices(population, size, args.bootstrap_trials, rng)
        bootstrap[str(size)] = _evaluate_samples(samples, prepared, artifact, policy)
    mixtures = {}
    population_set = set(population)
    for name, group in groups.items():
        if name == "all":
            continue
        complement = tuple(sorted(population_set - set(group)))
        if not complement:
            continue
        proportions = {}
        for proportion in args.mixture_proportions:
            samples = _mixture_indices(
                group,
                complement,
                args.mixture_size,
                proportion,
                args.mixture_trials,
                rng,
            )
            proportions[f"{proportion:.2f}"] = _evaluate_samples(
                samples, prepared, artifact, policy
            )
        mixtures[name] = {
            "group_size": len(group),
            "complement_size": len(complement),
            "batch_size": args.mixture_size,
            "proportions": proportions,
        }
    standalone_failures = {
        name: [
            tier for tier in TIERS
            if not report["tiers"][tier]["budget_passed"]
        ]
        for name, report in standalone.items()
    }
    standalone_failures = {
        name: tiers for name, tiers in standalone_failures.items() if tiers
    }
    bootstrap_failures = sum(
        row["tiers"][tier]["budget_failures"]
        for row in bootstrap.values()
        for tier in TIERS
    )
    mixture_failures = sum(
        row["tiers"][tier]["budget_failures"]
        for group in mixtures.values()
        for row in group["proportions"].values()
        for tier in TIERS
    )
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "seed": args.seed,
        "input": {"path": str(args.input), "sha256": _sha256(args.input)},
        "outcomes": {"path": str(args.outcomes), "sha256": _sha256(args.outcomes)},
        "artifact": {
            "path": str(args.artifact) if args.artifact else "bundled-v5",
            "sha256": _sha256(args.artifact) if args.artifact else None,
        },
        "num_episodes": len(inputs.episodes),
        "router_version": aggressive_v6.ROUTER_VERSION,
        "groups": {name: len(indices) for name, indices in groups.items()},
        "standalone": standalone,
        "bootstrap": bootstrap,
        "mixtures": mixtures,
        "gates": {
            "standalone_budget_failures": standalone_failures,
            "bootstrap_budget_failures": bootstrap_failures,
            "mixture_budget_failures": mixture_failures,
            "all_budget_stress_passed": not (
                standalone_failures or bootstrap_failures or mixture_failures
            ),
        },
    }


def _float_list(value: str) -> Tuple[float, ...]:
    values = tuple(float(item) for item in value.split(",") if item)
    if not values or any(not 0.0 <= item <= 1.0 for item in values):
        raise argparse.ArgumentTypeError("0~1 사이 비율을 쉼표로 구분해야 합니다.")
    return values


def _int_list(value: str) -> Tuple[int, ...]:
    values = tuple(int(item) for item in value.split(",") if item)
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("1 이상의 정수를 쉼표로 구분해야 합니다.")
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--deepmind-selection", type=Path)
    parser.add_argument("--aime-selection", type=Path)
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument(
        "--bootstrap-sizes", type=_int_list, default=DEFAULT_BOOTSTRAP_SIZES
    )
    parser.add_argument("--bootstrap-trials", type=int, default=50)
    parser.add_argument("--mixture-size", type=int, default=300)
    parser.add_argument("--mixture-trials", type=int, default=20)
    parser.add_argument(
        "--mixture-proportions",
        type=_float_list,
        default=DEFAULT_MIXTURE_PROPORTIONS,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.bootstrap_trials < 1 or args.mixture_trials < 1:
            raise ValueError("stress trial 수는 1 이상이어야 합니다.")
        report = evaluate(args)
        _write(args.report, report)
    except (OSError, ProtocolError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: robustness report를 생성했습니다 "
        f"(budget_stress_passed={report['gates']['all_budget_stress_passed']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
