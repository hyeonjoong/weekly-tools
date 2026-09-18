# -*- coding: utf-8 -*-
"""정규화 — 이 툴의 존재 이유. 여기가 무너지면 매번 우는 체커가 된다."""

import hashlib
import os
import zipfile

import pytest

from conftest import write, 예제
from stalecheck import normalize


# ---------------------------------------------------------------- 원시 해시
def test_raw_hash_matches_hashlib(tmp_path):
    p = write(tmp_path / "a.txt", b"hello")
    assert normalize.raw_hash(p) == hashlib.sha256(b"hello").hexdigest()


def test_raw_hash_empty_file(tmp_path):
    p = write(tmp_path / "e.txt", b"")
    assert normalize.raw_hash(p) == hashlib.sha256(b"").hexdigest()


def test_raw_hash_large_multichunk(tmp_path):
    data = os.urandom(3 * normalize.CHUNK + 17)
    p = write(tmp_path / "big.bin", data)
    assert normalize.raw_hash(p) == hashlib.sha256(data).hexdigest()


def test_raw_hash_missing_file_raises(tmp_path):
    with pytest.raises(normalize.UnreadableFile):
        normalize.raw_hash(str(tmp_path / "없는파일.txt"))


def test_unreadable_carries_path_and_reason(tmp_path):
    with pytest.raises(normalize.UnreadableFile) as info:
        normalize.raw_hash(str(tmp_path / "없음"))
    assert info.value.path.endswith("없음")
    assert info.value.reason


# ---------------------------------------------------------------- PDF
def _pdf(created="20260730111000", doc_id="AA" * 16, text="Figure 2"):
    return 예제.make_pdf(text, created, doc_id)


def test_pdf_creationdate_only_difference_collapses(tmp_path):
    a = write(tmp_path / "a.pdf", _pdf(created="20260730111000", doc_id="AA" * 16))
    b = write(tmp_path / "b.pdf", _pdf(created="20260731104300", doc_id="AA" * 16))
    assert normalize.raw_hash(a) != normalize.raw_hash(b)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_pdf_id_only_difference_collapses(tmp_path):
    a = write(tmp_path / "a.pdf", _pdf(doc_id="AA" * 16))
    b = write(tmp_path / "b.pdf", _pdf(doc_id="BB" * 16))
    assert normalize.raw_hash(a) != normalize.raw_hash(b)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_pdf_both_creationdate_and_id_collapse(tmp_path):
    a = write(tmp_path / "a.pdf", _pdf("20260730111000", "AA" * 16))
    b = write(tmp_path / "b.pdf", _pdf("20260731104300", "BB" * 16))
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_pdf_real_content_difference_survives(tmp_path):
    """내용이 다르면 정규화해도 달라야 한다 — 과잉 정규화는 조용한 거짓말이다."""
    a = write(tmp_path / "a.pdf", _pdf(text="Figure 2"))
    b = write(tmp_path / "b.pdf", _pdf(text="Figure 3"))
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_pdf_tag_is_pdf_meta(tmp_path):
    p = write(tmp_path / "a.pdf", _pdf())
    assert normalize.content_hash(p)[1] == normalize.NORM_PDF


