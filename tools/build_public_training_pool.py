# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Combine materialized public Train/Dev files for final router training."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ossp_router.protocol import dumps_json, load_json


def _batch(path: Path, label: str) -> Dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict) or not isinstance(value.get("episodes"), list):
        raise ValueError(f"{label} 파일 형식이 올바르지 않습니다.")
    return value


def _combine(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("schema_version", "challenge_id"):
        if left.get(key) != right.get(key):
            raise ValueError(f"공개 Train/Dev의 {key}가 다릅니다.")
    episodes = [*left["episodes"], *right["episodes"]]
    episode_ids = [episode.get("episode_id") for episode in episodes]
    if len(set(episode_ids)) != len(episode_ids):
        raise ValueError("결합한 공개 자료에 중복 episode_id가 있습니다.")
    return {
        "schema_version": left["schema_version"],
        "challenge_id": left["challenge_id"],
        "split": "public-train-dev",
        "episodes": episodes,
    }


def _write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(value), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="공개 Train/Dev 입력과 outcome을 최종 학습용으로 결합합니다."
    )
    parser.add_argument("--train-input", type=Path, required=True)
    parser.add_argument("--train-outcomes", type=Path, required=True)
    parser.add_argument("--dev-input", type=Path, required=True)
    parser.add_argument("--dev-outcomes", type=Path, required=True)
    parser.add_argument("--output-input", type=Path, required=True)
    parser.add_argument("--output-outcomes", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    combined_inputs = _combine(
        _batch(args.train_input, "Train 입력"),
        _batch(args.dev_input, "Dev 입력"),
    )
    combined_outcomes = _combine(
        _batch(args.train_outcomes, "Train outcome"),
        _batch(args.dev_outcomes, "Dev outcome"),
    )
    input_ids = {episode["episode_id"] for episode in combined_inputs["episodes"]}
    outcome_ids = {
        episode["episode_id"] for episode in combined_outcomes["episodes"]
    }
    if input_ids != outcome_ids:
        raise ValueError("결합한 입력과 outcome의 문항 집합이 다릅니다.")
    _write(args.output_input, combined_inputs)
    _write(args.output_outcomes, combined_outcomes)
    print(f"OK: 공개 Train+Dev {len(input_ids)}문항을 결합했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
