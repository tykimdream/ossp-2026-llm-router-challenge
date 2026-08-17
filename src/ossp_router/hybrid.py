# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic learned router with explicit heuristic safety gates."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from . import competition
from .heuristic import episode_text, extract_features, write_submission_atomic
from .protocol import (
    MODEL_IDS,
    TIERS,
    Decision,
    Episode,
    InputBatch,
    ProtocolError,
    RoutingPolicy,
    Submission,
    load_bundled_policy,
    load_input,
    load_json,
    load_policy,
    parse_submission,
    policy_sha256,
    submission_to_dict,
)


ARTIFACT_TYPE = "ossp-hybrid-policy-v1"
DEFAULT_ARTIFACT = "hybrid-router.v1.json"
_SIMPLE_TRANSFORM = re.compile(
    r"\b(?:summari[sz]e|rewrite|translate|list|extract|"
    r"요약|바꾸|번역|나열|추출)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TierGuard:
    safety_ratio: float
    allow_k1: bool


@dataclass(frozen=True)
class HybridArtifact:
    policy_id: str
    policy_digest: str
    ax31_min_uplift: float
    k1_min_uplift: float
    block_ax31_simple_transform: bool
    k1_require_task_signal: bool
    k1_max_characters: int
    k1_max_hangul_ratio: float
    premium_ax31_fill_safety: float
    tiers: Mapping[str, TierGuard]
    training_summary: Mapping[str, Any]


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label}은(는) JSON 객체여야 합니다.")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ProtocolError(f"{label}은(는) 유한한 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError(f"{label}은(는) 유한한 숫자여야 합니다.")
    return result


def parse_artifact(value: Any) -> HybridArtifact:
    root = _object(value, "hybrid artifact")
    expected = {
        "artifact_type",
        "schema_version",
        "policy_id",
        "policy_sha256",
        "heuristics",
        "tiers",
        "training_summary",
    }
    if set(root) != expected:
        raise ProtocolError("hybrid artifact 필드가 올바르지 않습니다.")
    if root["artifact_type"] != ARTIFACT_TYPE or root["schema_version"] != 1:
        raise ProtocolError("지원하지 않는 hybrid artifact입니다.")
    heuristics = _object(root["heuristics"], "heuristics")
    heuristic_keys = {
        "ax31_min_predicted_uplift",
        "k1_min_predicted_uplift",
        "block_ax31_simple_transform",
        "k1_require_task_signal",
        "k1_max_characters",
        "k1_max_hangul_ratio",
        "premium_ax31_fill_safety",
    }
    if set(heuristics) != heuristic_keys:
        raise ProtocolError("hybrid heuristic 필드가 올바르지 않습니다.")
    for name in ("block_ax31_simple_transform", "k1_require_task_signal"):
        if not isinstance(heuristics[name], bool):
            raise ProtocolError(f"heuristics.{name}은(는) bool이어야 합니다.")
    max_characters = heuristics["k1_max_characters"]
    if isinstance(max_characters, bool) or not isinstance(max_characters, int):
        raise ProtocolError("heuristics.k1_max_characters는 정수여야 합니다.")
    if max_characters < 1:
        raise ProtocolError("heuristics.k1_max_characters는 양수여야 합니다.")
    tiers_raw = _object(root["tiers"], "tiers")
    if set(tiers_raw) != set(TIERS):
        raise ProtocolError("hybrid tier 설정이 완전하지 않습니다.")
    tiers = {}
    for tier in TIERS:
        tier_raw = _object(tiers_raw[tier], f"tiers.{tier}")
        if set(tier_raw) != {"safety_ratio", "allow_k1"}:
            raise ProtocolError(f"tiers.{tier} 필드가 올바르지 않습니다.")
        if not isinstance(tier_raw["allow_k1"], bool):
            raise ProtocolError(f"tiers.{tier}.allow_k1은 bool이어야 합니다.")
        safety = _number(tier_raw["safety_ratio"], f"tiers.{tier}.safety_ratio")
        if not 0 < safety <= 1:
            raise ProtocolError(f"tiers.{tier}.safety_ratio 범위가 올바르지 않습니다.")
        tiers[tier] = TierGuard(safety, tier_raw["allow_k1"])
    ax31_uplift = _number(
        heuristics["ax31_min_predicted_uplift"],
        "heuristics.ax31_min_predicted_uplift",
    )
    k1_uplift = _number(
        heuristics["k1_min_predicted_uplift"],
        "heuristics.k1_min_predicted_uplift",
    )
    hangul = _number(
        heuristics["k1_max_hangul_ratio"], "heuristics.k1_max_hangul_ratio"
    )
    fill = _number(
        heuristics["premium_ax31_fill_safety"],
        "heuristics.premium_ax31_fill_safety",
    )
    if ax31_uplift < 0 or k1_uplift < 0 or not 0 <= hangul <= 1 or not 0 < fill <= 1:
        raise ProtocolError("hybrid heuristic 숫자 범위가 올바르지 않습니다.")
    policy_id = root["policy_id"]
    policy_digest = root["policy_sha256"]
    if not isinstance(policy_id, str) or not isinstance(policy_digest, str):
        raise ProtocolError("hybrid artifact 정책 정보가 올바르지 않습니다.")
    return HybridArtifact(
        policy_id=policy_id,
        policy_digest=policy_digest,
        ax31_min_uplift=ax31_uplift,
        k1_min_uplift=k1_uplift,
        block_ax31_simple_transform=heuristics["block_ax31_simple_transform"],
        k1_require_task_signal=heuristics["k1_require_task_signal"],
        k1_max_characters=max_characters,
        k1_max_hangul_ratio=hangul,
        premium_ax31_fill_safety=fill,
        tiers=tiers,
        training_summary=dict(_object(root["training_summary"], "training_summary")),
    )


