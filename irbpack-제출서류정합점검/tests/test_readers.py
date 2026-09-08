"""문서 읽기 — docx 본문 + 표 인라인, 인코딩, 못 읽는 형식은 자백."""
import os
import zipfile

import pytest

from irbpack import readers
from tests.docx_builder import write_docx


def test_docx_paragraphs_and_tables_inline_in_order(tmp_path):
    p = write_docx(str(tmp_path / "a.docx"), ["첫 문단", [["연구제목", "가나다"], ["Version", "1.0"]], "둘째 문단"])
    doc = readers.load(p)
    assert doc.readable
    texts = [x.text for x in doc.paras]
    assert texts == ["첫 문단", "연구제목\t가나다", "Version\t1.0", "둘째 문단"]
    assert doc.paras[1].table == 1 and doc.paras[1].row == 1
    assert doc.paras[2].row == 2
    assert doc.paras[3].table == 0


def test_docx_cell_pipe_does_not_break_row(tmp_path):
    p = write_docx(str(tmp_path / "a.docx"), [[["a | b", "c"]]])
    doc = readers.load(p)
    assert doc.paras[0].text == "a | b\tc"


def test_docx_heading_sets_section(tmp_path):
    p = write_docx(str(tmp_path / "a.docx"), ["# 3. 연구 방법", "본 연구는 총 2회 방문한다."])
    doc = readers.load(p)
    assert doc.paras[1].section == "3. 연구 방법"
    assert "§3. 연구 방법" in doc.paras[1].where()


def test_docx_tracked_changes_read_as_accepted(tmp_path):
    from tests.docx_builder import p_xml
    raw = p_xml("총 ", ins="3회 방문", dele="2회 방문")
    p = write_docx(str(tmp_path / "a.docx"), [], raw_body=raw)
    doc = readers.load(p)
    assert doc.tracked_changes
    assert doc.paras[0].text == "총 3회 방문"
    assert "2회" not in doc.text


def test_docx_empty_is_unreadable(tmp_path):
    p = write_docx(str(tmp_path / "a.docx"), [])
    doc = readers.load(p)
    assert not doc.readable and "비어" in doc.unread_reason


def test_docx_table_only(tmp_path):
    p = write_docx(str(tmp_path / "a.docx"), [[["연구제목", "표만 있는 문서의 제목입니다"]]])
    doc = readers.load(p)
    assert doc.readable and doc.paras[0].table == 1


def test_bad_zip_is_unreadable(tmp_path):
    p = tmp_path / "x.docx"
    p.write_bytes(b"not a zip at all")
    doc = readers.load(str(p))
    assert not doc.readable


def test_zip_without_document_xml(tmp_path):
    p = tmp_path / "x.docx"
    with zipfile.ZipFile(str(p), "w") as zf:
        zf.writestr("hello.txt", "hi")
    doc = readers.load(str(p))
    assert not doc.readable and "docx" in doc.unread_reason


@pytest.mark.parametrize("ext", [".hwp", ".hwpx", ".doc", ".rtf"])
def test_unsupported_ext_is_unreadable_with_hint(tmp_path, ext):
    p = tmp_path / ("a" + ext)
    p.write_bytes(b"\x00" * 10)
    doc = readers.load(str(p))
    assert not doc.readable
    if ext.startswith(".hwp"):
        assert "DOCX" in doc.unread_reason


