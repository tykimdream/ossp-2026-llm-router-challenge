<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 라우터 일반화·예산 강건성 검증

## 목적

공개 split 전체에서 한 번 선택한 결과를 부분집합별로 잘라 보는 것만으로는
배치 구성이 바뀌었을 때의 예산 위험을 측정할 수 없다. 라우터는 배치 전체의
공통 비용 벌점을 계산하므로, 검증 group과 합성 batch를 각각 독립 실행 단위로
다시 선택해야 한다.

`tools/evaluate_router_robustness.py`는 고정 artifact의 예측을 문항별로 한 번
계산한 뒤 다음 모든 scenario에서 전역 selector를 다시 실행한다.

- 전체 split과 공개 provenance group의 standalone 실행
- 길이·언어·코드·수학·messages 구조의 content-only slice 실행
- 고정 seed의 batch-size bootstrap
- 특정 content/provenance group의 비율을 바꾼 mixture stress

보고서의 비용은 공개 outcome을 사용하는 진단값이다. 공식 scorer를 대체하지
않으며 비공개 평가 통과 확률을 주장하지 않는다.

## 검증 정보와 런타임 정보의 경계

DeepMind Mathematics와 AIME의 공개 provenance 선택 파일은 검증 group을
정의할 때만 사용한다. source label, `episode_id`, split 이름과 group 이름은
라우터 특징이나 배포 artifact에 포함하지 않는다. 나머지 slice는 prompt 또는
messages 내용에서 계산한 길이, Unicode 문자 비율과 공개 특징만 사용한다.

Bootstrap은 같은 문항을 복원 추출할 수 있다. 이는 중복 ID를 가진 프로토콜
입력을 만드는 것이 아니라, 동일한 prompt 분포가 반복되는 가상 batch에서
예측과 selector를 재생하는 오프라인 stress 계산이다.

## Champion gate

새 라우터는 평균 공개 점수만으로 승격하지 않는다. 최소 조건은 다음과 같다.

1. Train-only 또는 nested outer-fold artifact로 평가한다.
2. 모든 standalone group을 독립 batch로 다시 라우팅한다.
3. 지정한 batch-size bootstrap과 mixture stress의 예산 실패 수를 공개한다.
4. 예산 초과 scenario는 해당 tier 점수를 0으로 적용한 final score를 사용한다.
5. 점수·예산 개선 뒤에도 기존 결정론, ARM64 런타임과 edge-case gate를 별도로
   통과한다.

초기 목표는 standalone 예산 실패 0, 1,000개 이상의 block/mixture stress에서
예산 실패 0이다. 유한한 공개 stress의 0회 실패는 임의의 숨은 분포에 대한
수학적 보장이 아니다. 절대 보장이 필요한 workload의 fallback은
Always-Light만 가능하다.

## V6.1 Competitive 기준선

Train-only V5 artifact를 V6.1 런타임으로 실행한 고정 보고서는
[`aggressive-v6-competitive-robustness-dev.json`](../experiments/results/aggressive-v6-competitive-robustness-dev.json)에
있다. 전체 Dev는 Fast `1.143321`, Balanced `1.661573`, Premium `3.072558`로
통과했지만, 181개 `korean_hangul_10pct` group을 독립 batch로 다시 선택하면
Premium이 `4.117881`로 실패했다.

| 복원 추출 크기 | trial | Fast 실패 | Balanced 실패 | Premium 실패 |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 50 | 4 | 3 | 4 |
| 300 | 50 | 0 | 0 | 2 |
| 880 | 50 | 0 | 0 | 0 |

7개 group 비율을 `0.25/0.50/0.75/1.00`으로 바꾼 560개 mixture batch에서는
총 110개의 tier 예산 실패를 관측했다. 이 수치는 현재 정책의 상대 비교용
stress 결과이며 비공개 평가의 실패 확률 추정치가 아니다.

## 재현 예시

```console
PYTHONPATH=src python3 tools/evaluate_router_robustness.py \
  --input data/materialized/dev/inputs.json \
  --outcomes data/dev/outcomes.json \
  --artifact experiments/artifacts/aggressive-v5-train-only.v1.json \
  --deepmind-selection data/sources/deepmind-mathematics-selection.v1.json \
  --aime-selection data/dev/aime-selection.json \
  --bootstrap-sizes 100,300,880 \
  --bootstrap-trials 50 \
  --mixture-size 300 \
  --mixture-trials 20 \
  --report experiments/results/aggressive-v6-competitive-robustness-dev.json
```