def load_artifact(path: Optional[Path] = None) -> HybridArtifact:
    if path is not None:
        return parse_artifact(load_json(path))
    try:
        text = resources.read_text(
            "ossp_router.resources", DEFAULT_ARTIFACT, encoding="utf-8"
        )
    except (OSError, UnicodeError) as exc:
        raise ProtocolError(f"내장 hybrid artifact를 읽을 수 없습니다: {exc}") from exc
    return parse_artifact(json.loads(text))


def guarded_scores(
    episode: Episode,
    predicted_scores: Mapping[str, float],
    tier: str,
    artifact: HybridArtifact,
) -> Mapping[str, float]:
    """Apply deterministic content-only gates to learned score predictions."""

    light_id, ax31_id, k1_id = MODEL_IDS
    result = dict(predicted_scores)
    light_score = result[light_id]
    text = episode_text(episode)
    features = extract_features(episode)
    simple_transform = bool(_SIMPLE_TRANSFORM.search(text))
    if (
        result[ax31_id] - light_score < artifact.ax31_min_uplift
        or artifact.block_ax31_simple_transform
        and simple_transform
    ):
        result[ax31_id] = light_score - 1e-9
    task_signal = (
        features.code_marker_count > 0
        or features.math_marker_count > 0
        or features.numeric_density >= 0.08
        or simple_transform
    )
    guard = artifact.tiers[tier]
    k1_blocked = (
        not guard.allow_k1
        or result[k1_id] - light_score < artifact.k1_min_uplift
        or len(text) > artifact.k1_max_characters
        or features.hangul_ratio >= artifact.k1_max_hangul_ratio
        or artifact.k1_require_task_signal
        and not task_signal
    )
    if k1_blocked:
        result[k1_id] = light_score - 2e-9
    return result


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    ridge_artifact: competition.CompetitionArtifact,
    hybrid_artifact: HybridArtifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    digest = policy_sha256(policy)
    if (
        ridge_artifact.policy_id != policy.policy_id
        or ridge_artifact.policy_digest != digest
        or hybrid_artifact.policy_id != policy.policy_id
        or hybrid_artifact.policy_digest != digest
    ):
        raise ProtocolError("artifact와 실행 정책이 일치하지 않습니다.")
    predictions = [
        competition.predict_episode(episode, ridge_artifact)
        for episode in inputs.episodes
    ]
    scores = [
        guarded_scores(episode, prediction[0], tier, hybrid_artifact)
        for episode, prediction in zip(inputs.episodes, predictions)
    ]
    costs = [prediction[1] for prediction in predictions]
    guard = hybrid_artifact.tiers[tier]
    selected, _ratio = competition.select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=guard.safety_ratio,
    )
    if tier == "premium":
        selected, _ratio = competition.fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=hybrid_artifact.premium_ax31_fill_safety,
        )
    submission = Submission(
        schema_version=inputs.schema_version,
        challenge_id=inputs.challenge_id,
        policy_id=policy.policy_id,
        split=inputs.split,
        tier=tier,
        decisions=tuple(
            Decision(episode.episode_id, model_id)
            for episode, model_id in zip(inputs.episodes, selected)
        ),
    )
    return parse_submission(submission_to_dict(submission))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hybrid-router-run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--ridge-artifact", type=Path)
    parser.add_argument("--hybrid-artifact", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs = load_input(args.input)
        policy = load_policy(args.policy) if args.policy else load_bundled_policy()
        ridge_artifact = competition.load_artifact(args.ridge_artifact)
        hybrid_artifact = load_artifact(args.hybrid_artifact)
        submission = make_submission(
            inputs, policy, ridge_artifact, hybrid_artifact, args.tier
        )
        write_submission_atomic(args.output, submission)
    except (OSError, ProtocolError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} hybrid 제출 파일을 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
