# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""V9 router that selectively recovers V7's static budget reserve."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from . import aggressive_v5, aggressive_v6, aggressive_v7
from .protocol import (
    MODEL_IDS,
    TIERS,
    Decision,
    InputBatch,
    ProtocolError,
    RoutingPolicy,
    Submission,
    load_json,
    parse_submission,
    policy_sha256,
    submission_to_dict,
)


ARTIFACT_TYPE = "ossp-selective-budget-router-v9"
ROUTER_VERSION = "9.0-selective-budget"
DEFAULT_ARTIFACT = "selective-budget.v9.json"


@dataclass(frozen=True)
class SelectiveBudgetProfile:
    policy_id: str
    policy_digest: str
    hash_bins: int
    uplift_heads: Mapping[str, aggressive_v6.RawLinearHead]
    ridge_alpha: float
    blend_weight: float
    threshold: float
    recovery_fraction: float
    training_summary: Mapping[str, Any]


@dataclass(frozen=True)
class V9Artifact:
    base: aggressive_v7.V7Artifact
    selective: SelectiveBudgetProfile

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
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ProtocolError(f"{label}은 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError(f"{label}은 유한해야 합니다.")
    return result


def _head(value: Any, label: str, feature_count: int) -> aggressive_v6.RawLinearHead:
    if not isinstance(value, dict) or set(value) != {"intercept", "coefficients"}:
        raise ProtocolError(f"{label} 형식이 올바르지 않습니다.")
    coefficients = value["coefficients"]
    if not isinstance(coefficients, list) or len(coefficients) != feature_count:
        raise ProtocolError(f"{label}.coefficients 길이가 올바르지 않습니다.")
    return aggressive_v6.RawLinearHead(
        _number(value["intercept"], f"{label}.intercept"),
        tuple(_number(item, f"{label}.coefficients") for item in coefficients),
    )


def parse_profile(value: Any) -> SelectiveBudgetProfile:
    if not isinstance(value, dict):
        raise ProtocolError("V9 selective budget artifact는 객체여야 합니다.")
    expected = {
        "artifact_type",
        "schema_version",
        "policy_id",
        "policy_sha256",
        "hash_bins",
        "uplift_heads",
        "ridge_alpha",
        "blend_weight",
        "threshold",
        "recovery_fraction",
        "training_summary",
    }
    if set(value) != expected:
        raise ProtocolError("V9 selective budget artifact 필드가 올바르지 않습니다.")
    if value["artifact_type"] != ARTIFACT_TYPE or value["schema_version"] != 1:
        raise ProtocolError("지원하지 않는 V9 selective budget artifact입니다.")
    policy_id = value["policy_id"]
    digest = value["policy_sha256"]
    hash_bins = value["hash_bins"]
    if not isinstance(policy_id, str) or not isinstance(digest, str):
        raise ProtocolError("V9 정책 정보가 올바르지 않습니다.")
    if (
        isinstance(hash_bins, bool)
        or not isinstance(hash_bins, int)
        or hash_bins < 16
        or hash_bins & (hash_bins - 1)
    ):
        raise ProtocolError("V9 hash_bins가 올바르지 않습니다.")
    heads = value["uplift_heads"]
    expected_heads = set(MODEL_IDS[1:])
    if not isinstance(heads, dict) or set(heads) != expected_heads:
        raise ProtocolError("V9 uplift head 구성이 올바르지 않습니다.")
    feature_count = len(aggressive_v6.competition.DENSE_FEATURE_NAMES) + hash_bins
    blend_weight = _number(value["blend_weight"], "blend_weight")
    threshold = _number(value["threshold"], "threshold")
    recovery_fraction = _number(value["recovery_fraction"], "recovery_fraction")
    if (
        not 0.0 <= blend_weight <= 1.0
        or threshold < 0.0
        or not 0.0 <= recovery_fraction <= 1.0
    ):
        raise ProtocolError("V9 선택 파라미터 범위가 올바르지 않습니다.")
    summary = value["training_summary"]
    if not isinstance(summary, dict):
        raise ProtocolError("V9 training_summary는 객체여야 합니다.")
    return SelectiveBudgetProfile(
        policy_id,
        digest,
        hash_bins,
        {
            model_id: _head(heads[model_id], model_id, feature_count)
            for model_id in MODEL_IDS[1:]
        },
        _number(value["ridge_alpha"], "ridge_alpha"),
        blend_weight,
        threshold,
        recovery_fraction,
        dict(summary),
    )


def load_profile(path: Optional[Path] = None) -> SelectiveBudgetProfile:
    if path is None:
        value = json.loads(
            resources.files("ossp_router.resources")
            .joinpath(DEFAULT_ARTIFACT)
            .read_text(encoding="utf-8")
        )
    else:
        value = load_json(path)
    return parse_profile(value)


def load_artifact(
    selective_path: Optional[Path] = None,
    profile_path: Optional[Path] = None,
    base_path: Optional[Path] = None,
) -> V9Artifact:
    base = aggressive_v7.load_artifact(profile_path, base_path)
    selective = load_profile(selective_path)
    if (
        selective.policy_id != base.policy_id
        or selective.policy_digest != base.policy_digest
        or selective.hash_bins != base.hash_bins
    ):
        raise ProtocolError("V9 selective head와 V7 base artifact가 일치하지 않습니다.")
    return V9Artifact(base, selective)


def learned_path_allowed(inputs: InputBatch) -> bool:
    return aggressive_v7.learned_path_allowed(inputs)


def predict_uplifts(
    raw: Sequence[float], profile: SelectiveBudgetProfile
) -> Mapping[str, float]:
    return {
        model_id: aggressive_v6._linear(head, raw)
        for model_id, head in profile.uplift_heads.items()
    }


def selective_upgrades(
    selected: Sequence[str],
    scores: Sequence[Mapping[str, float]],
    costs: Sequence[Mapping[str, float]],
    uplifts: Sequence[Mapping[str, float]],
    *,
    tier: str,
    budget_multiplier: float,
    base_safety_ratio: float,
    safety_multiplier: float,
    profile: SelectiveBudgetProfile,
) -> Tuple[Tuple[str, ...], float]:
    if not selected or not (len(selected) == len(scores) == len(costs) == len(uplifts)):
        raise ValueError("V9 선택 배열의 길이가 올바르지 않습니다.")
    light_id, ax31_id, k1_id = MODEL_IDS
    light_total = math.fsum(row[light_id] for row in costs)
    current_total = math.fsum(row[model_id] for row, model_id in zip(costs, selected))
    recovered_safety = base_safety_ratio + profile.recovery_fraction * (
        1.0 - base_safety_ratio
    )
    cap = light_total * max(
        1.0, budget_multiplier * recovered_safety * safety_multiplier
    )
    choices = []
    for index, (current, score_row, cost_row, uplift_row) in enumerate(
        zip(selected, scores, costs, uplifts)
    ):
        if current == light_id:
            target = ax31_id
            direct_gain = uplift_row[ax31_id]
        elif current == ax31_id and tier != "fast":
            target = k1_id
            direct_gain = uplift_row[k1_id] - uplift_row[ax31_id]
        else:
            continue
        baseline_gain = score_row[target] - score_row[current]
        gain = (
            profile.blend_weight * direct_gain
            + (1.0 - profile.blend_weight) * baseline_gain
        )
        extra = cost_row[target] - cost_row[current]
        if gain >= profile.threshold and extra > 0.0:
            choices.append((gain / extra, gain, -index, index, target, extra))
    result = list(selected)
    for _utility, _gain, _tie, index, target, extra in sorted(choices, reverse=True):
        if current_total + extra <= cap:
            result[index] = target
            current_total += extra
    return tuple(result), current_total / light_total


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    artifact: V9Artifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    if (
        artifact.policy_id != policy.policy_id
        or artifact.policy_digest != policy_sha256(policy)
    ):
        raise ProtocolError("aggressive v9 artifact와 실행 정책이 일치하지 않습니다.")

    cache: Dict[
        Tuple[object, ...],
        Tuple[
            Mapping[str, float],
            Mapping[str, float],
            Tuple[bool, ...],
            Mapping[str, float],
        ],
    ] = {}
    predictions = []
    for episode in inputs.episodes:
        key = aggressive_v6._content_key(episode)
        prediction = cache.get(key)
        if prediction is None:
            raw = aggressive_v6.raw_feature_vector(episode, artifact.hash_bins)
            scores, costs = aggressive_v6.predict_raw(raw, artifact.base.base, tier)
            prediction = (
                scores,
                costs,
                aggressive_v7.signal_row(episode, raw),
                predict_uplifts(raw, artifact.selective),
            )
            cache[key] = prediction
        predictions.append(prediction)
    scores = [dict(item[0]) for item in predictions]
    costs = [item[1] for item in predictions]
    multiplier = aggressive_v7.safety_multiplier(
        [item[2] for item in predictions], artifact.base.profile
    )
    if tier == "fast":
        for row in scores:
            row[MODEL_IDS[2]] = -2.0
    selected, _ratio = aggressive_v5._select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=artifact.base.base.base.tiers[tier].safety_ratio * multiplier,
        steps=artifact.base.base.base.fixed_bisection_steps,
    )
    if tier == "premium":
        selected, _ratio = aggressive_v5._fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=(
                artifact.base.base.base.premium_fill_safety_ratio * multiplier
            ),
            steps=artifact.base.base.base.fixed_bisection_steps,
        )
    selected, _ratio = selective_upgrades(
        selected,
        scores,
        costs,
        [item[3] for item in predictions],
        tier=tier,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        base_safety_ratio=artifact.base.base.base.tiers[tier].safety_ratio,
        safety_multiplier=multiplier,
        profile=artifact.selective,
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
