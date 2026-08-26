<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V9 selective budget recovery

## 결론

V9는 V7의 adaptive reserve를 유지하면서 고정 safety reserve의 일부만 선택적으로
회수한다. Train template-group OOF와 content/source batch stress에서 고정한
후보 `a30000-w0.5-t0.1-r0.75`는 공정 Dev 점수를 `0.691477`에서 `0.695114`로
높였고 Train·Dev stress tier 실패는 모두 0이었다.

## 선택 방식

V7의 첫 배정은 변경하지 않는다. 두 번째 단계에서만 다음 조건을 만족하는
문항을 `Light → AX31`, 또는 Fast 이외 tier에서 `AX31 → K1`로 한 단계 올린다.

- Train template-group 4-fold OOF로 Light 대비 AX31/K1 uplift Ridge를 학습
- V7의 기존 score uplift와 direct-uplift 예측을 각각 0.5로 결합
- 예측 uplift가 0.1 이상인 승격만 후보로 허용
- V7의 고정 safety reserve 중 75%만 회수
- content-shift·small-batch multiplier는 회수하지 않고 그대로 유지
- 예측 uplift/추가비용 순으로 배정하되 회수 cap 안에서만 승격

alpha, 결합 가중치, uplift 문턱과 회수율의 81개 조합은 Train에서만 선택했다.
Dev는 선택된 후보를 고정한 뒤 한 번 평가했다.

## 결과

| 평가 | 라우터 | 점수 | Fast | Balanced | Premium | stress 실패 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Train OOF upgrade | V7 | `0.697287` | `1.119748` | `1.696363` | `3.208359` | `0` |
| Train OOF upgrade | V9 | `0.698963` | `1.156513` | `1.713077` | `3.245979` | `0` |
| Train-only → Dev | V7 | `0.691477` | `1.145521` | `1.653162` | `3.006001` | `0` |
| Train-only → Dev | V9 | `0.695114` | `1.161024` | `1.664234` | `3.071147` | `0` |

Dev 전체에서는 Fast 38건, Balanced 3건, Premium 11건이 V7보다 한 단계
승격됐다. Fast가 `+0.007386`, Premium이 `+0.002273` 개선됐고 Balanced 점수는
같았다. 최종 가중 점수는 `+0.003636` 상승했다.

source stress에는 Train/Dev의 DeepMind Mathematics `303/153`문항과 AIME
`24/12`문항의 standalone·mixture를 포함했다. Dev에서는 standalone 7개,
bootstrap 30개, mixture 140개 시나리오의 모든 tier가 예산을 통과했다.
원본은
[`selective-budget-v9-source-stress-train-to-dev.json`](../experiments/results/selective-budget-v9-source-stress-train-to-dev.json)에
있다.

공개 Train+Dev 2,640문항으로 다시 적합한 replay 점수는 V7 `0.703277`, V9
`0.705777`이다. 같은 자료를 학습하고 다시 평가한 값이므로 일반화 근거로
사용하지 않는다.

### V10 이항 점수 head 후속 실험

공개 score가 성공 횟수 `k/n`으로 복원되는 점을 이용해 모델별 이항 로지스틱
head를 추가하고 V9 Ridge score와 등급별로 혼합했다. 규제 강도와 혼합 비율은
Train template-group OOF와 동일한 177개 source/content stress에서만 선택했다.
모든 비영(非零) 혼합은 Train 전체 품질이 V9보다 낮았고 Fast/Balanced/Premium
모두 혼합 비율 `0`이 선택됐다. 고정 선택 후 Dev 점수는 V9와 같은 `0.695114`,
stress tier 실패는 Train/Dev 모두 0이었다. 따라서 이 head는 런타임에 넣지
않았다. 전체 결과는
[`binomial-score-v10-train-to-dev.json`](../experiments/results/binomial-score-v10-train-to-dev.json)에
보존한다.

ARM64 로컬 제출 이미지에서 공개 2,640문항을 두 번씩 실행한 최대 시간은
Fast/Balanced/Premium `13.045 / 12.721 / 12.754초`였고, 반복 출력 SHA-256은
tier별로 같았다. V7/V9 합성 엣지 42개도 timeout·형식·4 MiB 실패 없이
`42/42`를 통과했다. 로컬 ARM64 결과는
[`aggressive-v9-runtime-local.json`](../experiments/results/aggressive-v9-runtime-local.json),
엣지 전체 표는 [`EDGE_CASE_RESULTS_V9.md`](EDGE_CASE_RESULTS_V9.md)에 있다.

## 잔여 위험과 롤백

- 공개 stress 실패 0은 숨은 분포의 절대 예산 보장이 아니다.
- 공정 Dev 점수는 V6.1 `0.695170`보다 약 `0.000057` 낮아 사실상 동률이다.
- 공개 경쟁안의 검증 방식과 직접 비교할 수 없으며 우승을 보장하지 않는다.
- uplift head가 새로운 분포에서 틀리면 회수 예산은 점수 이득 없이 소비될 수 있다.
- V9를 비활성화하려면 제출 기본 artifact를 `aggressive_v7.load_artifact()`로
  되돌리면 된다.

## 재현

```console
PYTHONPATH=src:tools .venv-data/bin/python \
  tools/evaluate_selective_budget_v9.py \
  --train-input data/materialized/train/inputs.json \
  --train-outcomes data/train/outcomes.json \
  --eval-input data/materialized/dev/inputs.json \
  --eval-outcomes data/dev/outcomes.json \
  --base-artifact experiments/artifacts/aggressive-v5-train-only.v1.json \
  --deepmind-selection data/sources/deepmind-mathematics-selection.v1.json \
  --train-aime-selection data/train/aime-selection.json \
  --eval-aime-selection data/dev/aime-selection.json \
  --report experiments/results/selective-budget-v9-source-stress-train-to-dev.json
```
