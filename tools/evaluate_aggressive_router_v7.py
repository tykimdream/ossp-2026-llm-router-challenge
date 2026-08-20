# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Compare V6.1 with the fixed V7 adaptive budget runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from ossp_router import aggressive_v5, aggressive_v6, aggressive_v7, submission
from ossp_router.protocol import TIERS, load_bundled_policy, load_input, load_outcomes
from ossp_router.scoring import score_submissions


REPORT_TYPE = "ossp-aggressive-v7-comparison-v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_PATH = ROOT / "src/ossp_router/resources/aggressive-router.v5.json"
DEFAULT_PROFILE_PATH = ROOT / "src/ossp_router/resources/adaptive-safety.v7.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decision_digest(routed: Any) -> str:
    value = "\n".join(
        f"{decision.episode_id}\t{decision.model_id}"
        for decision in routed.decisions
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


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
    artifact_path = args.artifact or DEFAULT_ARTIFACT_PATH
    profile_path = args.profile or DEFAULT_PROFILE_PATH
    v5_artifact = aggressive_v5.load_artifact(artifact_path)
    v6_artifact = aggressive_v6.compile_artifact(v5_artifact)
    profile = aggressive_v7.load_profile(profile_path)
    artifacts = {
        "v6": v6_artifact,
        "v7": aggressive_v7.V7Artifact(v6_artifact, profile),
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
            digests[name][tier] = _decision_digest(routed)
            submissions[name].append(routed)
    scores = {
        name: score_submissions(inputs, outcomes, routed, policy)
        for name, routed in submissions.items()
    }
    mismatches = {}
    for tier_index, tier in enumerate(TIERS):
        left = submissions["v6"][tier_index].decisions
        right = submissions["v7"][tier_index].decisions
        mismatches[tier] = sum(
            a.model_id != b.model_id for a, b in zip(left, right)
        )
    return {
        "report_type": REPORT_TYPE,
        "schema_version": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "input": {"path": _display_path(args.input), "sha256": _sha256(args.input)},
        "outcomes": {
            "path": _display_path(args.outcomes),
            "sha256": _sha256(args.outcomes),
        },
        "artifact": {
            "path": _display_path(artifact_path),
            "sha256": _sha256(artifact_path),
        },
        "profile": {
            "path": _display_path(profile_path),
            "sha256": _sha256(profile_path),
            "stable_batch_size": profile.stable_batch_size,
            "composition_penalty": profile.composition_penalty,
            "small_batch_penalty": profile.small_batch_penalty,
        },
        "num_episodes": len(inputs.episodes),
        "routers": {
            name: {
                "router_version": (
                    aggressive_v6.ROUTER_VERSION
                    if name == "v6"
                    else aggressive_v7.ROUTER_VERSION
                ),
                "runtime_seconds": runtimes[name],
                "decision_sha256": digests[name],
                "score": scores[name],
            }
            for name in ("v6", "v7")
        },
        "v6_v7_decision_mismatches": mismatches,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    report = evaluate(args)
    _write(args.report, report)
    print(
        "OK: V6/V7 비교를 생성했습니다 "
        f"(v6={report['routers']['v6']['score']['final_score']}, "
        f"v7={report['routers']['v7']['score']['final_score']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
