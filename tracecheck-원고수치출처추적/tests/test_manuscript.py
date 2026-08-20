"""원고 파싱 — 4개 형식, 절 분류, 표 셀 좌표."""

import os

import pytest
from conftest import make_docx, write

from tracecheck.manuscript import (classify_heading, is_caption,
                                   read_manuscript)
from tracecheck.safety import InputError


def test_md_sections_and_table_cells(tmp_path):
    path = write(str(tmp_path / "m.md"),
                 "# 제목\n\n"
                 "## Abstract\n\n초록 12.4 입니다.\n\n"
                 "## Introduction\n\n서론 99.9 입니다.\n\n"
                 "## Results\n\n결과 3.14 입니다.\n\n"
                 "Table 1. 캡션 7.7\n\n"
                 "| A | B |\n|---|---|\n| 1.5 | 2.5 |\n")
    manuscript = read_manuscript(path)
    sections = {b.text: b.section for b in manuscript.blocks}
    assert sections["초록 12.4 입니다."] == "abstract"
    assert sections["서론 99.9 입니다."] == "introduction"
    assert sections["결과 3.14 입니다."] == "results"
    cells = [b for b in manuscript.blocks if b.kind == "table"]
    assert [(c.table_no, c.row, c.col, c.text) for c in cells] == [
        (1, 1, 1, "A"), (1, 1, 2, "B"), (1, 2, 1, "1.5"), (1, 2, 2, "2.5")]
    assert cells[2].loc == "표1 셀(2행,1열)"
    captions = [b for b in manuscript.blocks if b.kind == "caption"]
    assert captions and captions[0].text.startswith("Table 1.")


def test_md_table_cell_target_key_is_tables(tmp_path):
    """표는 어느 절 안에 있든 '표'로 대조 대상에 들어갑니다."""
    path = write(str(tmp_path / "m.md"),
                 "## Discussion\n\n| A |\n|---|\n| 9.9 |\n")
    manuscript = read_manuscript(path)
    cell = [b for b in manuscript.blocks if b.kind == "table"][-1]
    assert cell.section == "discussion"
    assert cell.target_key == "tables"


def test_docx_paragraphs_tables_and_styles(tmp_path):
    path = make_docx(str(tmp_path / "m.docx"),
                     ["Abstract", "초록 값 12.4", "Results", "결과 값 3.14"],
                     table=[["지표", "값"], ["ISI", "15.9"]],
                     styles={"Abstract": "Heading1", "Results": "Heading1"})
    manuscript = read_manuscript(path)
    assert manuscript.line_kind == "문단 번호"
    by_text = {b.text: b for b in manuscript.blocks}
    assert by_text["초록 값 12.4"].section == "abstract"
    assert by_text["결과 값 3.14"].section == "results"
    cells = [b for b in manuscript.blocks if b.kind == "table"]
    assert (cells[3].row, cells[3].col, cells[3].text) == (2, 2, "15.9")
    assert manuscript.table_count == 1


