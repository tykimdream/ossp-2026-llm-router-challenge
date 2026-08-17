# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Compare the frozen V5 router with operationally hardened V6."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from ossp_router import aggressive_v5, aggressive_v6, submission
from ossp_router.protocol import TIERS, load_bundled_policy, load_input, load_outcomes
from ossp_router.scoring import score_submissions


def _digest(routed) -> str:
    value = "\n".join(
        f"{decision.episode_id}\t{decision.model_id}"
        for decision in routed.decisions
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    policy = load_bundled_policy()
    v5_artifact = aggressive_v5.load_artifact(args.artifact)
    artifacts = {
        "v5": v5_artifact,
        "v6": aggressive_v6.compile_artifact(v5_artifact),
    }
    submissions: Dict[str, list] = {}
    runtimes: Dict[str, Dict[str, float]] = {}
    digests: Dict[str, Dict[str, str]] = {}
    for name, artifact in artifacts.items():
        submissions[name] = []
        runtimes[name] = {}
        digests[name] = {}
        for tier in TIERS:
            started = time.perf_counter()
            routed = submission.make_submission(inputs, policy, artifact, tier)
            runtimes[name][tier] = time.perf_counter() - started
            digests[name][tier] = _digest(routed)
            submissions[name].append(routed)
    scores = {
        name: score_submissions(inputs, outcomes, routed, policy)
        for name, routed in submissions.items()
    }
    mismatches = {}
    for tier_index, tier in enumerate(TIERS):
        left = submissions["v5"][tier_index].decisions
        right = submissions["v6"][tier_index].decisions
        mismatches[tier] = sum(
            a.model_id != b.model_id for a, b in zip(left, right)
        )
    report = {
        "report_type": "ossp-aggressive-v6-comparison-v1",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "input": str(args.input),
        "outcomes": str(args.outcomes),
        "artifact": str(args.artifact) if args.artifact else "bundled-v5",
        "num_episodes": len(inputs.episodes),
        "routers": {
            name: {
                "runtime_seconds": runtimes[name],
                "decision_sha256": digests[name],
                "score": scores[name],
            }
            for name in ("v5", "v6")
        },
        "v5_v6_decision_mismatches": mismatches,
        "v6_safety_ratios": dict(aggressive_v6.TIER_SAFETY_RATIOS),
        "v6_premium_fill_safety_ratio": aggressive_v6.PREMIUM_FILL_SAFETY_RATIO,
    }
    _write(args.report, report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    report = evaluate(args)
    print(
        "OK: V5/V6 비교를 생성했습니다 "
        f"(v5={report['routers']['v5']['score']['final_score']}, "
        f"v6={report['routers']['v6']['score']['final_score']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
