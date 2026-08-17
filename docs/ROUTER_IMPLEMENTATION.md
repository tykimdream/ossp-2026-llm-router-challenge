<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 참가 라우터 구현

이 fork의 `router-run`은 [`submission.py`](../src/ossp_router/submission.py)를
유일한 제출 진입점으로 사용하고, 내부에서 공개 Train 1,760문항과 Dev
880문항으로 학습한 prompt-only tier-specific Ridge ensemble을 실행합니다. 모델 답변을
생성하거나 평가용 모델을 호출하지 않으며, 실행 중 네트워크와 외부 파일이
필요하지 않습니다. 제출 경계와 결정론은
[`SUBMISSION_ROUTER.md`](SUBMISSION_ROUTER.md)에 분리해 기록합니다.

## 모델 선택 방식

[`competition.py`](../src/ossp_router/competition.py)는 현재 프롬프트의
내용에서 다음 특징만 계산합니다.

- 글자·단어·문장·메시지 수와 언어 비율
- 숫자·수식·코드·객관식·논리 규칙·단순 변환 표지
- 길이 구간과 일부 문제 형식의 조합 특징
- 숫자 값을 정규화한 단어 1~3-gram과 문자 3~4-gram feature hashing

학습기는 세 모델의 공개 score와 log-cost를 각각 Ridge로 예측합니다.
[`aggressive_v5.py`](../src/ossp_router/aggressive_v5.py)는 tier별로 Train OOF에서
선택한 alpha head를 평균합니다. 런타임은 등급별 공격적 내부 예산 안에서
예측 품질과 비용을 함께 고려해 모델을
선택합니다. 문항 ID, 입력 순서, `challenge_id`와 `split`은 특징으로 사용하지
않습니다.

최종 컨테이너에는 NumPy나 공개 프롬프트를 넣지 않습니다. 표준 라이브러리
런타임과 약 888 KiB의 512-bin ensemble 계수
[`aggressive-router.v5.json`](../src/ossp_router/resources/aggressive-router.v5.json)을
활성 경로에 사용합니다. 이전 `risk-router.v4.json`과 256-bin
`competition-router.v1.json`은 비교·롤백 재현용으로 보존합니다.

## 학습 재현

먼저 루트 README의 공개 데이터 materialization을 완료합니다. 학습용 NumPy는
제출 컨테이너가 아니라 `.venv-data`에만 설치합니다.

```console
.venv-data/bin/pip install -r baselines/requirements-train.txt

PYTHONPATH=src .venv/bin/python tools/build_public_training_pool.py \
  --train-input data/materialized/train/inputs.json \
  --train-outcomes data/train/outcomes.json \
  --dev-input data/materialized/dev/inputs.json \
  --dev-outcomes data/dev/outcomes.json \
  --output-input build/training/public-train-dev-inputs.json \
  --output-outcomes build/training/public-train-dev-outcomes.json

VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=src \
  .venv-data/bin/python tools/train_aggressive_router_v5.py \
  --input build/training/public-train-dev-inputs.json \
  --outcomes build/training/public-train-dev-outcomes.json \
  --artifact src/ossp_router/resources/aggressive-router.v5.json \
  --report experiments/results/aggressive-v5-full-training.json
```

템플릿 그룹 교차검증은 숫자와 공백을 정규화한 프롬프트 내용의 FNV-1a 해시로
5개 fold를 만듭니다. 같은 정규화 템플릿은 같은 fold에 들어갑니다. 최종
artifact는 공개 Train+Dev 2,640문항 전체로 적합합니다.

artifact의 학습 입력과 outcome SHA-256은 각각 다음과 같습니다.

```text
e7e384c59218f8786f430bcba2aa3b7f565fddf678a0cb5e64a2c9beabb85ea3
b4262e9764c399ba332412553287657474708dd471bbc06e88d50cd55282b3a1
```

원천 자료의 라이선스와 재배포 조건은 [`DATA_LICENSES.md`](../DATA_LICENSES.md),
고정 출처와 공개 파일 해시는 `data/sources/`와
[`public-data.v1.json`](../data/public-data.v1.json)을 따릅니다. AIME 원문,
materialized 입력과 캐시는 artifact나 제출 이미지에 포함하지 않습니다.

## 공개 검증 결과

Train-only → Dev 검증에서 v5와 이전 champion은 다음과 같았습니다.

| 라우터 | 최종 점수 | Fast 비용 | Balanced 비용 | Premium 비용 |
| --- | --- | ---: | ---: | ---: |
| Aggressive Router v5 | **0.695170** | 1.143321 | 1.661573 | 3.072558 |
| Risk Router v4 | 0.689318 | 1.157468 | 1.596435 | 2.725700 |
| Ridge v1 | 0.687188 | 1.190838 | 1.884889 | 2.923078 |

