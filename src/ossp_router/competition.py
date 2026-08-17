# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Content-only learned router used by the competition submission."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

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


ARTIFACT_TYPE = "ossp-competition-linear-v1"
# v4 won the fair Train-only -> Dev gate and is the immutable submission default.
DEFAULT_ARTIFACT = "risk-router.v4.json"
FEATURE_VERSION = 1
HASH_BINS = 256
PREMIUM_AX31_FILL_SAFETY = 0.65
_FNV_OFFSET = 14_695_981_039_346_656_037
_FNV_PRIME = 1_099_511_628_211
_UINT64_MASK = (1 << 64) - 1

_TOKEN = re.compile(r"[A-Za-z]+|[가-힣]+|\d+|[^\w\s]", re.UNICODE)
_NUMBER_RUN = re.compile(r"\d+(?:[.,]\d+)*")
_SPACE = re.compile(r"\s+")
_CHOICE = re.compile(r"(?:^|\n)\s*[A-D][.)]\s", re.MULTILINE)
_FORMAL_REASONING = re.compile(
    r"\b(?:prove|derive|theorem|lemma|counterexample|induction|"
    r"증명|유도|정리|보조정리|반례|귀납)\b",
    re.IGNORECASE,
)
_PROGRAM_ANALYSIS = re.compile(
    r"```|\b(?:traceback|exception|complexity|big[- ]?o|"
    r"시간\s*복잡도|공간\s*복잡도|예외|스택\s*추적)\b",
    re.IGNORECASE,
)
_MULTI_CONSTRAINT = re.compile(
    r"\b(?:exactly|at least|at most|must|only|without|"
    r"정확히|이상|이하|반드시|오직|제외하고)\b",
    re.IGNORECASE,
)
_SIMPLE_TRANSFORM = re.compile(
    r"\b(?:summari[sz]e|rewrite|translate|list|extract|"
    r"요약|바꾸|번역|나열|추출)\b",
    re.IGNORECASE,
)
_ARITHMETIC_REQUEST = re.compile(
    r"\b(?:calculate|compute|evaluate|round|divided by|minus|plus|"
    r"nearest|what is|계산|반올림|나누|더하|빼기)\b",
    re.IGNORECASE,
)
_LOGIC_RULES = re.compile(
    r"\b(?:if someone|if something|all \w+ people|question:)\b",
    re.IGNORECASE,
)

DENSE_FEATURE_NAMES = (
    "log_character_count",
    "log_word_count",
    "log_sentence_count",
    "log_message_count",
    "hangul_ratio",
    "log_code_marker_count",
    "log_math_marker_count",
    "numeric_density",
    "long_context",
    "log_reasoning_marker_count",
    "formal_reasoning",
    "program_analysis",
    "log_multi_constraint_count",
    "simple_transform",
    "log_line_count",
    "ascii_ratio",
    "log_digit_count",
    "log_choice_count",
    "has_question_label",
    "has_assert_hole",
    "has_python_function",
    "has_logic_rules",
    "has_arithmetic_request",
    "has_latex",
    "has_binary_choices",
    "length_le_60",
    "length_le_100",
    "length_101_500",
    "length_501_2000",
    "length_over_2000",
    "length_over_8000",
    "korean_multiple_choice",
    "short_code",
    "short_math_numeric",
    "multi_message",
    "quote_density",
    "newline_density",
    "ends_with_question",
    "contains_answer_placeholder",
    "contains_currency_or_percent",
)


@dataclass(frozen=True)
class LinearHead:
    intercept: float
    coefficients: Tuple[float, ...]


@dataclass(frozen=True)
class CompetitionArtifact:
    hash_bins: int
    feature_mean: Tuple[float, ...]
    feature_scale: Tuple[float, ...]
    score_heads: Mapping[str, LinearHead]
    log_cost_heads: Mapping[str, LinearHead]
    tier_safety_ratios: Mapping[str, float]
    policy_id: str
    policy_digest: str
    training_summary: Mapping[str, Any]


def stable_hash(value: str) -> int:
    digest = _FNV_OFFSET
    for byte in value.encode("utf-8"):
        digest ^= byte
        digest = (digest * _FNV_PRIME) & _UINT64_MASK
    return digest


def normalized_template(text: str) -> str:
    """Normalize values and whitespace for template-group validation folds."""

    return _SPACE.sub(" ", _NUMBER_RUN.sub("<number>", text.casefold())).strip()


