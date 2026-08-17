<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 제출용 라우터

## 활성 제출 경로

최종 컨테이너는 실험 모듈을 직접 실행하지 않습니다. 유일한 제출 진입점은
[`submission.py`](../src/ossp_router/submission.py)이고, 고정된
[`risk-router.v4.json`](../src/ossp_router/resources/risk-router.v4.json)
계수로 512-bin Ridge 점수·비용을 예측합니다.

```text
container/entrypoint.py
  → ossp_router.submission
    → canonical content order
      → competition Ridge
        → 고정 80회 비용 벌점 탐색
          → 원래 입력 순서로 결과 복원
```

v4는 Train-only → Dev 공정 검증에서 기존 v1을 이긴 뒤 같은 구조를 공개
2,640문항 전체로 refit했습니다. 제출 wrapper는 canonical 정렬과 고정된
batch 최적화를 적용하여 입력 순서와 ID가 선택에 영향을 주지 않게 합니다.

| 항목 | 활성 값 |
| --- | --- |
| 컨테이너 진입점 | `ossp_router.submission:main` |
| 활성 predictor | Risk Router v4, 512-bin Ridge |
| 공정 Train-only → Dev 점수 | `0.689318` |
| 공정 Fast/Balanced/Premium 비용 | `1.157468 / 1.596435 / 2.725700` |
| 공개 2,640문항 컨테이너 런타임 | `16.825 / 17.711 / 18.133초` |
| 공식 제한 | 등급별 `90초`, `2 GiB`, CPU 2개 |

비용 한도에 대해서는 중요한 경계가 있습니다. 라우팅 시점에는 숨은 평가의
실제 출력 토큰과 전체 비용 분모가 없으므로 학습형 라우터가 수학적으로 절대
한도 미초과를 보장할 수는 없습니다. v4는 공식 한도보다 낮은 내부 목표와
Train OOF 최악 fold 보정으로 위험을 줄입니다. 어떤 분포에서도 절대 보장이
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
3. 고정 artifact로 예측
4. 고정 80회 이분 탐색
5. 모델 동점은 `Light → AX31 → K1` 고정 순서로 해소
6. 선택 뒤 원래 `episode_id` 순서로 출력만 복원

20문항을 무작위로 섞은 10,000회 감사에서 Fast, Balanced, Premium 모두
달라진 실행과 결정이 `0`이었습니다. 활성 v4의 전체 Dev 880문항을 역순으로
바꾸고 ID·split·challenge_id를 전면 교체한 감사에서도 세 등급 불일치가
`0/880`이었습니다.

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
중간에 정책을 바꾸지 않습니다. 대신 실행 전에 순서와 무관한 두 값만
계산합니다.

```text
문항 수 > 6,000
또는 전체 prompt 문자 수 > 30,000,000
```

하나라도 참이면 전체 배치를 고정 Prompt Heuristic으로 처리합니다. 이 경로는
시간에 따라 발동하지 않으므로 같은 배치의 반복·순서 변경 결과가 같습니다.
공개 2,640문항, 약 11.8 MiB는 항상 학습형 Ridge 경로를 사용합니다.

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
  tests.test_submission_router tests.test_robust_router

docker build --platform linux/arm64 \
  --file container/Dockerfile --tag ossp-router:risk-v4 .

PYTHONPATH=src .venv/bin/python tools/check_runtime.py \
  --image ossp-router:risk-v4 \
  --report experiments/results/risk-v4-runtime.json
```
