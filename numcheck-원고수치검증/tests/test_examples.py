"""제안서가 필수로 요구한 두 시험.

1. **오탐 시험** — 깨끗한 합성 원고에서 치명 0건·경고 0건.
   여기서 무언가 나오면 규칙을 넓히지 말고 **좁혀야** 한다.
2. **조용한 통과 시험** — 오류 8종을 심어 둔 원고에서 8종 전부 검출, 종료코드 1.
"""

from __future__ import annotations

from conftest import EXAMPLES
from numcheck.engine import analyze

CLEAN = EXAMPLES / "clean_manuscript.md"
FLAWED = EXAMPLES / "flawed_manuscript.md"
SERENE = EXAMPLES / "serene_style.docx"


def test_clean_manuscript_has_no_critical_or_warning():
    report = analyze(CLEAN)
    assert report.by_level("치명") == [], [f.message for f in report.by_level("치명")]
    assert report.by_level("경고") == [], [f.message for f in report.by_level("경고")]
    assert report.exit_code() == 0


def test_clean_manuscript_actually_checked_things():
    """오탐 0 이 '아무것도 안 봤다'는 뜻이면 안 된다."""
    report = analyze(CLEAN)
    assert report.n_checked >= 15
    kinds = {c.kind for c in report.claims if c.checked}
    assert {"proportion", "statistic", "nsum", "grim", "delta", "ci", "wording"} <= kinds


def test_flawed_manuscript_detects_all_eight_error_types():
    report = analyze(FLAWED)
    items = [f.item for f in report.findings]

    def present(needle):
        return any(needle in item for item in items)

    assert present("비율 재계산")      # 1. 비율
    assert present("p 재계산")         # 2. p 재계산
    assert present("N 합계")           # 3. N 합계
    assert present("GRIM")             # 4. GRIM
    assert present("변화량")           # 5. 변화량
    assert present("신뢰구간")         # 6. CI 포함
    assert present("유의성 문구")      # 7. 유의성 문구
    assert present("CI–p")             # 8. CI–p 모순
    assert report.exit_code() == 1


def test_flawed_manuscript_downgrades_are_explained():
    report = analyze(FLAWED)
    downgraded = [f for f in report.findings if f.downgraded]
    assert len(downgraded) >= 3
    assert any("단측" in f.downgraded for f in downgraded)
    assert any("greenhouse" in f.downgraded.lower() for f in downgraded)


def test_flawed_manuscript_offers_scale_configuration():
    report = analyze(FLAWED)
    info = report.by_level("정보")
    assert info and any("--scale" in f.message for f in info)


def test_serene_docx_reads_tables_and_ignores_deleted_text():
    report = analyze(SERENE)
    assert report.table_rows == 4
    # 추적 변경으로 지운 "99/46 (250.0%)" 는 검사 대상이 아니다
    assert all("99/46" not in c.quote for c in report.claims)
    grim = [f for f in report.findings if f.item.startswith("GRIM")]
    # 같은 GRIM 위반이 본문과 표에 함께 있다 → **한 건으로 합치고** 다른 위치를 적는다
    assert len(grim) == 1
    assert "L16" in grim[0].message


def test_examples_contain_no_real_patient_data():
    """예제는 전부 합성이어야 한다 — 파일 안에 그렇게 적혀 있어야 한다."""
    for path in (CLEAN, FLAWED):
        text = path.read_text(encoding="utf-8")
        assert "합성" in text