def test_pdf_without_metadata_is_not_labelled_normalized(tmp_path):
    p = write(tmp_path / "a.pdf", b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n%%EOF\n")
    h, tag = normalize.content_hash(p)
    assert tag == normalize.NORM_NONE
    assert h == normalize.raw_hash(p)


def test_pdf_moddate_stripped(tmp_path):
    base = b"%PDF-1.4\n<< /ModDate (D:20260101000000) /X 1 >>\n%%EOF"
    other = b"%PDF-1.4\n<< /ModDate (D:20261231235959) /X 1 >>\n%%EOF"
    a = write(tmp_path / "a.pdf", base)
    b = write(tmp_path / "b.pdf", other)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_pdf_no_space_before_paren(tmp_path):
    a = write(tmp_path / "a.pdf", b"%PDF-1.4\n/CreationDate(D:20260101000000)\n/X 1")
    b = write(tmp_path / "b.pdf", b"%PDF-1.4\n/CreationDate(D:20260202000000)\n/X 1")
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_pdf_escaped_paren_inside_date_does_not_eat_rest(tmp_path):
    """이스케이프된 괄호가 있어도 정규식이 문서를 통째로 삼키면 안 된다."""
    a = write(tmp_path / "a.pdf", rb"%PDF-1.4/CreationDate (D:2026\)01) /Title (KEEP-A)")
    b = write(tmp_path / "b.pdf", rb"%PDF-1.4/CreationDate (D:2026\)02) /Title (KEEP-B)")
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_pdf_id_array_with_hex_strings(tmp_path):
    a = write(tmp_path / "a.pdf", b"%PDF-1.4 /ID [<AB12> <AB12>] /X 9")
    b = write(tmp_path / "b.pdf", b"%PDF-1.4 /ID [<9F00> <9F00>] /X 9")
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_pdf_empty_file(tmp_path):
    p = write(tmp_path / "a.pdf", b"")
    assert normalize.content_hash(p) == (hashlib.sha256(b"").hexdigest(),
                                         normalize.NORM_NONE)


def test_pdf_binary_garbage_does_not_raise(tmp_path):
    data = os.urandom(4096)
    p = write(tmp_path / "a.pdf", data)
    h, tag = normalize.content_hash(p)
    assert tag == normalize.NORM_NONE
    assert h == hashlib.sha256(data).hexdigest()


def test_pdf_missing_file_raises(tmp_path):
    with pytest.raises(normalize.UnreadableFile):
        normalize.content_hash(str(tmp_path / "없음.pdf"))


def test_generated_pdf_is_wellformed(tmp_path):
    data = _pdf()
    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"startxref" in data


# ---------------------------------------------------------------- DOCX / zip
def _docx(modified="2026-07-30T02:10:00Z", paragraph="예제 원고 본문"):
    return 예제.make_docx(paragraph, modified)


def test_docx_docprops_only_difference_collapses(tmp_path):
    a = write(tmp_path / "a.docx", _docx("2026-07-30T02:10:00Z"))
    b = write(tmp_path / "b.docx", _docx("2026-07-31T01:39:00Z"))
    assert normalize.raw_hash(a) != normalize.raw_hash(b)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_docx_body_difference_survives(tmp_path):
    a = write(tmp_path / "a.docx", _docx(paragraph="본문 A"))
    b = write(tmp_path / "b.docx", _docx(paragraph="본문 B"))
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_docx_tag_is_zip_docprops(tmp_path):
    p = write(tmp_path / "a.docx", _docx())
    assert normalize.content_hash(p)[1] == normalize.NORM_ZIP


def test_zip_without_docprops_is_labelled_zip_container(tmp_path):
    """docProps 가 없어도 zip 컨테이너 수준은 이미 벗겨 냈다 — none 이라 하면 거짓말이다."""
    p = str(tmp_path / "a.docx")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("word/document.xml", "<x/>")
    assert normalize.content_hash(p)[1] == normalize.NORM_ZIP_CONTAINER


def test_zip_container_tag_reported_when_only_timestamps_differ(tmp_path):
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    for path, when in ((a, (2026, 1, 1, 0, 0, 0)), (b, (2026, 9, 9, 9, 9, 8))):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(zipfile.ZipInfo("word/document.xml", date_time=when), "<x/>")
    assert normalize.raw_hash(a) != normalize.raw_hash(b)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]
    assert normalize.content_hash(a)[1] == normalize.NORM_ZIP_CONTAINER


def test_zip_entry_order_does_not_matter(tmp_path):
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    entries = [("word/document.xml", "<a/>"), ("word/styles.xml", "<b/>"),
               ("docProps/core.xml", "<c/>")]
    with zipfile.ZipFile(a, "w") as zf:
        for n, d in entries:
            zf.writestr(n, d)
    with zipfile.ZipFile(b, "w") as zf:
        for n, d in reversed(entries):
            zf.writestr(n, d)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_zip_entry_rename_changes_hash(tmp_path):
    """엔트리명도 해시에 넣는다 — 내용만 같고 이름이 다르면 다른 문서다."""
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    with zipfile.ZipFile(a, "w") as zf:
        zf.writestr("word/document.xml", "<x/>")
        zf.writestr("docProps/core.xml", "<c/>")
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("word/document2.xml", "<x/>")
        zf.writestr("docProps/core.xml", "<c/>")
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_zip_concatenation_ambiguity_guarded(tmp_path):
    """이름+내용을 이어 붙일 때 경계가 없으면 'ab'+'c' 와 'a'+'bc' 가 충돌한다."""
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    with zipfile.ZipFile(a, "w") as zf:
        zf.writestr("w/ab", "c")
        zf.writestr("docProps/core.xml", "")
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("w/a", "bc")
        zf.writestr("docProps/core.xml", "")
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_corrupt_zip_falls_back_to_raw_bytes(tmp_path):
    p = write(tmp_path / "a.docx", b"not a zip at all")
    h, tag = normalize.content_hash(p)
    assert tag == normalize.NORM_NONE
    assert h == normalize.raw_hash(p)


