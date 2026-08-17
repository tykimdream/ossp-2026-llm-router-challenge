<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 역대 라우터 벤치마크

`공정`은 해당 Dev outcome을 모델·규칙·안전계수 선택에 사용하지 않았다는
뜻입니다. `참고`는 Train+Dev refit 또는 Dev를 본 뒤 구성한 결과이므로 숨은
평가 일반화 점수로 해석하지 않습니다.

## 성능과 비용

| 라우터 | 평가 | 점수 | Fast | Balanced | Premium | 결정론 | 현재 판단 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| Aggressive v6 Full Public | 참고 | 0.708722 | 1.138882 | 1.601014 | 2.605352 | 강화됨 | **활성 제출 경로** |
| Aggressive v5 Full Public | 참고 | 0.714915 | 1.178468 | 1.753652 | 2.893612 | 강화됨 | 이전 활성 경로 |
| Risk Router v4 Full Public | 참고 | 0.710341 | 1.164139 | 1.638858 | 3.021285 | 강화됨 | 이전 활성 경로 |
| Submission Router v1 | 참고 | **0.716477** | 1.168267 | 1.834377 | 3.367389 | 강화됨 | 이전 활성 경로 |
| Ridge Full Public | 참고 | **0.716477** | 1.168267 | 1.834377 | 3.367389 | 예 | 이전 predictor |
| Hybrid Full Public | 참고 | 0.714801 | 1.158894 | 1.761224 | 3.287951 | 예 | 안전하지만 점수 하락 |
| Robust v2 Full Public | 참고 | 0.705511 | 1.146125 | 1.647003 | 3.214455 | 강화됨 | 기각·보존 |
| Robust v3 Full Public | 참고 | 0.698693 | 1.152963 | 1.647003 | 2.807513 | 강화됨 | 기각·보존 |
| Uplift Full Public | 참고 | 0.701023 | 1.150062 | 1.670668 | 2.959508 | 예 | 기각·보존 |
| Provided Hash Regex | 참고 | 0.695369 | 1.235989 | 1.961506 | 3.985205 | 예 | 한도에 너무 근접 |
| Robust v2 Train-only 구성 | Dev 조정 | 0.687983 | 1.122335 | 1.588707 | 3.299693 | 강화됨 | 안전 후보, 공정 주장 안 함 |
| Aggressive v5 Train-only | 공정 | **0.695170** | 1.143321 | 1.661573 | 3.072558 | 강화됨 | 점수 champion |
| Aggressive v6 Train-only | 공정 | 0.685256 | 1.142155 | 1.569580 | 2.523548 | 강화됨 | 운영 안전 우선 |
| Risk Router v4 Train-only | 공정 | 0.689318 | 1.157468 | 1.596435 | 2.725700 | 강화됨 | 이전 champion |
| Ridge Train-only | 공정 | **0.687187** | 1.190838 | 1.884889 | 2.923078 | 예 | 공정 비교 기준 |
| Hybrid Safe | 공정 | 0.686903 | 1.158051 | 1.832795 | 3.365117 | 예 | Ridge보다 0.000284 낮음 |
| Direct Uplift Train-only | 공정 | 0.685966 | 1.127580 | 1.608746 | 2.694441 | 예 | 높은 안전 여유 |
| Robust v3 Train-OOF | 공정 | 0.685966 | 1.127580 | 1.608746 | 2.694441 | 강화됨 | V2 취약점 보완, 점수는 하락 |
| Extra Trees Uplift | 공정 | 0.675710 | 1.123850 | 1.625282 | 1.981720 | seed 고정 | 런타임 artifact 미제작 |
| Prompt Heuristic | 공정 | 0.655341 | 1.072334 | 1.367866 | 2.102044 | 예 | 경량 fallback |
| Always Light | 공정 | 0.619318 | 1.000000 | 1.000000 | 1.000000 | 예 | 하한선·형식 기준 |
| Hybrid Train-calibrated | 공정 | 0.483580 | 1.187790 | **2.056211** | 3.494137 | 예 | Balanced 0점 |
| Hash Regex Train-only | 공정 | 0.428864 | **1.257062** | 1.961506 | 3.673910 | 예 | Fast 0점 |

굵은 비용은 공식 한도 초과입니다. Fast `1.25`, Balanced `2.0`, Premium
`4.0`을 조금이라도 넘으면 해당 등급 점수는 0입니다.

## 특장점과 실패 모드