v5는 512-bin Ridge를 유지하면서 Fast `300/500/5000`, Balanced `10000`,
Premium `3000/10000/15000` alpha head를 사용합니다. pooled와 각 fold의 실제
비용이 모두 Fast `1.23`, Balanced `1.95`, Premium `3.85` 아래인 안전계수만
허용합니다. 전체 공개 refit의 결합 Train+Dev 점수는 `0.703277`, 비용은
`1.152316 / 1.744540 / 2.931772`입니다. 이 refit 값은 일반화 점수가 아닙니다.

Apple Silicon·Colima `linux/arm64`에서 공개 Train+Dev 2,640문항을 공식
`check_runtime.py` 조건으로 검사한 결과 `13.592 / 12.434 / 14.170초`에
완료했습니다.

## 검증 명령

```console
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py'

docker build --pull --platform linux/arm64 \
  --file container/Dockerfile --tag ossp-router:local .

PYTHONPATH=src .venv/bin/python tools/check_runtime.py \
  --image ossp-router:local \
  --report build/final/runtime-check-report.json
```

## 실험 보존과 하이브리드 후보

`competition-router.v1.json`을 사용하는 기존 Ridge 결과는 삭제하거나
덮어쓰지 않습니다. 실험 artifact와 원본 점수 보고서는
[`experiments/`](../experiments/) 아래에 보존하고, 정규화된 결과는
`experiments/registry.json`에 누적합니다.

[`hybrid.py`](../src/ossp_router/hybrid.py)는 Ridge의 점수·비용 예측에 다음
결정론적 안전 규칙을 추가한 별도 실험 라우터입니다.

- Fast에서 K1 선택 금지
- 학습된 uplift가 작은 AX31·K1 후보 제거
- K1을 코드·수식·숫자·단순 변환 신호가 있는 문제로 제한
- 긴 문맥과 한국어 비율이 높은 문제의 K1 선택 제한
- 등급별 보수적 예측 비용 한도와 Premium AX31 fill

Train-only Ridge와 고정 안전 규칙을 Dev에서 평가한 `Hybrid Safe v1`은 세
내부 비용 목표를 통과했지만 최종 점수 `0.686903`으로 Ridge Train-only와 제공
기준선보다 낮아 `rejected`로 기록했습니다. Train 비용만 보고 안전계수를
높인 실험은 Dev Balanced 비용 비율 `2.056211`로 공식 한도를 넘어 0점이
되었습니다. 이 실패는 비용 보정도 OOF 또는 독립 검증으로 해야 한다는 증거로
보존합니다.

추가로 다음 학습 목표와 모델 계열도 Train-only → Dev 홀드아웃으로
비교했습니다.

| 실험 | 핵심 가설 | Dev 점수 | 판정 |
| --- | --- | ---: | --- |
| Direct Uplift Ridge | 절대 점수 대신 Light 대비 품질 상승분을 직접 예측 | 0.685966 | 기각 |
| Extra Trees Uplift | 트리 앙상블로 비선형 특징 조합을 학습 | 0.675710 | 기각 |
| Hash Regex Train-only | 제공 기준선에서 Dev 안전계수 보정만 제거 | 0.428864 | 예산 초과로 기각 |

Direct Uplift는 예산을 안전하게 지켰지만 기존 Ridge보다 `0.001221` 낮았습니다.
Extra Trees는 Train 표본 수에 비해 모델 자유도가 커 일반화 품질이 더 낮았고,
최악 fold 비용 제한 때문에 Premium에서 K1을 3문항만 선택했습니다. Hash Regex는
Fast 품질 자체는 `0.664773`이었지만 비용 비율이 `1.257062`로 한도 `1.25`를
조금 넘어 Fast 점수 전체가 0이 됐습니다. 따라서 현재 증거로는 비선형 모델의
자유도를 늘리는 것보다 템플릿 그룹 OOF와 tier별 Ridge 규제 강도를 분리하는
방식이 더 높은 상방을 보였습니다.

비용 예측 잔차의 50%·60% 분위수를 모델별 log-cost에 더하는 보정도 Train OOF
성능 게이트에서 각각 `0.654063`, `0.653494`에 그쳤습니다. 기존 후보보다 낮아
Dev를 보며 추가 튜닝하지 않고 중단했습니다. v5는 여러 Ridge head를 단순
consensus gate로 쓰는 대신 tier별 연속 예측 평균으로 결합해 이 실패를
우회했습니다. 이후 후보도 문항 ID가 아니라 prompt 내용만 사용하고,
template-group OOF의 최악 fold가 내부 비용 목표를 통과할 때만 평가합니다.

Direct Uplift 재현 명령은 다음과 같습니다.

```console
VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=src \
  .venv-data/bin/python tools/train_uplift_router.py \
  --input data/materialized/train/inputs.json \
  --outcomes data/train/outcomes.json \
  --artifact experiments/artifacts/uplift-train-only.v1.json \
  --report experiments/results/uplift-train-only-training.json
```

현재 비교 결과와 그래프는
[`EXPERIMENT_COMPARISON.md`](EXPERIMENT_COMPARISON.md)를 참고하십시오.
