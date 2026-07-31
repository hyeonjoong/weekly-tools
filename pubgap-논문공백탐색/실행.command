#!/bin/bash
cd "$(dirname "$0")"
echo "============================================================"
echo "  pubgap — 논문 주제·연구공백 탐색기"
echo "  PubMed 동향 요약 + 덜 연구된 각도(저조 조합) 제안"
echo "============================================================"
echo
echo "▶ 번들 예시(수면·호흡·HRV·EEG·수면제 28편)로 리포트를 출력합니다:"
echo "  python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml"
echo
python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml
echo
echo "------------------------------------------------------------"
echo "실제 PubMed 를 조회하려면(네트워크 필요):"
echo "  python3 -m pubgap.cli \"slow breathing AND sleep\" --email 내이메일@lab.org"
echo "  python3 -m pubgap.cli \"hearing loss AND cognitive decline\" --out 결과.md"
echo
echo "내려받은 NBIB/RIS/CSV/XML(.gz 가능) 파일도 형식을 자동 판별합니다:"
echo "  python3 -m pubgap.cli --from-file 내보낸논문.nbib"
echo "  python3 -m pubgap.cli --from-file examples/sleep_export.csv     # 지저분한 CSV 예시"
echo
echo "기본은 '부족 편수' 순입니다. 미개척 정도(lift) 순으로 보려면:"
echo "  python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml --gap-sort lift"
echo
echo "근거공백 표를 스프레드시트로:"
echo "  python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml --format csv --csv-section topic-evidence --out ~/Downloads/근거공백.csv"
echo
echo "연구 각도(MeSH 부주제어) 공백 — '이 주제를 약물치료 관점에서 본 논문이 없다':"
echo "  python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml --format csv --csv-section angles"
echo
echo "여러 출처를 합쳐 분석(중복은 PMID→DOI→제목+연도로 자동 제거):"
echo "  python3 -m pubgap.cli --from-file examples/sleep_pubmed.xml --from-file examples/sleep_export.csv"
echo "자세한 사용법은 사용법.md / README.md 참고."
echo
# '|| true': 입력이 파이프/리다이렉트로 들어와 EOF 면 read 가 1 을 돌려주는데,
# 그게 스크립트의 종료코드가 되면 자동 검증에서 실패로 보인다.
read -p "엔터를 누르면 창이 닫힙니다..." || true
