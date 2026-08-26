<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V7/V9 엣지케이스 벤치마크

- 플랫폼: `macOS-26.5.2-arm64-arm-64bit` / Python `3.12.13`
- timeout: 실행당 `90.0`초
- 범위: 별도 Python 프로세스 시작, 입력 파싱, 라우팅, JSON 쓰기 포함
- 모델 수 표기: `Light/AX31/K1`
- 시간은 로컬 참고값이며 공식 ARM64 컨테이너 측정값이 아님
- 경로 열은 manifest의 guard 분류이며 V9 guard fallback은 Always-Light

## 요약

| Router | 성공/실행 | Timeout | 잘못된 출력 | 4 MiB 초과 | 합계 초 | 최대 RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V7 | 42/42 | 0 | 0 | 0 | 69.488 | 143.6 |
| V9 | 42/42 | 0 | 0 | 0 | 71.284 | 143.6 |

## 전체 측정

| 케이스 | Tier | 경로 | V7 초 | V9 초 | V7 L/A/K | V9 L/A/K | 불일치 | V7/V9 판정 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| character-guard-at-30000000 | fast | learned | 8.912 | 8.971 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | balanced | learned | 9.230 | 8.943 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | premium | learned | 8.999 | 9.032 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | fast | heuristic-fallback | 0.084 | 0.085 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | balanced | heuristic-fallback | 0.084 | 0.084 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | premium | heuristic-fallback | 0.083 | 0.085 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | fast | learned | 1.581 | 1.759 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | balanced | learned | 1.593 | 1.747 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | premium | learned | 1.613 | 1.793 | 6000/0/0 | 3556/2444/0 | 2444 | OK/OK |
| episode-guard-over-6001 | fast | heuristic-fallback | 0.084 | 0.083 | 6001/0/0 | 6001/0/0 | 0 | OK/OK |
| episode-guard-over-6001 | balanced | heuristic-fallback | 0.082 | 0.083 | 6001/0/0 | 6001/0/0 | 0 | OK/OK |
| episode-guard-over-6001 | premium | heuristic-fallback | 0.083 | 0.083 | 6001/0/0 | 6001/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | fast | learned | 0.326 | 0.333 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | balanced | learned | 0.330 | 0.330 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | premium | learned | 0.357 | 0.357 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| k1-pressure-5000 | fast | learned | 2.785 | 2.977 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| k1-pressure-5000 | balanced | learned | 2.764 | 2.967 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| k1-pressure-5000 | premium | learned | 2.813 | 2.999 | 3750/1250/0 | 2756/2244/0 | 994 | OK/OK |
| message-fanout-64000 | fast | learned | 5.180 | 5.230 | 500/0/0 | 500/0/0 | 0 | OK/OK |
| message-fanout-64000 | balanced | learned | 5.200 | 5.286 | 500/0/0 | 500/0/0 | 0 | OK/OK |
| message-fanout-64000 | premium | learned | 5.193 | 5.221 | 500/0/0 | 500/0/0 | 0 | OK/OK |
| order-audit-a | fast | learned | 0.349 | 0.381 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-a | balanced | learned | 0.376 | 0.405 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-a | premium | learned | 0.386 | 0.416 | 1010/14/0 | 1010/3/11 | 11 | OK/OK |
| order-audit-b | fast | learned | 0.349 | 0.381 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-b | balanced | learned | 0.375 | 0.407 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-b | premium | learned | 0.383 | 0.417 | 1010/14/0 | 1010/3/11 | 11 | OK/OK |
| output-volume-over-4mib | fast | heuristic-fallback | 0.156 | 0.157 | 21399/0/0 | 21399/0/0 | 0 | OK/OK |
| output-volume-over-4mib | balanced | heuristic-fallback | 0.155 | 0.156 | 21399/0/0 | 21399/0/0 | 0 | OK/OK |
| output-volume-over-4mib | premium | heuristic-fallback | 0.158 | 0.159 | 21399/0/0 | 21399/0/0 | 0 | OK/OK |
| output-volume-under-4mib | fast | heuristic-fallback | 0.155 | 0.157 | 21398/0/0 | 21398/0/0 | 0 | OK/OK |
| output-volume-under-4mib | balanced | heuristic-fallback | 0.156 | 0.161 | 21398/0/0 | 21398/0/0 | 0 | OK/OK |
| output-volume-under-4mib | premium | heuristic-fallback | 0.158 | 0.157 | 21398/0/0 | 21398/0/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | fast | learned | 1.152 | 1.296 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | balanced | learned | 1.150 | 1.290 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | premium | learned | 1.176 | 1.325 | 4955/45/0 | 2953/2047/0 | 2002 | OK/OK |
| token-density-1000000 | fast | learned | 1.445 | 1.436 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | balanced | learned | 1.434 | 1.423 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | premium | learned | 1.420 | 1.422 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| unicode-pathologies-1200 | fast | learned | 0.387 | 0.427 | 1200/0/0 | 1200/0/0 | 0 | OK/OK |
| unicode-pathologies-1200 | balanced | learned | 0.389 | 0.427 | 1200/0/0 | 1200/0/0 | 0 | OK/OK |
| unicode-pathologies-1200 | premium | learned | 0.401 | 0.436 | 887/313/0 | 672/528/0 | 215 | OK/OK |

`FAIL`은 timeout, 비정상 종료, 잘못된 submission 또는 공식 4 MiB 출력 한도 초과 중 하나입니다.
합성 입력에는 실제 outcome이 없으므로 이 보고서의 모델 수는 실제 비용 통과를 보장하지 않습니다.
