"""2라운드 적대적 검토에서 나온 결함들의 회귀 테스트."""

import os
import sys
import tempfile
import time
import unicodedata
import zipfile

import pytest

from packlist import EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE
from packlist.analysis import _recoverable_waste, analyse
from packlist.cli import main
from packlist.docxpkg import read_docx, _referenced_ids
from packlist.envelope import choose_manuscript, scan_envelope
from packlist.frontmatter import MATCH, MISMATCH, compare_frontmatter
from packlist.report import _detail_lines, mask_name, markdown
from packlist.safeio import UsageError, csv_cell, prepare_out_dir, read_text_any
from packlist.spec import load_limits
from packlist.textrefs import (extract_refs, looks_like_supplement_container,
                               token_matches_file, token_text_pattern)
from conftest import TITLE, make_manuscript

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
IMG = NS_R + "/image"
RELS = ('<?xml version="1.0"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>')


def doc(body, extra_ns=""):
    return ('<?xml version="1.0"?><w:document xmlns:w="%s" xmlns:r="%s"%s><w:body>%s</w:body>'
            "</w:document>" % (NS_W, NS_R, extra_ns, body))


def run_report(folder, manuscript=None, **kwargs):
    env = scan_envelope(str(folder))
    chosen = choose_manuscript(env, manuscript)
    info = read_docx(env.root / chosen.name, chosen.name)
    others = [read_docx(env.root / f.name, f.name)
              for f in env.docx_files if f.name != chosen.name]
    return analyse(env, chosen, info, others, **kwargs)


# ── H1 `Supplementary Table 1.pdf` 형 파일명 ────────────────────────
@pytest.mark.parametrize("token,filename", [
    ("S1 Table", "Supplementary Table 1.pdf"),
    ("S1 Fig", "Supplementary Figure 1.pdf"),
    ("S1 Fig", "Supplemental Figure 1.tif"),
    ("S1 Fig", "supp_fig1.tif"),
    ("S1 Table", "supp_table1.docx"),
    ("S1 Data", "Supplementary Data 1.xlsx"),
])
def test_worded_supplementary_filenames_match(token, filename):
    assert token_matches_file(token, filename)


# ── H2 S1 이 S11 을 삼키면 안 된다 ─────────────────────────────────
@pytest.mark.parametrize("token,filename", [
    ("S1 Fig", "Fig_S11.pdf"),
    ("S1 Fig", "Figure_S12.tif"),
    ("S1 Table", "Table_S10.pdf"),
    ("S1 Fig", "S11_Fig.pdf"),
    ("S1 Table", "Supplementary Table 10.pdf"),
])
def test_digit_boundary_on_supplementary_numbers(token, filename):
    assert not token_matches_file(token, filename)


def test_missing_supplementary_is_not_hidden_by_a_higher_number(envelope, put_file, tmp_path):
    """`S1` 이 `S11` 파일로 만족되면 진짜 누락이 조용히 사라진다."""
    import json
    from packlist.spec import load_expectations
    make_manuscript(envelope, paragraphs=["Body", "See Fig. S1 and Fig. S11."])
    put_file(envelope, "Fig_S11.pdf")
    spec = tmp_path / "expect.json"
    spec.write_text(json.dumps({"required": ["S1 Fig", "S11 Fig"]}), encoding="utf-8")
    report = run_report(envelope, expect=load_expectations(str(spec)))
    verdicts = {row.token: row.verdict for row in report.promises}
    assert verdicts["S1 Fig"] == "없음"
    assert verdicts["S11 Fig"] == "있음"
    assert report.critical_count == 1


def test_container_text_reads_worded_form():
    assert token_text_pattern("S1 Fig").search("Supplementary Figure 1. Design")
    assert not token_text_pattern("S1 Table").search("Tables 1 and 2 are omitted.")
    assert not token_text_pattern("S2 Data").search("Datasets 2 Data points were excluded.")


def test_single_item_supplement_is_not_a_container():
    assert not looks_like_supplement_container("Supplementary_Table_S1.pdf")
    assert not looks_like_supplement_container("Supplementary_Fig_S1.pdf")
    assert looks_like_supplement_container("Supplementary_Material.docx")


# ── H3 패널 글자가 그림 번호를 지우면 안 된다 ──────────────────────
@pytest.mark.parametrize("text,numbers", [
    ("Figure 2I shows the trace", {2}),
    ("Fig. 3i and Fig. 3j", {3}),
    ("Figure 4K is the summary", {4}),
    ("Figure 2A-2H are panels", {2}),
    ("the Fig. 2nd revision", set()),
])
def test_panel_letters_never_delete_the_figure_number(text, numbers):
    from packlist.docxpkg import Paragraph
    result = extract_refs([Paragraph(1, "Intro.", False), Paragraph(2, text, False)])
    assert set(result.fig_citations) == numbers


