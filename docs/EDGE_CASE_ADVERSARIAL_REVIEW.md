<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V4/V5 엣지케이스 적대적 리뷰

## 결론

V4와 V5 모두 90초 timeout 없이 14개 합성 케이스의 42개 tier 조합을
완료했습니다. 그러나 두 버전 모두 Fast 출력이 공식 4 MiB를 93바이트 넘는
재현 가능한 실행 실패가 하나 있었고, 현재 workload guard가 실제 feature
추출 비용을 충분히 설명하지 못한다는 증거도 확인했습니다.

| 지표 | V4 | V5 | 해석 |
| --- | ---: | ---: | --- |
| 운영 판정 통과 | 41/42 | 41/42 | 양쪽 모두 같은 Fast 출력 경계에서 실패 |
| 전체 로컬 실행 시간 | 112.222초 | 132.793초 | V5가 이 합성 묶음에서 18.3% 느림 |
| 최대 RSS | 185.5 MiB | 188.8 MiB | 100만 자 token-density 입력에서 발생 |
| timeout | 0 | 0 | 로컬 macOS 기준, 공식 컨테이너 보장은 아님 |
| 순서·ID 감사 불일치 | 0 | 0 | 두 라우터 모두 세 tier에서 통과 |

합성 입력에는 모델별 실제 outcome이 없습니다. 따라서 모델 선택의 의미적
타당성은 취약점 후보로만 다루며, 실제 품질·비용 문제로 확정하려면 outcome이
있는 독립 평가가 필요합니다.

## 발견 사항

### P0 — pretty JSON 때문에 유효 결과가 출력 볼륨을 넘는다

128자 `episode_id` 21,399개에서 두 라우터의 Fast 결과는 4,194,397바이트로
공식 한도 4,194,304바이트를 93바이트 넘었습니다. 한 문항 적은 결과는
4,194,201바이트로 통과합니다. 라우팅은 정상 종료하지만 공식 환경에서는 tier
실패가 됩니다.

원인은 [`write_submission_atomic`](../src/ossp_router/heuristic.py)이 사람이 읽기
좋은 2칸 들여쓰기 JSON을 쓰는 데 있습니다. 동일 결과를 compact JSON으로 쓰면
3,637,995바이트로 556,402바이트가 줄어듭니다.

개선안:

1. submission 출력 전용 serializer를 `separators=(",", ":")`로 compact하게
   바꿉니다. 학습 artifact와 보고서의 pretty JSON은 그대로 둡니다.
2. 실제 직렬화한 UTF-8 바이트 수를 원자적 교체 전에 확인하는 회귀 테스트를
   추가합니다.
3. compact 형식으로도 4 MiB를 넘는 입력은 완전한 결과를 만들 방법이 없으므로,
   실행 전에 명확한 오류를 남기도록 합니다.

### P1 — 문자 수 guard는 token feature의 메모리 비용을 방어하지 못한다

현재 guard는 episode 수와 전체 문자 수만 봅니다. 하지만 feature 추출기는
모든 token을 리스트로 만들고, 다시 단어 1·2·3-gram 문자열 리스트를 만듭니다.

| 입력 | 문자 수 | 구조 | 최대 RSS V4/V5 |
| --- | ---: | --- | ---: |
| `character-guard-at-30000000` | 30,000,000 | 무공백 token 1개 | 161.3 / 166.5 MiB |
| `token-density-1000000` | 1,000,000 | 짧은 token 500,000개 | 185.5 / 188.8 MiB |
| `message-fanout-64000` | 1,914,420 | message 객체 64,000개 | 최대 85.0 MiB |

30분의 1 길이인 token-density 입력이 더 많은 메모리를 사용했습니다. 현재
3천만 자 한도 안에서 짧은 token을 더 늘리면 공식 2 GiB 메모리 한도를 넘을
가능성이 큽니다.

개선안:

1. `_hashed_features`에서 `tokens`와 `values` 전체 리스트를 제거하고,
   `deque(maxlen=3)`로 1·2·3-gram을 즉시 hash bin에 누적합니다.
2. 문자 수뿐 아니라 token 수, message 수, 추정 n-gram 수를 포함한
   `work_units` guard를 둡니다.
3. 문항별 hash token 상한을 두고 head+tail을 결정론적으로 표본화합니다.
   dense 길이·언어·기호 특징은 전체 입력에서 유지할 수 있습니다.
4. 2 GiB 컨테이너에서 token-density의 크기를 단계적으로 늘리는 회귀 테스트를
   추가합니다.

### P1 — 합성 난이도와 상위 모델 배정 우선순위가 반대로 움직인다

`skewed-budget-cliff-5000`은 쉬운 산술 4,950개와 형식적 증명·분산 알고리즘 등
고난도 50개로 구성했습니다. V4와 V5의 결과는 완전히 같았습니다.

| Tier | 쉬운 4,950개 | 고난도 50개 |
| --- | --- | --- |
| Fast/Balanced | Light 4,905 / AX31 45 | Light 50 |
| Premium | AX31 4,905 / K1 45 | AX31 50 |

즉, 제한된 45개 상위 모델 배정이 모두 쉬운 산술 쪽에 갔고 고난도 그룹은
하나도 우선 배정되지 않았습니다. `k1-pressure-5000`에서도 Premium은 두 버전 모두
5,000개 전부 AX31을 선택했습니다. 공개 학습 분포에서 짧은 산술·코드 형식이
K1 uplift 신호로 강하게 학습됐을 가능성이 있습니다.

