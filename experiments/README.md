<!--
SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
SPDX-License-Identifier: Apache-2.0
-->

# 라우터 실험 기록

이 디렉터리는 성공한 라우터뿐 아니라 예산 초과·점수 하락으로 채택하지 않은
실험도 함께 보존합니다. 원본 점수 보고서는 `results/`, 재실행에 필요한 모델과
정책은 `artifacts/`, 정규화한 비교 정보는 `registry.json`에 있습니다.

상태의 의미는 다음과 같습니다.

- `reference`: 비교용 공개 기준선
- `candidate`: 제출 후보지만 아직 공정한 승리 조건을 만족하지 않음
- `rejected`: 점수 또는 예산 조건을 통과하지 못한 보존 실험
- `champion`: 공정한 홀드아웃·예산·런타임 조건을 모두 통과한 현재 우승 후보

새 점수 보고서를 추가할 때는 다음 형식으로 레지스트리를 갱신합니다.

```console
PYTHONPATH=src python3 tools/record_router_experiment.py \
  --registry experiments/registry.json \
  --id example-v1 \
  --title "Example v1" \
  --family learned-plus-heuristic \
  --status candidate \
  --protocol train-only-to-dev-holdout \
  --trained-on train \
  --evaluated-on dev \
  --fair-holdout \
  --artifact experiments/artifacts/example.v1.json \
  --score-report experiments/results/example-dev.json \
  --pro "장점" \
  --con "단점"
```

표와 SVG 그래프는 다음 명령으로 다시 생성합니다.

```console
PYTHONPATH=src python3 tools/render_experiment_report.py \
  --registry experiments/registry.json \
  --markdown docs/EXPERIMENT_COMPARISON.md \
  --score-svg docs/experiment-score.svg \
  --budget-svg docs/experiment-budget.svg
```

Train+Dev 전체로 재학습한 모델을 같은 Dev에서 측정한 값은 배포 확인값으로만
기록합니다. `fair_holdout`은 모델·안전계수·휴리스틱 선택에 평가 split의
outcome을 사용하지 않았을 때만 지정합니다.
