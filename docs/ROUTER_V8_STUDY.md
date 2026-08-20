<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V8 residual ensemble·quantile cost 검토

## 결론

V8 후보는 활성화하지 않고 V7 Adaptive를 유지한다. Train nested template OOF와
content/source batch stress에서 고정한 최선 후보가 Train stress tier 실패 3건,
고정 Dev stress tier 실패 3건을 남겼고, Dev 전체 점수도 V7 `0.691477`보다 낮은
`0.666222`였다. 실험 도구와 기각 근거는 보존하지만 제출 진입점과 artifact는
변경하지 않는다.

## 검토한 네 축

1. V5 tier별 Ridge 예측에 cross-fitted GBM score residual을 더했다.
2. 비용 중앙값으로 일차 배정한 뒤 split-conformal 상위 분위의 Light 대비 추가
   비용으로 위험한 승격을 제거했다.
3. 4 outer × 4 inner normalized-template fold와 content/source standalone,
   bootstrap, mixture stress로 후보를 Train에서만 선택했다.
4. V6.1과 V7의 점수 보험료를 tier 실패 손실과 비교해 rollback 손익분기점을
   계산했다.

GBM은 80개 depth-2 tree, Huber score residual, log-cost quantile
`0.25/0.50/0.70/0.80`을 사용했다. 후보 grid는 residual weight
`0/0.25/0.5/0.75/1`, risk quantile `0.7/0.8`, reserve
`0.85/0.875/0.9/0.925/0.95/0.975/1.0`이다.

## 검증 규율

- 후보 선택에는 Train 1,760문항의 nested OOF 예측만 사용했다.
- residual target은 inner-fold Ridge OOF 잔차로 만들었다.
- quantile cost는 별도 template calibration fold의 잔차로 승산 보정했다.
- 동일 normalized template은 같은 fold에 유지했다.
- DeepMind Mathematics와 AIME source ID는 검증 group에만 사용하고 학습 특징과
  artifact에는 넣지 않았다.
- Dev 후보와 파라미터는 Train 선택 뒤 고정했다.

기존 FNV template hash의 하위 비트에 직접 modulo를 적용하면 2/4-fold에서 한
fold에 거의 모든 문항이 몰리는 현상을 확인했다. V8 하네스는 64비트 전체를
SplitMix64 방식으로 섞은 뒤 modulo를 적용하며, 동일 template의 group 경계는
그대로 보존한다.

## 결과

Train source group은 DeepMind Mathematics 303개, AIME 24개이고 Dev는 각각
153개, 12개다. content 5개와 source 2개 standalone, bootstrap 30개, mixture
140개를 각 split에서 평가했다.

| 평가 | 라우터 | 점수 | stress tier 실패 | Fast | Balanced | Premium |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 고정 Dev | V6.1 | `0.695170` | 기존 보고 `123` | `1.143321` | `1.661573` | `3.072558` |
| 고정 Dev | V7 | `0.691477` | 기존 보고 `0` | `1.145521` | `1.653162` | `3.006001` |
| Train nested OOF | V8 선택 후보 | `0.639858` | `3` | - | - | - |
| 고정 Dev | V8 선택 후보 | `0.666222` | `3` | `1.023629` | `1.354611` | `2.183763` |

Train에서 stress 실패 0인 후보는 70개 중 없었다. 실패 수를 먼저 최소화한 뒤
전체 점수를 비교해 `rw0.75-q0.8-r0.85`가 고정됐다. Dev tier 점수는 Fast
`0.630682`, Balanced `0.671307`, Premium `0.708523`이었다.

Residual weight별 최소 Train stress 실패는 `0→5`, `0.25→4`, `0.5→3`,
`0.75→3`, `1.0→3`이었다. 비선형 잔차가 위험 순위를 일부 개선했지만 점수
하락과 source-mixture 실패를 함께 해결하지 못했다. Quantile/conformal 비용도
선택별 위험을 구분했으나 안전 reserve에서 K1과 AX31을 과도하게 제거했다.

## V6.1/V7 위험 판단

V7의 Dev 보험료는 `0.003693`이다. V6.1에서 해당 tier 하나가 실패한다고 놓으면
V7의 손익분기 실패확률은 Fast `1.384%`, Balanced `1.785%`, Premium `1.668%`다.
공개 stress 빈도는 비공개 실패확률 추정치가 아니므로 이 수치만으로 확률을
정할 수 없다. 다만 V8이 V7보다 점수와 stress 양쪽에서 열세이므로 현재 증거는
V7 유지와 V6.1 rollback 보존을 지지한다.

## 재현

원본 보고서는
[`router-v8-nested-train-to-dev.json`](../experiments/results/router-v8-nested-train-to-dev.json)에
있고 SHA-256은
`4f2885d4b5da495ff5a5c4a81693ff1c651cf4c6656256715beb6375902945ba`다.

```console
PYTHONPATH=src:tools VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv-data/bin/python tools/evaluate_router_v8.py \
  --train-input data/materialized/train/inputs.json \
  --train-outcomes data/train/outcomes.json \
  --eval-input data/materialized/dev/inputs.json \
  --eval-outcomes data/dev/outcomes.json \
  --base-artifact experiments/artifacts/aggressive-v5-train-only.v1.json \
  --deepmind-selection data/sources/deepmind-mathematics-selection.v1.json \
  --train-aime-selection data/train/aime-selection.json \
  --eval-aime-selection data/dev/aime-selection.json \
  --report experiments/results/router-v8-nested-train-to-dev.json \
  --outer-folds 4 --inner-folds 4 \
  --quantiles 0.25,0.5,0.7,0.8 --risk-quantiles 0.7,0.8 \
  --reserves 0.85,0.875,0.9,0.925,0.95,0.975,1.0 \
  --bootstrap-sizes 100,300,880 --bootstrap-trials 10 \
  --mixture-size 300 --mixture-trials 5 \
  --mixture-proportions 0.25,0.5,0.75,1.0
```
