#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo " paperforge — 보유 데이터 → 멀티모달 논문 아이디어 매트릭스"
echo "=================================================================="
echo " 무엇: 가진 데이터(EEG·워치·호흡·설문·MoA)를 적은 매니페스트(JSON)에서"
echo "       논문 아이디어(가설·변수·분석법·저널·표본 실현가능성)를 표로 생성"
echo " 사용: paperforge <매니페스트.json> [--top N --out ideas.md --csv ideas.csv]"
echo "------------------------------------------------------------------"
echo " 아래는 예시 데이터(examples/sleep_moa_manifest.json)로 실행한 결과입니다."
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트를, 아니면 모듈로 실행 (둘 다 안되면 안내).
if command -v paperforge >/dev/null 2>&1; then
  paperforge examples/sleep_moa_manifest.json --top 4
else
  python3 -m paperforge.cli examples/sleep_moa_manifest.json --top 4
fi

echo ""
echo "------------------------------------------------------------------"
echo " 내 데이터로 쓰려면 examples/sleep_moa_manifest.json 을 복사해 고치세요."
echo " 자세한 안내: 사용법.md"
echo "------------------------------------------------------------------"
read -p "엔터를 누르면 창이 닫힙니다..."