def test_txt_cp949(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("연구계획서\n총 2회 방문".encode("cp949"))
    doc = readers.load(str(p))
    assert doc.readable and doc.paras[1].text == "총 2회 방문"


def test_txt_utf8_sig(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("﻿연구계획서\n총 2회 방문".encode("utf-8"))
    doc = readers.load(str(p))
    assert doc.paras[0].text == "연구계획서"


def test_txt_unknown_encoding_unreadable(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"\xff\xfe\x00\xd8" + b"\x81\x81\x81" * 20)
    doc = readers.load(str(p))
    assert not doc.readable


def test_md_table_rows_become_tab_rows(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# 제목\n\n| 연구제목 | 가 |\n|---|---|\n| Version | 1.0 |\n", encoding="utf-8")
    doc = readers.load(str(p))
    assert doc.paras[1].text == "연구제목\t가" and doc.paras[1].table == 1 and doc.paras[2].row == 2


def test_md_heading_sets_section(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("## 4. 보상\n교통비 실비\n", encoding="utf-8")
    doc = readers.load(str(p))
    assert doc.paras[1].section == "4. 보상"


def test_nfd_filename_and_text_normalised(tmp_path):
    import unicodedata
    name = unicodedata.normalize("NFD", "동의서.md")
    p = tmp_path / name
    p.write_text(unicodedata.normalize("NFD", "총 2회 방문"), encoding="utf-8")
    doc = readers.load(str(p))
    assert doc.name == "동의서.md" and doc.paras[0].text == "총 2회 방문"


def test_sanitize_name_newline_and_control():
    assert readers.sanitize_name("a\nb\r[치명]\x07c") == "a␤b␤[치명]�c"


def test_symlink_not_followed(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("총 2회 방문", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        os.symlink(str(real), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlink 불가")
    doc = readers.load(str(link))
    assert not doc.readable and "심볼릭" in doc.unread_reason


def test_huge_single_paragraph_docx_is_handled(tmp_path):
    big = "가" * (2 * 1024 * 1024)
    p = write_docx(str(tmp_path / "big.docx"), [big])
    doc = readers.load(p)
    assert doc.readable and len(doc.paras) == 1


def test_pdf_text_layer(tmp_path):
    import zlib
    content = b"BT /F1 12 Tf (Protocol version 1.0 total two visits about ninety minutes each visit for every enrolled adult subject in this study) Tj ET"
    stream = zlib.compress(content)
    pdf = (b"%PDF-1.4\n1 0 obj << /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
           + stream + b"\nendstream\nendobj\n%%EOF")
    p = tmp_path / "a.pdf"
    p.write_bytes(pdf)
    doc = readers.load(str(p))
    assert doc.readable and "version 1.0" in doc.text


def test_pdf_without_text_layer_unreadable(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4\n1 0 obj << >>\nstream\n\x00\x01\x02\nendstream\n%%EOF")
    doc = readers.load(str(p))
    assert not doc.readable and "텍스트 레이어" in doc.unread_reason


def test_not_pdf_signature(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"hello")
    assert not readers.load(str(p)).readable


def test_missing_file(tmp_path):
    doc = readers.load(str(tmp_path / "none.md"))
    assert not doc.readable


def test_pdf_cid_font_is_unreadable(tmp_path):
    import zlib
    content = b"BT /F1 12 Tf <00350036003700380039> Tj <00410042004300440045> Tj ET " * 20
    stream = zlib.compress(content)
    pdf = (b"%PDF-1.4\n1 0 obj << /Type /Font /Subtype /Type0 /Encoding /Identity-H /DescendantFonts [2 0 R] >> endobj\n"
           b"2 0 obj << /Type /Font /Subtype /CIDFontType2 >> endobj\n3 0 obj << /Length " + str(len(stream)).encode()
           + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream\nendobj\n%%EOF")
    p = tmp_path / "cid.pdf"
    p.write_bytes(pdf)
    doc = readers.load(str(p))
    assert not doc.readable and "CID" in doc.unread_reason


def test_pdf_glyph_garbage_without_marker_is_unreadable(tmp_path):
    import zlib
    content = b"BT (" + bytes(range(0x30, 0x7a)) * 3 + b") Tj ET"
    stream = zlib.compress(content)
    pdf = b"%PDF-1.4\n1 0 obj << /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream\n%%EOF"
    p = tmp_path / "g.pdf"
    p.write_bytes(pdf)
    assert not readers.load(str(p)).readable


def test_tex_is_collected_but_unreadable(tmp_path):
    p = tmp_path / "paper.tex"
    p.write_text("\\documentclass{article}", encoding="utf-8")
    doc = readers.load(str(p))
    assert not doc.readable and "원고" in doc.unread_reason


def _zip_flag_encrypted(path):
    data = bytearray(open(path, "rb").read())
    i = 0
    while True:
        i = data.find(b"PK\x03\x04", i)
        if i < 0:
            break
        data[i + 6] |= 1
        i += 4
    i = 0
    while True:
        i = data.find(b"PK\x01\x02", i)
        if i < 0:
            break
        data[i + 8] |= 1
        i += 4
    open(path, "wb").write(bytes(data))


def test_encrypted_docx_is_unreadable_not_crash(tmp_path):
    p = write_docx(str(tmp_path / "enc.docx"), ["연구계획서", "총 2회 방문"])
    _zip_flag_encrypted(p)
    doc = readers.load(p)
    assert not doc.readable and doc.unread_reason


def test_corrupted_deflate_is_unreadable_not_crash(tmp_path):
    p = write_docx(str(tmp_path / "bad.docx"), ["연구계획서 " * 200, "총 2회 방문 " * 200])
    data = bytearray(open(p, "rb").read())
    start = data.find(b"word/document.xml") + len(b"word/document.xml")
    for k in range(start + 10, start + 40):
        data[k] ^= 0xFF
    open(p, "wb").write(bytes(data))
    doc = readers.load(p)
    assert not doc.readable


def test_pdf_decompression_bomb_is_refused(tmp_path):
    import zlib
    payload = zlib.compress(b"BT (x) Tj ET " * 6_000_000)
    pdf = b"%PDF-1.4\n1 0 obj << /Length " + str(len(payload)).encode() + b" /Filter /FlateDecode >>\nstream\n" + payload + b"\nendstream\n%%EOF"
    p = tmp_path / "bomb.pdf"
    p.write_bytes(pdf)
    doc = readers.load(str(p))
    assert not doc.readable and ("넘습니다" in doc.unread_reason or "레이어" in doc.unread_reason)


def test_sdt_wrapped_rows_and_cells_are_read(tmp_path):
    from tests.docx_builder import p_xml
    raw = ('<w:tbl><w:tr><w:tc>{}</w:tc><w:tc>{}</w:tc></w:tr>'
           '<w:sdt><w:sdtContent><w:tr><w:tc>{}</w:tc><w:tc>{}</w:tc></w:tr></w:sdtContent></w:sdt>'
           '<w:tr><w:tc>{}</w:tc><w:sdt><w:sdtContent><w:tc>{}</w:tc></w:sdtContent></w:sdt></w:tr></w:tbl>'
           '<w:customXml>{}</w:customXml>').format(p_xml("연구제목"), p_xml("일반 셀"), p_xml("Version"), p_xml("1.0"),
                                                   p_xml("대상자 수"), p_xml("총 90명"), p_xml("총 3회 방문"))
    p = write_docx(str(tmp_path / "s.docx"), [], raw_body=raw)
    doc = readers.load(p)
    texts = [x.text for x in doc.paras]
    assert texts == ["연구제목\t일반 셀", "Version\t1.0", "대상자 수\t총 90명", "총 3회 방문"]


def test_tracked_deleted_row_and_movefrom_are_dropped(tmp_path):
    from tests.docx_builder import p_xml
    raw = ('<w:tbl><w:tr><w:trPr><w:del w:id="9" w:author="x"/></w:trPr><w:tc>{}</w:tc></w:tr>'
           '<w:tr><w:tc>{}</w:tc></w:tr></w:tbl>'
           '<w:p><w:moveFrom w:id="3" w:author="x"><w:r><w:t>옮겨간 옛 문장</w:t></w:r></w:moveFrom><w:r><w:t>남는 문장</w:t></w:r></w:p>').format(
        p_xml("Version 0.9"), p_xml("Version 1.2"))
    p = write_docx(str(tmp_path / "t.docx"), [], raw_body=raw)
    doc = readers.load(p)
    assert [x.text for x in doc.paras] == ["Version 1.2", "남는 문장"]
    assert doc.tracked_changes


def test_mc_fallback_is_not_duplicated(tmp_path):
    raw = ('<w:p><w:r><w:t>본문 </w:t></w:r>'
           '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
           '<mc:Choice><w:r><w:t>상자 문장</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>상자 문장</w:t></w:r></mc:Fallback>'
           '</mc:AlternateContent></w:p>')
    p = write_docx(str(tmp_path / "m.docx"), [], raw_body=raw)
    doc = readers.load(p)
    assert doc.paras[0].text == "본문 상자 문장"
