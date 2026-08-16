# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Summarize public prompt, score, token, and cost distributions."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from ossp_router.heuristic import episode_text, extract_features
from ossp_router.protocol import (
    MODEL_IDS,
    Episode,
    Outcome,
    RoutingPolicy,
    load_bundled_policy,
    load_input,
    load_outcomes,
)


_MULTIPLE_CHOICE = re.compile(r"(?:^|\n)\s*[A-D][.)]\s", re.MULTILINE)
_FORMAL_REASONING = re.compile(
    r"\b(?:prove|derive|theorem|lemma|counterexample|induction|"
    r"증명|유도|정리|보조정리|반례|귀납)\b",
    re.IGNORECASE,
)
_SIMPLE_TRANSFORM = re.compile(
    r"\b(?:summari[sz]e|rewrite|translate|list|extract|"
    r"요약|바꾸|번역|나열|추출)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AnalysisRow:
    episode: Episode
    text: str
    outcomes: Mapping[str, Outcome]
    costs: Mapping[str, Decimal]


def _cost(outcome: Outcome, policy: RoutingPolicy) -> Decimal:
    rates = policy.models[outcome.model_id]
    unit = Decimal(policy.token_unit)
    return (
        rates.fixed_cost
        + Decimal(outcome.input_tokens) * rates.input_token_rate / unit
        + Decimal(outcome.output_tokens) * rates.output_token_rate / unit
    )


def _mean(values: Iterable[Decimal]) -> Decimal:
    items = tuple(values)
    return sum(items, Decimal("0")) / Decimal(len(items))


def _number(value: Decimal) -> float:
    return round(float(value), 6)


def _percent(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _load_rows(
    input_path: Path, outcome_path: Path, policy: RoutingPolicy
) -> Tuple[str, List[AnalysisRow]]:
    inputs = load_input(input_path)
    outcomes = load_outcomes(outcome_path)
    if inputs.schema_version != outcomes.schema_version:
        raise ValueError("입력과 outcome의 schema_version이 다릅니다.")
    if inputs.challenge_id != outcomes.challenge_id or inputs.split != outcomes.split:
        raise ValueError("입력과 outcome의 실행 메타데이터가 다릅니다.")
    outcome_index = {
        (outcome.episode_id, outcome.model_id): outcome
        for outcome in outcomes.outcomes
    }
    expected = {
        (episode.episode_id, model_id)
        for episode in inputs.episodes
        for model_id in MODEL_IDS
    }
    if set(outcome_index) != expected:
        raise ValueError("outcome 행렬이 모든 문항과 모델을 포함하지 않습니다.")
    rows = []
    for episode in inputs.episodes:
        episode_outcomes = {
            model_id: outcome_index[(episode.episode_id, model_id)]
            for model_id in MODEL_IDS
        }
        rows.append(
            AnalysisRow(
                episode=episode,
                text=episode_text(episode),
                outcomes=episode_outcomes,
                costs={
                    model_id: _cost(outcome, policy)
                    for model_id, outcome in episode_outcomes.items()
                },
            )
        )
    return inputs.split, rows


def _group_metrics(rows: Sequence[AnalysisRow]) -> Dict[str, Any]:
    light_model = "ax31-light"
    light_cost = sum((row.costs[light_model] for row in rows), Decimal("0"))
    scores = {
        model_id: _mean(row.outcomes[model_id].score for row in rows)
        for model_id in MODEL_IDS
    }
    result: Dict[str, Any] = {
        "count": len(rows),
        "average_score": {
            model_id: _number(score) for model_id, score in scores.items()
        },
        "score_gain_vs_light": {
            model_id: _number(scores[model_id] - scores[light_model])
            for model_id in MODEL_IDS
            if model_id != light_model
        },
        "total_cost_ratio_vs_light": {
            model_id: _number(
                sum((row.costs[model_id] for row in rows), Decimal("0"))
                / light_cost
            )
            for model_id in MODEL_IDS
        },
        "average_input_tokens": {
            model_id: round(
                sum(row.outcomes[model_id].input_tokens for row in rows) / len(rows),
                2,
            )
            for model_id in MODEL_IDS
        },
        "average_output_tokens": {
            model_id: round(
                sum(row.outcomes[model_id].output_tokens for row in rows) / len(rows),
                2,
            )
            for model_id in MODEL_IDS
        },
    }
    for model_id in ("ax31", "axk1-think"):
        gains = [
            row.outcomes[model_id].score - row.outcomes[light_model].score
            for row in rows
        ]
        result.setdefault("gain_direction_vs_light", {})[model_id] = {
            "positive_count": sum(gain > 0 for gain in gains),
            "zero_count": sum(gain == 0 for gain in gains),
            "negative_count": sum(gain < 0 for gain in gains),
            "positive_percent": _percent(sum(gain > 0 for gain in gains), len(gains)),
            "negative_percent": _percent(sum(gain < 0 for gain in gains), len(gains)),
        }
    return result


def _winner_counts(rows: Sequence[AnalysisRow]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        best = max(outcome.score for outcome in row.outcomes.values())
        winners = tuple(
            model_id
            for model_id in MODEL_IDS
            if row.outcomes[model_id].score == best
        )
        key = "+".join(winners)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _length_summary(rows: Sequence[AnalysisRow]) -> Dict[str, float]:
    lengths = sorted(len(row.text) for row in rows)

    def percentile(fraction: float) -> int:
        index = round((len(lengths) - 1) * fraction)
        return lengths[index]

    return {
        "minimum": lengths[0],
        "p25": percentile(0.25),
        "median": percentile(0.5),
        "p75": percentile(0.75),
        "p90": percentile(0.9),
        "p99": percentile(0.99),
        "maximum": lengths[-1],
    }


def _categories() -> Tuple[Tuple[str, Callable[[AnalysisRow], bool]], ...]:
    def features(row: AnalysisRow):
        return extract_features(row.episode)

    return (
        ("all", lambda row: True),
        ("length_0_100", lambda row: len(row.text) <= 100),
        ("length_101_500", lambda row: 101 <= len(row.text) <= 500),
        ("length_501_2000", lambda row: 501 <= len(row.text) <= 2_000),
        ("length_over_2000", lambda row: len(row.text) > 2_000),
        ("korean", lambda row: features(row).hangul_ratio >= 0.1),
        ("code", lambda row: features(row).code_marker_count > 0),
        (
            "math_or_numeric",
            lambda row: features(row).math_marker_count > 0
            or features(row).numeric_density >= 0.08,
        ),
        (
            "reasoning",
            lambda row: features(row).reasoning_marker_count > 0
            or bool(_FORMAL_REASONING.search(row.text)),
        ),
        (
            "multiple_choice",
            lambda row: bool(_MULTIPLE_CHOICE.search(row.text)),
        ),
        (
            "multi_message",
            lambda row: row.episode.messages is not None
            and len(row.episode.messages) > 1,
        ),
        ("simple_transform", lambda row: bool(_SIMPLE_TRANSFORM.search(row.text))),
    )


def _top_upgrades(
    rows: Sequence[AnalysisRow], model_id: str, limit: int = 8
) -> List[Dict[str, Any]]:
    light_model = "ax31-light"
    candidates = []
    for row in rows:
        gain = row.outcomes[model_id].score - row.outcomes[light_model].score
        if gain <= 0:
            continue
        candidates.append(
            (
                gain,
                row.costs[model_id] - row.costs[light_model],
                row,
            )
        )
    candidates.sort(key=lambda item: (-item[0], item[1], item[2].episode.episode_id))
    result = []
    for gain, extra_cost, row in candidates[:limit]:
        preview = " ".join(row.text.split())[:140]
        result.append(
            {
                "episode_id": row.episode.episode_id,
                "score_gain": _number(gain),
                "extra_cost": _number(extra_cost),
                "prompt_preview": preview,
            }
        )
    return result


def _analyze_split(split: str, rows: Sequence[AnalysisRow]) -> Dict[str, Any]:
    groups = {}
    for name, predicate in _categories():
        selected = [row for row in rows if predicate(row)]
        if selected:
            groups[name] = _group_metrics(selected)
    return {
        "split": split,
        "episode_count": len(rows),
        "prompt_character_count": _length_summary(rows),
        "winner_combinations": _winner_counts(rows),
        "groups": groups,
        "top_positive_upgrades": {
            model_id: _top_upgrades(rows, model_id)
            for model_id in ("ax31", "axk1-think")
        },
    }


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
    return "\n".join(lines)


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# 공개 Train/Dev 모델·비용 분석",
        "",
        "이 보고서는 프롬프트 내용 특징과 공개 outcome만 집계합니다. ",
        "문항 ID, split 이름 또는 입력 순서를 라우팅 특징으로 사용하지 않습니다.",
        "",
    ]
    for split_name in ("train", "dev"):
        split = report["splits"][split_name]
        all_metrics = split["groups"]["all"]
        lines.extend(
            [
                f"## {split_name.title()} ({split['episode_count']}문항)",
                "",
                "### 모델 전체 요약",
                "",
            ]
        )
        model_rows = []
        for model_id in MODEL_IDS:
            direction = all_metrics["gain_direction_vs_light"].get(model_id)
            model_rows.append(
                (
                    model_id,
                    all_metrics["average_score"][model_id],
                    all_metrics["score_gain_vs_light"].get(model_id, 0),
                    all_metrics["total_cost_ratio_vs_light"][model_id],
                    all_metrics["average_output_tokens"][model_id],
                    direction["positive_percent"] if direction else 0,
                    direction["negative_percent"] if direction else 0,
                )
            )
        lines.extend(
            [
                _markdown_table(
                    (
                        "모델",
                        "평균 점수",
                        "Light 대비 점수",
                        "총비용/Light",
                        "평균 출력 토큰",
                        "점수 상승 %",
                        "점수 하락 %",
                    ),
                    model_rows,
                ),
                "",
                "### 프롬프트 특징별 결과",
                "",
            ]
        )
        group_rows = []
        for name, metrics in split["groups"].items():
            group_rows.append(
                (
                    name,
                    metrics["count"],
                    metrics["average_score"]["ax31-light"],
                    metrics["score_gain_vs_light"]["ax31"],
                    metrics["score_gain_vs_light"]["axk1-think"],
                    metrics["gain_direction_vs_light"]["ax31"]["positive_percent"],
                    metrics["gain_direction_vs_light"]["axk1-think"]["positive_percent"],
                    metrics["total_cost_ratio_vs_light"]["ax31"],
                    metrics["total_cost_ratio_vs_light"]["axk1-think"],
                )
            )
        lines.extend(
            [
                _markdown_table(
                    (
                        "그룹",
                        "문항",
                        "Light 점수",
                        "AX31 증가",
                        "K1 증가",
                        "AX31 상승 %",
                        "K1 상승 %",
                        "AX31 비용비",
                        "K1 비용비",
                    ),
                    group_rows,
                ),
                "",
                "### 프롬프트 길이 분포",
                "",
                "```json",
                json.dumps(
                    split["prompt_character_count"],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "```",
                "",
                "### 최고 점수 모델 조합",
                "",
                "```json",
                json.dumps(
                    split["winner_combinations"],
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="공개 Train/Dev의 모델 점수와 비용 분포를 분석합니다."
    )
    parser.add_argument("--train-input", type=Path, required=True)
    parser.add_argument("--train-outcomes", type=Path, required=True)
    parser.add_argument("--dev-input", type=Path, required=True)
    parser.add_argument("--dev-outcomes", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    policy = load_bundled_policy()
    train_split, train_rows = _load_rows(
        args.train_input, args.train_outcomes, policy
    )
    dev_split, dev_rows = _load_rows(args.dev_input, args.dev_outcomes, policy)
    if train_split != "train" or dev_split != "dev":
        raise ValueError("Train/Dev 입력의 split 값이 올바르지 않습니다.")
    report = {
        "report_type": "public-prompt-outcome-analysis",
        "splits": {
            "train": _analyze_split(train_split, train_rows),
            "dev": _analyze_split(dev_split, dev_rows),
        },
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(_render_markdown(report) + "\n", encoding="utf-8")
    print(
        f"OK: Train {len(train_rows)}문항, Dev {len(dev_rows)}문항 분석을 기록했습니다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
