<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V5/V6.1 Competitive 엣지케이스 벤치마크

- 플랫폼: `macOS-26.5.2-arm64-arm-64bit` / Python `3.9.6`
- timeout: 실행당 `90.0`초
- 범위: 별도 Python 프로세스 시작, 입력 파싱, 라우팅, JSON 쓰기 포함
- 모델 수 표기: `Light/AX31/K1`
- 시간은 로컬 참고값이며 공식 ARM64 컨테이너 측정값이 아님

## 요약

| Router | 성공/실행 | Timeout | 잘못된 출력 | 4 MiB 초과 | 합계 초 | 최대 RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V5 | 42/42 | 0 | 0 | 0 | 131.981 | 187.0 |
| V6.1 | 42/42 | 0 | 0 | 0 | 99.213 | 135.9 |

## 전체 측정

| 케이스 | Tier | 경로 | V5 초 | V6.1 초 | V5 L/A/K | V6.1 L/A/K | 불일치 | V5/V6.1 판정 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| character-guard-at-30000000 | fast | learned | 11.412 | 12.030 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | balanced | learned | 11.386 | 11.917 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | premium | learned | 11.544 | 12.466 | 0/1/0 | 0/1/0 | 0 | OK/OK |
| character-guard-over-30000001 | fast | heuristic-fallback | 2.840 | 2.841 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | balanced | heuristic-fallback | 2.891 | 2.840 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | premium | heuristic-fallback | 2.863 | 2.847 | 0/1/0 | 0/1/0 | 0 | OK/OK |
| episode-guard-at-6000 | fast | learned | 5.116 | 2.043 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | balanced | learned | 2.459 | 2.054 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | premium | learned | 5.082 | 2.018 | 0/6000/0 | 0/6000/0 | 0 | OK/OK |
| episode-guard-over-6001 | fast | heuristic-fallback | 0.158 | 0.157 | 6001/0/0 | 6001/0/0 | 0 | OK/OK |
| episode-guard-over-6001 | balanced | heuristic-fallback | 0.156 | 0.160 | 0/6001/0 | 0/6001/0 | 0 | OK/OK |
| episode-guard-over-6001 | premium | heuristic-fallback | 0.159 | 0.163 | 0/6001/0 | 0/6001/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | fast | learned | 6.023 | 0.413 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | balanced | learned | 4.028 | 0.412 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | premium | learned | 6.235 | 0.405 | 0/5000/0 | 0/5000/0 | 0 | OK/OK |
| k1-pressure-5000 | fast | learned | 5.938 | 3.532 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| k1-pressure-5000 | balanced | learned | 3.746 | 3.549 | 1475/3525/0 | 1475/3525/0 | 0 | OK/OK |
| k1-pressure-5000 | premium | learned | 5.941 | 3.598 | 0/5000/0 | 0/5000/0 | 0 | OK/OK |
| message-fanout-64000 | fast | learned | 6.373 | 6.440 | 500/0/0 | 500/0/0 | 0 | OK/OK |
| message-fanout-64000 | balanced | learned | 6.225 | 6.455 | 400/100/0 | 400/100/0 | 0 | OK/OK |
| message-fanout-64000 | premium | learned | 6.454 | 6.459 | 0/500/0 | 0/500/0 | 0 | OK/OK |
| order-audit-a | fast | learned | 0.959 | 0.449 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-a | balanced | learned | 0.546 | 0.478 | 554/470/0 | 554/470/0 | 0 | OK/OK |
| order-audit-a | premium | learned | 0.987 | 0.476 | 0/1024/0 | 0/1024/0 | 0 | OK/OK |
| order-audit-b | fast | learned | 0.956 | 0.446 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-b | balanced | learned | 0.557 | 0.474 | 554/470/0 | 554/470/0 | 0 | OK/OK |
| order-audit-b | premium | learned | 0.954 | 0.476 | 0/1024/0 | 0/1024/0 | 0 | OK/OK |
| output-volume-over-4mib | fast | heuristic-fallback | 0.334 | 0.343 | 21399/0/0 | 21399/0/0 | 0 | OK/OK |
| output-volume-over-4mib | balanced | heuristic-fallback | 0.372 | 0.348 | 0/21399/0 | 0/21399/0 | 0 | OK/OK |
| output-volume-over-4mib | premium | heuristic-fallback | 0.337 | 0.343 | 0/21399/0 | 0/21399/0 | 0 | OK/OK |
| output-volume-under-4mib | fast | heuristic-fallback | 0.330 | 0.334 | 21398/0/0 | 21398/0/0 | 0 | OK/OK |
| output-volume-under-4mib | balanced | heuristic-fallback | 0.335 | 0.338 | 0/21398/0 | 0/21398/0 | 0 | OK/OK |
| output-volume-under-4mib | premium | heuristic-fallback | 0.339 | 0.350 | 0/21398/0 | 0/21398/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | fast | learned | 4.098 | 1.485 | 4955/45/0 | 4955/45/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | balanced | learned | 1.946 | 1.494 | 4955/45/0 | 4955/45/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | premium | learned | 4.067 | 1.469 | 0/4955/45 | 0/4955/45 | 0 | OK/OK |
| token-density-1000000 | fast | learned | 1.684 | 1.889 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | balanced | learned | 1.677 | 1.879 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | premium | learned | 1.680 | 1.866 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| unicode-pathologies-1200 | fast | learned | 1.104 | 0.494 | 1025/175/0 | 1025/175/0 | 0 | OK/OK |
| unicode-pathologies-1200 | balanced | learned | 0.585 | 0.494 | 463/737/0 | 463/737/0 | 0 | OK/OK |
| unicode-pathologies-1200 | premium | learned | 1.101 | 0.493 | 0/1187/13 | 0/1187/13 | 0 | OK/OK |

`FAIL`은 timeout, 비정상 종료, 잘못된 submission 또는 공식 4 MiB 출력 한도 초과 중 하나입니다.
합성 입력에는 실제 outcome이 없으므로 이 보고서의 모델 수는 실제 비용 통과를 보장하지 않습니다.
