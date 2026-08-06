#!/bin/bash
# metapool — 더블클릭 데모. 번들 예제 3종을 차례로 분석해 보여 준다.
cd "$(dirname "$0")" || exit 1

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "❌ python3 을 찾을 수 없습니다. https://www.python.org 에서 설치한 뒤 다시 실행해 주세요."
  read -r -p "엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

cat <<'INTRO'
==============================================================================
  metapool — 메타분석기
==============================================================================
  무엇을 하나요?
    연구 목록 CSV 한 장으로 통합 효과크기(고정효과·변량효과), 이질성(Q·I²·τ²와 신뢰구간),
    95% 예측구간, 하위군 분석, 하나씩 제외(민감도)와 영향력 진단,
    출판편향(Egger·Begg·trim-and-fill·깔때기그림), NNT·절대위험차,
    텍스트 숲그림, 결과 CSV 내보내기, 그리고 논문에 붙일 한국어·영어 문장까지 만들어 줍니다.
    지표: SMD·MD·OR·RR·RD·상관계수(Fisher z)·단일군 비율(logit)·generic

  누구에게?
    체계적 문헌고찰/메타분석 원고를 쓰는 임상·제약 연구자.
    추출표(엑셀)만 있으면 결과 문단 초안이 30초 만에 나옵니다.

  내 데이터로 쓰려면?
    python3 -m metapool 내파일.csv
    (자세한 CSV 형식과 옵션은 같은 폴더의 사용법.md 를 보세요)
==============================================================================
INTRO

run() {
  echo
  echo "──────────────────────────────────────────────────────────────────────────────"
  echo "▶ $1"
  echo "  \$ python3 -m metapool $2"
  echo "──────────────────────────────────────────────────────────────────────────────"
  # shellcheck disable=SC2086
  "$PY" -m metapool $2
}

run "예제 1/5 — 연속형 원자료(두 군의 평균·SD) → 표준화 평균차 + 하위군 분석" \
    "examples/breathing_isi_smd.csv"

run "예제 2/5 — 이분형 2×2(사건 수) → 오즈비 (지표 자동 판별)" \
    "examples/adherence_or.csv --no-sensitivity"

run "예제 3/5 — 이미 계산된 효과크기 + Paule–Mandel τ² + 효과 크기순 정렬" \
    "examples/published_effects.csv --tau2 PM --sort effect --no-forest --no-funnel"

run "예제 4/5 — 단일군 비율(반응률) → logit 합성 + 하위군" \
    "examples/response_rate_prop.csv --no-sensitivity --no-funnel"

run "예제 5/5 — 상관계수 → Fisher z 합성 + REML τ²" \
    "examples/adherence_correlation.csv --tau2 REML --no-funnel"

echo
echo "=============================================================================="
echo "  끝났습니다. 위 예제 CSV들은 examples/ 폴더에 있습니다 — 형식을 따라 하세요."
echo "  전체 옵션:  python3 -m metapool --help"
echo "=============================================================================="
read -r -p "엔터를 누르면 창이 닫힙니다..."
