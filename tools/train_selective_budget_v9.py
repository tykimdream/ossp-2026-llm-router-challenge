# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Fit and serialize the frozen V9 direct-uplift Ridge heads."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

import train_competition_router as base
from ossp_router import aggressive_v9
from ossp_router.protocol import (
    MODEL_IDS,
    ProtocolError,
    load_bundled_policy,
    load_input,
    load_outcomes,
    policy_sha256,
)


DEFAULT_ALPHA = 30_000.0
DEFAULT_BLEND_WEIGHT = 0.5
DEFAULT_THRESHOLD = 0.1
DEFAULT_RECOVERY_FRACTION = 0.75


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _raw_head(
    mean: Any,
    scale: Any,
    intercept: Any,
    coefficients: Any,
    column: int,
) -> Mapping[str, Any]:
    raw_coefficients = coefficients[:, column] / scale
    raw_intercept = float(intercept[column] - np.sum(mean * raw_coefficients))
    return {
        "intercept": raw_intercept,
        "coefficients": [float(value) for value in raw_coefficients],
    }


def train(args: argparse.Namespace) -> Mapping[str, Any]:
    if np is None:
        raise RuntimeError("V9 학습에는 NumPy가 필요합니다.")
    policy = load_bundled_policy()
    inputs = load_input(args.input)
    outcomes = load_outcomes(args.outcomes)
    matrix, targets, _ = base._matrix(inputs, outcomes, policy, args.hash_bins)
    score_targets = targets[:, : len(MODEL_IDS)]
    uplift_targets = score_targets[:, 1:] - score_targets[:, :1]
    mean, scale, intercept, coefficients = base._fit(matrix, uplift_targets, args.alpha)
    fitted = base._predict(matrix, mean, scale, intercept, coefficients)
    summary = {
        "num_episodes": len(inputs.episodes),
        "protocol": "train-selected-v9-fixed-hyperparameters",
        "input_sha256": _sha256(args.input),
        "outcomes_sha256": _sha256(args.outcomes),
        "uplift_mse": float(np.mean((fitted - uplift_targets) ** 2)),
    }
    artifact = {
        "artifact_type": aggressive_v9.ARTIFACT_TYPE,
        "schema_version": 1,
        "policy_id": policy.policy_id,
        "policy_sha256": policy_sha256(policy),
        "hash_bins": args.hash_bins,
        "uplift_heads": {
            model_id: _raw_head(mean, scale, intercept, coefficients, model_index - 1)
            for model_index, model_id in enumerate(MODEL_IDS[1:], start=1)
        },
        "ridge_alpha": args.alpha,
        "blend_weight": args.blend_weight,
        "threshold": args.threshold,
        "recovery_fraction": args.recovery_fraction,
        "training_summary": summary,
    }
    aggressive_v9.parse_profile(artifact)
    base._write(args.artifact, artifact)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--hash-bins", type=int, default=512)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--blend-weight", type=float, default=DEFAULT_BLEND_WEIGHT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--recovery-fraction", type=float, default=DEFAULT_RECOVERY_FRACTION
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = train(args)
    except (OSError, ProtocolError, RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(
        "OK: V9 selective budget artifact를 생성했습니다 "
        f"(episodes={summary['num_episodes']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
