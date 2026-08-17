<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# Aggressive Router V6 평가와 적대적 리뷰

## 결론

V6는 V5의 학습 계수와 특징 의미를 동결한 채 운영 취약점을 보완한 버전이다.
14개 엣지케이스를 V5/V6, 세 tier로 실행한 84회 모두 timeout·형식 오류·4 MiB
초과 없이 성공했다. V6의 합계 실행 시간은 `132.779초 → 100.288초`, 최대 RSS는
`187.2 MiB → 135.9 MiB`였다.

예산 위험을 낮춘 대가로 공정 Train-only → Dev 점수는 `0.695170 → 0.685256`으로
`0.009915` 하락했다. 이 버전은 점수 상방보다 “tier 하나가 예산 초과로 0점이
되는 위험”과 입력 구조에 따른 실행 실패를 줄이는 선택이다.

최종 `linux/arm64` 이미지의 공개 2,640문항 검사는 Fast/Balanced/Premium
`12.762 / 12.746 / 12.727초`로 세 tier 모두 90초 한도를 통과했다. 원본은
[`aggressive-v6-runtime.json`](../experiments/results/aggressive-v6-runtime.json)에
있다.

## V5에서 바꾼 것

| 영역 | V5 취약점 | V6 변경 | 확인 결과 |
| --- | --- | --- | --- |
| 출력 | pretty JSON이 4 MiB 경계를 넘음 | 제출만 compact JSON | 과거 실패 크기 입력도 통과 |
| token 메모리 | token·n-gram 전체 리스트 생성 | `deque` 기반 streaming hash | 50만 token RSS 약 `187 → 34 MiB` |
| 중복 입력 | 같은 내용을 매번 예측 | 내용별 prediction cache | 5,000개 중복 `4.0–6.2 → 0.41초` |
| ensemble | Fast/Premium에서 여러 dot product | 원시 특징 공간의 단일 선형 head로 컴파일 | 예측값 `1e-12` 정밀도 동등 |
| workload guard | 문항 수·문자 수만 확인 | message·token·work unit 포함 | 조밀한 token/message 공격 방어 |
| 예산 목표 | 공식 한도에 비교적 가까움 | 예측 목표 `1.15/1.80/3.00` | 공개 실제 비용 여유 증가 |

V6는 합성 엣지케이스 outcome에 재학습하지 않았다. 합성 문장을 정답처럼
학습하는 대신 V5 artifact를 실행 시 동등한 선형식으로 컴파일한다.

## 공개 outcome 평가

### 공정 비교: Train-only artifact → Dev 880문항

| Router | 점수 | Fast 비용 | Balanced 비용 | Premium 비용 | 예산 초과 |
| --- | ---: | ---: | ---: | ---: | ---: |
| V5 | `0.695170` | `1.143321` | `1.661573` | `3.072558` | 0 |
| V6 | `0.685256` | `1.142155` | `1.569580` | `2.523548` | 0 |

V6는 Fast `30`, Balanced `45`, Premium `51`개 결정을 V5와 다르게 배정했다.
Premium 비용 여유가 가장 크게 늘었지만 품질 점수도 `0.738068 → 0.718750`으로
가장 크게 줄었다. 원본은
[`aggressive-v6-train-only-dev.json`](../experiments/results/aggressive-v6-train-only-dev.json)에
있다.

### 참고 비교: Full-public artifact → 같은 2,640문항 replay

이 비교는 Train+Dev로 학습한 artifact를 같은 자료에서 다시 채점하므로 독립
일반화 성능이 아니다. 배선·비용·결정 회귀 확인용으로만 사용한다.

| Router | 점수 | Fast 비용 | Balanced 비용 | Premium 비용 | 예산 초과 |
| --- | ---: | ---: | ---: | ---: | ---: |
| V5 | `0.703277` | `1.152316` | `1.744540` | `2.931772` | 0 |
| V6 | `0.696676` | `1.108840` | `1.566958` | `2.629515` | 0 |

원본은
[`aggressive-v6-full-public.json`](../experiments/results/aggressive-v6-full-public.json)에
있다.

## 배정 방식과 결정론

V6는 입력을 하나씩 Light에서 시작해 순서대로 상위 모델로 바꾸는 방식이 아니다.
모든 문항의 모델별 점수와 비용을 먼저 계산하고, 배치 전체에 공통인 비용 벌점
하나를 고정 48회 이분 탐색한다. 각 벌점 후보에서 모든 문항의 모델을 동시에
다시 계산한다. 따라서 앞 문항이 예산을 먼저 가져가는 순차 greedy 과정이 없다.

Premium의 후단에는 첫 전역 배정에서 Light인 문항만 대상으로 AX31 후보를
다시 계산하는 단계가 있다. 이것도 입력 순회를 하며 잔여 예산을 먼저 차지하는
방식이 아니라, 공통 벌점을 이용한 전역 동시 배정이다. 문서에서는 이 과정을
“추가 AX31 배정”이라 부르고 “순차 승격”이라고 부르지 않는다.