def test_docx_gridspan_advances_column(tmp_path):
    """병합 셀(gridSpan) 뒤의 열 번호가 밀려야 좌표가 살아 있습니다."""
    import zipfile
    body = ("<w:tbl><w:tr>"
            "<w:tc><w:tcPr><w:gridSpan w:val=\"2\"/></w:tcPr>"
            "<w:p><w:r><w:t>합쳐진</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>3.5</w:t></w:r></w:p></w:tc>"
            "</w:tr></w:tbl>")
    document = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
                'openxmlformats.org/wordprocessingml/2006/main"><w:body>%s'
                '</w:body></w:document>' % body)
    path = str(tmp_path / "span.docx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
    manuscript = read_manuscript(path)
    cells = [b for b in manuscript.blocks if b.kind == "table"]
    assert [(c.row, c.col, c.text) for c in cells] == [(1, 1, "합쳐진"), (1, 3, "3.5")]


def test_docx_tracked_deletions_are_dropped(tmp_path):
    """변경내용 추적: 삭제된 글자(delText)는 개정 후 상태로 보고 버립니다."""
    import zipfile
    body = ('<w:p><w:r><w:t>평균은 </w:t></w:r>'
            '<w:del><w:r><w:delText>99.9</w:delText></w:r></w:del>'
            '<w:ins><w:r><w:t>12.4</w:t></w:r></w:ins>'
            '<w:r><w:t> 이다</w:t></w:r></w:p>')
    document = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
                'openxmlformats.org/wordprocessingml/2006/main"><w:body>%s'
                '</w:body></w:document>' % body)
    path = str(tmp_path / "tracked.docx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
    manuscript = read_manuscript(path)
    assert manuscript.blocks[0].text == "평균은 12.4 이다"


def test_tex_sections_tabular_and_caption(tmp_path):
    path = write(str(tmp_path / "m.tex"),
                 "\\section{Results}\n"
                 "The mean was 12.44 (SD 4.08). % 주석의 999 는 무시\n"
                 "\\begin{table}\n\\caption{Table 1. 결과}\n"
                 "\\begin{tabular}{cc}\n"
                 "ISI & 15.91 \\\\\n"
                 "SWS & 41.72 \\\\\n"
                 "\\end{tabular}\n\\end{table}\n")
    manuscript = read_manuscript(path)
    texts = [b.text for b in manuscript.blocks]
    assert any("12.44" in t for t in texts)
    assert not any("999" in t for t in texts)     # 주석은 버립니다
    cells = [b for b in manuscript.blocks if b.kind == "table"]
    assert ("15.91", 1, 2) in [(c.text, c.row, c.col) for c in cells]
    assert any(b.kind == "caption" for b in manuscript.blocks)


def test_txt_headings_and_lines(tmp_path):
    path = write(str(tmp_path / "m.txt"),
                 "Results\n총 84명이었다.\n평균 12.4 였다.\n")
    manuscript = read_manuscript(path)
    assert manuscript.blocks[0].kind == "heading"
    assert manuscript.blocks[1].section == "results"
    assert manuscript.blocks[2].line == 3


def test_encoding_fallback_cp949(tmp_path):
    path = str(tmp_path / "cp949.txt")
    with open(path, "wb") as handle:
        handle.write("Results\n평균은 12.4 였다.\n".encode("cp949"))
    manuscript = read_manuscript(path)
    assert "12.4" in manuscript.blocks[1].text
    assert manuscript.notes and "cp949" in manuscript.notes[0]


def test_fullwidth_and_unicode_minus_are_normalized(tmp_path):
    path = write(str(tmp_path / "wide.md"), "## Results\n평균차 \uff0d\uff13.\uff14\uff17 이다.\n")
    manuscript = read_manuscript(path)
    assert manuscript.blocks[-1].norm.count("-3.47") == 1
    # 원문은 건드리지 않습니다.
    assert "\uff13" in manuscript.blocks[-1].text


@pytest.mark.parametrize("heading,expected", [
    ("Abstract", "abstract"), ("초록", "abstract"), ("3. Results", "results"),
    ("연구 결과", "results"), ("Materials and Methods", "methods"),
    ("References", "references"), ("고찰", "discussion"),
    ("Ⅲ. 알 수 없는 제목", None), ("표 1. 기저 특성", None),
])
def test_classify_heading(heading, expected):
    assert classify_heading(heading) == expected


@pytest.mark.parametrize("text", ["Table 2. 결과", "Figure 1: 흐름", "표 3. 요약",
                                  "그림 2 흐름도", "Supplementary Table S1."])
def test_is_caption(text):
    assert is_caption(text)


def test_unsupported_and_pdf_are_refused(tmp_path):
    pdf = write(str(tmp_path / "m.pdf"), "%PDF-1.4")
    with pytest.raises(InputError) as exc:
        read_manuscript(pdf)
    assert "PDF" in str(exc.value)
    other = write(str(tmp_path / "m.rtf"), "{\\rtf1}")
    with pytest.raises(InputError):
        read_manuscript(other)


def test_broken_docx_is_reported_not_crashed(tmp_path):
    path = write(str(tmp_path / "bad.docx"), "이건 zip 이 아닙니다")
    with pytest.raises(InputError) as exc:
        read_manuscript(path)
    assert "zip" in str(exc.value) or "열 수 없" in str(exc.value)
    assert "이건 zip 이 아닙니다" not in str(exc.value)     # 내용 미노출


def test_docx_without_document_xml(tmp_path):
    import zipfile
    path = str(tmp_path / "empty.docx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/settings.xml", "<a/>")
    with pytest.raises(InputError):
        read_manuscript(path)


def test_docx_with_dtd_is_refused(tmp_path):
    """엔티티 폭탄 방어 — DTD 가 있으면 파싱하지 않습니다."""
    import zipfile
    path = str(tmp_path / "bomb.docx")
    document = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body/></w:document>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
    with pytest.raises(InputError) as exc:
        read_manuscript(path)
    assert "DTD" in str(exc.value)


def test_example_manuscripts_parse(examples_dir):
    for name in ("clean", "flawed"):
        path = os.path.join(examples_dir, name, "원고.md")
        manuscript = read_manuscript(path)
        assert manuscript.table_count == 1
        assert any(b.section == "results" for b in manuscript.blocks)
