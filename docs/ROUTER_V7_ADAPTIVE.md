<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V7 adaptive budget reserve

## 목적

V6.1은 전체 Dev에서 높은 점수와 충분한 비용 여유를 보였지만, 동일한 selector를
독립 content group이나 100~300문항 재표본 배치에 다시 적용하면 실제 비용 꼬리
때문에 tier 점수가 0이 되는 경우가 있었다. V7은 score·cost head와 승격 순위를
바꾸지 않고, 관측 가능한 배치 구성과 표본 수에 따라 내부 cap만 수축한다.

## 런타임 규칙

각 문항에서 ID나 source label이 아닌 여섯 content signal만 집계한다.

- Hangul 10% 이상
- code marker 존재
- math marker 또는 숫자 밀도 8% 이상
- 8,000자 초과
- 100자 이하
- messages 입력

Train 1,760문항의 기준 비율과 현재 배치 비율의 최대 절대 차이를 `d`, 배치 크기를
`n`이라 하면 다음 고정 multiplier를 V5 safety ratio와 Premium fill ratio에
곱한다.

```text
exp(-1.0 × d - 0.25 × max(0, sqrt(1000 / n) - 1))
```

배치가 크고 Train 구성에 가까우면 1에 가깝다. 작거나 특정 content가 과대표집된
배치는 연속적으로 Light 쪽으로 수축한다. 난수, 현재 시각, 입력 순서, ID와 Dev
결과는 파라미터 선택에 쓰지 않았다.

workload guard를 넘는 입력은 V6의 Prompt Heuristic 대신 Always-Light로 보낸다.
실제 출력 token을 모르는 조건에서 임의 데이터의 비용을 절대 보장하는 유일한
경로가 Light이기 때문이다.

## Train 선택과 고정 Dev 평가

원본 보고서는
[`adaptive-safety-v7-train-to-dev.json`](../experiments/results/adaptive-safety-v7-train-to-dev.json)에
있다. seed `20260819`, 크기 `100/300/880` bootstrap 각 50회, observable group의
`25/50/75/100%` mixture 각 20회를 사용했다.

| small-batch penalty | Train stress tier 실패 | Train stress 평균 점수 |
| ---: | ---: | ---: |
| `0.200` | 1 | `0.611833` |
| `0.225` | 1 | `0.610493` |
| `0.250` | **0** | `0.609610` |

Train gate를 처음 통과한 `0.250`을 고정한 뒤 Dev를 한 번 평가했다.

| 평가 | V6.1 | V7 adaptive |
| --- | ---: | ---: |
| 전체 Dev weighted score | `0.695170` | `0.691477` |
| 전체 Fast 비용 | `1.143321` | `1.145521` |
| 전체 Balanced 비용 | `1.661573` | `1.653162` |
| 전체 Premium 비용 | `3.072558` | `3.006001` |
| standalone 실패 | 1 | **0** |
| bootstrap tier 실패 | 13 | **0** |
| mixture tier 실패 | 110 | **0** |

V7은 공정 Dev 점수 `0.003693`을 보험료로 지불하고 이 프로토콜의 123개 stress
실패를 제거한다. 이는 숨은 분포에서의 실패 확률이나 수학적 예산 보장을 뜻하지
않는다. V6.1로 즉시 롤백할 수 있도록 score/cost artifact는 그대로 유지한다.

## 남은 위험

- content signal과 배치 크기가 같아도 드문 출력 비용 꼬리는 남을 수 있다.
- 1,000 미만의 정상 소배치에서도 보수적으로 동작해 품질을 더 양보한다.
- 기준 비율은 현재 Train에 messages가 없음을 OOD 신호로 취급한다. 새로운
  메시지형 데이터가 정상 분포라면 별도 독립 calibration이 필요하다.
- 임의 데이터의 절대 예산 보장은 workload guard의 Always-Light 경로에만 있다.

최종 ARM64·결정론·엣지 gate와 활성 판단은
[`FINAL_BAKEOFF_V7.md`](FINAL_BAKEOFF_V7.md)에 기록한다.