def test_corrupt_deflate_stream_falls_back_not_traceback(tmp_path):
    """한두 바이트 손상된 docx 는 흔하다(동기화 중단·USB 복사). 터지면 안 된다."""
    p = str(tmp_path / "a.docx")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docProps/core.xml", "<c/>")
        zf.writestr("word/document.xml", "<x/>" * 2000)
    data = bytearray(open(p, "rb").read())
    # 압축 데이터 한복판을 망가뜨린다
    for i in range(len(data) // 3, len(data) // 3 + 8):
        data[i] ^= 0xFF
    with open(p, "wb") as fh:
        fh.write(bytes(data))
    h, tag = normalize.content_hash(p)
    assert tag == normalize.NORM_NONE
    assert h == normalize.raw_hash(p)


def test_zip_bomb_does_not_exhaust_memory(tmp_path):
    """작은 .docx 가 수 GB 로 부풀 수 있다 — 상한을 넘으면 정규화를 포기한다."""
    p = str(tmp_path / "bomb.docx")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docProps/core.xml", "<c/>")
        zf.writestr("word/big.bin", b"\0" * (4 * 1024 * 1024))
    assert os.path.getsize(p) < 100 * 1024
    h, tag = normalize.content_hash(p)
    assert tag == normalize.NORM_ZIP  # 4MB 는 상한 아래라 정상 처리
    original = normalize.MAX_ZIP_UNCOMPRESSED
    normalize.MAX_ZIP_UNCOMPRESSED = 1024
    try:
        h2, tag2 = normalize.content_hash(p)
        assert tag2 == normalize.NORM_NONE, "상한을 넘으면 정규화했다고 말하면 안 된다"
        assert h2 == normalize.raw_hash(p)
    finally:
        normalize.MAX_ZIP_UNCOMPRESSED = original


def test_fifo_at_scanned_path_does_not_hang(tmp_path):
    """스캔 뒤 FIFO 로 바뀐 경로를 평범하게 열면 영원히 멈춘다."""
    p = str(tmp_path / "paper.md")
    os.mkfifo(p)
    with pytest.raises(normalize.UnreadableFile) as info:
        normalize.raw_hash(p)
    assert "일반 파일" in info.value.reason


def test_directory_path_is_unreadable_not_a_crash(tmp_path):
    with pytest.raises(normalize.UnreadableFile):
        normalize.raw_hash(str(tmp_path))


@pytest.mark.parametrize("ext", sorted(normalize.ZIP_EXTS))
def test_all_zip_exts_use_zip_path(tmp_path, ext):
    a = write(tmp_path / ("a" + ext), _docx("2026-01-01T00:00:00Z"))
    b = write(tmp_path / ("b" + ext), _docx("2026-12-31T00:00:00Z"))
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


@pytest.mark.parametrize("ext", [".png", ".csv", ".md", ".py", ".txt", ".hwp"])
def test_other_exts_use_raw_bytes(tmp_path, ext):
    p = write(tmp_path / ("a" + ext), b"\x01\x02\x03")
    h, tag = normalize.content_hash(p)
    assert tag == normalize.NORM_NONE
    assert h == hashlib.sha256(b"\x01\x02\x03").hexdigest()


def test_extension_case_insensitive(tmp_path):
    a = write(tmp_path / "a.PDF", _pdf(created="20260101000000"))
    b = write(tmp_path / "b.PDF", _pdf(created="20261231000000"))
    assert normalize.content_hash(a)[0] == normalize.content_hash(b)[0]


def test_hwp_is_hashed_but_not_normalized(tmp_path):
    p = write(tmp_path / "보고서.hwp", b"HWP Document File\x00\x01")
    h, tag = normalize.content_hash(p)
    assert tag == normalize.NORM_NONE and len(h) == 64


def test_identical_files_identical_hash(tmp_path):
    data = _pdf()
    a = write(tmp_path / "a.pdf", data)
    b = write(tmp_path / "b.pdf", data)
    assert normalize.content_hash(a) == normalize.content_hash(b)


def test_png_one_byte_difference_detected(tmp_path):
    a = write(tmp_path / "a.png", 예제.make_png(8, 8, (1, 2, 3)))
    b = write(tmp_path / "b.png", 예제.make_png(8, 8, (1, 2, 4)))
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_zip_excluded_prefix_is_docprops_only():
    assert normalize.ZIP_EXCLUDED_PREFIXES == ("docProps/",)


@pytest.mark.parametrize("pattern", normalize._PDF_META_PATTERNS)
def test_every_pdf_pattern_is_length_bounded(pattern):
    """제한 없는 와일드카드는 스트림 안의 우연한 일치로 본문을 삼킨다."""
    src = pattern.pattern
    for unbounded in (b"]*", b"]+", b".*", b".+", b'"*', b'<*'):
        assert unbounded not in src, "%r 에 제한 없는 %r" % (src, unbounded)


def test_pdf_id_pattern_cannot_eat_a_whole_document():
    blob = b"/ID[" + b"A" * 5000 + b"]"
    for pattern in normalize._PDF_META_PATTERNS:
        assert pattern.sub(b"", blob) == blob or len(pattern.sub(b"", blob)) > 4000


def test_zip_length_framing_prevents_collision(tmp_path):
    """name||content 를 그냥 이어 붙이면 서로 다른 문서가 같은 해시를 낸다."""
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    with zipfile.ZipFile(a, "w") as zf:
        zf.writestr("docProps/core.xml", "")
        zf.writestr("a", b"Xb\x00Y")
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("docProps/core.xml", "")
        zf.writestr("a", b"X")
        zf.writestr("b", b"Y")
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


def test_duplicate_zip_entry_names_are_all_hashed(tmp_path):
    """namelist()+read() 는 중복 이름에서 마지막 것만 읽어 앞엣것을 숨긴다."""
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    for path, first in ((a, "<첫번째/>"), (b, "<다른것/>")):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("docProps/core.xml", "")
            zf.writestr("word/document.xml", first)
            zf.writestr("word/document.xml", "<마지막/>")
    assert normalize.content_hash(a)[0] != normalize.content_hash(b)[0]


@pytest.mark.parametrize("tag", ["xmp:CreateDate", "xmp:ModifyDate",
                                 "xmp:MetadataDate", "xmpMM:InstanceID",
                                 "xmpMM:DocumentID"])
def test_xmp_metadata_only_difference_collapses(tmp_path, tag):
    """실제 PDF 의 절반 가까이가 XMP 패킷을 함께 싣는다 — 이걸 빼면 매번 운다."""
    body = b"%PDF-1.5\n<x:xmpmeta><rdf:RDF><rdf:Description>"
    tail = b"</rdf:Description></rdf:RDF></x:xmpmeta>\n/Title (KEEP)\n%%EOF"
    t = tag.encode()
    a = write(tmp_path / "a.pdf", body + b"<" + t + b">2026-07-30T11:10:00Z</" + t + b">" + tail)
    b_ = write(tmp_path / "b.pdf", body + b"<" + t + b">2026-07-31T10:43:00Z</" + t + b">" + tail)
    assert normalize.raw_hash(a) != normalize.raw_hash(b_)
    assert normalize.content_hash(a)[0] == normalize.content_hash(b_)[0]


def test_xmp_attribute_form_collapses(tmp_path):
    a = write(tmp_path / "a.pdf", b'%PDF-1.5 xmp:CreateDate="2026-07-30" /T (K)')
    b_ = write(tmp_path / "b.pdf", b'%PDF-1.5 xmp:CreateDate="2026-07-31" /T (K)')
    assert normalize.content_hash(a)[0] == normalize.content_hash(b_)[0]


def test_xmp_stripping_does_not_hide_real_content(tmp_path):
    a = write(tmp_path / "a.pdf", b"%PDF-1.5 <xmp:CreateDate>X</xmp:CreateDate> (VALUE 0.42)")
    b_ = write(tmp_path / "b.pdf", b"%PDF-1.5 <xmp:CreateDate>Y</xmp:CreateDate> (VALUE 0.31)")
    assert normalize.content_hash(a)[0] != normalize.content_hash(b_)[0]