def _hashed_features(text: str, hash_bins: int) -> Tuple[float, ...]:
    tokens = []
    for token in _TOKEN.findall(text):
        normalized = token.casefold()
        if normalized.isdecimal():
            normalized = "<number>"
        tokens.append(normalized)
    values = [f"w1:{token}" for token in tokens]
    values.extend(
        f"w2:{left}\x1f{right}" for left, right in zip(tokens, tokens[1:])
    )
    values.extend(
        f"w3:{left}\x1f{middle}\x1f{right}"
        for left, middle, right in zip(tokens, tokens[1:], tokens[2:])
    )
    characters = normalized_template(text)
    if len(characters) > 4_000:
        characters = characters[:3_000] + characters[-1_000:]
    for size in (3, 4):
        values.extend(
            f"c{size}:{characters[index:index + size]}"
            for index in range(0, max(0, len(characters) - size + 1), 2)
        )
    bins = [0.0] * hash_bins
    for value in values:
        digest = stable_hash(value)
        index = digest & (hash_bins - 1)
        bins[index] += -1.0 if digest & (1 << 63) else 1.0
    norm = math.sqrt(math.fsum(value * value for value in bins))
    if norm:
        bins = [value / norm for value in bins]
    return tuple(bins)


def raw_feature_vector(episode: Episode, hash_bins: int = HASH_BINS) -> Tuple[float, ...]:
    if hash_bins < 16 or hash_bins & (hash_bins - 1):
        raise ValueError("hash_bins는 16 이상의 2의 거듭제곱이어야 합니다.")
    base = extract_features(episode)
    text = episode_text(episode)
    characters = len(text)
    nonspace = max(1, sum(not character.isspace() for character in text))
    ascii_count = sum(ord(character) < 128 for character in text)
    digit_count = sum(character.isdigit() for character in text)
    choice_count = len(_CHOICE.findall(text))
    lower = text.casefold()
    math_numeric = base.math_marker_count > 0 or base.numeric_density >= 0.08
    code = base.code_marker_count > 0
    dense = (
        math.log1p(base.character_count),
        math.log1p(base.word_count),
        math.log1p(base.sentence_count),
        math.log1p(base.message_count),
        base.hangul_ratio,
        math.log1p(base.code_marker_count),
        math.log1p(base.math_marker_count),
        base.numeric_density,
        float(base.long_context),
        math.log1p(base.reasoning_marker_count),
        float(bool(_FORMAL_REASONING.search(text))),
        float(bool(_PROGRAM_ANALYSIS.search(text))),
        math.log1p(len(_MULTI_CONSTRAINT.findall(text))),
        float(bool(_SIMPLE_TRANSFORM.search(text))),
        math.log1p(text.count("\n") + 1),
        ascii_count / max(1, characters),
        math.log1p(digit_count),
        math.log1p(choice_count),
        float("question:" in lower),
        float("assert f(" in lower and "??" in text),
        float(bool(re.search(r"(?:^|\n)\s*(?:def|class)\s+", text))),
        float(bool(_LOGIC_RULES.search(text))),
        float(bool(_ARITHMETIC_REQUEST.search(text))),
        float("\\frac" in text or "\\boxed" in text or "$$" in text),
        float(choice_count == 2),
        float(characters <= 60),
        float(characters <= 100),
        float(101 <= characters <= 500),
        float(501 <= characters <= 2_000),
        float(characters > 2_000),
        float(characters > 8_000),
        float(base.hangul_ratio >= 0.1 and choice_count >= 2),
        float(code and characters <= 500),
        float(math_numeric and characters <= 150),
        float(episode.messages is not None and len(episode.messages) > 1),
        (text.count('"') + text.count("'")) / nonspace,
        text.count("\n") / nonspace,
        float(text.rstrip().endswith("?")),
        float("??" in text or "[MASK]" in text),
        float("$" in text or "%" in text),
    )
    assert len(dense) == len(DENSE_FEATURE_NAMES)
    return dense + _hashed_features(text, hash_bins)


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label}은(는) JSON 객체여야 합니다.")
    return value


def _vector(value: Any, length: int, label: str) -> Tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ProtocolError(f"{label} 길이가 올바르지 않습니다.")
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        raise ProtocolError(f"{label}에 유한하지 않은 값이 있습니다.")
    return result


def _head(value: Any, length: int, label: str) -> LinearHead:
    raw = _object(value, label)
    if set(raw) != {"intercept", "coefficients"}:
        raise ProtocolError(f"{label} 필드가 올바르지 않습니다.")
    intercept = float(raw["intercept"])
    if not math.isfinite(intercept):
        raise ProtocolError(f"{label}.intercept가 유한하지 않습니다.")
    return LinearHead(intercept, _vector(raw["coefficients"], length, label))


