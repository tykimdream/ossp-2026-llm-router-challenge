<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V6/V7 엣지케이스 벤치마크

- 플랫폼: `macOS-26.5.2-arm64-arm-64bit` / Python `3.9.6`
- timeout: 실행당 `90.0`초
- 범위: 별도 Python 프로세스 시작, 입력 파싱, 라우팅, JSON 쓰기 포함
- 모델 수 표기: `Light/AX31/K1`
- 시간은 로컬 참고값이며 공식 ARM64 컨테이너 측정값이 아님
- 경로 열은 manifest의 guard 분류이며 V7 guard fallback은 Always-Light

## 요약

| Router | 성공/실행 | Timeout | 잘못된 출력 | 4 MiB 초과 | 합계 초 | 최대 RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V6 | 42/42 | 0 | 0 | 0 | 99.838 | 135.9 |
| V7 | 42/42 | 0 | 0 | 0 | 90.814 | 135.8 |

## 전체 측정

| 케이스 | Tier | 경로 | V6 초 | V7 초 | V6 L/A/K | V7 L/A/K | 불일치 | V6/V7 판정 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| character-guard-at-30000000 | fast | learned | 12.185 | 12.033 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | balanced | learned | 12.145 | 12.084 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | premium | learned | 12.190 | 12.002 | 0/1/0 | 1/0/0 | 1 | OK/OK |
| character-guard-over-30000001 | fast | heuristic-fallback | 2.905 | 0.092 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | balanced | heuristic-fallback | 2.871 | 0.093 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | premium | heuristic-fallback | 2.870 | 0.094 | 0/1/0 | 1/0/0 | 1 | OK/OK |
| episode-guard-at-6000 | fast | learned | 2.062 | 2.068 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | balanced | learned | 2.073 | 2.070 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | premium | learned | 2.053 | 2.130 | 0/6000/0 | 6000/0/0 | 6000 | OK/OK |
| episode-guard-over-6001 | fast | heuristic-fallback | 0.168 | 0.110 | 6001/0/0 | 6001/0/0 | 0 | OK/OK |
| episode-guard-over-6001 | balanced | heuristic-fallback | 0.164 | 0.109 | 0/6001/0 | 6001/0/0 | 6001 | OK/OK |
| episode-guard-over-6001 | premium | heuristic-fallback | 0.164 | 0.110 | 0/6001/0 | 6001/0/0 | 6001 | OK/OK |
| homogeneous-duplicates-5000 | fast | learned | 0.418 | 0.421 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | balanced | learned | 0.416 | 0.427 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | premium | learned | 0.411 | 0.465 | 0/5000/0 | 5000/0/0 | 5000 | OK/OK |
| k1-pressure-5000 | fast | learned | 3.579 | 3.557 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| k1-pressure-5000 | balanced | learned | 3.578 | 3.602 | 1475/3525/0 | 5000/0/0 | 3525 | OK/OK |
| k1-pressure-5000 | premium | learned | 3.573 | 3.628 | 0/5000/0 | 3750/1250/0 | 3750 | OK/OK |
| message-fanout-64000 | fast | learned | 6.525 | 6.582 | 500/0/0 | 500/0/0 | 0 | OK/OK |
| message-fanout-64000 | balanced | learned | 6.513 | 6.537 | 400/100/0 | 500/0/0 | 100 | OK/OK |
| message-fanout-64000 | premium | learned | 6.459 | 6.547 | 0/500/0 | 500/0/0 | 500 | OK/OK |
| order-audit-a | fast | learned | 0.450 | 0.451 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-a | balanced | learned | 0.483 | 0.489 | 554/470/0 | 1024/0/0 | 470 | OK/OK |
| order-audit-a | premium | learned | 0.482 | 0.497 | 0/1024/0 | 1010/14/0 | 1010 | OK/OK |
| order-audit-b | fast | learned | 0.455 | 0.453 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-b | balanced | learned | 0.479 | 0.487 | 554/470/0 | 1024/0/0 | 470 | OK/OK |
| order-audit-b | premium | learned | 0.483 | 0.497 | 0/1024/0 | 1010/14/0 | 1010 | OK/OK |
| output-volume-over-4mib | fast | heuristic-fallback | 0.345 | 0.243 | 21399/0/0 | 21399/0/0 | 0 | OK/OK |
| output-volume-over-4mib | balanced | heuristic-fallback | 0.345 | 0.248 | 0/21399/0 | 21399/0/0 | 21399 | OK/OK |
| output-volume-over-4mib | premium | heuristic-fallback | 0.341 | 0.244 | 0/21399/0 | 21399/0/0 | 21399 | OK/OK |
| output-volume-under-4mib | fast | heuristic-fallback | 0.344 | 0.245 | 21398/0/0 | 21398/0/0 | 0 | OK/OK |
| output-volume-under-4mib | balanced | heuristic-fallback | 0.344 | 0.243 | 0/21398/0 | 21398/0/0 | 21398 | OK/OK |
| output-volume-under-4mib | premium | heuristic-fallback | 0.345 | 0.245 | 0/21398/0 | 21398/0/0 | 21398 | OK/OK |
| skewed-budget-cliff-5000 | fast | learned | 1.501 | 1.508 | 4955/45/0 | 5000/0/0 | 45 | OK/OK |
| skewed-budget-cliff-5000 | balanced | learned | 1.497 | 1.493 | 4955/45/0 | 5000/0/0 | 45 | OK/OK |
| skewed-budget-cliff-5000 | premium | learned | 1.494 | 1.564 | 0/4955/45 | 4955/45/0 | 5000 | OK/OK |
| token-density-1000000 | fast | learned | 1.868 | 1.879 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | balanced | learned | 1.887 | 1.861 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | premium | learned | 1.859 | 1.886 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| unicode-pathologies-1200 | fast | learned | 0.526 | 0.502 | 1025/175/0 | 1200/0/0 | 175 | OK/OK |
| unicode-pathologies-1200 | balanced | learned | 0.495 | 0.504 | 463/737/0 | 1200/0/0 | 737 | OK/OK |
| unicode-pathologies-1200 | premium | learned | 0.492 | 0.513 | 0/1187/13 | 887/313/0 | 888 | OK/OK |

`FAIL`은 timeout, 비정상 종료, 잘못된 submission 또는 공식 4 MiB 출력 한도 초과 중 하나입니다.
합성 입력에는 실제 outcome이 없으므로 이 보고서의 모델 수는 실제 비용 통과를 보장하지 않습니다.
