#!/bin/bash
# longistat — 반복측정(전-후·다시점) 추이 분석기. 더블클릭하면 예제 리포트가 나옵니다.
cd "$(dirname "$0")" || exit 1

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "❌ python3 을 찾을 수 없습니다. https://www.python.org 에서 설치한 뒤 다시 실행하세요."
  read -r -p "엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

cat <<'EOF'
========================================================================
 longistat — 반복측정 추이 분석기
========================================================================
 무엇을 하나요?
   같은 사람을 여러 번 측정한 CSV 하나로
     · 시점/그룹별 기술통계와 결측·탈락 프로파일
     · 정규성(Shapiro-Wilk) · 구형성(Mauchly) 점검 + GG/HF 보정
     · 반복측정 ANOVA / 혼합(군 x 시점) ANOVA, Friedman 교차확인
     · 시점 간·군간 사후비교 (Holm 보정)
     · 기저 대비 변화량과 "군간 변화량 차이 + 95% CI"
     · 기저값 보정 ANCOVA (조정평균차 + 95% CI)
     · MCID 반응자 비율과 RD/RR/OR/NNT, 신뢰변화지수(RCI)
     · 논문에 바로 넣는 한/영 결과 문장
   을 한 번에 만들어 줍니다. (인터넷 접속 없음, 외부 라이브러리 없음)

 내 파일로 쓰려면:
   python3 -m longistat.cli 내파일.csv --id 대상 --time 방문 --value 점수 --group 군
   (한 번 `python3 -m pip install -e .` 을 해 두면 `longistat ...` 로도 됩니다.)
   자세한 설명은 같은 폴더의 '사용법.md' 를 열어보세요.
========================================================================
EOF

echo
echo "▶ 예제 1/2 — 불면 ISI, 2군(능동/가짜) × 3시점, 탈락 포함"
echo "  \$ python3 -m longistat.cli examples/isi_serene_예시.csv --id 대상 --time 방문 --value ISI --group 군 \\"
echo "        --time-order 기저,4주,8주 --primary-time 8주 --mcid 6 --direction lower --reliability 0.9 --recovery-cutoff 7"
echo
"$PY" -m longistat.cli examples/isi_serene_예시.csv \
  --id 대상 --time 방문 --value ISI --group 군 \
  --time-order 기저,4주,8주 --primary-time 8주 \
  --mcid 6 --direction lower --reliability 0.9 --recovery-cutoff 7
STATUS1=$?

echo
echo "========================================================================"
echo "▶ 예제 2/2 — 와우핏 단어인지도, 1군 × 4시점 (높을수록 좋음), 요약만"
echo "  \$ python3 -m longistat.cli examples/와우핏_단어인지도_wide예시.csv --wide --id 환자 \\"
echo "        --columns 기저,4주,8주,12주 --mcid 10 --direction higher --brief"
echo
"$PY" -m longistat.cli examples/와우핏_단어인지도_wide예시.csv \
  --wide --id 환자 --columns 기저,4주,8주,12주 \
  --mcid 10 --direction higher --brief
STATUS2=$?

echo
if [ $STATUS1 -eq 0 ] && [ $STATUS2 -eq 0 ]; then
  echo "✅ 예제 실행 완료. 이제 위 명령의 파일 이름과 열 이름만 바꿔 쓰시면 됩니다."
  echo "   (논문 표로 붙여넣으려면 --format md -o 결과.md 를 덧붙이세요.)"
else
  echo "⚠️ 예제 실행 중 오류가 있었습니다 (종료코드 $STATUS1 / $STATUS2)."
fi
echo
read -r -p "엔터를 누르면 창이 닫힙니다..."