def parse_artifact(value: Any) -> CompetitionArtifact:
    root = _object(value, "artifact")
    if root.get("artifact_type") != ARTIFACT_TYPE:
        raise ProtocolError("지원하지 않는 competition artifact입니다.")
    if root.get("feature_version") != FEATURE_VERSION:
        raise ProtocolError("지원하지 않는 feature_version입니다.")
    if root.get("dense_feature_names") != list(DENSE_FEATURE_NAMES):
        raise ProtocolError("artifact와 런타임 특징 정의가 다릅니다.")
    if root.get("model_ids") != list(MODEL_IDS):
        raise ProtocolError("artifact 모델 목록이 올바르지 않습니다.")
    hash_bins = int(root.get("hash_bins", 0))
    if hash_bins < 16 or hash_bins & (hash_bins - 1):
        raise ProtocolError("artifact hash_bins가 올바르지 않습니다.")
    length = len(DENSE_FEATURE_NAMES) + hash_bins
    mean = _vector(root.get("feature_mean"), length, "feature_mean")
    scale = _vector(root.get("feature_scale"), length, "feature_scale")
    if any(item <= 0 for item in scale):
        raise ProtocolError("feature_scale은 0보다 커야 합니다.")
    score_raw = _object(root.get("score_heads"), "score_heads")
    cost_raw = _object(root.get("log_cost_heads"), "log_cost_heads")
    if set(score_raw) != set(MODEL_IDS) or set(cost_raw) != set(MODEL_IDS):
        raise ProtocolError("artifact head 모델 집합이 올바르지 않습니다.")
    safety_raw = _object(root.get("tier_safety_ratios"), "tier_safety_ratios")
    safety = {tier: float(safety_raw[tier]) for tier in TIERS}
    if any(not 0 < value <= 1 for value in safety.values()):
        raise ProtocolError("artifact 안전계수가 올바르지 않습니다.")
    policy_id = root.get("policy_id")
    policy_digest = root.get("policy_sha256")
    if not isinstance(policy_id, str) or not isinstance(policy_digest, str):
        raise ProtocolError("artifact 정책 정보가 올바르지 않습니다.")
    return CompetitionArtifact(
        hash_bins=hash_bins,
        feature_mean=mean,
        feature_scale=scale,
        score_heads={m: _head(score_raw[m], length, f"score_heads.{m}") for m in MODEL_IDS},
        log_cost_heads={m: _head(cost_raw[m], length, f"log_cost_heads.{m}") for m in MODEL_IDS},
        tier_safety_ratios=safety,
        policy_id=policy_id,
        policy_digest=policy_digest,
        training_summary=dict(_object(root.get("training_summary"), "training_summary")),
    )


def load_artifact(path: Optional[Path] = None) -> CompetitionArtifact:
    if path is not None:
        return parse_artifact(load_json(path))
    try:
        text = resources.read_text(
            "ossp_router.resources", DEFAULT_ARTIFACT, encoding="utf-8"
        )
    except (OSError, UnicodeError) as exc:
        raise ProtocolError(f"내장 competition artifact를 읽을 수 없습니다: {exc}") from exc
    return parse_artifact(json.loads(text))


def _linear(head: LinearHead, values: Sequence[float]) -> float:
    return head.intercept + math.fsum(
        coefficient * value for coefficient, value in zip(head.coefficients, values)
    )


def predict_linear_features(
    raw: Sequence[float], artifact: CompetitionArtifact
) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """Return unbounded score and log-cost heads for a precomputed feature row."""

    expected = len(artifact.feature_mean)
    if len(raw) != expected:
        raise ValueError("artifact와 입력 특징 길이가 다릅니다.")
    values = tuple(
        (value - mean) / scale
        for value, mean, scale in zip(
            raw, artifact.feature_mean, artifact.feature_scale
        )
    )
    return (
        tuple(_linear(artifact.score_heads[model_id], values) for model_id in MODEL_IDS),
        tuple(
            _linear(artifact.log_cost_heads[model_id], values)
            for model_id in MODEL_IDS
        ),
    )


def predict_episode(
    episode: Episode, artifact: CompetitionArtifact
) -> Tuple[Mapping[str, float], Mapping[str, float]]:
    raw = raw_feature_vector(episode, artifact.hash_bins)
    score_values, log_cost_values = predict_linear_features(raw, artifact)
    scores = {
        model_id: min(1.0, max(0.0, score_values[index]))
        for index, model_id in enumerate(MODEL_IDS)
    }
    costs = {
        model_id: math.exp(
            min(50.0, max(-50.0, log_cost_values[index]))
        )
        for index, model_id in enumerate(MODEL_IDS)
    }
    light = costs[MODEL_IDS[0]]
    costs[MODEL_IDS[1]] = max(costs[MODEL_IDS[1]], light * (1.0 + 1e-12))
    costs[MODEL_IDS[2]] = max(costs[MODEL_IDS[2]], costs[MODEL_IDS[1]] * (1.0 + 1e-12))
    return scores, costs


