#!/bin/bash
cd "$(dirname "$0")"
echo "============================================================"
echo "  pubgap — 논문 주제·연구공백 탐색기"
echo "  PubMed 동향 요약 + 덜 연구된 각도(저조 조합) 제안"
echo "============================================================"
echo
echo "▶ 번들 예시(수면·호흡·HRV·EEG 18편)로 리포트를 출력합니다:"
echo "  python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml"
echo
python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml
echo
echo "------------------------------------------------------------"
echo "실제 PubMed 를 조회하려면(네트워크 필요):"
echo "  python3 -m pubgap.cli \"slow breathing AND sleep\" --email 내이메일@lab.org"
echo "  python3 -m pubgap.cli \"hearing loss AND cognitive decline\" --out 결과.md"
echo
echo "내려받은 NBIB/XML(.gz 가능) 파일이나 CSV 출력도 됩니다:"
echo "  python3 -m pubgap.cli --from-file 내보낸논문.nbib"
echo "  python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml --format csv --out gaps.csv"
echo "자세한 사용법은 사용법.md / README.md 참고."
echo
read -p "엔터를 누르면 창이 닫힙니다..."
