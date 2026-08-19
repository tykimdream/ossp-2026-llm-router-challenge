<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 제출용 라우터

## 활성 제출 경로

최종 컨테이너는 실험 모듈을 직접 실행하지 않습니다. 유일한 제출 진입점은
[`submission.py`](../src/ossp_router/submission.py)이고, 고정된
[`aggressive-router.v5.json`](../src/ossp_router/resources/aggressive-router.v5.json)
계수를 V6 런타임에서 동등한 단일 선형 head로 컴파일하고 V7의 Train-calibrated
adaptive budget reserve를 적용해 tier별 점수·비용을 예측합니다.

```text
container/entrypoint.py
  → ossp_router.submission
    → canonical content order
      → streaming feature + content cache
        → compiled tier-specific Ridge head
          → content-shift·small-batch reserve
            → 고정 48회 전역 비용 벌점 탐색
            → 원래 입력 순서로 결과 복원
```

V7 Adaptive는 V5의 계수와 tier별 승격 순위를 재학습·재조정하지 않고
compact 출력, streaming n-gram, 중복 내용 cache, ensemble head 컴파일과
token/message work guard를 적용합니다. 제출 wrapper는 canonical 정렬과 고정된 batch 최적화를 적용하여
입력 순서와 ID가 선택에 영향을 주지 않게 합니다.

| 항목 | 활성 값 |
| --- | --- |
| 컨테이너 진입점 | `ossp_router.submission:main` |
| 활성 predictor | Aggressive Router v7 Adaptive |
| 공정 Train-only → Dev 점수 | `0.691477` |
| 공정 Fast/Balanced/Premium 비용 | `1.145521 / 1.653162 / 3.006001` |
| group/bootstrap/mixture stress 실패 | `0 / 0 / 0` (V6.1은 `1 / 13 / 110`) |
| V6.1 점수 보험료 | `-0.003693` (약 0.53%) |
| 입력 순서·ID 변경 감사 | 세 tier 내용별 불일치 `0` |
| V6.1 ARM64 기준선 | `13.006 / 12.671 / 12.725초`, 모두 통과 |
| V7 ARM64·엣지 검증 | 최종 bake-off checkpoint에서 갱신 |
| 공식 제한 | 등급별 `90초`, `2 GiB`, CPU 2개 |

비용 한도에 대해서는 중요한 경계가 있습니다. 라우팅 시점에는 숨은 평가의
실제 출력 토큰과 전체 비용 분모가 없으므로 학습형 라우터가 수학적으로 절대
한도 미초과를 보장할 수는 없습니다. V7은 V5 artifact의 공격적 OOF 검증에
Train-only batch reserve를 더합니다. 공개 stress와 Dev는 모두 통과했지만 어떤
분포에서도 절대 보장이
필요하다면 모든 문항을 Light로 보내는 정책을 선택해야 합니다.

## 결정론 경계

런타임 선택은 다음 값을 사용하지 않습니다.

- 현재 시각, 남은 실행시간과 CPU 부하
- 난수와 난수 seed
- `episode_id`, `challenge_id`, `split`
- 입력 배열에서의 위치
- 네트워크와 외부 서비스 상태

같은 문항 집합과 tier에서는 다음 고정 절차를 사용합니다.

1. prompt 또는 role을 포함한 messages 내용을 canonical key로 변환
2. FNV-1a 내용 해시와 전체 내용으로 정렬
3. 고정 artifact를 컴파일한 선형 head로 예측
4. 모든 문항을 동시에 다시 계산하는 고정 48회 전역 이분 탐색
5. 모델 동점은 `Light → AX31 → K1` 고정 순서로 해소
6. 선택 뒤 원래 `episode_id` 순서로 출력만 복원

`order-audit-a/b` 1,024문항의 순서를 뒤집고 ID를 전면 교체한 감사에서 V6의
내용별 선택 불일치는 세 등급 모두 `0/1,024`였습니다. 모델 배정은 문항을
순서대로 Light에서 올리는 greedy 절차가 아니다. 배치 전체의 공통 벌점을
정한 뒤 모든 문항을 동시에 배정한다. Premium의 후단 AX31 추가 배정도 같은
전역 벌점 방식이다.

다음 항목은 비결정성이 아니라 입력 자체가 달라진 경우입니다.

| 변화 | 같은 선택 보장 | 이유 |
| --- | --- | --- |
| 같은 문항 집합의 순서만 변경 | 예 | canonical 정렬 |
| ID·split·challenge_id만 변경 | 예 | 선택 특징에서 제외 |
| 같은 실행을 반복 | 예 | 시계·난수·외부 상태 미사용 |
| 문항 집합 자체를 변경 | 아니오 | 전체가 공유하는 비용 예산이 달라짐 |
| artifact 또는 tier 변경 | 아니오 | 라우팅 정책 자체가 달라짐 |

학습 과정의 BLAS 구현, 재학습 데이터와 Docker 계층 생성 시각은 새로운
artifact나 이미지 다이제스트를 만들 때 영향을 줄 수 있습니다. 하지만 제출
이미지에는 학습 코드가 없고 고정 JSON 계수만 있으므로 공식 런타임 선택의
비결정성은 아닙니다.

## 대형 입력 처리

입력 스키마에 문항 수와 prompt 길이의 상한이 없으므로, 실행시간을 확인하며
중간에 정책을 바꾸지 않습니다. 대신 실행 전에 순서와 무관한 네 종류의 값을
계산합니다.

```text
문항 수 > 6,000
또는 전체 prompt 문자 수 > 30,000,000
또는 전체 message 수 > 100,000
또는 문자 수 + 3 × 추정 token 수 > 40,000,000
```

하나라도 참이면 V7은 전체 배치를 고정 Always-Light로 처리합니다. 이 경로는
시간에 따라 발동하지 않으므로 같은 배치의 반복·순서 변경 결과가 같습니다.
공개 2,640문항, 약 11.8 MiB는 항상 학습형 Ridge 경로를 사용합니다. 자세한
구현·비교·잔여 위험은 [`ROUTER_V7_ADAPTIVE.md`](ROUTER_V7_ADAPTIVE.md)에
있습니다.

## Robust v2 실험 후보

[`robust.py`](../src/ossp_router/robust.py)는 논의한 재계산을 시간 기반이 아닌
고정 비용 시나리오로 구현합니다.

- Fast/Balanced: Direct Uplift
- Premium: Hybrid
- 기본 비용 시나리오
- AX31 비용 `1.02배`, K1 비용 `1.05배` stress 시나리오
- 모든 시나리오 중 가장 큰 예측 비용으로 고정 80회 최적화
- 남은 예측 예산은 동일한 고정 알고리즘으로 AX31 fill

Train-only 구성의 Dev 결과는 `0.687983`, 비용은
`1.122335 / 1.588707 / 3.299693`입니다. Ridge Train-only보다 `0.000796`
높고 비용 여유도 크지만, 등급별 predictor와 stress 배수를 공개 Dev를 본 뒤
선택했으므로 독립 홀드아웃 승리로 주장하지 않습니다.

전체 공개 자료로 refit한 결과는 `0.705511`로 활성 제출 라우터보다 낮아 현재
제출 진입점으로 채택하지 않았습니다.

## 검증 명령

```console
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_submission_router tests.test_aggressive_v6_router \
  tests.test_aggressive_v7_router

docker build --platform linux/arm64 \
  --file container/Dockerfile --tag ossp-router:aggressive-v6 .

PYTHONPATH=src .venv/bin/python tools/check_runtime.py \
  --image ossp-router:aggressive-v6 \
  --report experiments/results/aggressive-v6-runtime.json
```
