<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V4/V5 엣지케이스 벤치마크

- 플랫폼: `macOS-26.5.2-arm64-arm-64bit` / Python `3.9.6`
- timeout: 실행당 `90.0`초
- 범위: 별도 Python 프로세스 시작, 입력 파싱, 라우팅, JSON 쓰기 포함
- 모델 수 표기: `Light/AX31/K1`
- 시간은 로컬 참고값이며 공식 ARM64 컨테이너 측정값이 아님

| 케이스 | Tier | 경로 | V4 초 | V5 초 | V4 L/A/K | V5 L/A/K | 불일치 | V4/V5 판정 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| character-guard-at-30000000 | fast | learned | 11.395 | 11.295 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | balanced | learned | 11.413 | 11.327 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | premium | learned | 11.470 | 11.526 | 0/1/0 | 0/1/0 | 0 | OK/OK |
| character-guard-over-30000001 | fast | heuristic-fallback | 2.917 | 2.870 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | balanced | heuristic-fallback | 2.860 | 2.860 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | premium | heuristic-fallback | 2.849 | 2.873 | 0/1/0 | 0/1/0 | 0 | OK/OK |
| episode-guard-at-6000 | fast | learned | 2.581 | 5.120 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | balanced | learned | 2.662 | 2.572 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | premium | learned | 2.625 | 5.115 | 0/6000/0 | 0/6000/0 | 0 | OK/OK |
| episode-guard-over-6001 | fast | heuristic-fallback | 0.156 | 0.165 | 6001/0/0 | 6001/0/0 | 0 | OK/OK |
| episode-guard-over-6001 | balanced | heuristic-fallback | 0.157 | 0.169 | 0/6001/0 | 0/6001/0 | 0 | OK/OK |
| episode-guard-over-6001 | premium | heuristic-fallback | 0.162 | 0.164 | 0/6001/0 | 0/6001/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | fast | learned | 4.156 | 6.391 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | balanced | learned | 4.146 | 4.169 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | premium | learned | 4.511 | 6.238 | 0/5000/0 | 0/5000/0 | 0 | OK/OK |
| k1-pressure-5000 | fast | learned | 3.728 | 5.932 | 4975/25/0 | 5000/0/0 | 25 | OK/OK |
| k1-pressure-5000 | balanced | learned | 3.840 | 3.760 | 2497/2503/0 | 1475/3525/0 | 1022 | OK/OK |
| k1-pressure-5000 | premium | learned | 3.764 | 5.800 | 0/5000/0 | 0/5000/0 | 0 | OK/OK |
| message-fanout-64000 | fast | learned | 6.130 | 6.414 | 490/10/0 | 500/0/0 | 10 | OK/OK |
| message-fanout-64000 | balanced | learned | 6.239 | 6.125 | 400/100/0 | 400/100/0 | 0 | OK/OK |
| message-fanout-64000 | premium | learned | 6.199 | 6.382 | 0/500/0 | 0/500/0 | 0 | OK/OK |
| order-audit-a | fast | learned | 0.538 | 0.966 | 1010/14/0 | 1024/0/0 | 14 | OK/OK |
| order-audit-a | balanced | learned | 0.560 | 0.547 | 1010/14/0 | 554/470/0 | 456 | OK/OK |
| order-audit-a | premium | learned | 0.554 | 0.996 | 0/1014/10 | 0/1024/0 | 10 | OK/OK |
| order-audit-b | fast | learned | 0.555 | 0.936 | 1010/14/0 | 1024/0/0 | 14 | OK/OK |
| order-audit-b | balanced | learned | 0.554 | 0.540 | 1010/14/0 | 554/470/0 | 456 | OK/OK |
| order-audit-b | premium | learned | 0.551 | 0.986 | 0/1014/10 | 0/1024/0 | 10 | OK/OK |
| output-volume-over-4mib | fast | heuristic-fallback | 0.358 | 0.366 | 21399/0/0 | 21399/0/0 | 0 | FAIL/FAIL |
| output-volume-over-4mib | balanced | heuristic-fallback | 0.355 | 0.371 | 0/21399/0 | 0/21399/0 | 0 | OK/OK |
| output-volume-over-4mib | premium | heuristic-fallback | 0.355 | 0.364 | 0/21399/0 | 0/21399/0 | 0 | OK/OK |
| output-volume-under-4mib | fast | heuristic-fallback | 0.365 | 0.366 | 21398/0/0 | 21398/0/0 | 0 | OK/OK |
| output-volume-under-4mib | balanced | heuristic-fallback | 0.350 | 0.368 | 0/21398/0 | 0/21398/0 | 0 | OK/OK |
| output-volume-under-4mib | premium | heuristic-fallback | 0.358 | 0.362 | 0/21398/0 | 0/21398/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | fast | learned | 2.012 | 4.204 | 4955/45/0 | 4955/45/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | balanced | learned | 2.016 | 2.026 | 4955/45/0 | 4955/45/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | premium | learned | 2.006 | 4.221 | 0/4955/45 | 0/4955/45 | 0 | OK/OK |
| token-density-1000000 | fast | learned | 1.651 | 1.687 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | balanced | learned | 1.655 | 1.733 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | premium | learned | 1.662 | 1.694 | 0/1/0 | 1/0/0 | 1 | OK/OK |
| unicode-pathologies-1200 | fast | learned | 0.610 | 1.106 | 1025/175/0 | 1025/175/0 | 0 | OK/OK |
| unicode-pathologies-1200 | balanced | learned | 0.604 | 0.584 | 587/613/0 | 463/737/0 | 124 | OK/OK |
| unicode-pathologies-1200 | premium | learned | 0.592 | 1.104 | 0/1200/0 | 0/1187/13 | 13 | OK/OK |

`FAIL`은 timeout, 비정상 종료, 잘못된 submission 또는 공식 4 MiB 출력 한도 초과 중 하나입니다.
합성 입력에는 실제 outcome이 없으므로 이 보고서의 모델 수는 실제 비용 통과를 보장하지 않습니다.