ID와 순서를 바꾼 `order-audit-a/b`에서 V6의 내용별 선택 불일치는 세 tier 모두
0이었다. 현재 시각, 난수, `episode_id`, `challenge_id`, `split`, 배열 위치는
선택 특징에 들어가지 않는다.

## 엣지케이스 결과

| 지표 | V5 | V6 | 판단 |
| --- | ---: | ---: | --- |
| 성공 | `42/42` | `42/42` | 모두 통과 |
| timeout | 0 | 0 | 로컬 90초 기준 |
| 잘못된 submission | 0 | 0 | 모두 파싱·ID 검증 통과 |
| 4 MiB 초과 | 0 | 0 | compact writer 적용 후 |
| 합계 실행 시간 | `132.779초` | `100.288초` | V6 24.5% 감소 |
| 최대 RSS | `187.2 MiB` | `135.9 MiB` | 최대값은 3천만 자 입력 |

여기서 V5도 새 compact writer를 공유한 상태로 다시 실행했기 때문에 42회 모두
통과한다. 변경 전 V4/V5 실행에서는 21,399개 128자 ID의 Fast 출력이
4,194,397바이트로 93바이트 초과했다. 동일 결과의 compact 출력은
3,637,995바이트다.

전체 표는 [`EDGE_CASE_RESULTS_V6.md`](EDGE_CASE_RESULTS_V6.md), 기계 판독 원본은
[`edge-cases-v5-v6.json`](../experiments/results/edge-cases-v5-v6.json)에 있다.

## 남은 취약점

### P0 — 숨은 실제 비용의 절대 보장은 여전히 불가능

라우팅 시점에는 각 모델의 실제 출력 token을 알 수 없다. 따라서 Light 외 모델을
하나라도 선택하는 학습형 정책은 어떤 숨은 분포에서도 한도 미초과를 수학적으로
보장할 수 없다. V6의 더 낮은 예측 목표와 공개 평가 0회 초과는 위험 감소의
증거이지 보장이 아니다. 절대 보장이 최우선이면 유일하게 확실한 정책은 모든
문항을 Light로 보내 비용 비율을 1로 만드는 것이다.

### P1 — 의미적 hard-tail 오분류는 해결되지 않음

`skewed-budget-cliff-5000`에는 쉬운 산술 4,950개와 형식 추론 50개가 있다.
실제 outcome이 없어 정오를 단정할 수는 없지만, V6 Premium은 5,000개 모두
AX31을 선택해 hard-tail에 K1을 별도 배정하지 않았다. V6는 운영 hardening이며
의미 분류 모델을 재학습한 버전이 아니다. 독립 outcome을 확보한 뒤 유형별
worst-group uplift·calibration gate가 필요하다.

### P1 — 보수성으로 품질이 하락

공정 Dev 점수가 약 0.0099 줄었다. 특히 Premium에서 K1 수가 `119 → 71`로
감소했다. 숨은 비용 위험과 점수 사이의 선택이므로, 독립 홀드아웃 없이 안전
계수를 다시 높여서는 안 된다.

### P2 — guard 경계와 추가 scan 비용

6,000/6,001문항과 30,000,000/30,000,001자 경계에서 배치 전체가 학습형에서
휴리스틱으로 바뀌는 절벽은 남아 있다. 또한 token work를 세는 사전 scan 때문에
50만 token 입력은 약 `1.7 → 1.9초`, 공개 2,640문항은 로컬에서 소폭 느려졌다.
향후에는 파싱 단계에서 work count를 함께 누적하거나 문항별 fallback을 검토할
수 있다.

### P2 — compact 형식도 무한한 출력 크기를 해결하지 못함

이번 경계 입력은 통과하지만 스키마에는 episode 수 상한이 없다. compact JSON도
4 MiB를 넘는 입력에서는 모든 결정을 기록하면서 공식 한도를 만족시킬 수 없다.
이는 라우터 선택 알고리즘이 아니라 프로토콜 수용량의 한계다.

## 재현

```console
PYTHONPATH=src python3 tools/generate_edge_cases.py \
  --profile full --output-dir build/edge-cases

PYTHONPATH=src python3 tools/benchmark_edge_cases.py \
  --routers v5 v6 \
  --cases-dir build/edge-cases \
  --report-json experiments/results/edge-cases-v5-v6.json \
  --report-markdown docs/EDGE_CASE_RESULTS_V6.md

PYTHONPATH=src python3 tools/evaluate_aggressive_router_v6.py \
  --input data/materialized/dev/inputs.json \
  --outcomes data/dev/outcomes.json \
  --artifact experiments/artifacts/aggressive-v5-train-only.v1.json \
  --report experiments/results/aggressive-v6-train-only-dev.json
```
