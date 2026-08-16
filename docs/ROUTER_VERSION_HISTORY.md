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
