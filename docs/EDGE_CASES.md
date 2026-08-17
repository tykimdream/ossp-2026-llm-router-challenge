<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 라우터 엣지케이스 모음

이 문서의 입력은 비공개 평가 자료를 추정한 것이 아니라, 현재 제출 라우터의
성능·결정론·형식 경계를 공격적으로 점검하기 위한 합성 데이터입니다. 정답이나
모델별 실제 outcome은 포함하지 않습니다. 따라서 `K1 pressure`는 모든 문항의
문면이 깊은 추론을 요구한다는 뜻이며, `axk1-think`가 실제로 정답이거나 가장
경제적인 모델임을 보증한다는 뜻은 아닙니다.

## 생성

일상적으로 돌릴 수 있는 core 케이스는 다음 명령으로 생성합니다.

```console
python3 tools/generate_edge_cases.py --profile core
```

30 MB 입력과 4 MiB 출력 경계까지 포함하려면 full을 사용합니다. 생성물은 Git에
추적하지 않는 `build/edge-cases/`에 저장되며 `manifest.json`에 문항 수, 문자 수,
파일 크기와 SHA-256이 기록됩니다.

```console
python3 tools/generate_edge_cases.py --profile full
```

하나만 만들 수도 있습니다.

```console
python3 tools/generate_edge_cases.py \
  --case k1-pressure-5000 \
  --output-dir build/edge-cases
```

생성한 입력은 다음처럼 실행합니다.

```console
/usr/bin/time -p env PYTHONPATH=src python3 -m ossp_router.submission \
  --input build/edge-cases/k1-pressure-5000.json \
  --tier premium \
  --output build/edge-cases/k1-pressure-5000.premium.submission.json
```

## 케이스별 의도

| 케이스 | 규모 | 겨냥하는 실패 모드 | 기대되는 점검 |
| --- | ---: | --- | --- |
| `k1-pressure-5000` | 5,000문항 | 거의 모든 요청이 고난도인 분포에서 K1 후보가 몰림 | 전부 K1에 배정하지 않고 batch 예산에 맞춰 희소 자원을 배분하는지, 90초 안에 끝나는지 확인 |
| `homogeneous-duplicates-5000` | 5,000문항 | 완전히 같은 고난도 prompt의 반복 | ID나 순서로 동점을 깨지 않는지, 불필요한 특징 재계산이 병목인지 확인 |
| `skewed-budget-cliff-5000` | 쉬움 4,950 + 어려움 50 | 99:1 불균형과 소수의 고가치 꼬리 | 쉬운 다수 때문에 어려운 소수가 묻히지 않는지, 소수 상위 모델 배정이 예산 절벽을 넘지 않는지 확인 |
| `episode-guard-at-6000` | 6,000문항 | 학습 경로 문항 수 경계값 | 현재 구현에서 학습 경로로 실행되며 90초·2 GiB 안에 들어오는지 확인 |
| `episode-guard-over-6001` | 6,001문항 | 상한을 단 하나 넘는 입력 | 현재 구현에서 정렬 전에 결정론적 휴리스틱으로 전환되는지 확인 |
| `message-fanout-64000` | 500문항, 64,000메시지 | episode 수는 작지만 message 객체가 매우 많은 경우 | JSON 파싱, 문자열 결합, 메모리 사용이 message 수에 의해 폭증하지 않는지 확인 |
| `unicode-pathologies-1200` | 1,200문항 | NFC/NFD, ZWJ, emoji, RTL, NUL 등 | UTF-8 입출력, 해시, 정규식이 예외 없이 결정론적으로 동작하는지 확인 |
| `order-audit-a/b` | 각 1,024문항 | 같은 내용을 역순과 새 ID로 재실행 | prompt 내용별 선택이 ID와 입력 순서가 바뀌어도 같은지 확인 |
| `character-guard-at-30000000` | 1문항, 3천만 자 | 문자 상한에서 거대한 무공백 token 처리 | 학습 경로의 최악 시간·메모리와 90초 timeout 가능성을 확인 |
| `character-guard-over-30000001` | 1문항, 3천만+1자 | 경계를 하나 넘을 때 경로가 급변 | 휴리스틱 fallback이 적용되는지, 경계 직전보다 오히려 빨라지는 성능 절벽을 확인 |
| `token-density-1000000` | 1문항, 100만 자·50만 token | 단어 1~3-gram 중간 문자열의 폭발 | 문자 수 guard만으로 CPU·메모리를 방어할 수 있는지 확인 |
| `output-volume-under-4mib` | 약 2.14만 문항 | 128자 ID로 Fast 결과가 4 MiB 바로 아래 | 원자적 쓰기와 결과 검증이 성공하는지 확인 |
| `output-volume-over-4mib` | under보다 1문항 많음 | Fast 결과가 4 MiB를 처음 초과 | 로컬 파일 쓰기는 성공해도 공식 4 MiB 출력 볼륨에서는 실패한다는 사실을 조기에 탐지 |

`character-guard-at-30000000`은 의도적으로 위험합니다. 일반 단위 테스트에 넣지
말고 공식 자원 제한과 동일한 컨테이너에서 개별 실행하십시오. 반대로
`character-guard-over-30000001`이 더 빠르더라도 최적화가 잘 된 것이 아니라
보호장치 경계를 넘어 다른 알고리즘이 실행된 결과입니다.

## 판정 기준

유효한 입력에 대해서는 종료 코드 `0`, 완전한 submission JSON, 입력의 모든
`episode_id`가 정확히 한 번씩 등장하는 것을 먼저 확인합니다. 그다음 다음
경계를 별도로 봅니다.

- 실행 시간: 각 tier가 90초 미만이어야 합니다.
- 메모리: 2 GiB 미만이며 추가 swap을 사용하지 않아야 합니다.
- 출력: `submission.json`이 4 MiB 이하여야 합니다.
- 로그: stdout과 stderr가 각각 총 1 MiB 이하여야 합니다.
- 결정론: 같은 내용을 재실행하거나 ID·순서를 바꿔도 내용별 선택이 같아야 합니다.
- 경로 경계: 6,000/6,001문항과 30,000,000/30,000,001자가 의도한 경로로 나뉘어야 합니다.

합성 prompt-only 입력만으로 실제 비용 한도 통과 여부를 판정할 수는 없습니다.
비용은 모델별 실제 `input_tokens`, `output_tokens`, `num_generations`가 든 outcome이
있어야 계산됩니다. 이 케이스에서는 선택 모델 분포를 회귀 지표로 사용하되,
공식 예산 통과를 의미한다고 해석하지 마십시오.
