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

## v3 — Train OOF 기반 robust router

### 문제

v2의 predictor 조합과 stress 배수에는 Dev 결과가 관여했고, 고정 `1.02/1.05`
배수는 K1 비용의 긴 꼬리를 설명하기에 약했습니다. 대형 입력도 fallback 여부를
결정하기 전에 전체를 정렬했으며, 고정 80회 이분 탐색은 필요 이상이었습니다.

### 선택

- Train 템플릿 그룹 5-fold OOF만으로 predictor와 안전계수 선택
- Ridge와 Uplift 혼합 비교 후 Uplift-only 채택
- 평균이 아니라 전체 OOF와 최악 fold 비용을 함께 내부 목표에 맞춤
- 근거가 약한 가상 stress 배수를 제거
- 대형 workload guard를 정렬 앞으로 이동
- 공개 2,640문항에서 결과가 같음을 확인하고 이분 탐색을 고정 48회로 축소

### 검증과 판단

- 공정 Train-only → Dev 점수: `0.685966`
- 비용 비율: Fast `1.127580`, Balanced `1.608746`, Premium `2.694441`
- Train+Dev refit의 Dev 참고 점수: `0.698693`
- v2보다 검증 규율과 비용 여유는 좋아졌지만 공정 점수가 v1보다 낮아 활성
  제출 라우터로 승격하지 않고 robust 실험으로 보존했습니다.

### 기각한 대안과 롤백

- V2 Hybrid Premium은 Train OOF 실제 비용이 전체 `4.013`, 최악 fold
  `5.010`으로 내부 목표와 공식 한도를 넘어 제외했습니다.
- 80회 선택기는 48회와 선택 차이가 없어 추가 계산만 발생했습니다.
- 활성 제출은 v1을 유지하며, v3 롤백 artifact는 `robust-router.v3.json`입니다.

## v4 — 512-bin Ridge와 worst-fold 비용 보정

### 문제

v3는 비용 여유를 늘렸지만 v1보다 공정 Dev 점수가 낮았습니다. 품질을 다시
올리되 Dev에 맞춘 규칙을 추가하지 않고, Train 안에서만 모델 용량과 비용
안전계수를 선택해야 했습니다.

### 선택

- 직접 정책 분류와 pairwise uplift 대신 검증된 score·log-cost Ridge 유지
- Train 템플릿 그룹 5-fold OOF에서 256/512/1024 hash 차원 비교
- 512-bin을 채택하고 1024-bin은 이득 부재와 런타임 증가로 기각
- pooled OOF와 최악 template fold를 모두 통과하도록 tier 안전계수 보정
- 선택 로직은 시계·난수 없이 고정 반복과 canonical content order 유지

### 검증과 판단

- 공정 Train-only → Dev 점수: `0.689318` (v1 `0.687187`)
- 공정 비용 비율: Fast `1.157468`, Balanced `1.596435`, Premium `2.725700`
- 전체 공개 OOF 최악 fold: Fast `1.172459`, Balanced `1.815622`,
  Premium `3.470637`
- Train+Dev refit artifact의 공개 전체 실제 점수: `0.696667`
- 공개 전체 실제 비용: Fast `1.155856`, Balanced `1.629934`,
  Premium `3.020807`
- 공식 컨테이너 런타임: `16.825 / 17.711 / 18.133초`
- Dev 역순·ID·split·challenge 교체 감사: 세 등급 모두 불일치 `0/880`
- 공정 점수와 모든 검증 gate를 통과하여 v4를 활성 제출 artifact로 채택했습니다.

### 안전 한계와 롤백

- 라우팅 시점에는 숨은 평가의 실제 출력 토큰과 전체 비용 분모를 알 수 없어,
  학습형 라우터는 수학적인 절대 예산 보장을 할 수 없습니다. 절대 보장이
  필요하면 모든 문항을 Light로 보내는 정책만 가능합니다.
- v4는 공식 한도보다 낮은 내부 목표와 최악 fold 보정으로 위험을 낮추지만,
  이 차이를 보장으로 표현하지 않습니다.
- 롤백 artifact는 `competition-router.v1.json`; v4 재현 artifact는
  `risk-router.v4.json`입니다.

## v5 — Tier-specific aggressive Ridge ensemble

### 문제

v4는 하나의 `alpha=10000` Ridge를 모든 tier에 사용해 안정적이었지만, Fast와
Premium의 서로 다른 bias-variance 구간을 활용하지 못했습니다. 또한 내부 목표
`1.18/1.85/3.5`가 공식 한도보다 크게 낮아 대회 점수의 상방을 충분히 쓰지
못했습니다.

### 선택

- 512-bin 특징은 유지하고 tier마다 Train OOF 상위 Ridge alpha를 고정 ensemble
- Fast `300/500/5000`, Balanced `10000`, Premium `3000/10000/15000`
- 공격적 내부 목표를 Fast `1.23`, Balanced `1.95`, Premium `3.85`로 상향
- pooled 비용과 최악 template fold가 모두 내부 목표 아래인 안전계수만 허용
- Fast K1 금지, Premium AX31 fill, 고정 48회 선택 유지
- 문항 특징은 한 번만 추출한 뒤 여러 head에 공유해 ensemble 런타임을 억제

### 검증과 판단

- Train OOF 점수: `0.670568`
- 공정 Train-only → Dev 점수: `0.695170` (v4 `0.689318`, `+0.005852`)
- 공정 비용: Fast `1.143321`, Balanced `1.661573`, Premium `3.072558`
- Train OOF 최악 fold: Fast `1.200492`, Balanced `1.913526`,
  Premium `3.771347`
