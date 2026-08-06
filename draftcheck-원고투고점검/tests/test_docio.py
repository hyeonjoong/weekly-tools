"""파일 읽기 계층: 4개 포맷, 인코딩, 구조 탐지, 그리고 망가진 입력."""

from __future__ import annotations

import hashlib
import zipfile

import pytest
from conftest import EXAMPLES, analyse

from draftcheck import docio
from draftcheck.docio import (
    ManuscriptError,
    caption_of,
    detect_sections,
    heading_key,
    read_manuscript,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(tmp_path, body_xml, name="t.docx", extra=None):
    path = tmp_path / name
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body_xml}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        for member, data in (extra or {}).items():
            zf.writestr(member, data)
    return path


def _p(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


# ── 4개 포맷 ─────────────────────────────────────────────────────────────────


def test_markdown_lines_keep_real_line_numbers(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("first\n\nthird\n", encoding="utf-8")
    ms = read_manuscript(path)
    assert ms.fmt == "md"
    assert [(l.no, l.text) for l in ms.lines[:3]] == [(1, "first"), (2, ""), (3, "third")]
    assert ms.line_label == "줄"


def test_plain_text_is_supported(tmp_path):
    path = tmp_path / "m.txt"
    path.write_text("Title line\n\nAbstract\nN = 12 patients were enrolled [1].\n", encoding="utf-8")
    ms = read_manuscript(path)
    assert ms.fmt == "txt"
    assert ms.lines[3].text.startswith("N = 12")


def test_latex_comments_are_stripped_and_tables_marked(tmp_path):
    path = tmp_path / "m.tex"
    path.write_text(
        "\\section{Introduction}\n"
        "Real text \\cite{kim2024}. % a comment with \\cite{ghost}\n"
        "100\\% of the sample\n"
        "\\begin{table}\nrow one\n\\end{table}\n",
        encoding="utf-8",
    )
    ms = read_manuscript(path)
    assert ms.fmt == "tex"
    assert "a comment" not in ms.text()
    assert "ghost" not in ms.text()
    assert "100\\%" in ms.lines[2].text  # 이스케이프된 퍼센트는 주석이 아니다
    assert ms.lines[4].kind == "table"


def test_docx_paragraph_numbers_and_table_kind(tmp_path):
    path = _docx(
        tmp_path,
        _p("Title")
        + _p("")
        + '<w:tbl><w:tr><w:tc>'
        + _p("cell A")
        + "</w:tc><w:tc>"
        + _p("cell B")
        + "</w:tc></w:tr></w:tbl>",
    )
    ms = read_manuscript(path)
    assert ms.fmt == "docx"
    assert ms.line_label == "문단"
    assert [(l.no, l.text, l.kind) for l in ms.lines] == [
        (1, "Title", "body"),
        (2, "", "body"),
        (3, "cell A", "table"),
        (4, "cell B", "table"),
    ]


def test_docx_tabs_and_breaks_become_spaces(tmp_path):
    path = _docx(
        tmp_path,
        '<w:p><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t>'
        "<w:noBreakHyphen/><w:t>d</w:t></w:r></w:p>",
    )
    ms = read_manuscript(path)
    assert ms.lines[0].text == "a b c-d"


def test_docx_footnotes_are_read_and_marked(tmp_path):
    footnotes = (
        f'<?xml version="1.0"?><w:footnotes xmlns:w="{W_NS}">'
        f'<w:footnote w:id="2">{_p("A footnote citing [4].")}</w:footnote></w:footnotes>'
    )
    path = _docx(tmp_path, _p("Body"), extra={"word/footnotes.xml": footnotes})
    ms = read_manuscript(path)
    assert [l.kind for l in ms.lines] == ["body", "footnote"]
    assert any("각주" in note for note in ms.notes)


def test_docx_tracked_deletions_are_excluded(tmp_path):
    path = _docx(
        tmp_path,
        "<w:p><w:r><w:t>Kept text.</w:t></w:r>"
        '<w:del w:id="1"><w:r><w:delText> Removed [99].</w:delText></w:r></w:del></w:p>',
    )
    ms = read_manuscript(path)
    assert ms.lines[0].text == "Kept text."
    assert ms.deleted_runs == 1


def test_docx_field_instructions_are_excluded(tmp_path):
    path = _docx(
        tmp_path,
        "<w:p><w:r><w:t>Sentence </w:t></w:r>"
        '<w:r><w:instrText> ADDIN EN.CITE &lt;rec-number&gt;77&lt;/rec-number&gt; </w:instrText></w:r>'
        "<w:r><w:t>[2]</w:t></w:r></w:p>",
    )
    ms = read_manuscript(path)
    assert ms.lines[0].text == "Sentence [2]"
    assert ms.field_citations == 1


# ── 인코딩 ───────────────────────────────────────────────────────────────────


def test_cp949_manuscript_is_decoded(tmp_path):
    path = tmp_path / "m.md"
    path.write_bytes("# 제목\n\n참가자 45명이 참여했다.\n".encode("cp949"))
    ms = read_manuscript(path)
    assert "참가자 45명" in ms.text()
    assert ms.encoding in ("cp949", "euc-kr")
    assert any("인코딩" in note for note in ms.notes)


def test_utf8_bom_is_handled(tmp_path):
    path = tmp_path / "m.md"
    path.write_bytes("\ufeff# Title\n".encode("utf-8"))
    ms = read_manuscript(path)
    assert ms.lines[0].text == "# Title"


def test_crlf_line_endings(tmp_path):
    path = tmp_path / "m.md"
    path.write_bytes(b"one\r\ntwo\r\n")
    ms = read_manuscript(path)
    assert [l.text for l in ms.lines[:2]] == ["one", "two"]


# ── 망가진 / 위험한 입력 ────────────────────────────────────────────────────


def test_missing_file(tmp_path):
    with pytest.raises(ManuscriptError, match="파일이 없습니다"):
        read_manuscript(tmp_path / "nope.md")


def test_directory_input(tmp_path):
    with pytest.raises(ManuscriptError, match="폴더가 아니라"):
        read_manuscript(tmp_path)


def test_empty_file_is_readable(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("", encoding="utf-8")
    ms = read_manuscript(path)
    assert [l.text for l in ms.lines] == [""]


def test_docx_without_document_xml(tmp_path):
    path = tmp_path / "broken.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "not a word file")
    with pytest.raises(ManuscriptError, match="Word 문서가 아닙니다"):
        read_manuscript(path)


def test_docx_that_is_not_a_zip(tmp_path):
    path = tmp_path / "fake.docx"
    path.write_text("I renamed a text file", encoding="utf-8")
    with pytest.raises(ManuscriptError, match="열 수 없습니다"):
        read_manuscript(path)


def test_pdf_is_rejected_with_a_useful_message(tmp_path):
    path = tmp_path / "m.pdf"
    path.write_bytes(b"%PDF-1.7\n rest")
    with pytest.raises(ManuscriptError, match="PDF는 지원하지 않습니다"):
        read_manuscript(path)


def test_legacy_doc_is_rejected(tmp_path):
    path = tmp_path / "m.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(ManuscriptError, match="구형 .doc"):
        read_manuscript(path)


def test_zip_renamed_to_md_is_reported(tmp_path):
    path = tmp_path / "m.md"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", "x")
    with pytest.raises(ManuscriptError, match="zip 형식"):
        read_manuscript(path)


def test_xml_entity_bomb_is_refused(tmp_path):
    path = tmp_path / "bomb.docx"
    evil = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;">]>'
        f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r>'
        "</w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", evil)
    with pytest.raises(ManuscriptError, match="DTD/ENTITY"):
        read_manuscript(path)


def test_oversized_uncompressed_docx_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(docio, "MAX_UNCOMPRESSED_BYTES", 10)
    path = _docx(tmp_path, _p("some text that is longer than ten bytes"))
    with pytest.raises(ManuscriptError, match="압축 해제 크기"):
        read_manuscript(path)


def test_too_many_zip_members_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(docio, "MAX_ZIP_MEMBERS", 1)
    path = _docx(tmp_path, _p("x"), extra={"word/styles.xml": "<a/>"})
    with pytest.raises(ManuscriptError, match="내부 파일 수"):
        read_manuscript(path)


def test_oversized_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(docio, "MAX_FILE_BYTES", 5)
    path = tmp_path / "m.md"
    path.write_text("more than five bytes", encoding="utf-8")
    with pytest.raises(ManuscriptError, match="너무 큽니다"):
        read_manuscript(path)


def test_unknown_extension_is_read_as_plain_text(tmp_path):
    path = tmp_path / "m.rtfish"
    path.write_text("hello [1]\n", encoding="utf-8")
    ms = read_manuscript(path)
    assert ms.fmt == "txt"
    assert any("평문으로 읽었습니다" in note for note in ms.notes)


# ── 원본을 절대 건드리지 않는다 ─────────────────────────────────────────────


def test_reading_never_modifies_the_manuscript(tmp_path):
    src = (EXAMPLES / "manuscript_flawed.docx").read_bytes()
    path = tmp_path / "copy.docx"
    path.write_bytes(src)
    before = (hashlib.sha256(src).hexdigest(), path.stat().st_mtime_ns)
    analyse(path)
    after = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    assert before == after


# ── 섹션·캡션 탐지 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("## Abstract", "abstract"),
        ("# 1. Introduction", "introduction"),
        ("**Methods**", "methods"),
        ("2.1 Materials and Methods", "methods"),
        ("\\section{References}", "references"),
        ("참고문헌", "references"),
        ("Acknowledgements", "tail"),
        ("Figure legends", "tail"),
        ("A sentence about methods that is far too long to be a heading", None),
        ("", None),
        ("Results showed that the intervention worked", None),
    ],
)
def test_heading_key(text, expected):
    assert heading_key(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("**Figure 1.** Study flow", ("figure", 1)),
        ("Figure 2: change scores", ("figure", 2)),
        ("Fig. 3 - trajectory", ("figure", 3)),
        ("Table 1. Baseline characteristics", ("table", 1)),
        ("표 2. 이차 결과", ("table", 2)),
        ("그림 1. 흐름도", ("figure", 1)),
        ("TABLE III. Roman", ("table", 3)),
        ("Supplementary Figure 1. not in scope", None),
        ("As shown in Figure 1, the effect was small", None),
        ("Figures were prepared in R", None),
    ],
)
def test_caption_of(text, expected):
    assert caption_of(text) == expected


def test_sections_of_the_bundled_example():
    ms = read_manuscript(EXAMPLES / "manuscript_clean.md")
    sec = detect_sections(ms)
    assert sec.abstract_found and sec.references_found
    assert sec.title.startswith("Acoustic slow-breathing guidance")
    assert len(sec.captions) == 5
    assert "References" not in " ".join(l.text for l in sec.body)
    # 참고문헌 뒤의 그림 범례/표는 참고문헌 섹션에 딸려 들어가면 안 된다
    assert not any("Figure 1." in l.text for l in sec.references)


def test_manuscript_without_headings_still_parses(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("Just a title\n\nSome prose with no structure at all.\n", encoding="utf-8")
    sec = detect_sections(read_manuscript(path))
    assert not sec.abstract_found and not sec.references_found
    assert sec.title == "Just a title"
