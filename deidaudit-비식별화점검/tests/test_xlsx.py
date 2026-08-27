"""XLSX 숨은 내용 적발 — zipfile + ElementTree 로 직접 읽습니다."""

from __future__ import annotations

import datetime as _dt
import re
import zipfile

from deidaudit.xlsx import col_letters_to_index, index_to_col_letters, load_xlsx

from .xlsx_builder import Sheet, build_xlsx


def _kinds(result):
    return {f.kind for f in result.structural}


def test_hidden_sheet_is_reported_and_still_scanned(tmp_path):
    path = build_xlsx(
        tmp_path / "a.xlsx",
        [
            Sheet(name="보이는시트", rows=[["id"], ["S01"]]),
            Sheet(name="원본명단", rows=[["이름"], ["김현중"]], hidden=True),
        ],
    )
    result = load_xlsx(path)
    assert "숨김 시트" in _kinds(result)
    labels = {t.sheet for t in result.tables}
    assert labels == {"보이는시트", "원본명단"}
    hidden = [t for t in result.tables if t.sheet == "원본명단"][0]
    assert hidden.hidden_sheet and hidden.rows == [["김현중"]]


def test_hidden_columns_and_rows(tmp_path):
    path = build_xlsx(
        tmp_path / "b.xlsx",
        [Sheet(name="S", rows=[["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]],
               hidden_columns=(1,), hidden_rows=(3,))],
    )
    result = load_xlsx(path)
    assert "숨김 열" in _kinds(result)
    assert "숨김 행" in _kinds(result)
    table = result.tables[0]
    assert table.hidden_columns == {1}
    assert table.hidden_rows == {3}


def test_cell_comment_and_author(tmp_path):
    path = build_xlsx(
        tmp_path / "c.xlsx",
        [Sheet(name="S", rows=[["a"], ["1"]], comments=(("A2", "연구간호사", "전화로 확인 필요"),))],
    )
    result = load_xlsx(path)
    comments = [f for f in result.structural if f.kind == "셀 주석"]
    assert len(comments) == 1
    assert "A2" in comments[0].evidence
    # 주석 본문과 작성자는 마스킹되어야 합니다.
    assert "전화로 확인 필요" not in comments[0].evidence
    assert "연구간호사" not in comments[0].note


def test_docprops_creator_and_company(tmp_path):
    path = build_xlsx(
        tmp_path / "d.xlsx", [Sheet(name="S", rows=[["a"], ["1"]])],
        creator="hyeonjoong.k", last_modified_by="연구간호사1", company="BELL",
    )
    result = load_xlsx(path)
    kinds = _kinds(result)
    assert "문서 메타데이터(작성자)" in kinds
    assert "문서 메타데이터(최종수정자)" in kinds
    assert "문서 메타데이터(회사)" in kinds
    for finding in result.structural:
        assert "hyeonjoong.k" not in finding.evidence


def test_defined_names_reported_but_internal_ones_ignored(tmp_path):
    path = build_xlsx(
        tmp_path / "e.xlsx", [Sheet(name="S", rows=[["a"], ["1"]])],
        defined_names={"명단범위": "S!$A$1:$A$2", "_xlnm.Print_Area": "S!$A$1"},
    )
    result = load_xlsx(path)
    hits = [f for f in result.structural if f.kind == "정의된 이름"]
    assert len(hits) == 1                       # _xlnm.* 는 엑셀 내부 이름이라 제외
    assert hits[0].sheet == "S"


def test_defined_name_containing_pii_is_masked(tmp_path):
    """엑셀의 '선택 영역에서 만들기'는 헤더 셀로 이름을 만듭니다 — 이름 자체가 식별자일 수 있습니다."""
    path = build_xlsx(
        tmp_path / "pii.xlsx", [Sheet(name="S", rows=[["a"], ["1"]])],
        defined_names={"연락처_01023456789": "S!$A$1", "환자_박준호_1979": "S!$A$2"},
    )
    result = load_xlsx(path)
    for finding in result.structural:
        assert "01023456789" not in finding.evidence
        assert "박준호" not in finding.evidence
        assert re.fullmatch(r"이름 \*+ \(\d+자\)", finding.evidence) or finding.kind != "정의된 이름"


def test_stray_formatted_cell_far_down_does_not_explode_memory(tmp_path):
    """엑셀에서 행 전체를 선택하면 맨 아래에 서식만 남은 셀이 생깁니다."""
    import zipfile

    src = build_xlsx(tmp_path / "bloat.xlsx", [Sheet(name="S", rows=[["a", "b"], ["1", "2"]])])
    raw = src.read_bytes()
    with zipfile.ZipFile(src) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    sheet = parts["xl/worksheets/sheet1.xml"].decode("utf-8")
    sheet = sheet.replace(
        "</sheetData>",
        '<row r="1048000"><c r="CV1048000" s="0"/></row></sheetData>',
    )
    parts["xl/worksheets/sheet1.xml"] = sheet.encode("utf-8")
    with zipfile.ZipFile(src, "w") as zf:
        for n, data in parts.items():
            zf.writestr(n, data)
    table = load_xlsx(src).tables[0]
    assert table.n_rows == 1               # 100만 행짜리 격자를 만들지 않습니다
    assert table.columns == ["a", "b"]


def test_out_of_range_cell_reference_is_ignored_and_confessed(tmp_path):
    import zipfile

    src = build_xlsx(tmp_path / "badref.xlsx", [Sheet(name="S", rows=[["a"], ["1"]])])
    with zipfile.ZipFile(src) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    sheet = parts["xl/worksheets/sheet1.xml"].decode("utf-8")
    sheet = sheet.replace("</sheetData>", '<row r="3"><c r="AAAAAAA3" t="str"><v>x</v></c></row></sheetData>')
    parts["xl/worksheets/sheet1.xml"] = sheet.encode("utf-8")
    with zipfile.ZipFile(src, "w") as zf:
        for n, data in parts.items():
            zf.writestr(n, data)
    result = load_xlsx(src)
    assert result.tables[0].n_cols == 1
    assert any("범위를 벗어난" in reason for _, reason in result.skipped)


def test_header_not_on_first_row_is_found(tmp_path):
    """제목 줄과 빈 줄이 위에 있는 시트 — 첫 행을 헤더로 쓰면 규칙이 전부 죽습니다."""
    path = build_xlsx(
        tmp_path / "title.xlsx",
        [Sheet(name="S", rows=[
            ["2026년 수면연구 참여 현황", "", ""],
            ["", "", ""],
            ["이름", "성별", "생년"],
            ["김현중", "M", "1988-04-02"],
        ])],
    )
    result = load_xlsx(path)
    table = result.tables[0]
    assert table.columns == ["이름", "성별", "생년"]
    assert table.rows == [["김현중", "M", "1988-04-02"]]
    assert table.source_rows == [4]
    assert any("빈 행" in reason for _, reason in result.skipped)


def test_trailing_empty_row_does_not_create_a_phantom_class(tmp_path):
    """빈 행 하나가 k=1 동치류를 만들어 '재식별 위험'을 날조하면 안 됩니다."""
    from deidaudit.kanon import compute_k

    rows = [["sex", "site"]] + [["M", "A"]] * 10 + [["F", "A"]] * 10
    clean = build_xlsx(tmp_path / "clean.xlsx", [Sheet(name="S", rows=rows)])
    dirty = build_xlsx(tmp_path / "dirty.xlsx", [Sheet(name="S", rows=rows + [["", ""]])])
    k_clean = compute_k(load_xlsx(clean).tables[0], ["sex", "site"])
    k_dirty = compute_k(load_xlsx(dirty).tables[0], ["sex", "site"])
    assert k_clean.min_k == k_dirty.min_k == 10
    assert k_clean.n_units == k_dirty.n_units == 20


def test_date_serial_is_converted(tmp_path):
    path = build_xlsx(
        tmp_path / "f.xlsx",
        [Sheet(name="S", rows=[["visit_date", "night"],
                               [_dt.date(2026, 3, 14), _dt.datetime(2026, 3, 13, 23, 40)]])],
    )
    table = load_xlsx(path).tables[0]
    assert table.rows[0][0] == "2026-03-14"
    assert table.rows[0][1] == "2026-03-13 23:40:00"


def test_inline_strings_are_read(tmp_path):
    path = build_xlsx(
        tmp_path / "g.xlsx",
        [Sheet(name="S", rows=[["이름"], ["김현중"]], use_inline_strings=True)],
    )
    assert load_xlsx(path).tables[0].rows == [["김현중"]]


def test_sparse_cells_are_padded(tmp_path):
    """엑셀은 빈 셀을 생략합니다 — 격자로 되살려야 열이 밀리지 않습니다."""
    path = tmp_path / "sparse.xlsx"
    build_xlsx(path, [Sheet(name="S", rows=[["a", "b", "c"], ["1", "", "3"]])])
    table = load_xlsx(path).tables[0]
    assert table.columns == ["a", "b", "c"]
    assert table.rows == [["1", "", "3"]]


def test_corrupt_file_is_reported_not_crashed(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a zip file at all")
    result = load_xlsx(path)
    assert result.fatal and "zip" in result.fatal


def test_workbook_without_sheets_is_reported(tmp_path):
    path = tmp_path / "empty.xlsx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheets/></workbook>",
        )
    result = load_xlsx(path)
    assert result.fatal


def test_column_letter_conversion_round_trip():
    for index in [0, 1, 25, 26, 27, 51, 52, 701, 702, 16383]:
        assert col_letters_to_index(index_to_col_letters(index)) == index
