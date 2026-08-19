<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V7 final bake-off

## 결론

V7 Adaptive를 활성 제출 후보로 확정한다. V6.1 대비 공정 Dev 점수 `0.003693`을
양보하지만 Train에서만 선택한 reserve가 고정 Dev의 123개 stress tier 실패를
0으로 줄였고, ARM64·엣지·결정론 gate를 모두 통과했다. 배포 후 즉시 롤백
지점은 V6.1이다.

## 점수와 비용

| 평가 | 라우터 | 점수 | Fast | Balanced | Premium |
| --- | --- | ---: | ---: | ---: | ---: |
| Train-only → Dev | V6.1 | `0.695170` | `1.143321` | `1.661573` | `3.072558` |
| Train-only → Dev | V7 | `0.691477` | `1.145521` | `1.653162` | `3.006001` |
| Train+Dev replay | V6.1 | `0.703277` | `1.152316` | `1.744540` | `2.931772` |
| Train+Dev replay | V7 | `0.703277` | `1.150400` | `1.744540` | `2.927918` |

Train+Dev replay는 학습 자료 재평가이므로 일반화 근거가 아니다. 비교 원본은
[`aggressive-v7-fair-dev.json`](../experiments/results/aggressive-v7-fair-dev.json)과
[`aggressive-v7-full-public.json`](../experiments/results/aggressive-v7-full-public.json)에
있다.

## 강건성과 운영 gate

| Gate | 결과 |
| --- | --- |
| Train stress | tier 실패 `0` |
| 고정 Dev standalone/bootstrap/mixture | `0 / 0 / 0` |
| 로컬 합성 엣지 | V7 `42/42`, timeout·형식·4 MiB 실패 `0` |
| 로컬 엣지 합계 | V7 `90.814초`, V6.1 `99.838초` |
| 순서·ID 교체 1,024문항 | 세 tier 내용별 불일치 `0` |
| ARM64 공개 2,640문항 최대 | `13.081 / 12.922 / 13.213초` |
| ARM64 2회 반복 출력 | tier별 SHA-256 동일 |
| 컨테이너 격리 통합 테스트 | `83/83` 통과 |
| 소스 결속 | image OCI label과 source manifest SHA-256 일치 |

ARM64 보고서는
[`aggressive-v7-runtime.json`](../experiments/results/aggressive-v7-runtime.json),
엣지 전체 표는 [`EDGE_CASE_RESULTS_V7.md`](EDGE_CASE_RESULTS_V7.md)에 있다.

## 최종 판단의 범위

V7을 택하는 이유는 공개 평균 점수가 아니라 한도 초과 시 tier 전체가 0점이
되는 비대칭 손실이다. `0.003693`의 점수 보험료는 V6.1 점수의 약 0.53%다.
이 결정은 다음을 보장하지 않는다.

- 숨은 평가의 비용 실패 확률이 0이라는 보장
- content signal이 모든 의미적 분포 이동을 포착한다는 보장
- Light가 아닌 선택을 포함한 임의 배치의 수학적 한도 보장

공식 전 최종 dry-run에서 V7이 자원·형식 gate를 잃거나 실제 평가 batch가 매우
작아 품질 손실이 과도하다는 근거가 생기면 V6.1로 롤백한다. 그 외에는 V7을
유지한다.
