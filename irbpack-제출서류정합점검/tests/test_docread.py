"""문서 읽기 — 표 인라인, 인코딩, 손상 파일, 못 읽는 형식의 자백."""

import os
import zipfile

import pytest

from conftest import make_docx, write_md
from irbpack import docread
from irbpack.docread import collect_paths, read_document


def test_docx_reads_paragraphs(tmp_path):
    path = make_docx(tmp_path / "a.docx", [("p", "연구계획서"), ("p", "총 60명")])
    doc = read_document(path)
    assert doc.read_ok
    assert [block.text for block in doc.blocks] == ["연구계획서", "총 60명"]


def test_docx_inlines_table_rows_in_order(tmp_path):
    path = make_docx(tmp_path / "a.docx", [
        ("p", "앞 문단"),
        ("t", [["연구제목", "가상 연구"], ["대상자 수", "총 60명"]]),
        ("p", "뒤 문단"),
    ])
    doc = read_document(path)
    texts = [block.text for block in doc.blocks]
    assert texts[0] == "앞 문단"
    assert texts[1] == "연구제목 | 가상 연구"
    assert texts[2] == "대상자 수 | 총 60명"
    assert texts[3] == "뒤 문단"


def test_docx_table_rows_are_marked_as_table(tmp_path):
    path = make_docx(tmp_path / "a.docx", [("t", [["a", "b"]])])
    doc = read_document(path)
    assert doc.blocks[0].kind == "t"


def test_docx_cell_pipe_is_replaced(tmp_path):
    """셀 안의 '|' 가 표 구분자를 뭉개면 안 된다 (deidaudit 사고 재발 방지)."""
    path = make_docx(tmp_path / "a.docx", [("t", [["기간", "3년|5년"]])])
    doc = read_document(path)
    assert doc.blocks[0].text.count("|") == 1


def test_docx_empty_document_is_unreadable(tmp_path):
    path = make_docx(tmp_path / "a.docx", [])
    doc = read_document(path)
    assert not doc.read_ok
    assert "글자" in doc.error


