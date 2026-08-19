<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V7 score objective 검토

## 목적

현재 오라클 gap의 대부분은 추가 예산보다 승격 순위에서 발생한다. V5의 모델별
절대 score Ridge와 다음 두 선형 objective를 같은 비용 예측·selector 아래에서
비교한다.

- `Light score + AX31/K1 uplift` 직접 회귀
- `num_generations`를 관측 신뢰도로 사용하는 모델별 가중 score Ridge

모든 head는 Train 1,760문항으로만 적합하고 Dev에는 고정 alpha 후보 전체를
보고한다. Dev 결과로 active candidate를 선택하지 않는다.

## 결과와 판단

원본 결과는
[`score-objectives-train-to-dev.json`](../experiments/results/score-objectives-train-to-dev.json)에
있다.

| 후보 | Dev weighted score | content standalone 예산 실패 |
| --- | ---: | ---: |
| V6.1 기준 | `0.695170` | 1 |
| direct uplift, alpha 10,000 | `0.694886` | 1 |
| generation-weighted, alpha 30,000 | `0.694318` | 1 |
| standalone-safe direct uplift, alpha 3,000 | `0.691705` | 0 |

두 objective 모두 V6.1 Train-only 기준을 넘지 못했다. 명시적 TF-IDF sparse
Ridge와 제한된 gradient boosting도 별도 진단에서 기준을 넘지 못했으므로
artifact와 런타임 복잡도를 추가하지 않았다. 특히 `best_candidate`는 보고용
최대값일 뿐 Dev 선택 결과가 아니며, active router를 변경하지 않는다.

따라서 score objective 변경은 활성화하지 않는다. 다음 checkpoint는 V5 예측을
유지한 채 batch content의 OOD 위험에 따라 비용 cap을 줄이는 selector와
Always-Light workload fallback을 검증한다. 이는 점수 개선과 독립적으로 tier
점수 전체가 0이 되는 하방을 먼저 줄이는 변경이다.
