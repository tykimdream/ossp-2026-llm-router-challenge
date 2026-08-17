<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 라우터 개정 이력과 의사결정 기록

이 문서는 라우터 버전별 가설, 검증 프로토콜, 채택·기각 근거와 롤백 지점을
기록합니다. 공개 데이터에 다시 적합한 점수는 일반화 근거로 사용하지 않으며,
Train-only → Dev 또는 그룹 OOF 결과를 우선합니다.

모든 버전은 다음 불변식을 지킵니다.

- 모델 선택은 prompt 또는 messages 내용과 tier만 사용한다.
- `episode_id`, `split`, `challenge_id`, 입력 위치를 선택 특징으로 쓰지 않는다.
- 시계, 난수와 네트워크 상태에 따라 정책을 바꾸지 않는다.
- 같은 문항 집합은 입력 순서가 달라도 내용별 선택이 같다.
- 공식 예산보다 낮은 내부 목표를 사용하고, 예산 초과 후보는 승격하지 않는다.

## v1 — Content Ridge와 결정론적 제출 경계

### 문제

제공 휴리스틱은 세밀한 문제 유형과 모델별 품질 상승을 구분하지 못했고, 단순
hash-regex 정책은 예산 한도에 지나치게 가까웠습니다. 학습형 정책과 공식 제출
경계를 분리해 품질과 운영 안정성을 함께 확보할 필요가 있었습니다.

### 선택

- 40개 dense prompt 특징과 256-bin 단어·문자 feature hashing
- 모델별 score와 log-cost를 예측하는 Ridge head
- 예측 품질에서 비용 벌점을 빼는 batch 단위 선택
- prompt 내용의 canonical 정렬 후 원래 입력 순서로 결과 복원
- 대형 workload의 순서 독립적 휴리스틱 fallback

### 검증과 판단

- Train-only → Dev 점수: `0.687187`
- 비용 비율: Fast `1.190838`, Balanced `1.884889`, Premium `2.923078`
- Train+Dev refit의 Dev 참고 점수: `0.716477`
- v1은 첫 학습형 비교 기준과 결정론적 제출 경계로 채택했습니다.

### 알려진 한계와 롤백

- 선형 score 회귀는 희소한 모델 상승 사건을 충분히 분리하지 못합니다.
- log-cost 오차가 분포 이동 시 예산 위험으로 이어질 수 있습니다.
- 롤백 artifact: `competition-router.v1.json`

## v2 — Hybrid gate와 고정 비용 stress

### 문제

v1은 전체 점수는 높았지만 K1의 긴 출력 비용 꼬리와 분포 이동을 직접 방어하지
못했습니다. 특히 강한 모델의 예측 비용이 작게 추정되면 한 tier 전체가 0점이
될 수 있었습니다.

### 선택

- Light 대비 직접 uplift 예측을 Fast와 Balanced 후보로 분리
- K1을 코드·수학·숫자 등 근거가 있는 prompt로 제한하는 Hybrid gate
- AX31 `1.02배`, K1 `1.05배`의 고정 비용 stress 시나리오
- 기본·stress 시나리오의 최대 예측 비용으로 batch 선택
- Fast에서 K1을 금지하고 Premium 잔여 예산을 AX31로 채움

### 검증과 판단

- Train-only component의 Dev 참고 점수: `0.687983`
- 비용 비율: Fast `1.122335`, Balanced `1.588707`, Premium `3.299693`
- 전체 공개 refit의 Dev 참고 점수: `0.705511`
- v1보다 비용 여유는 커졌지만 predictor 조합과 stress 배수 선택에 Dev가
  관여했으므로 공정 champion으로 승격하지 않고 실험 후보로 보존했습니다.

### 기각한 대안과 롤백

- Train 비용만으로 보정한 Hybrid는 Dev Balanced `2.056211`로 공식 한도를
  넘어서 기각했습니다.
- Extra Trees는 작은 표본에서 과적합해 Dev `0.675710`에 그쳤습니다.
- 롤백 지점은 v1의 `competition-router.v1.json`입니다.
