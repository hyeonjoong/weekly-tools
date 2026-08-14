"""파일 읽기: 네 포맷 × 세 위치, 표 셀, 변경내용 추적, zip/XML 폭탄 방어."""

from __future__ import annotations

import pathlib
import zipfile

import pytest

from docx_fixture import p, p_tracked, simple_docx, tbl, write_docx
from revcheck.docio import DocumentError, read_document

SIMPLE_PARAS = [
    "## Methods",
    "Participants were randomised to the active or the sham device.",
    "## Results",
    "The mean ISI decreased by 5.2 points (SD 3.1).",
]


def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ── 네 포맷 × 세 위치 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["제출본", "개정본", "응답서"])
@pytest.mark.parametrize("suffix", [".md", ".txt", ".tex", ".docx"])
def test_all_four_formats_in_all_three_positions(tmp_path, suffix, role):
    if suffix == ".docx":
        path = simple_docx(tmp_path / f"doc{role}.docx", SIMPLE_PARAS)
    else:
        body = {
            ".md": "## Results\n\nThe mean ISI decreased by 5.2 points (SD 3.1).\n",
            ".txt": "Results\n\nThe mean ISI decreased by 5.2 points (SD 3.1).\n",
            ".tex": "\\section{Results}\nThe mean ISI decreased by 5.2 points (SD 3.1).\n",
        }[suffix]
        path = _write(tmp_path, f"doc{role}{suffix}", body)
    doc = read_document(path, role, split_lines=(role == "응답서"))
    assert doc.role == role
    assert any("5.2" in para.text for para in doc.paras)
    assert doc.paras[0].no == 1


def test_docx_reads_table_cells(tmp_path):
    path = write_docx(
        tmp_path / "t.docx",
        [p("## Results"), tbl([["Arm", "n"], ["Active", "42"]])],
    )
    doc = read_document(path, "개정본")
    table_rows = [para for para in doc.paras if para.kind == "table"]
    assert [row.text for row in table_rows] == ["Arm | n", "Active | 42"]


def test_text_formats_keep_real_line_numbers(tmp_path):
    path = _write(tmp_path, "a.md", "# T\n\nline three para\n\nline five para\n")
    doc = read_document(path, "개정본")
    body = [para for para in doc.paras if para.text.startswith("line")]
    assert [(para.line_start, para.line_end) for para in body] == [(3, 3), (5, 5)]
    assert doc.has_line_numbers is True
    assert doc.total_lines == 6


def test_docx_has_no_line_numbers(tmp_path):
    path = simple_docx(tmp_path / "a.docx", SIMPLE_PARAS)
    doc = read_document(path, "개정본")
    assert doc.has_line_numbers is False
    assert doc.total_lines == 0


def test_tex_comments_are_dropped(tmp_path):
    path = _write(tmp_path, "a.tex", "\\section{Results}\n% secret note 999\nISI fell.\n")
    doc = read_document(path, "개정본")
    assert all("secret" not in para.text for para in doc.paras)


