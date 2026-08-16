# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Add or replace one reproducible router result in the experiment registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
TIERS = ("fast", "balanced", "premium")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _file_record(path: Optional[Path]) -> Optional[Mapping[str, str]]:
    if path is None:
        return None
    if not path.is_file():
        raise ValueError(f"파일이 없습니다: {path}")
    return {"path": _relative(path), "sha256": _sha256(path)}


def _metrics(report: Mapping[str, Any]) -> Mapping[str, Any]:
    tiers = report.get("tiers")
    if not isinstance(tiers, dict) or set(tiers) != set(TIERS):
        raise ValueError("score report의 tier가 완전하지 않습니다.")
    result = {}
    for tier in TIERS:
        row = tiers[tier]
        ratio = float(row["budget_ratio"])
        limit = float(row["budget_multiplier"])
        result[tier] = {
            "score": float(row["tier_score"]),
            "quality_score": float(row["quality_score"]),
            "budget_ratio": ratio,
            "budget_limit": limit,
            "budget_utilization": ratio / limit,
            "budget_headroom": limit - ratio,
            "budget_passed": bool(row["budget_passed"]),
            "model_counts": dict(row["model_counts"]),
        }
    return {"final_score": float(report["final_score"]), "tiers": result}


def _runtime_metrics(report: Mapping[str, Any]) -> Mapping[str, Any]:
    tiers = {}
    for row in report.get("tiers", []):
        tiers[row["tier"]] = {
            "elapsed_seconds_max": float(row["elapsed_seconds_max"]),
            "passed": bool(row["passed"]),
        }
    return {
        "passed": bool(report.get("passed")),
        "platform": report.get("image", {}).get("platform"),
        "tiers": tiers,
    }


def record(args: argparse.Namespace) -> None:
    score_report = json.loads(args.score_report.read_text(encoding="utf-8"))
    runtime_report = (
        json.loads(args.runtime_report.read_text(encoding="utf-8"))
        if args.runtime_report
        else None
    )
    if args.registry.exists():
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
    else:
        registry = {
            "schema_version": 1,
            "report_title": "OSSP Router Experiment Comparison",
            "experiments": [],
        }
    if registry.get("schema_version") != 1 or not isinstance(
        registry.get("experiments"), list
    ):
        raise ValueError("지원하지 않는 experiment registry입니다.")
    experiment = {
        "id": args.id,
        "title": args.title,
        "family": args.family,
        "status": args.status,
        "evaluation": {
            "protocol": args.protocol,
            "trained_on": args.trained_on,
            "evaluated_on": args.evaluated_on,
            "fair_holdout": args.fair_holdout,
            "validation_tuned": args.validation_tuned,
        },
        "artifact": _file_record(args.artifact),
        "score_report": _file_record(args.score_report),
        "runtime_report": _file_record(args.runtime_report),
        "metrics": _metrics(score_report),
        "runtime": _runtime_metrics(runtime_report) if runtime_report else None,
        "pros": args.pro,
        "cons": args.con,
        "warnings": args.warning,
        "commands": args.command,
    }
    experiments = [item for item in registry["experiments"] if item["id"] != args.id]
    experiments.append(experiment)
    registry["experiments"] = sorted(experiments, key=lambda item: item["id"])
    args.registry.parent.mkdir(parents=True, exist_ok=True)
    args.registry.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument(
        "--status",
        choices=("reference", "candidate", "rejected", "champion"),
        required=True,
    )
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--trained-on", required=True)
    parser.add_argument("--evaluated-on", required=True)
    parser.add_argument("--fair-holdout", action="store_true")
    parser.add_argument("--validation-tuned", action="store_true")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--score-report", type=Path, required=True)
    parser.add_argument("--runtime-report", type=Path)
    parser.add_argument("--pro", action="append", default=[])
    parser.add_argument("--con", action="append", default=[])
    parser.add_argument("--warning", action="append", default=[])
    parser.add_argument("--command", action="append", default=[])
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.id} 실험을 {args.registry}에 기록했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
