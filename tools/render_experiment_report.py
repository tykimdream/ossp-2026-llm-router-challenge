# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Render Markdown and dependency-free SVG charts from the experiment registry."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


TIERS = ("fast", "balanced", "premium")
TIER_LABELS = {"fast": "Fast", "balanced": "Balanced", "premium": "Premium"}
STATUS_LABELS = {
    "reference": "reference",
    "candidate": "candidate",
    "rejected": "rejected",
    "champion": "champion",
}


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(
        value.get("experiments"), list
    ):
        raise ValueError("지원하지 않는 experiment registry입니다.")
    return value


def _fmt(value: float) -> str:
    return f"{value:.6f}"


def _markdown(registry: Mapping[str, Any], score_svg: str, budget_svg: str) -> str:
    lines = [
        "<!--",
        "SPDX-File" + "CopyrightText: Copyright 2026 SK TELECOM CO., LTD.",
        "SPDX-License-" + "Identifier: Apache-2.0",
        "-->",
        "",
        "# 라우터 실험 비교",
        "",
        "이 문서는 `experiments/registry.json`에서 자동 생성됩니다. 공정한 홀드아웃과",
        "학습·검증에 재사용한 참고 점수를 구분해 해석해야 합니다.",
        "",
        f"![실험별 최종 점수]({score_svg})",
        "",
        f"![실험별 예산 사용률]({budget_svg})",
        "",
        "## 요약",
        "",
        "| 실험 | 상태 | 공정 홀드아웃 | 최종 점수 | Fast 비용 | Balanced 비용 | Premium 비용 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for experiment in registry["experiments"]:
        metrics = experiment["metrics"]
        tiers = metrics["tiers"]
        lines.append(
            "| {title} | {status} | {holdout} | {score} | {fast} | {balanced} | {premium} |".format(
                title=experiment["title"],
                status=STATUS_LABELS[experiment["status"]],
                holdout="예" if experiment["evaluation"]["fair_holdout"] else "아니오",
                score=_fmt(metrics["final_score"]),
                fast=_fmt(tiers["fast"]["budget_ratio"]),
                balanced=_fmt(tiers["balanced"]["budget_ratio"]),
                premium=_fmt(tiers["premium"]["budget_ratio"]),
            )
        )
    lines.extend(["", "## 실험별 판단", ""])
    for experiment in registry["experiments"]:
        evaluation = experiment["evaluation"]
        lines.extend(
            [
                f"### {experiment['title']}",
                "",
                f"- ID: `{experiment['id']}`",
                f"- 상태: `{experiment['status']}`",
                f"- 평가: `{evaluation['protocol']}` (`{evaluation['trained_on']}` → `{evaluation['evaluated_on']}`)",
                f"- 공정 홀드아웃: `{'yes' if evaluation['fair_holdout'] else 'no'}`",
                f"- 최종 점수: `{_fmt(experiment['metrics']['final_score'])}`",
            ]
        )
        if experiment["pros"]:
            lines.extend(["", "장점:", ""])
            lines.extend(f"- {item}" for item in experiment["pros"])
        if experiment["cons"]:
            lines.extend(["", "단점:", ""])
            lines.extend(f"- {item}" for item in experiment["cons"])
        if experiment["warnings"]:
            lines.extend(["", "주의:", ""])
            lines.extend(f"- {item}" for item in experiment["warnings"])
        lines.append("")
    lines.extend(
        [
            "## 해석 기준",
            "",
            "- 최종 후보는 공정 홀드아웃 점수와 세 등급 예산 통과를 모두 만족해야 합니다.",
            "- 비용 사용률 100% 선을 넘으면 해당 등급 점수는 0입니다.",
            "- Train+Dev 전체로 재학습한 뒤 같은 Dev에서 측정한 값은 배포 확인값이지 일반화 점수가 아닙니다.",
            "- `rejected` 실험도 삭제하지 않아 같은 실패를 반복하지 않도록 합니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def _score_svg(experiments: Sequence[Mapping[str, Any]]) -> str:
    width = 1100
    row_height = 54
    left = 300
    right = 70
    top = 72
    height = top + row_height * len(experiments) + 60
    plot_width = width - left - right
    maximum = max(0.85, max(item["metrics"]["final_score"] for item in experiments) * 1.08)
    parts = [
        '<!-- SPDX-File' + 'CopyrightText: Copyright 2026 SK TELECOM CO., LTD. -->',
        '<!-- SPDX-License-' + 'Identifier: Apache-2.0 -->',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">라우터 실험별 최종 점수</title>',
        '<desc id="desc">각 라우터의 공개 평가 최종 점수와 홀드아웃 여부를 비교한다.</desc>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="24" y="34" font-family="sans-serif" font-size="22" font-weight="700" fill="#172033">라우터 실험별 최종 점수</text>',
        '<text x="24" y="56" font-family="sans-serif" font-size="12" fill="#596579">진한 막대는 공정 홀드아웃, 흐린 막대는 학습·검증 재사용 참고값</text>',
    ]
    for tick in range(0, 10):
        value = maximum * tick / 9
        x = left + plot_width * value / maximum
        parts.append(f'<line x1="{x:.1f}" y1="{top-10}" x2="{x:.1f}" y2="{height-42}" stroke="#e5e9f0"/>')
        parts.append(f'<text x="{x:.1f}" y="{height-20}" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#596579">{value:.2f}</text>')
    colors = {"reference": "#64748b", "candidate": "#2563eb", "rejected": "#dc2626", "champion": "#16a34a"}
    for index, experiment in enumerate(experiments):
        y = top + index * row_height
        score = experiment["metrics"]["final_score"]
        bar_width = plot_width * score / maximum
        opacity = "1" if experiment["evaluation"]["fair_holdout"] else "0.48"
        label = html.escape(experiment["title"])
        parts.append(f'<text x="{left-14}" y="{y+22}" text-anchor="end" font-family="sans-serif" font-size="13" fill="#172033">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y+7}" width="{bar_width:.1f}" height="24" rx="3" fill="{colors[experiment["status"]]}" opacity="{opacity}"/>')
        parts.append(f'<text x="{left+bar_width+8:.1f}" y="{y+24}" font-family="monospace" font-size="12" fill="#172033">{score:.6f}</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def _budget_svg(experiments: Sequence[Mapping[str, Any]]) -> str:
    width = 1100
    left = 300
    right = 70
    top = 72
    row_height = 34
    rows = len(experiments) * len(TIERS)
    height = top + rows * row_height + 60
    plot_width = width - left - right
    maximum = 1.12
    parts = [
        '<!-- SPDX-File' + 'CopyrightText: Copyright 2026 SK TELECOM CO., LTD. -->',
        '<!-- SPDX-License-' + 'Identifier: Apache-2.0 -->',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">라우터 실험별 예산 사용률</title>',
        '<desc id="desc">Fast, Balanced, Premium의 공식 한도 대비 실제 비용 사용률을 비교한다.</desc>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="24" y="34" font-family="sans-serif" font-size="22" font-weight="700" fill="#172033">공식 한도 대비 예산 사용률</text>',
        '<text x="24" y="56" font-family="sans-serif" font-size="12" fill="#596579">100%를 넘으면 해당 등급 점수는 0</text>',
    ]
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        x = left + plot_width * tick / maximum
        stroke = "#dc2626" if tick == 1 else "#e5e9f0"
        width_value = "2" if tick == 1 else "1"
        parts.append(f'<line x1="{x:.1f}" y1="{top-10}" x2="{x:.1f}" y2="{height-42}" stroke="{stroke}" stroke-width="{width_value}"/>')
        parts.append(f'<text x="{x:.1f}" y="{height-20}" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#596579">{tick*100:.0f}%</text>')
    tier_colors = {"fast": "#2563eb", "balanced": "#7c3aed", "premium": "#ea580c"}
    row = 0
    for experiment in experiments:
        for tier in TIERS:
            y = top + row * row_height
            value = experiment["metrics"]["tiers"][tier]["budget_utilization"]
            bar_width = min(plot_width, plot_width * value / maximum)
            label = html.escape(experiment["title"])
            parts.append(f'<text x="{left-14}" y="{y+18}" text-anchor="end" font-family="sans-serif" font-size="12" fill="#172033">{label} · {TIER_LABELS[tier]}</text>')
            parts.append(f'<rect x="{left}" y="{y+6}" width="{bar_width:.1f}" height="17" rx="2" fill="{tier_colors[tier]}"/>')
            parts.append(f'<text x="{left+bar_width+7:.1f}" y="{y+19}" font-family="monospace" font-size="11" fill="#172033">{value*100:.1f}%</text>')
            row += 1
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def render(args: argparse.Namespace) -> None:
    registry = _load(args.registry)
    experiments = sorted(
        registry["experiments"],
        key=lambda item: item["metrics"]["final_score"],
        reverse=True,
    )
    if not experiments:
        raise ValueError("표시할 실험이 없습니다.")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.score_svg.parent.mkdir(parents=True, exist_ok=True)
    args.budget_svg.parent.mkdir(parents=True, exist_ok=True)
    args.score_svg.write_text(_score_svg(experiments), encoding="utf-8")
    args.budget_svg.write_text(_budget_svg(experiments), encoding="utf-8")
    args.markdown.write_text(
        _markdown(registry, args.score_svg.name, args.budget_svg.name),
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--score-svg", type=Path, required=True)
    parser.add_argument("--budget-svg", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        render(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(f"OK: 실험 비교 보고서를 {args.markdown}에 생성했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