def test_docx_corrupt_file_is_unreadable(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip at all")
    doc = read_document(str(path))
    assert not doc.read_ok
    assert "손상" in doc.error


def test_docx_zip_without_document_xml(tmp_path):
    path = tmp_path / "weird.docx"
    with zipfile.ZipFile(str(path), "w") as archive:
        archive.writestr("hello.txt", "hi")
    doc = read_document(str(path))
    assert not doc.read_ok
    assert "Word 문서 구조" in doc.error


def test_docx_encrypted_package(tmp_path):
    path = tmp_path / "locked.docx"
    with zipfile.ZipFile(str(path), "w") as archive:
        archive.writestr("EncryptedPackage", "x")
        archive.writestr("EncryptionInfo", "x")
    doc = read_document(str(path))
    assert not doc.read_ok
    assert "암호" in doc.error


def test_docx_bad_xml(tmp_path):
    path = tmp_path / "bad.docx"
    with zipfile.ZipFile(str(path), "w") as archive:
        archive.writestr("word/document.xml", "<w:document><unclosed>")
    doc = read_document(str(path))
    assert not doc.read_ok


def test_docx_tracked_changes_note(tmp_path):
    body = ('<w:p><w:ins w:id="1"><w:r><w:t>새 문장</w:t></w:r></w:ins>'
            '<w:del w:id="2"><w:r><w:delText>지운 문장</w:delText></w:r></w:del></w:p>')
    path = make_docx(tmp_path / "tracked.docx", [], body_xml=body)
    doc = read_document(path)
    assert doc.read_ok
    assert any("변경내용 추적" in note for note in doc.notes)


def test_docx_tracked_changes_excludes_deleted_text(tmp_path):
    body = ('<w:p><w:ins w:id="1"><w:r><w:t>새 문장</w:t></w:r></w:ins>'
            '<w:del w:id="2"><w:r><w:delText>지운 문장</w:delText></w:r></w:del></w:p>')
    path = make_docx(tmp_path / "tracked.docx", [], body_xml=body)
    doc = read_document(path)
    assert "새 문장" in doc.text
    assert "지운 문장" not in doc.text


def test_docx_nested_table(tmp_path):
    body = ("<w:tbl><w:tr><w:tc><w:tbl><w:tr><w:tc>"
            '<w:p><w:r><w:t>안쪽</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>')
    path = make_docx(tmp_path / "nested.docx", [], body_xml=body)
    doc = read_document(path)
    assert "안쪽" in doc.text


def test_docx_only_tables(tmp_path):
    path = make_docx(tmp_path / "t.docx", [("t", [["가", "나"]]), ("t", [["다", "라"]])])
    doc = read_document(path)
    assert doc.read_ok and len(doc.blocks) == 2


def test_markdown_table_rows(tmp_path):
    path = write_md(tmp_path / "a.md", ["# 제목", "| 항목 | 값 |", "| --- | --- |", "| 대상자 | 60명 |"])
    doc = read_document(path)
    kinds = [block.kind for block in doc.blocks]
    assert "t" in kinds
    assert all("---" not in block.text for block in doc.blocks)


def test_markdown_heading_becomes_paragraph(tmp_path):
    path = write_md(tmp_path / "a.md", ["## 연구 개요"])
    doc = read_document(path)
    assert doc.blocks[0].text == "연구 개요"


def test_txt_cp949(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("연구계획서 총 60명".encode("cp949"))
    doc = read_document(str(path))
    assert doc.read_ok
    assert "총 60명" in doc.text
    assert any("cp949" in note or "euc-kr" in note for note in doc.notes)


def test_txt_utf8_sig(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("﻿연구계획서".encode("utf-8"))
    doc = read_document(str(path))
    assert doc.blocks[0].text == "연구계획서"


def test_empty_text_file_is_unreadable(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("")
    doc = read_document(str(path))
    assert not doc.read_ok


def test_hwp_is_reported_not_ignored(tmp_path):
    path = tmp_path / "동의서.hwp"
    path.write_text("아무거나")
    doc = read_document(str(path))
    assert not doc.read_ok
    assert "한글" in doc.error and "docx" in doc.error


def test_hwpx_is_reported(tmp_path):
    path = tmp_path / "동의서.hwpx"
    path.write_text("x")
    assert not read_document(str(path)).read_ok


def test_legacy_doc_is_reported(tmp_path):
    path = tmp_path / "동의서.doc"
    path.write_text("x")
    doc = read_document(str(path))
    assert not doc.read_ok and "구형" in doc.error


def test_missing_file(tmp_path):
    doc = read_document(str(tmp_path / "없는파일.docx"))
    assert not doc.read_ok and "없습니다" in doc.error


def test_directory_is_not_a_document(tmp_path):
    doc = read_document(str(tmp_path))
    assert not doc.read_ok


def test_unsupported_extension(tmp_path):
    path = tmp_path / "표.xlsx"
    path.write_text("x")
    doc = read_document(str(path))
    assert not doc.read_ok and "지원하지 않는" in doc.error


def test_oversized_file_refused(tmp_path, monkeypatch):
    path = tmp_path / "big.txt"
    path.write_text("가" * 100)
    monkeypatch.setattr(docread, "MAX_BYTES", 10)
    doc = read_document(str(path))
    assert not doc.read_ok and "너무 큽니다" in doc.error


def test_single_huge_paragraph_is_split(tmp_path, monkeypatch):
    monkeypatch.setattr(docread, "MAX_BLOCK_CHARS", 100)
    path = make_docx(tmp_path / "huge.docx", [("p", "가" * 500)])
    doc = read_document(path)
    assert len(doc.blocks) == 5
    assert doc.read_ok


def test_collect_paths_skips_spreadsheets(tmp_path):
    (tmp_path / "a.docx").write_bytes(b"x")
    (tmp_path / "b.xlsx").write_bytes(b"x")
    (tmp_path / ".hidden.docx").write_bytes(b"x")
    names = [os.path.basename(path) for path in collect_paths([str(tmp_path)])]
    assert names == ["a.docx"]


def test_collect_paths_keeps_hwp_for_confession(tmp_path):
    (tmp_path / "a.docx").write_bytes(b"x")
    (tmp_path / "b.hwp").write_bytes(b"x")
    names = sorted(os.path.basename(path) for path in collect_paths([str(tmp_path)]))
    assert names == ["a.docx", "b.hwp"]


def test_collect_paths_dedupes(tmp_path):
    path = tmp_path / "a.docx"
    path.write_bytes(b"x")
    assert len(collect_paths([str(path), str(path)])) == 1


def test_collect_paths_accepts_explicit_files(tmp_path):
    path = tmp_path / "note.tex"
    path.write_text("x")
    assert collect_paths([str(path)]) == [str(path)]


def test_block_where_mentions_section(tmp_path):
    path = write_md(tmp_path / "a.md", ["1. 연구 개요", "총 60명이 참여한다."])
    doc = read_document(path)
    assert "연구 개요" in doc.blocks[1].where()


def test_head_text_limit(tmp_path):
    path = write_md(tmp_path / "a.md", ["가" * 3000])
    doc = read_document(path)
    assert len(doc.head_text(100)) == 100
