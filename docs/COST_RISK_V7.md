<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V7 비용 위험 모델 — 상대비용 target 검토

## 가설

V5는 세 모델의 절대 log-cost를 독립 회귀한다. 공식 예산은 선택 비용을
Light 전체 비용으로 나눈 비율이므로 다음 target이 분포 이동에 더 직접적일 수
있다.

```text
log(C_light), log(C_ax31 / C_light), log(C_k1 / C_light)
```

이 checkpoint는 V5 Train-only score head와 safety ratio를 고정하고 비용 target과
Ridge alpha만 바꾼다. 따라서 score model 개선과 비용 표현 개선을 섞지 않는다.

## 검증 규율

- relative cost head는 Train 1,760문항으로만 적합한다.
- 고정 alpha 후보 전체를 Dev에 보고하되 Dev 결과로 하나를 활성화하지 않는다.
- 전체 Dev와 content-only standalone group을 각각 다시 라우팅한다.
- 활성 제출 artifact와 런타임 코드는 변경하지 않는다.

원본 결과는
[`relative-cost-targets-train-to-dev.json`](../experiments/results/relative-cost-targets-train-to-dev.json)에
기록한다.

## 판단

단일 상대비용 Ridge는 점수와 group 예산 안정성을 동시에 지배하지 못했다.
낮은 alpha는 점수를 높였지만 여러 standalone group에서 예산을 넘었고, 모든
standalone group을 통과한 강한 정규화 후보는 V6.1 점수보다 낮았다. 따라서
상대비용 target만 활성 제출에 넣지 않는다.

다음 checkpoint에서는 비용 평균점을 더 미세하게 조정하기보다, 선택의 주된
손실인 Light 대비 uplift 순위를 직접 학습한다. 비용 모델은 이후 OOF 불확실성과
OOD shrink를 결합할 때 다시 비교한다.