# ── M1 회수 가능 바이트는 앵커된 한 벌을 남긴다 ────────────────────
def test_waste_leaves_one_anchored_copy(envelope, builder, png):
    blob = png("twin", 3000)
    path = builder(envelope / "a.docx", paragraphs=["Body"],
                   images={"a_orphan.png": blob, "b_anchored.png": blob},
                   anchored=["b_anchored.png"])
    info = read_docx(path, "a.docx")
    assert _recoverable_waste(info) == 3000
    assert _recoverable_waste(info) < info.media_bytes


def test_waste_reclaims_a_fully_orphaned_group(envelope, builder, png):
    blob = png("twin", 2500)
    path = builder(envelope / "a.docx", paragraphs=["Body"],
                   images={"x.png": blob, "y.png": blob}, anchored=[])
    info = read_docx(path, "a.docx")
    assert _recoverable_waste(info) == 5000


# ── M2 앵커 회귀 예산 ──────────────────────────────────────────────
def _baseline(tmp_path, builder, images, anchored, name="원고.docx"):
    base = tmp_path / ("기준" + str(len(list(tmp_path.iterdir()))))
    base.mkdir()
    builder(base / name, paragraphs=["Body"], images=images, anchored=anchored)
    (base / "S1_Table.pdf").write_bytes(b"%PDF")
    return read_docx(base / name, name)


def test_duplicate_created_this_round_is_not_a_regression(envelope, builder, png, tmp_path):
    blob = png("h", 2000)
    base_info = _baseline(tmp_path, builder, {"image1.png": blob}, ["image1.png"])
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"image1.png": blob, "image9.png": blob}, anchored=["image1.png"])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    report = run_report(envelope, baseline=("기준", base_info, 2000))
    assert not [f for f in report.findings if f.code == "ANCHOR_REGRESSION"]


def test_regression_never_exceeds_baseline_anchor_count(envelope, builder, png, tmp_path):
    old_blob, new_blob = png("old", 2000), png("new", 2100)
    base_info = _baseline(tmp_path, builder, {"image1.png": old_blob}, ["image1.png"])
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"image1.png": new_blob, "image7.png": old_blob}, anchored=[])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    report = run_report(envelope, baseline=("기준", base_info, 2000))
    finding = [f for f in report.findings if f.code == "ANCHOR_REGRESSION"][0]
    assert "1개" in finding.title


# ── M3 저자 순서: 평범한 단어가 저자 등장으로 잡히면 안 된다 ────────
def test_ordinary_words_do_not_repair_author_order(envelope, builder):
    authors = "Jiwon Kim, Minsu Han, Sora Park"
    builder(envelope / "원고.docx", paragraphs=[TITLE, authors, "Body."])
    builder(envelope / "기타.docx", paragraphs=[
        "The author list is Sora Park, Minsu Han, Jiwon Kim.",
        "These changes were sparked by the reviewer comments received last month."])
    result = compare_frontmatter(read_docx(envelope / "원고.docx", "원고.docx"),
                                 [read_docx(envelope / "기타.docx", "기타.docx")])
    verdicts = [c.verdict for checks in result.checks.values()
                for c in checks if c.field == "저자순서"]
    assert verdicts == [MISMATCH]


def test_correct_author_order_still_matches(envelope, builder):
    authors = "Jiwon Kim, Minsu Han, Sora Park"
    builder(envelope / "원고.docx", paragraphs=[TITLE, authors, "Body."])
    builder(envelope / "기타.docx", paragraphs=["Title page", authors,
                                              "These changes were sparked by review."])
    result = compare_frontmatter(read_docx(envelope / "원고.docx", "원고.docx"),
                                 [read_docx(envelope / "기타.docx", "기타.docx")])
    verdicts = [c.verdict for checks in result.checks.values()
                for c in checks if c.field == "저자순서"]
    assert verdicts == [MATCH]


# ── M4 리포트 파일은 근거를 자르지 않는다 ──────────────────────────
def test_markdown_keeps_every_detail_line(envelope, builder, png, put_file):
    images = {"i%d.png" % n: png("i%d" % n, 200) for n in range(50)}
    builder(envelope / "원고.docx", paragraphs=["Body"], images=images, anchored=[])
    put_file(envelope, "S1_Table.pdf")
    report = run_report(envelope)
    body = markdown(report)
    assert "i49.png" in body
    assert "외 " not in body.split("## 근거")[0].split("치명")[-1] or "i49.png" in body


def test_console_truncation_points_at_the_report_file(envelope, builder, png, put_file):
    from packlist.report import console_lines
    images = {"i%d.png" % n: png("i%d" % n, 200) for n in range(50)}
    builder(envelope / "원고.docx", paragraphs=["Body"], images=images, anchored=[])
    put_file(envelope, "S1_Table.pdf")
    lines = console_lines(run_report(envelope))
    note = [line for line in lines if "외 " in line and "건" in line]
    assert note and "봉투점검.md" in note[0]


