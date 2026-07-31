#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  powerplan — 임상연구 표본수·검정력 설계기"
echo "=================================================================="
echo "  \"이 연구 몇 명 필요한가?\"를 계산하고, 프로토콜/IRB에 붙일"
echo "  한국어·영어 문장까지 만들어 줍니다. (외부 의존성 0)"
echo "   · 두 군/전후/3군 이상/반복측정(MMRM)/비율/상관/생존분석 설계"
echo "   · 비열등성·동등성(TOST) — 연속형과 이분형 모두 · 대응 비율(McNemar)"
echo "   · 중간분석(군차별설계): α 소비함수 경계·표본수 팽창계수·기대 표본수"
echo "   · 무익성(futility) 중단 경계: β 소비함수, 비구속적 — DSMB 헌장용"
echo "   · 평균·ANOVA 계열은 비중심 t·F 분포로 정확 계산 (G*Power 값과 일치)"
echo "     비율은 정규근사 z, 상관은 Fisher z, ICC·LoA는 근사식 (한계는 결과에 표시)"
echo "   · 분석 표본수 → 설계효과(군집) → 탈락 보정 → 모집 표본수를 구분해 제시"
echo "   · 신뢰도 연구(ICC)·Bland-Altman·범주형 일치도(kappa)는 '정밀도 기준'"
echo "   · 기저값이 있으면 ANCOVA 보정(--analysis ancova)으로 표본수를 크게 줄임"
echo "   · 사전연구 CSV를 주면 효과크기·신뢰구간·탈락률·기저값 상관까지 뽑아 줌"
echo ""
echo "  (예제 CSV는 모두 합성 데이터입니다 — examples/README.md)"
echo ""
echo "  내 값으로 실행:"
echo "    powerplan ttest2 --d 0.5 --power 0.8 --dropout 0.15"
echo "    powerplan ttest2 --mean1 8 --mean2 5 --sd 6 --power 0.9    # 원래 단위로"
echo "    powerplan paired --diff 3 --sd-diff 6 --power 0.8          # 전후 비교"
echo "    powerplan prop2 --p1 0.30 --p2 0.50 --power 0.8            # 반응률 비교"
echo "    powerplan survival --hr 0.7 --median1 12 --accrual 18 --followup 12 --power 0.8"
echo "    powerplan repeated --d 0.4 --post 3 --rho 0.6 --power 0.8   # 반복측정"
echo "    powerplan ttest2 --d 0.5 --power 0.9 --interim 1            # 중간분석 1회"
echo "    powerplan ttest2 --d 0.5 --power 0.9 --interim 1 --futility obf  # + 무익성 경계"
echo "    powerplan pilot 내파일.csv --value 결과 --group 군 --power 0.8"
echo "    powerplan --help                                           # 전체 설계 목록"
echo "=================================================================="
echo ""

run() {
  if command -v powerplan >/dev/null 2>&1; then
    powerplan "$@"
  else
    python3 -m powerplan.cli "$@"
  fi
}

echo "### 예제 1) 두 군 비교 — ISI 3점 차이(SD 6), 탈락 15% (수면 디바이스 시험 상황)"
echo "\$ powerplan ttest2 --mean1 8 --mean2 5 --sd 6 --power 0.8 --dropout 0.15 --sensitivity"
echo ""
run ttest2 --mean1 8 --mean2 5 --sd 6 --power 0.8 --dropout 0.15 --sensitivity

echo ""
echo "### 예제 2) 기저값 보정(ANCOVA) — 같은 가정에서 표본수가 절반"
echo "\$ powerplan ttest2 --d 0.309 --power 0.8                                   # 추적값만: 군당 166명"
echo "\$ powerplan ttest2 --d 0.309 --power 0.8 --analysis ancova --baseline-r 0.711  # ANCOVA: 군당 83명"
echo ""
run ttest2 --d 0.309 --power 0.8 --analysis ancova --baseline-r 0.711

echo ""
echo "### 예제 3) 사전연구 CSV(합성 예제) → 효과크기 → 본연구 표본수"
echo "\$ powerplan pilot examples/wowfit_pilot.csv --pre 훈련전_단어인지도 --post 훈련후_단어인지도 --filter 군=중재 --power 0.8"
echo ""
run pilot examples/wowfit_pilot.csv --pre 훈련전_단어인지도 --post 훈련후_단어인지도 --filter 군=중재 --power 0.8

echo ""
echo "### 예제 4) 비접촉 호흡센서 vs PSG 검증 — 일치도(ICC·LoA) 정밀도 기준"
echo "\$ powerplan icc --icc 0.8 --width 0.15 --raters 2"
echo ""
run icc --icc 0.8 --width 0.15 --raters 2
echo ""
echo "\$ powerplan loa --sd-diff 2.0 --half-width 0.5"
echo ""
run loa --sd-diff 2.0 --half-width 0.5

echo ""
echo "### 예제 5) 반복측정 — 1차 평가변수를 무엇으로 정하느냐가 표본수를 가른다"
echo "\$ powerplan repeated --d 0.4 --post 1 --baseline-n 0 --analysis post --rho 0 --power 0.8  # 보정 없음: 군당 100명"
echo "\$ powerplan repeated --d 0.4 --post 3 --rho 0.6 --power 0.8                  # 마지막 방문(기본): 군당 65명"
echo "\$ powerplan repeated --d 0.4 --post 3 --rho 0.6 --power 0.8 --estimand average  # 사후 평균: 군당 39명"
echo ""
run repeated --d 0.4 --post 3 --rho 0.6 --power 0.8

echo ""
echo "### 예제 6) 생존분석 — 검정력을 결정하는 것은 인원이 아니라 '사건 수'"
echo "\$ powerplan survival --hr 0.7 --median1 12 --accrual 18 --followup 12 --power 0.8"
echo ""
run survival --hr 0.7 --median1 12 --accrual 18 --followup 12 --power 0.8

echo ""
echo "### 예제 7) 중간분석을 1회 계획한다면 (군차별설계 · α 소비함수)"
echo "\$ powerplan ttest2 --d 0.5 --power 0.9 --interim 1 --spending pocock"
echo ""
run ttest2 --d 0.5 --power 0.9 --interim 1 --spending pocock

echo ""
echo "### 예제 8) DSMB 헌장에 넣을 무익성(futility) 중단 경계까지 함께"
echo "\$ powerplan ttest2 --d 0.5 --power 0.9 --interim 1 --futility obf"
echo ""
run ttest2 --d 0.5 --power 0.9 --interim 1 --futility obf

echo ""
echo "### 예제 9) 확보 가능한 인원이 군당 30명뿐이라면? (검정력 역산)"
echo "\$ powerplan ttest2 --d 0.5 --n 30 --power 0.8"
echo ""
run ttest2 --d 0.5 --n 30 --power 0.8

echo ""
echo "자세한 한글 안내: 사용법.md   ·   전체 옵션: powerplan --help"
echo ""
read -p "엔터를 누르면 창이 닫힙니다..." _ || true
