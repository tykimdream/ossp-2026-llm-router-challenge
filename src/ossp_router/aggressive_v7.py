# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""V7 router with Train-calibrated content-shift and small-batch reserves."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from . import aggressive_v5, aggressive_v6, competition
from .protocol import (
    MODEL_IDS,
    TIERS,
    Decision,
    Episode,
    InputBatch,
    ProtocolError,
    RoutingPolicy,
    Submission,
    parse_submission,
    policy_sha256,
    submission_to_dict,
)


ARTIFACT_TYPE = "ossp-adaptive-safety-profile-v7"
ROUTER_VERSION = "7.0-adaptive-budget"
DEFAULT_PROFILE = "adaptive-safety.v7.json"
SIGNAL_NAMES = (
    "korean_hangul_10pct",
    "code_marker",
    "math_or_numeric",
    "long_context_gt_8000",
    "short_context_le_100",
    "messages",
)
_DENSE_INDEX = {
    name: index for index, name in enumerate(competition.DENSE_FEATURE_NAMES)
}


@dataclass(frozen=True)
class AdaptiveSafetyProfile:
    reference_rates: Mapping[str, float]
    stable_batch_size: int
    composition_penalty: float
    small_batch_penalty: float
    calibration: Mapping[str, Any]


@dataclass(frozen=True)
class V7Artifact:
    base: aggressive_v6.V6Artifact
    profile: AdaptiveSafetyProfile

    @property
    def policy_id(self) -> str:
        return self.base.policy_id

    @property
    def policy_digest(self) -> str:
        return self.base.policy_digest

    @property
    def hash_bins(self) -> int:
        return self.base.hash_bins


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{label}은 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError(f"{label}은 유한한 숫자여야 합니다.")
    return result


def parse_profile(root: Any) -> AdaptiveSafetyProfile:
    if not isinstance(root, dict):
        raise ProtocolError("adaptive safety profile은 객체여야 합니다.")
    expected = {
        "artifact_type",
        "schema_version",
        "signal_names",
        "reference_rates",
        "stable_batch_size",
        "composition_penalty",
        "small_batch_penalty",
        "calibration",
    }
    if set(root) != expected:
        raise ProtocolError("adaptive safety profile 필드가 올바르지 않습니다.")
    if root["artifact_type"] != ARTIFACT_TYPE or root["schema_version"] != 1:
        raise ProtocolError("adaptive safety profile 버전이 올바르지 않습니다.")
    if root["signal_names"] != list(SIGNAL_NAMES):
        raise ProtocolError("adaptive safety signal 순서가 올바르지 않습니다.")
    raw_rates = root["reference_rates"]
    if not isinstance(raw_rates, dict) or set(raw_rates) != set(SIGNAL_NAMES):
        raise ProtocolError("adaptive safety reference rate가 올바르지 않습니다.")
    rates = {name: _number(raw_rates[name], name) for name in SIGNAL_NAMES}
    if any(not 0.0 <= value <= 1.0 for value in rates.values()):
        raise ProtocolError("adaptive safety reference rate는 0~1이어야 합니다.")
    stable_batch_size = root["stable_batch_size"]
    if isinstance(stable_batch_size, bool) or not isinstance(stable_batch_size, int):
        raise ProtocolError("stable_batch_size는 정수여야 합니다.")
    composition_penalty = _number(
        root["composition_penalty"], "composition_penalty"
    )
    small_batch_penalty = _number(
        root["small_batch_penalty"], "small_batch_penalty"
    )
    if (
        stable_batch_size < 1
        or composition_penalty < 0.0
        or small_batch_penalty < 0.0
    ):
        raise ProtocolError("adaptive safety 보정값은 음수일 수 없습니다.")
    calibration = root["calibration"]
    if not isinstance(calibration, dict):
        raise ProtocolError("adaptive safety calibration은 객체여야 합니다.")
    return AdaptiveSafetyProfile(
        rates,
        stable_batch_size,
        composition_penalty,
        small_batch_penalty,
        calibration,
    )


def load_profile(path: Optional[Path] = None) -> AdaptiveSafetyProfile:
    if path is None:
        text = (
            resources.files("ossp_router.resources")
            .joinpath(DEFAULT_PROFILE)
            .read_text(encoding="utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    return parse_profile(json.loads(text))


def load_artifact(
    profile_path: Optional[Path] = None,
    base_path: Optional[Path] = None,
) -> V7Artifact:
    return V7Artifact(
        aggressive_v6.load_artifact(base_path),
        load_profile(profile_path),
    )


def learned_path_allowed(inputs: InputBatch) -> bool:
    return aggressive_v6.learned_path_allowed(inputs)


def signal_row(episode: Episode, raw: Sequence[float]) -> Tuple[bool, ...]:
    return (
        raw[_DENSE_INDEX["hangul_ratio"]] >= 0.1,
        raw[_DENSE_INDEX["log_code_marker_count"]] > 0.0,
        raw[_DENSE_INDEX["log_math_marker_count"]] > 0.0
        or raw[_DENSE_INDEX["numeric_density"]] >= 0.08,
        raw[_DENSE_INDEX["length_over_8000"]] > 0.0,
        raw[_DENSE_INDEX["length_le_100"]] > 0.0,
        episode.messages is not None,
    )


def safety_multiplier(
    rows: Sequence[Sequence[bool]], profile: AdaptiveSafetyProfile
) -> float:
    if not rows:
        raise ValueError("adaptive safety batch가 비어 있습니다.")
    rates = {
        name: math.fsum(float(row[index]) for row in rows) / len(rows)
        for index, name in enumerate(SIGNAL_NAMES)
    }
    maximum_shift = max(
        abs(rates[name] - profile.reference_rates[name]) for name in SIGNAL_NAMES
    )
    size_shift = max(
        0.0,
        math.sqrt(profile.stable_batch_size / len(rows)) - 1.0,
    )
    return math.exp(
        -profile.composition_penalty * maximum_shift
        -profile.small_batch_penalty * size_shift
    )


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    artifact: V7Artifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    if (
        artifact.policy_id != policy.policy_id
        or artifact.policy_digest != policy_sha256(policy)
    ):
        raise ProtocolError("aggressive v7 artifact와 실행 정책이 일치하지 않습니다.")

    cache: Dict[
        Tuple[object, ...],
        Tuple[Mapping[str, float], Mapping[str, float], Tuple[bool, ...]],
    ] = {}
    predictions = []
    for episode in inputs.episodes:
        key = aggressive_v6._content_key(episode)
        prediction = cache.get(key)
        if prediction is None:
            raw = aggressive_v6.raw_feature_vector(episode, artifact.hash_bins)
            scores, costs = aggressive_v6.predict_raw(raw, artifact.base, tier)
            prediction = (scores, costs, signal_row(episode, raw))
            cache[key] = prediction
        predictions.append(prediction)
    scores = [dict(item[0]) for item in predictions]
    costs = [item[1] for item in predictions]
    multiplier = safety_multiplier([item[2] for item in predictions], artifact.profile)
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, _ratio = aggressive_v5._select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=artifact.base.base.tiers[tier].safety_ratio * multiplier,
        steps=artifact.base.base.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, _ratio = aggressive_v5._fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=(
                artifact.base.base.premium_fill_safety_ratio * multiplier
            ),
            steps=artifact.base.base.fixed_bisection_steps,
        )
    routed = Submission(
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
    return parse_submission(submission_to_dict(routed))
