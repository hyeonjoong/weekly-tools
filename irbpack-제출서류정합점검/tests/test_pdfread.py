"""PDF 텍스트 레이어 — 읽히면 읽고, 못 읽으면 **못 읽었다고 말한다**."""

import zlib

from irbpack.docread import read_document
from irbpack.pdfread import _is_font_program, extract_pdf_text


def build_pdf(path, content_stream, extra_objects=b""):
    """FlateDecode 콘텐츠 스트림 하나만 든 최소 PDF."""
    payload = zlib.compress(content_stream)
    body = (b"%PDF-1.4\n"
            b"1 0 obj\n<< /Filter /FlateDecode /Length " + str(len(payload)).encode() + b" >>\nstream\n"
            + payload + b"\nendstream\nendobj\n" + extra_objects + b"trailer\n<< >>\n%%EOF\n")
    with open(str(path), "wb") as handle:
        handle.write(body)
    return str(path)


# 40자 미만이면 '텍스트 레이어가 사실상 없다'로 보므로 예제 문장을 충분히 길게 둔다.
SIMPLE = (b"BT /F1 12 Tf 72 720 Td (Study Protocol Version 1.2 of the packet) Tj "
          b"0 -14 Td (Informed Consent Form and Case Report Form) Tj ET")


def test_simple_pdf_text(tmp_path):
    path = build_pdf(tmp_path / "a.pdf", SIMPLE)
    lines, error, note = extract_pdf_text(path)
    assert error == ""
    assert any("Study Protocol" in line for line in lines)


def test_pdf_document_is_read(tmp_path):
    path = build_pdf(tmp_path / "a.pdf", SIMPLE)
    document = read_document(path)
    assert document.read_ok
    assert "Version 1.2" in document.text


def test_glyphs_on_one_line_are_joined(tmp_path):
    """글자마다 Td 로 위치를 잡아도 세로 이동이 없으면 한 줄이다."""
    stream = (b"BT 72 720 Td (padding line to reach the minimum length) Tj 0 -14 Td "
              b"(x) Tj 10 0 Td (A) Tj 10 0 Td (B) Tj 0 -14 Td (C) Tj ET")
    path = build_pdf(tmp_path / "a.pdf", stream)
    lines, error, _ = extract_pdf_text(path)
    assert error == ""
    assert any(line.endswith("xAB") for line in lines)
    assert "C" in lines[-1]


def test_new_line_on_vertical_move(tmp_path):
    stream = (b"BT 72 720 Td (first line of the document with enough characters) Tj "
              b"0 -14 Td (second line of the document) Tj ET")
    path = build_pdf(tmp_path / "a.pdf", stream)
    lines, _, _ = extract_pdf_text(path)
    assert "first" in lines[0] and "second" in lines[1]


def test_tj_array_kerning_becomes_space(tmp_path):
    stream = (b"BT 72 720 Td (padding line to reach the minimum length) Tj "
              b"0 -14 Td [(A) -400 (B)] TJ ET")
    path = build_pdf(tmp_path / "a.pdf", stream)
    lines, _, _ = extract_pdf_text(path)
    assert any("A B" in line for line in lines)


def test_encrypted_pdf_is_refused(tmp_path):
    path = tmp_path / "enc.pdf"
    path.write_bytes(b"%PDF-1.4\ntrailer << /Encrypt 5 0 R >>\n%%EOF")
    _, error, _ = extract_pdf_text(str(path))
    assert "암호" in error


def test_non_pdf_is_refused(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"hello")
    _, error, _ = extract_pdf_text(str(path))
    assert "PDF 형식" in error


def test_scanned_pdf_without_text_layer(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Subtype /Image >>\nstream\nxx\nendstream\n%%EOF")
    _, error, _ = extract_pdf_text(str(path))
    assert "텍스트 레이어" in error


def test_scanned_pdf_document_is_unreadable(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Subtype /Image >>\nstream\nxx\nendstream\n%%EOF")
    document = read_document(str(path))
    assert not document.read_ok and document.error


def test_too_little_text_is_refused(tmp_path):
    path = build_pdf(tmp_path / "a.pdf", b"BT 72 720 Td (hi) Tj ET")
    _, error, _ = extract_pdf_text(path)
    assert "거의 찾지 못했습니다" in error


def test_font_program_stream_is_skipped():
    assert _is_font_program(b"/Length1 268664 /Filter /FlateDecode")
    assert _is_font_program(b"/Subtype /Image")


def test_length_is_not_mistaken_for_length1():
    """'/Length 12673' 을 '/Length1' 로 읽어 본문을 통째로 버리던 사고."""
    assert not _is_font_program(b"/Filter /FlateDecode /Length 12673")


def test_tounicode_cmap_maps_hex_codes(tmp_path):
    cmap = (b"/CIDInit /ProcSet findresource begin\n"
            b"1 beginbfchar\n<0041> <D55C>\nendbfchar\nend")
    cmap_payload = zlib.compress(cmap)
    extra = (b"2 0 obj\n<< /Filter /FlateDecode /Length " + str(len(cmap_payload)).encode() +
             b" >>\nstream\n" + cmap_payload + b"\nendstream\nendobj\n")
    stream = b"BT 72 720 Td <0041> Tj ET " + b"(padding text for the minimum length check) Tj " * 2
    path = build_pdf(tmp_path / "a.pdf", stream, extra_objects=extra)
    lines, error, _ = extract_pdf_text(path)
    assert error == ""
    assert any("한" in line for line in lines)


def test_unmapped_hex_lowers_confidence(tmp_path):
    stream = b"BT 72 720 Td " + b"<00410042> Tj " * 40 + b"ET"
    path = build_pdf(tmp_path / "a.pdf", stream)
    _, error, _ = extract_pdf_text(path)
    assert "해독하지 못했습니다" in error


def test_missing_file(tmp_path):
    _, error, _ = extract_pdf_text(str(tmp_path / "없음.pdf"))
    assert "열지 못했습니다" in error