- 전체 공개 refit 실제 점수: `0.703277` (v4 `0.696667`)
- 전체 공개 실제 비용: `1.152316 / 1.744540 / 2.931772`
- ARM64 컨테이너 런타임: `13.592 / 12.434 / 14.170초`
- Dev 역순·ID·split·challenge 교체 감사: 세 등급 불일치 `0/880`
- 점수, 비용, 결정론, 런타임 gate를 모두 통과하여 v5를 활성화했습니다.

### 안전 한계와 롤백

- v5는 하방 최소화보다 공식 한도에 가까운 공격적 목표로 상방을 우선합니다.
- 공개 최악 fold는 모두 공식 한도 아래지만 숨은 출력 토큰의 절대 보장은
  아니며, 한도 초과 시 tier 전체가 0점이 되는 위험은 남습니다.
- 즉시 롤백 가능한 이전 champion은 `risk-router.v4.json`; v5 artifact는
  `aggressive-router.v5.json`입니다.

## v6.0 — Conservative operational hardening

### 문제

V4/V5 합성 엣지 평가에서 pretty submission JSON의 4 MiB 초과, token-density
입력의 약 189 MiB RSS, 반복 prompt 재계산, ensemble dot product 중복과 문자
수만 보는 workload guard가 발견됐습니다. V5의 공격적 비용 목표도 한도 초과가
tier 전체 0점이 되는 규칙에 비해 위험했습니다.

### 선택

- V5 학습 계수는 동결하고 원시 특징 공간의 단일 선형 head로 컴파일
- word 1·2·3-gram을 중간 리스트 없이 streaming hash
- 같은 prompt/messages의 prediction을 내용 기반으로 cache
- 문항·문자뿐 아니라 message와 `문자 + 3×token` work unit guard 추가
- 제출 JSON만 compact 직렬화
- 예측 비용 목표를 Fast/Balanced/Premium `1.15/1.80/3.00`으로 하향
- 순차 greedy 없이 고정 48회 공통 벌점으로 모든 문항을 전역 동시 배정

### 검증과 판단

- 공정 Train-only → Dev: `0.685256`, 비용 `1.142155 / 1.569580 / 2.523548`
- V5 공정 Dev 대비 점수 `-0.009915`, 세 tier 예산 초과 0
- V5/V6 엣지 84회 모두 성공, timeout·형식·4 MiB 실패 0
- V6 42회 합계 `100.288초`, V5 `132.779초`
- 50만-token RSS `34.0 MiB`, V5 `187.2 MiB`
- ARM64 컨테이너 공개 2,640문항 `12.762 / 12.746 / 12.727초`, 세 tier 통과
- 1,024문항 역순·ID 교체 감사: 세 tier 내용별 불일치 0
- 운영 실패와 예산 위험 최소화 가능성을 확인한 Safe 실험으로 보존

### 안전 한계와 롤백

- 숨은 실제 출력 token을 모르므로 Light 외 모델을 쓰는 한 절대 비용 보장은
  불가능합니다. 공개 통과와 안전 여유를 보장으로 표현하지 않습니다.
- 의미적 hard-tail 오분류와 hard guard의 배치 전체 정책 절벽은 남아 있습니다.
- 점수 우선 롤백은 V5 artifact와 `aggressive_v5.py`로 재현할 수 있습니다.
- 구현·수치·잔여 위험은 [`ROUTER_V6.md`](ROUTER_V6.md)에 기록합니다.

## v6.1 — Competitive operational hardening

### 문제

V6.0은 운영 취약점을 해결했지만 보수적인 비용 목표 때문에 공정 Dev 점수가
V5 대비 `0.009915` 낮아졌다. compact 출력·streaming hash·cache·compiled head는
점수 하락 없이 적용할 수 있으므로 운영 변경과 배정 정책을 분리해야 했다.

### 선택

- V6.0의 모든 운영 hardening 유지
- 고정된 별도 안전계수를 제거하고 사용 중인 V5 artifact의 tier별 safety ratio와
  Premium AX31 fill ratio를 그대로 상속
- `6.1-competitive`를 런타임 버전으로 기록
- V5 대비 prediction 정밀도뿐 아니라 최종 모델 배정 SHA와 case별 불일치를 gate로 추가

### 검증과 판단

- 공정 Train-only → Dev: V5/V6.1 모두 `0.695170`, 세 tier 불일치 `0/880`
- 공정 비용: 양쪽 모두 `1.143321 / 1.661573 / 3.072558`
- full-public 2,640문항 replay: 양쪽 모두 `0.703277`, 세 tier 불일치 `0/2,640`
- 엣지 42개 case-tier: V5 대비 모델 배정 불일치 0, 실행 실패 0
- 엣지 합계 `131.981 → 99.213초`, 50만-token RSS `187.0 → 32.3 MiB`
- ARM64 컨테이너 `13.006 / 12.671 / 12.725초`, 세 tier 통과
- V5의 경쟁 점수와 V6의 운영 개선을 동시에 보존하여 활성 제출 경로로 채택

### 안전 한계

- V5의 공격적 배정과 완전히 같으므로 공개 OOF 통과 근거도 같지만 숨은 실제
  비용의 절대 보장은 여전히 불가능합니다.
- 출력은 공식 규모와 합성 21,399문항 경계에서 통과했지만, episode 수 상한이
  없는 프로토콜의 임의 크기 입력까지 4 MiB를 보장할 수는 없습니다.