이 결과만으로 실제 오답이나 비용 낭비를 확정할 수는 없습니다. 다음 후보를
채택하기 전에 실제 outcome이 있는 독립 데이터에서 그룹별 uplift와 비용
calibration을 확인해야 합니다.

개선안:

1. `simple arithmetic`, `formal reasoning`, `code trace`, `long context`,
   `multilingual/unicode` 그룹별 OOF score·비용·상위 모델 배정률을 보고합니다.
2. template fold뿐 아니라 문제 유형을 보존한 worst-group gate를 추가합니다.
3. 형식적 추론 신호의 단조 prior 또는 별도 uplift head를 후보로 비교하되,
   전체 평균이 아니라 최악 그룹과 비용 한도를 함께 통과시킵니다.
4. 의미는 같고 표면 형식만 다른 prompt 쌍을 만들어 배정 안정성을 측정합니다.

### P1 — V5 ensemble은 Fast와 Premium에서 계산을 중복한다

이 합성 묶음에서 V5의 전체 실행 시간은 V4보다 18.3% 길었습니다. tier별
V5/V4 중앙 시간 비율은 Fast 1.295배, Balanced 0.996배, Premium 1.206배입니다.
Balanced는 head 하나를 쓰지만 Fast와 Premium은 세 Ridge head를 각각 평가한
뒤 평균하므로 짧은 prompt가 많은 입력에서 차이가 뚜렷합니다.

선형 head의 평균은 계수와 절편을 미리 평균한 단일 선형 head와 같습니다.
artifact 생성 시 tier별 score/log-cost head를 하나로 접으면 예측 의미를
유지하면서 반복 dot product를 제거할 수 있습니다. 부동소수점 합산 순서 차이가
예산 경계 선택을 바꿀 수 있으므로 전체 공개 데이터와 이 엣지케이스에서 결정
SHA-256 동등성 검사를 통과한 경우에만 적용해야 합니다.

### P2 — hard guard가 큰 품질·성능 절벽을 만든다

V5 Fast는 6,000문항에서 5.120초였지만 6,001문항에서는 휴리스틱 fallback으로
0.165초였습니다. 문자 수도 30,000,000자에서 최대 11.526초, 한 글자 많은
입력에서 최대 2.873초로 줄었습니다. 성능 방어로는 유효하지만 한 문항이나 한
글자 차이로 라우팅 정책 전체가 바뀝니다.

개선안:

- batch 전체 대신 문항별 복잡도 한도를 적용해 정상 문항은 학습 경로에 남깁니다.
- batch가 큰 경우 content별 prediction cache와 compact vectorizer로 먼저
  처리량을 줄이고, 그래도 work budget을 넘을 때만 대칭적인 fallback을 씁니다.
- 경계 전후 입력의 모델 선택률과 공개 outcome 점수를 함께 회귀 지표로 둡니다.

### P2 — 동일 prompt를 매번 다시 vectorize한다

동일 고난도 prompt 5,000개에서 V4는 최대 4.511초, V5는 최대 6.391초를
사용했습니다. 내용이 같은 episode의 score·비용 prediction은 같으므로,
`prompt` 또는 `(role, content)` tuple을 키로 한 bounded cache로 특징과 예측을
한 번만 계산할 수 있습니다. 다만 동일 내용의 일부만 다른 모델로 보내는 것은
ID나 입력 순서 의존성을 만들 수 있으므로 허용하면 안 됩니다. 동일 내용 그룹을
하나의 가중치 있는 항목으로 최적화한 뒤 같은 결정을 복원해야 합니다.

### 통과한 방어

- V4/V5 모두 42개 조합에서 timeout이 없었습니다.
- `order-audit-a/b`의 ID와 순서를 바꿔도 두 버전, 세 tier 모두 내용별 선택이
  완전히 같았습니다.
- 유니코드와 64,000-message 입력에서 예외나 잘못된 JSON은 없었습니다.
- 30,000,000자 입력도 로컬에서는 90초와 2 GiB 안에 완료됐습니다.

## 권장 구현 순서

1. **출력 compact화**: 작은 변경으로 실제 실행 실패 하나를 제거합니다.
2. **streaming n-gram + work-unit guard**: 메모리 초과 가능성을 닫습니다.
3. **V5 head 사전 평균**: Fast/Premium의 중복 계산을 줄입니다.
4. **동일 content prediction cache**: 반복 입력의 시간을 줄입니다.
5. **outcome 기반 worst-group 평가**: semantic hard-tail 배정 문제를 검증하고
   그 결과로 V6 모델 변경 여부를 결정합니다.

## 다음 버전의 통과 기준

- 14개 케이스의 42개 tier 조합 모두 유효한 4 MiB 이하 결과 생성
- `token-density-1000000` 최대 RSS 100 MiB 이하를 목표로 개선
- V5-equivalent Fast/Premium 시간이 V4의 1.1배 이내
- 순서·ID 감사 불일치 0 유지
- 공개 Train-only → Dev 점수와 tier 비용 gate를 악화시키지 않음
- hard/easy 그룹별 실제 uplift·비용표를 남기고 최악 그룹 회귀가 없을 것

원시 측정값은 [`edge-cases-v4-v5.json`](../experiments/results/edge-cases-v4-v5.json),
전체 비교표는 [`EDGE_CASE_RESULTS.md`](EDGE_CASE_RESULTS.md)에 있습니다.
