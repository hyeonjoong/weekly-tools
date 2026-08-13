"""원고 읽기 — .docx 표/추적변경/각주, 인코딩, 하드랩 되붙이기, 거부해야 할 입력."""

from __future__ import annotations

import zipfile

import pytest

from docx_fixture import build_docx, deleted_paragraph, paragraph, table
from numcheck.docio import (
    ManuscriptError,
    manuscript_from_text,
    read_manuscript,
)


# ── .docx ────────────────────────────────────────────────────────────────────


def test_docx_reads_table_cells_as_one_row(tmp_path):
    path = tmp_path / "t.docx"
    build_docx(path, ["본문 문단.", [["지표", "값"], ["ISI", "14.37 (N = 23)"]]])
    ms = read_manuscript(path)
    rows = [ln for ln in ms.lines if ln.kind == "table"]
    assert len(rows) == 2
    assert rows[1].text == "ISI | 14.37 (N = 23)"
    assert ms.table_rows == 2


def test_docx_excludes_tracked_deletions(tmp_path):
    path = tmp_path / "t.docx"
    build_docx(path, ["남는 문장 23/46 (50.0%).",
                      deleted_paragraph("지워진 문장 99/46 (250.0%).")])
    ms = read_manuscript(path)
    body = ms.text()
    assert "23/46" in body
    assert "99/46" not in body
    assert ms.deleted_runs >= 1
    assert any("추적 변경" in note for note in ms.notes)


def test_docx_reads_footnotes(tmp_path):
    path = tmp_path / "t.docx"
    build_docx(path, ["본문."])
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr(
            "word/footnotes.xml",
            '<?xml version="1.0"?><w:footnotes xmlns:w="http://schemas.openxmlformats.org'
            '/wordprocessingml/2006/main">' + paragraph("각주 숫자 12/24 (50.0%).")
            + "</w:footnotes>",
        )
    ms = read_manuscript(path)
    assert any(ln.kind == "footnote" for ln in ms.lines)
    assert "12/24" in ms.text()


def test_docx_nested_table_is_flattened(tmp_path):
    inner = table([["안쪽", "7"]])
    path = tmp_path / "t.docx"
    build_docx(path, ["머리말.", f"<w:tbl><w:tr><w:tc>{paragraph('바깥')}{inner}"
                                 f"</w:tc></w:tr></w:tbl>"])
    ms = read_manuscript(path)
    assert any("바깥" in ln.text and "7" in ln.text for ln in ms.lines)


def test_docx_rejects_dtd_bomb(tmp_path):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", '<?xml version="1.0"?><!DOCTYPE x [ ]><x/>')
    with pytest.raises(ManuscriptError, match="DTD"):
        read_manuscript(path)


def test_docx_without_document_xml_is_rejected(tmp_path):
    path = tmp_path / "nope.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(ManuscriptError, match="Word 문서가 아닙니다"):
        read_manuscript(path)


def test_corrupt_docx_gives_readable_error(tmp_path):
    path = tmp_path / "bad.docx"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(ManuscriptError, match="열 수 없습니다"):
        read_manuscript(path)


def test_broken_footnotes_do_not_kill_the_run(tmp_path):
    path = tmp_path / "t.docx"
    build_docx(path, ["본문 23/46 (50.0%)."])
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("word/footnotes.xml", "<not-xml")
    ms = read_manuscript(path)
    assert "23/46" in ms.text()
    assert any("해석하지 못해" in note for note in ms.notes)


# ── 거부해야 할 입력 ─────────────────────────────────────────────────────────


def test_pdf_is_refused_with_a_reason(tmp_path):
    path = tmp_path / "m.pdf"
    path.write_bytes(b"%PDF-1.7\n%stuff")
    with pytest.raises(ManuscriptError, match="PDF"):
        read_manuscript(path)


def test_legacy_doc_is_refused(tmp_path):
    path = tmp_path / "m.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
    with pytest.raises(ManuscriptError, match=r"\.doc"):
        read_manuscript(path)


def test_zip_with_wrong_extension_is_refused(tmp_path):
    path = tmp_path / "m.xlsx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", "x")
    with pytest.raises(ManuscriptError, match="zip"):
        read_manuscript(path)


def test_missing_file_and_directory(tmp_path):
    with pytest.raises(ManuscriptError, match="파일이 없습니다"):
        read_manuscript(tmp_path / "nope.md")
    with pytest.raises(ManuscriptError, match="폴더가 아니라"):
        read_manuscript(tmp_path)


# (오류 메시지에 원고 내용이 새지 않는지는 tests/test_safety.py 가 여러 경로로 검사한다)


# ── 텍스트 포맷 ──────────────────────────────────────────────────────────────


def test_cp949_manuscript_is_decoded(tmp_path):
    path = tmp_path / "m.md"
    path.write_bytes("총 46명 (능동자극 23, 대조 23).".encode("cp949"))
    ms = read_manuscript(path)
    assert "능동자극" in ms.text()
    assert ms.encoding in ("cp949", "euc-kr")


def test_utf8_bom_is_stripped(tmp_path):
    path = tmp_path / "m.md"
    path.write_bytes("﻿# 제목".encode("utf-8"))
    ms = read_manuscript(path)
    assert ms.lines[0].text.startswith("# 제목")


def test_hard_wrapped_sentence_is_rejoined():
    ms = manuscript_from_text("차이는 유의하였다, t(44) = 3.05,\np = .004.\n")
    assert len(ms.lines) >= 1
    assert "t(44) = 3.05, p = .004." in ms.lines[0].text
    assert ms.lines[0].no == 1


def test_completed_sentences_are_not_joined():
    ms = manuscript_from_text("첫 문장이다.\n둘째 문장이다.\n")
    assert ms.lines[0].text == "첫 문장이다."
    assert ms.lines[1].no == 2


def test_list_and_heading_start_new_blocks():
    ms = manuscript_from_text("이어질 것 같은 줄\n# 제목\n- 항목\n")
    texts = [ln.text for ln in ms.lines]
    assert "# 제목" in texts
    assert "- 항목" in texts


def test_markdown_table_rows_are_marked():
    ms = manuscript_from_text("| a | b |\n| 1 | 2 |\n")
    assert all(ln.kind == "table" for ln in ms.lines if ln.stripped)


def test_tex_comments_removed_and_tables_marked():
    ms = manuscript_from_text(
        "본문 % 주석은 사라진다\n\\begin{table}\n행\n\\end{table}\n", fmt="tex")
    assert "주석" not in ms.text()
    assert any(ln.kind == "table" for ln in ms.lines)


def test_manuscript_from_text_rejects_docx():
    with pytest.raises(ManuscriptError):
        manuscript_from_text("x", fmt="docx")


def test_unknown_extension_read_as_plain_text(tmp_path):
    path = tmp_path / "m.rtfish"
    path.write_text("숫자 23/46 (50.0%)", encoding="utf-8")
    ms = read_manuscript(path)
    assert ms.fmt == "txt"
    assert any("평문" in note for note in ms.notes)


def test_empty_file_reads_but_has_no_content(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("", encoding="utf-8")
    ms = read_manuscript(path)
    assert ms.word_count == 0
