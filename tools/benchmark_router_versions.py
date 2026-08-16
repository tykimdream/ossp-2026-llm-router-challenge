# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Benchmark V1/V2/V3 routing and audit V3's fixed-step equivalence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

from ossp_router import competition, hybrid, robust, robust_v3, submission
from ossp_router.heuristic import episode_text
from ossp_router.protocol import MODEL_IDS, TIERS, load_bundled_policy, load_input


def _decision_digest(result) -> str:
    canonical = "\n".join(
        f"{item.episode_id}\t{item.model_id}" for item in result.decisions
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")

    inputs = load_input(args.input)
    policy = load_bundled_policy()
    ridge_artifact = competition.load_artifact()
    uplift_artifact = robust.load_uplift_artifact()
    hybrid_artifact = hybrid.load_artifact()
    v2_artifact = robust.load_artifact()
    v3_artifact = robust_v3.load_artifact()
    routers = {
        "v1-submission-ridge": lambda tier: submission.make_submission(
            inputs, policy, ridge_artifact, tier
        ),
        "v2-fixed-scenario": lambda tier: robust.make_submission(
            inputs,
            policy,
            ridge_artifact,
            uplift_artifact,
            hybrid_artifact,
            v2_artifact,
            tier,
        ),
        "v3-train-oof-uplift": lambda tier: robust_v3.make_submission(
            inputs, policy, uplift_artifact, v3_artifact, tier
        ),
    }
    measurements = {}
    for name, route in routers.items():
        tiers = {}
        for tier in TIERS:
            elapsed = []
            digests = []
            for _ in range(args.repetitions):
                started = time.perf_counter()
                result = route(tier)
                elapsed.append(time.perf_counter() - started)
                digests.append(_decision_digest(result))
            tiers[tier] = {
                "elapsed_seconds": elapsed,
                "elapsed_seconds_median": statistics.median(elapsed),
                "elapsed_seconds_max": max(elapsed),
                "decision_sha256": digests[0],
                "repeated_output_identical": len(set(digests)) == 1,
            }
        measurements[name] = tiers

    canonical = submission._canonical_batch(inputs)
    predictions = [
        robust_v3.uplift.predict_episode(episode, uplift_artifact)
        for episode in canonical.episodes
    ]
    base_scores = [dict(item[0]) for item in predictions]
    costs = [item[1] for item in predictions]
    equivalence = {}
    for tier in TIERS:
        scores = [dict(row) for row in base_scores]
        if tier == "fast":
            for row in scores:
                row[MODEL_IDS[2]] = -2.0
        kwargs = {
            "budget_multiplier": float(policy.tiers[tier].budget_multiplier),
            "safety_ratio": v3_artifact.tier_safety_ratios[tier],
        }
        original = competition.select_models(scores, costs, **kwargs)[0]
        shortened = robust_v3._select_models(scores, costs, **kwargs)[0]
        if tier == "premium":
            original = competition.fill_ax31(
                original,
                scores,
                costs,
                budget_multiplier=kwargs["budget_multiplier"],
                safety_ratio=v3_artifact.premium_fill_safety_ratio,
            )[0]
            shortened = robust_v3._fill_ax31(
                shortened,
                scores,
                costs,
                budget_multiplier=kwargs["budget_multiplier"],
                safety_ratio=v3_artifact.premium_fill_safety_ratio,
            )[0]
        mismatches = sum(a != b for a, b in zip(original, shortened))
        equivalence[tier] = {
            "mismatches": mismatches,
            "identical": mismatches == 0,
        }

    report = {
        "report_type": "ossp-router-version-runtime-v1",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "input": str(args.input),
        "num_episodes": len(inputs.episodes),
        "num_characters": sum(
            len(episode_text(episode)) for episode in inputs.episodes
        ),
        "repetitions": args.repetitions,
        "scope": "in-process routing only; excludes process/container startup",
        "routers": measurements,
        "v3_48_vs_v1_80_step_equivalence": equivalence,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"OK: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