| 계열 | 가장 잘하는 것 | 주요 장점 | 주요 약점·예외 |
| --- | --- | --- | --- |
| Aggressive v6 | 운영 안전성 | compact 출력, streaming hash, 중복 cache, 보수적 비용 목표 | 공정 Dev 점수 약 0.0099 하락, 절대 비용 보장은 아님 |
| Submission wrapper | 제출 안정성 | canonical 정렬, multi-signal workload fallback, 활성 경로 분리 | guard 경계에서 배치 전체 정책 절벽 |
| Aggressive v5 | 점수 상방 | tier별 alpha ensemble, 공격적 OOF 예산 목표, 공유 특징 추출 | v4보다 Premium 비용과 ensemble 복잡도 증가 |
| Ridge | 전체 점수 | 단순·빠름, 공개 확인 점수 최고 | 선형 관계, 분포 이동 시 비용 오차 |
| Hybrid | 위험한 선택 차단 | Fast K1 금지, 규칙 설명 가능 | 규칙 밖의 좋은 문제도 차단 가능 |
| Direct Uplift | Light 대비 승격 판단 | Fast/Balanced 비용 여유, 직접적인 학습 목표 | Premium 품질 상한이 Ridge보다 낮음 |
| Robust v2 | 비용 충격 대응 | 고정 stress 시나리오, 시간 비의존, tier별 전문화 | 공개 점수 약 0.011 하락, Dev 구성 선택 편향 |
| Robust v3 | 검증·운영 강건성 | Train 최악 fold 보정, 사전 workload guard, 48회 고정 선택 | V1/V2보다 점수가 낮고 실제 숨은 비용 절대 보장은 아님 |
| Extra Trees | 비선형 조합 | 복잡한 특징 상호작용 | 작은 자료에서 과적합, 의존성과 artifact 변환 필요 |
| Hash Regex | 템플릿·단어 신호 | 제공 기준선 중 강함 | 안전계수에 극도로 민감, 한 tier 전체 0점 위험 |
| Prompt Heuristic | 초고속 fallback | 학습 artifact 불필요, 설명 가능 | 세밀한 문제 유형과 K1 가치를 구분하지 못함 |

## 런타임과 안정성

| 검사 | 결과 |
| --- | --- |
| 활성 v6 이미지 공개 2,640문항 | Fast/Balanced/Premium `12.762 / 12.746 / 12.727초` |
| 활성 v6 엣지케이스 | `42/42` 성공, timeout·형식·4 MiB 실패 `0` |
| v6 50만-token 입력 | 최대 RSS `34.0 MiB` (v5 `187.2 MiB`) |
| v6 동일 prompt 5,000개 | `0.412~0.419초` (v5 `4.033~6.242초`) |
| v6 엣지케이스 42회 합계 | `100.288초` (v5 `132.779초`) |
| 활성 v5 이미지 공개 2,640문항 Fast | `13.592초 / 90초` |
| 활성 v5 이미지 공개 2,640문항 Balanced | `12.434초 / 90초` |
| 활성 v5 이미지 공개 2,640문항 Premium | `14.170초 / 90초` |
| v1 선택 최적화만 2,640문항 | 약 `0.13초` |
| v1 특징 추출과 Ridge 예측 2,640문항 | 약 `13.60초` 로컬 측정 |
| 5,280문항 합성 배치 | `27.397초` 로컬 측정 |
| 10,560문항 합성 배치 | `54.888초` 로컬 측정 |
| 20문항 무작위 순서 감사 | 등급별 10,000회, 불일치 `0` |
| 전체 Dev 위치 18·119 감사 | Ridge/Hybrid/Uplift 불일치 `0/880` |
| V1/V2/V3 2,640문항 로컬 중앙값 | V1 `14.35~14.39초`, V2 `14.26~16.09초`, V3 `14.22~14.25초` |
| V3 48회 대 기존 80회 전체 공개 감사 | 세 등급 선택 불일치 `0/2,640` |
| V3 Dev 역순·ID 전면 교체 감사 | Premium 내용별 선택 불일치 `0/880` |
| 활성 v4 Dev 역순·ID·split·challenge 변경 감사 | 세 등급 내용별 선택 불일치 `0/880` |
| 활성 v5 Dev 역순·ID·split·challenge 변경 감사 | 세 등급 내용별 선택 불일치 `0/880` |
| 활성 v6 1,024문항 역순·ID 변경 감사 | 세 등급 내용별 선택 불일치 `0/1,024` |

점수·비용의 원본과 자동 생성 그래프는
[`EXPERIMENT_COMPARISON.md`](EXPERIMENT_COMPARISON.md), 제출 경계와 결정론은
[`SUBMISSION_ROUTER.md`](SUBMISSION_ROUTER.md), V2 공격 리뷰와 V3의 정확한
트레이드오프는
[`ROBUST_V3_ADVERSARIAL_REVIEW.md`](ROBUST_V3_ADVERSARIAL_REVIEW.md)를
참고하십시오. V6의 운영 변경과 적대적 평가는
[`ROUTER_V6.md`](ROUTER_V6.md)에 있습니다.
