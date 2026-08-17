<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# V5/V6 엣지케이스 벤치마크

- 플랫폼: `macOS-26.5.2-arm64-arm-64bit` / Python `3.9.6`
- timeout: 실행당 `90.0`초
- 범위: 별도 Python 프로세스 시작, 입력 파싱, 라우팅, JSON 쓰기 포함
- 모델 수 표기: `Light/AX31/K1`
- 시간은 로컬 참고값이며 공식 ARM64 컨테이너 측정값이 아님

## 요약

| Router | 성공/실행 | Timeout | 잘못된 출력 | 4 MiB 초과 | 합계 초 | 최대 RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V5 | 42/42 | 0 | 0 | 0 | 132.779 | 187.2 |
| V6 | 42/42 | 0 | 0 | 0 | 100.288 | 135.9 |

## 전체 측정

| 케이스 | Tier | 경로 | V5 초 | V6 초 | V5 L/A/K | V6 L/A/K | 불일치 | V5/V6 판정 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| character-guard-at-30000000 | fast | learned | 11.383 | 12.588 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | balanced | learned | 12.000 | 12.150 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-at-30000000 | premium | learned | 11.389 | 12.191 | 0/1/0 | 0/1/0 | 0 | OK/OK |
| character-guard-over-30000001 | fast | heuristic-fallback | 2.937 | 2.868 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | balanced | heuristic-fallback | 2.835 | 2.903 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| character-guard-over-30000001 | premium | heuristic-fallback | 2.863 | 2.874 | 0/1/0 | 0/1/0 | 0 | OK/OK |
| episode-guard-at-6000 | fast | learned | 5.127 | 2.096 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | balanced | learned | 2.574 | 2.030 | 6000/0/0 | 6000/0/0 | 0 | OK/OK |
| episode-guard-at-6000 | premium | learned | 5.089 | 2.010 | 0/6000/0 | 0/6000/0 | 0 | OK/OK |
| episode-guard-over-6001 | fast | heuristic-fallback | 0.165 | 0.162 | 6001/0/0 | 6001/0/0 | 0 | OK/OK |
| episode-guard-over-6001 | balanced | heuristic-fallback | 0.158 | 0.161 | 0/6001/0 | 0/6001/0 | 0 | OK/OK |
| episode-guard-over-6001 | premium | heuristic-fallback | 0.157 | 0.162 | 0/6001/0 | 0/6001/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | fast | learned | 6.242 | 0.419 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | balanced | learned | 4.033 | 0.417 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| homogeneous-duplicates-5000 | premium | learned | 6.186 | 0.412 | 0/5000/0 | 0/5000/0 | 0 | OK/OK |
| k1-pressure-5000 | fast | learned | 5.923 | 3.505 | 5000/0/0 | 5000/0/0 | 0 | OK/OK |
| k1-pressure-5000 | balanced | learned | 3.738 | 3.574 | 1475/3525/0 | 2497/2503/0 | 1022 | OK/OK |
| k1-pressure-5000 | premium | learned | 5.929 | 3.572 | 0/5000/0 | 0/5000/0 | 0 | OK/OK |
| message-fanout-64000 | fast | learned | 6.356 | 6.589 | 500/0/0 | 500/0/0 | 0 | OK/OK |
| message-fanout-64000 | balanced | learned | 6.163 | 6.589 | 400/100/0 | 400/100/0 | 0 | OK/OK |
| message-fanout-64000 | premium | learned | 6.421 | 6.535 | 0/500/0 | 100/400/0 | 100 | OK/OK |
| order-audit-a | fast | learned | 0.960 | 0.446 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-a | balanced | learned | 0.557 | 0.486 | 554/470/0 | 1010/14/0 | 456 | OK/OK |
| order-audit-a | premium | learned | 0.989 | 0.483 | 0/1024/0 | 133/891/0 | 133 | OK/OK |
| order-audit-b | fast | learned | 0.964 | 0.456 | 1024/0/0 | 1024/0/0 | 0 | OK/OK |
| order-audit-b | balanced | learned | 0.541 | 0.477 | 554/470/0 | 1010/14/0 | 456 | OK/OK |
| order-audit-b | premium | learned | 0.992 | 0.487 | 0/1024/0 | 133/891/0 | 133 | OK/OK |
| output-volume-over-4mib | fast | heuristic-fallback | 0.345 | 0.339 | 21399/0/0 | 21399/0/0 | 0 | OK/OK |
| output-volume-over-4mib | balanced | heuristic-fallback | 0.346 | 0.341 | 0/21399/0 | 0/21399/0 | 0 | OK/OK |
| output-volume-over-4mib | premium | heuristic-fallback | 0.340 | 0.339 | 0/21399/0 | 0/21399/0 | 0 | OK/OK |
| output-volume-under-4mib | fast | heuristic-fallback | 0.336 | 0.337 | 21398/0/0 | 21398/0/0 | 0 | OK/OK |
| output-volume-under-4mib | balanced | heuristic-fallback | 0.333 | 0.340 | 0/21398/0 | 0/21398/0 | 0 | OK/OK |
| output-volume-under-4mib | premium | heuristic-fallback | 0.339 | 0.344 | 0/21398/0 | 0/21398/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | fast | learned | 4.097 | 1.497 | 4955/45/0 | 4955/45/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | balanced | learned | 1.984 | 1.507 | 4955/45/0 | 4955/45/0 | 0 | OK/OK |
| skewed-budget-cliff-5000 | premium | learned | 4.064 | 1.488 | 0/4955/45 | 0/5000/0 | 45 | OK/OK |
| token-density-1000000 | fast | learned | 1.686 | 1.884 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | balanced | learned | 1.718 | 1.865 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| token-density-1000000 | premium | learned | 1.712 | 1.879 | 1/0/0 | 1/0/0 | 0 | OK/OK |
| unicode-pathologies-1200 | fast | learned | 1.119 | 0.497 | 1025/175/0 | 1063/137/0 | 38 | OK/OK |
| unicode-pathologies-1200 | balanced | learned | 0.586 | 0.491 | 463/737/0 | 612/588/0 | 149 | OK/OK |
| unicode-pathologies-1200 | premium | learned | 1.102 | 0.495 | 0/1187/13 | 0/1199/1 | 12 | OK/OK |

`FAIL`은 timeout, 비정상 종료, 잘못된 submission 또는 공식 4 MiB 출력 한도 초과 중 하나입니다.
합성 입력에는 실제 outcome이 없으므로 이 보고서의 모델 수는 실제 비용 통과를 보장하지 않습니다.