# ── NEW-2 거대 정수 / NEW-9 FIFO 입력 ──────────────────────────────
def test_huge_integer_limit_is_refused(tmp_path):
    spec = tmp_path / "l.json"
    spec.write_text('{"max_total_mb": 1%s}' % ("0" * 400), encoding="utf-8")
    with pytest.raises(UsageError):
        load_limits(str(spec))


def test_fifo_spec_file_is_refused_without_hanging(tmp_path):
    fifo = tmp_path / "l.json"
    os.mkfifo(fifo)
    started = time.time()
    with pytest.raises(UsageError):
        read_text_any(fifo, "--limits")
    assert time.time() - started < 3


def test_directory_spec_file_is_refused(tmp_path):
    folder = tmp_path / "l.json"
    folder.mkdir()
    with pytest.raises(UsageError):
        read_text_any(folder, "--expect")


# ── NEW-3 정상 out-dir 을 거절하지 않는다 ──────────────────────────
def test_tmp_out_dir_is_accepted(tmp_path):
    assert prepare_out_dir(str(tmp_path / "reports")).is_dir()


def test_relative_parent_out_dir_is_accepted(tmp_path, monkeypatch):
    work = tmp_path / "봉투"
    work.mkdir()
    monkeypatch.chdir(work)
    assert prepare_out_dir("../봉투점검_0911").is_dir()


def test_real_symlinked_ancestor_is_still_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(UsageError):
        prepare_out_dir(str(link / "sub"))


# ── NEW-4 / NEW-5 성능 ─────────────────────────────────────────────
def test_many_namespace_prefixes_stay_fast():
    prefixes = "".join(' xmlns:p%d="%s"' % (i, NS_R) for i in range(8000))
    xml = doc("<w:p/>", prefixes)
    started = time.time()
    _referenced_ids(xml)
    assert time.time() - started < 2.0


def test_deeply_nested_paragraphs_stay_fast(envelope):
    body = "<w:p>" * 8000 + "<w:r><w:t>x</w:t></w:r>" + "</w:p>" * 8000
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc(body))
    started = time.time()
    info = read_docx(path, "원고.docx")
    assert time.time() - started < 5.0
    assert info.raw_paragraph_count == 8000


# ── NEW-7 `=` 주변 공백 / NEW-8 NFD ────────────────────────────────
def test_whitespace_around_equals_is_understood(envelope, png):
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml",
                    doc('<w:p><w:r><w:drawing r:embed = "rId1"/></w:r></w:p>'))
        zf.writestr("word/_rels/document.xml.rels",
                    RELS % ('<Relationship Id="rId1" Type="%s" Target="media/a.png"/>' % IMG))
        zf.writestr("word/media/a.png", png("a"))
    assert read_docx(path, "원고.docx").orphans == []


def test_decomposed_media_name_matches_encoded_target(envelope, png):
    path = envelope / "원고.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml",
                    doc('<w:p><w:r><w:drawing r:embed="rId1"/></w:r></w:p>'))
        zf.writestr("word/_rels/document.xml.rels",
                    RELS % ('<Relationship Id="rId1" Type="%s" '
                            'Target="media/%%EA%%B7%%B8%%EB%%A6%%BC1.png"/>' % IMG))
        zf.writestr("word/media/" + unicodedata.normalize("NFD", "그림1.png"), png("a"))
    info = read_docx(path, "원고.docx")
    assert info.orphans == [] and info.broken_image_rels == []


# ── NEW-11 보이지 않는 줄바꿈류 ────────────────────────────────────
@pytest.mark.parametrize("code", [0x85, 0x2028, 0x2029])
def test_line_separator_prefix_cannot_bypass_csv_guard(code):
    """줄바꿈류 문자는 보이는 대체 문자로 바뀌어 셀이 `=` 로 시작하지 않는다."""
    cell = csv_cell(chr(code) + "=cmd.pdf")
    assert cell[:1] not in ("=", "+", "-", "@")
    assert chr(code) not in cell


# ── NEW-12/13/14 경로·순서 ─────────────────────────────────────────
def test_missing_envelope_message_hides_home_path(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    main([str(tmp_path / "없는봉투")])
    err = capsys.readouterr().err
    assert str(tmp_path) not in err and "~" in err


def test_unreadable_only_docx_is_undecidable(envelope, put_file, capsys):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    blocked = envelope / "원고.docx"
    os.chmod(blocked, 0o000)
    try:
        assert main([str(envelope)]) == EXIT_UNDECIDABLE
        assert "docx 가 없습니다" not in capsys.readouterr().err
    finally:
        os.chmod(blocked, 0o600)


# ── L3 마스킹이 "(없음)" 을 망가뜨리지 않는다 ───────────────────────
def test_mask_keeps_the_no_value_marker():
    assert mask_name("(없음)") == "(없음)"


def test_markdown_shows_no_value_marker_intact(envelope, put_file):
    make_manuscript(envelope, creator="", last_modified_by="김현중")
    put_file(envelope, "S1_Table.pdf")
    body = markdown(run_report(envelope))
    assert "작성자: (없음)" in body
