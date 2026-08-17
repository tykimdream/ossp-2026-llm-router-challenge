# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Calibrate deterministic hybrid safety ratios on a named public split."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from ossp_router import competition, hybrid
from ossp_router.protocol import (
    TIERS,
    Decision,
    InputBatch,
    ProtocolError,
    RoutingPolicy,
    Submission,
    load_bundled_policy,
    load_input,
    load_outcomes,
)
from ossp_router.scoring import score_submissions


DEFAULT_TARGETS = {"fast": 1.18, "balanced": 1.85, "premium": 3.5}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _submission(
    inputs: InputBatch, policy: RoutingPolicy, tier: str, selected: Sequence[str]
) -> Submission:
    return Submission(
        inputs.schema_version,
        inputs.challenge_id,
        policy.policy_id,
        inputs.split,
        tier,
        tuple(
            Decision(episode.episode_id, model_id)
            for episode, model_id in zip(inputs.episodes, selected)
        ),
    )


def _tier_report(inputs, outcomes, policy, tier, selected):
    light = tuple(policy.light_model_id for _episode in inputs.episodes)
    submissions = [
        _submission(inputs, policy, candidate, selected if candidate == tier else light)
        for candidate in TIERS
    ]
    return score_submissions(inputs, outcomes, submissions, policy)["tiers"][tier]


def calibrate(args: argparse.Namespace) -> Mapping[str, Any]:
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    policy = load_bundled_policy()
    ridge_artifact = competition.load_artifact(args.ridge_artifact)
    raw_hybrid = json.loads(args.hybrid_artifact.read_text(encoding="utf-8"))
    hybrid_artifact = hybrid.parse_artifact(raw_hybrid)
    predictions = [
        competition.predict_episode(episode, ridge_artifact)
        for episode in inputs.episodes
    ]
    costs = [prediction[1] for prediction in predictions]
    calibrated: Dict[str, Any] = {}
    for tier in TIERS:
        scores = [
            hybrid.guarded_scores(episode, prediction[0], tier, hybrid_artifact)
            for episode, prediction in zip(inputs.episodes, predictions)
        ]
        best = None
        for step in range(121):
            safety = 0.5 + step / 240
            selected, predicted_ratio = competition.select_models(
                scores,
                costs,
                budget_multiplier=float(policy.tiers[tier].budget_multiplier),
                safety_ratio=safety,
            )
            if tier == "premium":
                selected, predicted_ratio = competition.fill_ax31(
                    selected,
                    scores,
                    costs,
                    budget_multiplier=float(policy.tiers[tier].budget_multiplier),
                    safety_ratio=hybrid_artifact.premium_ax31_fill_safety,
                )
            report = _tier_report(inputs, outcomes, policy, tier, selected)
            actual_ratio = float(report["budget_ratio"])
            if actual_ratio > args.targets[tier]:
                continue
            rank = (float(report["tier_score"]), -actual_ratio, -safety)
            if best is None or rank > best[0]:
                best = (rank, safety, predicted_ratio, report)
        if best is None:
            raise RuntimeError(f"{tier}의 안전한 hybrid 후보를 찾지 못했습니다.")
        raw_hybrid["tiers"][tier]["safety_ratio"] = best[1]
        calibrated[tier] = {
            "safety_ratio": best[1],
            "predicted_budget_ratio": best[2],
            "actual_budget_ratio": best[3]["budget_ratio"],
            "tier_score": best[3]["tier_score"],
            "model_counts": best[3]["model_counts"],
        }
    summary = dict(raw_hybrid["training_summary"])
    summary.update(
        {
            "calibration_input_sha256": _sha256(args.input),
            "calibration_outcomes_sha256": _sha256(args.outcomes),
            "calibration_split": inputs.split,
            "ridge_artifact_sha256": _sha256(args.ridge_artifact),
            "budget_ratio_targets": args.targets,
        }
    )
    raw_hybrid["training_summary"] = summary
    hybrid.parse_artifact(raw_hybrid)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(raw_hybrid, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = {
        "report_type": "ossp-hybrid-calibration-v1",
        "input": str(args.input),
        "outcomes": str(args.outcomes),
        "ridge_artifact": str(args.ridge_artifact),
        "hybrid_artifact": str(args.hybrid_artifact),
        "output": str(args.output),
        "targets": args.targets,
        "tiers": calibrated,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _targets(value: str) -> Mapping[str, float]:
    result = {}
    try:
        for item in value.split(","):
            tier, raw = item.split("=", 1)
            result[tier] = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("등급=비율 형식이 필요합니다.") from exc
    if set(result) != set(TIERS) or any(
        not math.isfinite(number) or number < 1 for number in result.values()
    ):
        raise argparse.ArgumentTypeError("세 등급의 1 이상 목표 비율이 필요합니다.")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--ridge-artifact", type=Path, required=True)
    parser.add_argument("--hybrid-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--targets",
        type=_targets,
        default=dict(DEFAULT_TARGETS),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = calibrate(args)
    except (
        OSError,
        ProtocolError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: hybrid 안전계수를 보정했습니다: "
        + ", ".join(
            f"{tier}={report['tiers'][tier]['safety_ratio']:.6f}" for tier in TIERS
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
