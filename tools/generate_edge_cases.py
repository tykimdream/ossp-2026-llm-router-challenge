# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

"""Generate deterministic prompt-only stress inputs for the submission router.

Generated files belong under ``build/`` and are intentionally not committed. The
large cases exercise the current submission workload guards and the official
4 MiB output-volume boundary without pretending to be hidden evaluation data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
CHALLENGE_ID_PREFIX = "ossp-2026-edge"
POLICY_ID = "ossp-2026-prompt-router-v1"
OUTPUT_LIMIT_BYTES = 4 * 1024 * 1024
LEARNED_EPISODE_LIMIT = 6_000
LEARNED_CHARACTER_LIMIT = 30_000_000

EpisodeDict = Dict[str, object]
CaseBuilder = Callable[[], List[EpisodeDict]]


@dataclass(frozen=True)
class CaseSpec:
    name: str
    profile: str
    purpose: str
    expected_path: str
    builder: CaseBuilder


def _prompt_episode(case: str, index: int, prompt: str) -> EpisodeDict:
    return {"episode_id": f"{case}-{index:06d}", "prompt": prompt}


def _hard_prompt(index: int) -> str:
    variants = (
        (
            "다음 최적화 명제를 엄밀히 증명하고, 조건 하나를 제거했을 때의 최소 "
            "반례를 구성하라. 모든 보조정리를 먼저 서술하고 귀납 단계의 불변식과 "
            "경계 조건을 검증하라: f(n)=sum_{{i=1}}^n i^3, n={n}. 답만 쓰지 말고 "
            "두 독립적인 증명 방법의 시간 복잡도까지 비교하라."
        ),
        (
            "분산 키-값 저장소에서 선형화 가능성과 lock-freedom을 동시에 만족하는 "
            "알고리즘을 설계하라. 아래 실행 번호 {n}에서 ABA, 재시도 폭주, 부분 장애를 "
            "모두 분석하고 의사코드, 안전성 증명, 최악 시간·공간 복잡도, 깨지는 "
            "스케줄의 반례를 제시하라."
        ),
        (
            "정수론 문제 {n}: 양의 정수 a,b,c가 a^2+b^2=c^2 및 추가 합동 조건을 "
            "만족한다고 하자. 가능한 해를 완전히 분류하고 필요충분성을 증명하라. "
            "단순 계산이나 수치 실험에 의존하지 말고 누락된 경우가 없음을 보이라."
        ),
        (
            "서로 모순될 수 있는 14개 규칙으로 이루어진 논리 퍼즐 실행 {n}을 풀어라. "
            "각 규칙의 의존 관계를 형식화하고 유일해 존재 여부를 증명한 뒤, 규칙 하나를 "
            "반전했을 때 해 집합이 어떻게 변하는지 최소 반례와 함께 분석하라."
        ),
    )
    return variants[index % len(variants)].format(n=index)


def _easy_prompt(index: int) -> str:
    return f"두 정수 {index % 97}과 {(index * 7) % 101}의 합을 계산하세요."


def _build_k1_pressure() -> List[EpisodeDict]:
    return [
        _prompt_episode("k1-pressure", index, _hard_prompt(index))
        for index in range(5_000)
    ]


def _build_homogeneous_duplicates() -> List[EpisodeDict]:
    prompt = _hard_prompt(0)
    return [_prompt_episode("duplicate", index, prompt) for index in range(5_000)]


def _build_skewed_budget_cliff() -> List[EpisodeDict]:
    episodes = [
        _prompt_episode("skew-easy", index, _easy_prompt(index))
        for index in range(4_950)
    ]
    episodes.extend(
        _prompt_episode("skew-hard", index, _hard_prompt(index))
        for index in range(50)
    )
    return episodes


def _build_episode_guard(count: int) -> List[EpisodeDict]:
    return [
        _prompt_episode(
            f"episode-guard-{count}",
            index,
            f"입력 개수 경계 확인용 문항 {index}: 17+25를 계산하세요.",
        )
        for index in range(count)
    ]


def _build_message_fanout() -> List[EpisodeDict]:
    roles = ("system", "user", "assistant")
    episodes: List[EpisodeDict] = []
    for episode_index in range(500):
        messages = [
            {
                "role": roles[message_index % len(roles)],
                "content": (
                    f"대화 {episode_index}, 메시지 {message_index}: 앞선 제약을 유지하세요."
                ),
            }
            for message_index in range(128)
        ]
        episodes.append(
            {
                "episode_id": f"message-fanout-{episode_index:06d}",
                "messages": messages,
            }
        )
    return episodes


def _build_unicode_pathologies() -> List[EpisodeDict]:
    fragments = (
        "한글 NFC: 각 값의 합을 계산하세요.",
        "한글 NFD: 같은 글자로 정규화되는지 설명하세요.",
        "emoji: 👨\u200d👩\u200d👧\u200d👦 🧑🏽\u200d💻 🏳️\u200d🌈를 grapheme으로 세세요.",
        "zero width: A\u200bB\u200cC\u200dD\ufeffE의 보이지 않는 문자를 식별하세요.",
        "RTL/LTR: English ثم العربية ואז עברית 12345의 표시 순서를 분석하세요.",
        "수학 유니코드: ∑ᵢ₌₁ⁿ i² ≤ ∫₀ⁿ(x+1)²dx, √2≠3/2를 증명하세요.",
        "제어문자: 탭\t줄바꿈\n캐리지리턴\r널\u0000을 JSON 문자열로 처리하세요.",
        "긴 결합문자: e\u0301e\u0301e\u0301와 ééé가 같은지 비교하세요.",
    )
    return [
        _prompt_episode(
            "unicode", index, f"케이스 {index}: {fragments[index % len(fragments)]}"
        )
        for index in range(1_200)
    ]


def _audit_prompt(index: int) -> str:
    return (
        f"순서 독립성 감사 문항 {index}: x={index % 31}, y={index % 37}일 때 "
        "(x+y)^2을 전개하고 검산하세요."
    )


def _build_order_audit(reverse: bool) -> List[EpisodeDict]:
    indices: Iterable[int] = range(1_024)
    if reverse:
        indices = reversed(tuple(indices))
    label = "b" if reverse else "a"
    return [
        _prompt_episode(f"order-{label}", index, _audit_prompt(index))
        for index in indices
    ]


def _repeat_to_length(seed: str, length: int) -> str:
    if length < 1:
        raise ValueError("length must be positive")
    repeats, remainder = divmod(length, len(seed))
    return seed * repeats + seed[:remainder]


def _build_character_guard(character_count: int) -> List[EpisodeDict]:
    # This isolates the character-count guard without also maximizing token count.
    prompt = _repeat_to_length("A", character_count)
    return [_prompt_episode(f"character-guard-{character_count}", 0, prompt)]


def _build_token_density() -> List[EpisodeDict]:
    # _hashed_features constructs word uni/bi/trigrams. Many tiny tokens expose
    # allocation growth that a same-length unbroken token does not.
    prompt = _repeat_to_length("a ", 1_000_000)
    return [_prompt_episode("token-density", 0, prompt)]


def _long_output_id(index: int) -> str:
    prefix = f"output-volume-{index:08d}-"
    return prefix + "x" * (128 - len(prefix))


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _submission_bytes_for_output_case(count: int) -> int:
    value = {
        "schema_version": SCHEMA_VERSION,
        "challenge_id": f"{CHALLENGE_ID_PREFIX}-output-volume",
        "policy_id": POLICY_ID,
        "split": "edge-output-volume",
        "tier": "fast",
        "decisions": [
            {"episode_id": _long_output_id(index), "model_id": "ax31-light"}
            for index in range(count)
        ],
    }
    return len(_json_text(value).encode("utf-8"))


def _output_boundary_counts() -> Tuple[int, int]:
    low, high = 1, 50_000
    while low + 1 < high:
        middle = (low + high) // 2
        if _submission_bytes_for_output_case(middle) <= OUTPUT_LIMIT_BYTES:
            low = middle
        else:
            high = middle
    return low, high


def _build_output_volume(over_limit: bool) -> List[EpisodeDict]:
    under, over = _output_boundary_counts()
    count = over if over_limit else under
    return [
        {"episode_id": _long_output_id(index), "prompt": "1+1을 계산하세요."}
        for index in range(count)
    ]


CASES: Tuple[CaseSpec, ...] = (
    CaseSpec(
        "k1-pressure-5000",
        "core",
        "5,000개 모두가 깊은 수학·코드·논리 추론을 요구하는 예산 압박 분포",
        "learned",
        _build_k1_pressure,
    ),
    CaseSpec(
        "homogeneous-duplicates-5000",
        "core",
        "동일한 고난도 프롬프트 5,000개로 중복 계산, 동점, 캐시 가정을 압박",
        "learned",
        _build_homogeneous_duplicates,
    ),
    CaseSpec(
        "skewed-budget-cliff-5000",
        "core",
        "쉬운 문항 4,950개와 고난도 문항 50개가 섞인 99:1 꼬리 분포",
        "learned",
        _build_skewed_budget_cliff,
    ),
    CaseSpec(
        "episode-guard-at-6000",
        "core",
        "현재 학습 경로 문항 수 상한과 정확히 같은 입력",
        "learned",
        lambda: _build_episode_guard(LEARNED_EPISODE_LIMIT),
    ),
    CaseSpec(
        "episode-guard-over-6001",
        "core",
        "현재 학습 경로 문항 수 상한을 하나 넘겨 휴리스틱으로 전환되는 입력",
        "heuristic-fallback",
        lambda: _build_episode_guard(LEARNED_EPISODE_LIMIT + 1),
    ),
    CaseSpec(
        "message-fanout-64000",
        "core",
        "500개 episode에 128개씩, 총 64,000개 메시지가 있는 파싱·결합 압박",
        "learned",
        _build_message_fanout,
    ),
    CaseSpec(
        "unicode-pathologies-1200",
        "core",
        "결합문자, ZWJ, emoji, RTL, NUL 등 유니코드 처리 입력",
        "learned",
        _build_unicode_pathologies,
    ),
    CaseSpec(
        "order-audit-a",
        "core",
        "ID와 입력 순서 독립성 비교를 위한 원본 배치",
        "learned",
        lambda: _build_order_audit(False),
    ),
    CaseSpec(
        "order-audit-b",
        "core",
        "동일 내용을 역순·새 ID로 제공하는 순서 독립성 비교 배치",
        "learned",
        lambda: _build_order_audit(True),
    ),
    CaseSpec(
        "character-guard-at-30000000",
        "full",
        "현재 문자 수 상한과 같은 단일 무공백 token; 느린 학습 경로",
        "learned",
        lambda: _build_character_guard(LEARNED_CHARACTER_LIMIT),
    ),
    CaseSpec(
        "character-guard-over-30000001",
        "full",
        "문자 수 상한을 하나 넘겨 휴리스틱으로 전환되는 입력",
        "heuristic-fallback",
        lambda: _build_character_guard(LEARNED_CHARACTER_LIMIT + 1),
    ),
    CaseSpec(
        "token-density-1000000",
        "full",
        "100만 자 안에 50만 개의 짧은 token을 넣어 n-gram 중간 객체를 압박",
        "learned",
        _build_token_density,
    ),
    CaseSpec(
        "output-volume-under-4mib",
        "full",
        "Fast 휴리스틱 출력이 4 MiB 바로 아래가 되도록 128자 ID를 채운 입력",
        "heuristic-fallback",
        lambda: _build_output_volume(False),
    ),
    CaseSpec(
        "output-volume-over-4mib",
        "full",
        "Fast 휴리스틱 출력이 4 MiB를 처음 초과하도록 128자 ID를 채운 입력",
        "heuristic-fallback",
        lambda: _build_output_volume(True),
    ),
)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _batch(spec: CaseSpec, episodes: Sequence[EpisodeDict]) -> Mapping[str, object]:
    challenge_id = f"{CHALLENGE_ID_PREFIX}-{spec.name}"
    split = f"edge-{spec.name}"
    if spec.name.startswith("output-volume"):
        challenge_id = f"{CHALLENGE_ID_PREFIX}-output-volume"
        split = "edge-output-volume"
    return {
        "schema_version": SCHEMA_VERSION,
        "challenge_id": challenge_id,
        "split": split,
        "episodes": list(episodes),
    }


def _episode_text(episode: Mapping[str, object]) -> str:
    prompt = episode.get("prompt")
    if isinstance(prompt, str):
        return prompt
    messages = episode["messages"]
    assert isinstance(messages, list)
    return "\n".join(str(message["content"]) for message in messages)


def generate_cases(output_dir: Path, specs: Sequence[CaseSpec]) -> Mapping[str, object]:
    records = []
    for spec in specs:
        episodes = spec.builder()
        batch = _batch(spec, episodes)
        text = _json_text(batch)
        path = output_dir / f"{spec.name}.json"
        _write_atomic(path, text)
        record: Dict[str, object] = {
            "name": spec.name,
            "profile": spec.profile,
            "purpose": spec.purpose,
            "expected_router_path": spec.expected_path,
            "episodes": len(episodes),
            "prompt_characters": sum(len(_episode_text(item)) for item in episodes),
            "input_bytes": len(text.encode("utf-8")),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "file": path.name,
        }
        if spec.name.startswith("output-volume"):
            record["expected_fast_submission_bytes"] = _submission_bytes_for_output_case(
                len(episodes)
            )
            record["official_output_limit_bytes"] = OUTPUT_LIMIT_BYTES
        records.append(record)
    manifest = {
        "schema_version": 1,
        "generator": "tools/generate_edge_cases.py",
        "notes": (
            "Synthetic router stress data only; scores and hidden-evaluation behavior "
            "must not be inferred from these prompts."
        ),
        "cases": records,
    }
    _write_atomic(output_dir / "manifest.json", _json_text(manifest))
    return manifest


def _selected_specs(profile: str, names: Sequence[str]) -> Tuple[CaseSpec, ...]:
    by_name = {spec.name: spec for spec in CASES}
    if names:
        unknown = sorted(set(names) - set(by_name))
        if unknown:
            raise ValueError(f"unknown cases: {', '.join(unknown)}")
        return tuple(by_name[name] for name in names)
    if profile == "core":
        return tuple(spec for spec in CASES if spec.profile == "core")
    return CASES


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/edge-cases"),
        help="generated JSON destination (default: build/edge-cases)",
    )
    parser.add_argument(
        "--profile",
        choices=("core", "full"),
        default="core",
        help="full additionally creates 30 MB and 4 MiB-boundary cases",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        dest="cases",
        help="generate only this named case; may be repeated",
    )
    parser.add_argument("--list", action="store_true", help="list cases and exit")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.list:
        for spec in CASES:
            print(f"{spec.name}\t{spec.profile}\t{spec.purpose}")
        return 0
    try:
        specs = _selected_specs(args.profile, args.cases)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    manifest = generate_cases(args.output_dir, specs)
    print(f"OK: generated {len(manifest['cases'])} cases in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