def test_cp949_manuscript_is_decoded(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("결과\n\n평균 ISI 가 5.2점 감소했다.\n".encode("cp949"))
    doc = read_document(path, "개정본")
    assert any("5.2" in para.text for para in doc.paras)
    assert doc.encoding in ("cp949", "euc-kr")


# ── 변경내용 추적 ───────────────────────────────────────────────────────────


def test_tracked_changes_detected_and_accepted(tmp_path):
    path = write_docx(
        tmp_path / "tracked.docx",
        [p_tracked(before="The ISI fell by ", deleted="5.2", inserted="5.8", after=" points.")],
    )
    doc = read_document(path, "개정본", tracked_mode="accept")
    assert doc.tracked.present
    assert doc.tracked.ins == 1 and doc.tracked.dele == 1
    assert doc.paras[0].text == "The ISI fell by 5.8 points."
    assert "수락" in doc.tracked.state_label  # 배너는 리포트 첫머리(engine)가 찍는다


def test_tracked_changes_reject_mode_reads_the_original(tmp_path):
    path = write_docx(
        tmp_path / "tracked.docx",
        [p_tracked(before="The ISI fell by ", deleted="5.2", inserted="5.8", after=" points.")],
    )
    doc = read_document(path, "개정본", tracked_mode="reject")
    assert doc.paras[0].text == "The ISI fell by 5.2 points."
    assert "원본" in doc.tracked.state_label


def test_untracked_docx_reports_nothing(tmp_path):
    path = simple_docx(tmp_path / "plain.docx", SIMPLE_PARAS)
    doc = read_document(path, "개정본")
    assert not doc.tracked.present
    assert doc.notes == []


def test_docx_renamed_to_md_is_refused(tmp_path):
    """확장자만 바꾼 파일을 그대로 읽으면 깨진 글자가 리포트에 찍힌다."""
    path = simple_docx(tmp_path / "real.docx", SIMPLE_PARAS)
    renamed = tmp_path / "real.md"
    renamed.write_bytes(pathlib.Path(path).read_bytes())
    with pytest.raises(DocumentError) as exc:
        read_document(renamed, "개정본")
    assert "압축" in str(exc.value)


def test_missing_file_says_so(tmp_path):
    with pytest.raises(DocumentError) as exc:
        read_document(tmp_path / "없는파일.md", "개정본")
    assert "찾을 수 없습니다" in str(exc.value)


def test_overlong_paragraph_is_confessed(tmp_path, monkeypatch):
    """조용히 잘라 놓고 '이상 없음'이라고 하면 안 된다."""
    from revcheck import docio

    monkeypatch.setattr(docio, "MAX_PARA_CHARS", 100)
    path = _write(tmp_path, "big.md", "# T\n\n" + "x" * 5000 + "\n")
    doc = read_document(path, "개정본")
    assert doc.truncated
    assert any("일부만 읽었습니다" in note for note in doc.notes)


def test_unnumbered_reference_list_is_split_into_entries(tmp_path):
    """APA/Harvard 목록이 문단 하나로 뭉치면 참고문헌 증감을 셀 수 없다."""
    path = _write(
        tmp_path,
        "refs.md",
        "# T\n\n## References\n\n"
        "Kim H, Park J. Slow breathing and vagal tone. J Sleep Res. 2019;28:e12812.\n"
        "Lee S, Choi B. Acoustic stimulation review. Sleep Med Rev. 2021;55:101388.\n"
        "Ahn Y, Seo M. ISI validation. Sleep Breath. 2020;24:541-548.\n",
    )
    doc = read_document(path, "개정본")
    refs = [para for para in doc.paras if para.section == "References" and para.kind != "heading"]
    assert len(refs) == 3


def test_word_style_run_splitting_is_stitched_back(tmp_path):
    sentence = "Assuming a difference of 3.0 points, 42 participants per arm suffice."
    path = simple_docx(tmp_path / "split.docx", [sentence], split=9)
    doc = read_document(path, "개정본")
    assert doc.paras[0].text == sentence


def test_heading_style_is_recognised(tmp_path):
    path = write_docx(tmp_path / "h.docx", [p("Results", style="Heading1"), p("ISI fell by 5.2.")])
    doc = read_document(path, "개정본")
    assert doc.paras[0].kind == "heading"
    assert doc.paras[1].section == "Results"


# ── 방어 ────────────────────────────────────────────────────────────────────


def test_rejects_unsupported_format(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4")
    with pytest.raises(DocumentError) as exc:
        read_document(path, "개정본")
    assert "PDF" in str(exc.value)


def test_rejects_symlink_input(tmp_path):
    real = _write(tmp_path, "real.md", "# T\n\nbody text here\n")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    with pytest.raises(DocumentError) as exc:
        read_document(link, "개정본")
    assert "심볼릭" in str(exc.value)


def test_rejects_directory(tmp_path):
    with pytest.raises(DocumentError):
        read_document(tmp_path, "개정본")


def test_rejects_empty_document(tmp_path):
    path = _write(tmp_path, "empty.md", "   \n\n \n")
    with pytest.raises(DocumentError) as exc:
        read_document(path, "개정본")
    assert "문단" in str(exc.value)


def test_rejects_non_docx_zip(tmp_path):
    path = tmp_path / "fake.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "not a word file")
    with pytest.raises(DocumentError) as exc:
        read_document(path, "개정본")
    assert "document.xml" in str(exc.value)


def test_rejects_broken_zip(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK\x03\x04 definitely not a zip")
    with pytest.raises(DocumentError):
        read_document(path, "개정본")


def test_rejects_xml_with_dtd_entities(tmp_path):
    """billion laughs — ElementTree 는 내부 엔티티 확장에 취약하므로 파싱 전에 막는다."""
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>"
    )
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", bomb)
    with pytest.raises(DocumentError) as exc:
        read_document(path, "개정본")
    assert "DTD" in str(exc.value)


def test_rejects_zip_bomb_by_uncompressed_size(tmp_path, monkeypatch):
    from revcheck import docio

    monkeypatch.setattr(docio, "MAX_UNCOMPRESSED_BYTES", 1024)
    path = write_docx(tmp_path / "big.docx", [p("x" * 5000)])
    with pytest.raises(DocumentError) as exc:
        read_document(path, "개정본")
    assert "압축 해제" in str(exc.value)


def test_rejects_too_many_xml_nodes(tmp_path, monkeypatch):
    from revcheck import docio

    monkeypatch.setattr(docio, "MAX_XML_TAGS", 10)
    path = simple_docx(tmp_path / "many.docx", SIMPLE_PARAS)
    with pytest.raises(DocumentError) as exc:
        read_document(path, "개정본")
    assert "복잡" in str(exc.value)


def test_error_message_never_contains_manuscript_text(tmp_path):
    """오류 메시지가 미공개 원고 문장을 흘리면 안 된다."""
    secret = "UNPUBLISHED SECRET FINDING 42.7"
    path = tmp_path / "secret.pdf"
    path.write_text(secret, encoding="utf-8")
    with pytest.raises(DocumentError) as exc:
        read_document(path, "개정본")
    assert secret not in str(exc.value)


def test_encrypted_docx_is_a_clean_error_not_a_crash(tmp_path):
    """암호 걸린 워드 파일이 트레이스백을 뱉으면 종료코드가 1(치명)이 된다."""
    path = tmp_path / "locked.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        info = zipfile.ZipInfo("word/document.xml")
        info.flag_bits |= 0x1  # 암호화 플래그
        zf.writestr(info, "<w:document/>")
    with pytest.raises(DocumentError):
        read_document(path, "개정본")


def test_corrupt_deflate_stream_is_a_clean_error(tmp_path):
    path = tmp_path / "corrupt.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>" * 200)
    raw = bytearray(path.read_bytes())
    raw[80:120] = b"\x00" * 40  # 압축 스트림을 망가뜨린다
    path.write_bytes(bytes(raw))
    with pytest.raises(DocumentError):
        read_document(path, "개정본")


def test_deeply_nested_docx_does_not_blow_the_stack(tmp_path):
    inner = "<w:r><w:t>deep text here</w:t></w:r>"
    for _ in range(3000):
        inner = f"<w:smartTag>{inner}</w:smartTag>"
    path = write_docx(tmp_path / "deep.docx", [f"<w:p>{inner}</w:p>"])
    try:
        doc = read_document(path, "개정본")
    except DocumentError:
        return  # 깔끔한 거절도 정답이다 — RecursionError 만 아니면 된다
    assert isinstance(doc.paras[0].text, str)
