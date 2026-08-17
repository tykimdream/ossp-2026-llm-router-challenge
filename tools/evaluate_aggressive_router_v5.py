# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Evaluate a fixed aggressive v5 artifact on one public split."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ossp_router import aggressive_v5, submission
from ossp_router.protocol import (
    TIERS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
)
from ossp_router.scoring import ScoringError, score_submissions


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
    policy = load_bundled_policy()
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    artifact = aggressive_v5.load_artifact(args.artifact)
    submissions = [
        submission.make_submission(inputs, policy, artifact, tier)
        for tier in TIERS
    ]
    report = score_submissions(inputs, outcomes, submissions, policy)
    _write(args.report, report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate(args)
    except (OSError, ProtocolError, ScoringError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        f"OK: aggressive v5 평가를 생성했습니다 (score={report['final_score']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