def select_models(
    predicted_scores: Sequence[Mapping[str, float]],
    predicted_costs: Sequence[Mapping[str, float]],
    *,
    budget_multiplier: float,
    safety_ratio: float,
) -> Tuple[Tuple[str, ...], float]:
    if len(predicted_scores) != len(predicted_costs) or not predicted_scores:
        raise ValueError("예측 배열의 길이가 올바르지 않습니다.")
    light_total = math.fsum(row[MODEL_IDS[0]] for row in predicted_costs)
    cap = light_total * max(1.0, budget_multiplier * safety_ratio)

    def choose(penalty: float) -> Tuple[Tuple[str, ...], float]:
        selected = tuple(
            max(
                MODEL_IDS,
                key=lambda model_id: (
                    scores[model_id] - penalty * costs[model_id] / light_total,
                    -MODEL_IDS.index(model_id),
                ),
            )
            for scores, costs in zip(predicted_scores, predicted_costs)
        )
        total = math.fsum(
            costs[model_id]
            for costs, model_id in zip(predicted_costs, selected)
        )
        return selected, total

    selected, total = choose(0.0)
    if total > cap:
        low, high = 0.0, 1.0
        selected, total = choose(high)
        while total > cap and high < 2**60:
            low, high = high, high * 2.0
            selected, total = choose(high)
        for _ in range(80):
            middle = (low + high) / 2.0
            candidate, candidate_total = choose(middle)
            if candidate_total <= cap:
                high, selected, total = middle, candidate, candidate_total
            else:
                low = middle
    if total > cap:
        selected = tuple(MODEL_IDS[0] for _ in predicted_scores)
        total = light_total
    return selected, total / light_total


def fill_ax31(
    selected: Sequence[str],
    scores: Sequence[Mapping[str, float]],
    costs: Sequence[Mapping[str, float]],
    *,
    budget_multiplier: float,
    safety_ratio: float,
) -> Tuple[Tuple[str, ...], float]:
    light_id, ax31_id, _ = MODEL_IDS
    light_total = math.fsum(row[light_id] for row in costs)
    current_total = math.fsum(
        row[model_id] for row, model_id in zip(costs, selected)
    )
    cap = max(current_total, light_total * budget_multiplier * safety_ratio)

    def choose(penalty: float) -> Tuple[Tuple[str, ...], float]:
        result = []
        for current, score_row, cost_row in zip(selected, scores, costs):
            if current != light_id:
                result.append(current)
                continue
            gain = score_row[ax31_id] - score_row[light_id]
            extra = cost_row[ax31_id] - cost_row[light_id]
            result.append(ax31_id if gain - penalty * extra / light_total > 0 else light_id)
        total = math.fsum(row[m] for row, m in zip(costs, result))
        return tuple(result), total

    result, total = choose(0.0)
    if total > cap:
        low, high = 0.0, 1.0
        result, total = choose(high)
        while total > cap and high < 2**60:
            low, high = high, high * 2.0
            result, total = choose(high)
        for _ in range(80):
            middle = (low + high) / 2.0
            candidate, candidate_total = choose(middle)
            if candidate_total <= cap:
                high, result, total = middle, candidate, candidate_total
            else:
                low = middle
    if total > cap:
        return tuple(selected), current_total / light_total
    return result, total / light_total


def make_submission(
    inputs: InputBatch,
    policy: RoutingPolicy,
    artifact: CompetitionArtifact,
    tier: str,
) -> Submission:
    if tier not in TIERS:
        raise ProtocolError(f"알 수 없는 tier: {tier}")
    if artifact.policy_id != policy.policy_id or artifact.policy_digest != policy_sha256(policy):
        raise ProtocolError("artifact와 실행 정책이 일치하지 않습니다.")
    predictions = [predict_episode(episode, artifact) for episode in inputs.episodes]
    scores = [item[0] for item in predictions]
    costs = [item[1] for item in predictions]
    selected, _ = select_models(
        scores,
        costs,
        budget_multiplier=float(policy.tiers[tier].budget_multiplier),
        safety_ratio=artifact.tier_safety_ratios[tier],
    )
    if tier == "premium":
        selected, _ = fill_ax31(
            selected,
            scores,
            costs,
            budget_multiplier=float(policy.tiers[tier].budget_multiplier),
            safety_ratio=PREMIUM_AX31_FILL_SAFETY,
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
    parser = argparse.ArgumentParser(prog="router-run")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=TIERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--artifact", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs = load_input(args.input)
        policy = load_policy(args.policy) if args.policy else load_bundled_policy()
        artifact = load_artifact(args.artifact)
        submission = make_submission(inputs, policy, artifact, args.tier)
        write_submission_atomic(args.output, submission)
    except (OSError, ProtocolError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.tier} 제출 파일을 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
