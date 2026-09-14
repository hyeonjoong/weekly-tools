"""경계 입력. 여기서 무너지면 실제 봉투에서도 무너진다."""

import os
import unicodedata
import zipfile

import pytest

from packlist import EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED, EXIT_UNDECIDABLE
from packlist.analysis import analyse
from packlist.cli import main
from packlist.docxpkg import read_docx
from packlist.envelope import choose_manuscript, scan_envelope
from packlist.report import console_lines, markdown
from conftest import make_manuscript


def run_report(folder, manuscript=None):
    env = scan_envelope(str(folder))
    chosen = choose_manuscript(env, manuscript)
    info = read_docx(env.root / chosen.name, chosen.name)
    others = [read_docx(env.root / f.name, f.name)
              for f in env.docx_files if f.name != chosen.name]
    return analyse(env, chosen, info, others)


def test_manuscript_with_no_media(envelope, builder, put_file):
    builder(envelope / "원고.docx", paragraphs=["본문만 있습니다."])
    put_file(envelope, "S1_Table.pdf")
    report = run_report(envelope)
    assert report.critical_count == 0
    assert "앵커 이미지 0개" in "\n".join(console_lines(report))


def test_manuscript_with_media_but_no_anchor_is_all_orphan(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["본문"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=[])
    put_file(envelope, "S1_Table.pdf")
    report = run_report(envelope)
    assert report.critical_count == 1
    assert len(report.manuscript.orphans) == 2


def test_same_image_anchored_five_times(envelope, builder, png, put_file):
    blob = png("same")
    builder(envelope / "원고.docx",
            paragraphs=[("Fig. 1. cap", "a.png")] * 5 + ["Body cites Fig. 1."],
            images={"a.png": blob}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    report = run_report(envelope)
    assert report.critical_count == 0


def test_header_only_image_is_not_critical(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["본문"],
            images={"logo.png": png("logo")}, anchored=[], header_images=["logo.png"])
    put_file(envelope, "S1_Table.pdf")
    assert run_report(envelope).critical_count == 0


def test_footnote_only_image_is_not_critical(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["본문"],
            images={"n.png": png("n")}, anchored=[], footnote_images=["n.png"])
    put_file(envelope, "S1_Table.pdf")
    assert run_report(envelope).critical_count == 0


def test_figure_without_caption(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=[("", "a.png"), "Body cites Fig. 1."],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    codes = [f.code for f in run_report(envelope).findings]
    assert "FIG_NO_CAPTION" in codes


def test_double_digit_figure_numbers_in_report(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body cites Fig. 11 and Fig. 12.",
                        ("Fig. 11. eleven.", "a.png"), "Fig. 12. twelve."],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    text = "\n".join(console_lines(run_report(envelope)))
    assert "Fig11×1" in text and "Fig12×1" in text


def test_mixed_figure_notation(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body cites Fig. 1, Figure 2 and Fig 3.",
                        ("Fig. 1. one.", "a.png"), "Fig. 2. two.", "Fig. 3. three."],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    refs = run_report(envelope).refs
    assert refs.fig_citations[1] == refs.fig_citations[2] == refs.fig_citations[3] == 1


def test_nfd_hangul_filename(envelope, put_file):
    decomposed = unicodedata.normalize("NFD", "원고_최종.docx")
    make_manuscript(envelope, decomposed)
    put_file(envelope, unicodedata.normalize("NFD", "보충자료.pdf"))
    env = scan_envelope(str(envelope))
    assert all(unicodedata.is_normalized("NFC", f.name) for f in env.files)
    assert main([str(envelope)]) in (EXIT_OK, EXIT_CRITICAL)


def test_filename_with_control_characters_cannot_forge_findings(envelope, put_file):
    evil = "정상\r[치명] 위조된 발견.pptx"
    try:
        put_file(envelope, evil)
    except OSError:
        pytest.skip("파일 시스템이 이 이름을 허용하지 않습니다")
    make_manuscript(envelope)
    lines = console_lines(run_report(envelope))
    assert not any(line.startswith("[치명] 위조된") for line in lines)


def test_very_long_filename_is_truncated_in_report(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "긴이름" * 40 + ".pptx")
    for line in console_lines(run_report(envelope)):
        assert len(line) < 600


def test_two_docx_requires_manuscript_flag(envelope, put_file):
    make_manuscript(envelope, "원고_A.docx")
    make_manuscript(envelope, "원고_B.docx")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) == EXIT_REFUSED


def test_empty_folder_refused(tmp_path):
    folder = tmp_path / "빈"
    folder.mkdir()
    assert main([str(folder)]) == EXIT_REFUSED


def test_folder_with_only_junk_is_refused(tmp_path):
    folder = tmp_path / "쓰레기만"
    folder.mkdir()
    (folder / ".DS_Store").write_bytes(b"\x00")
    assert main([str(folder)]) == EXIT_REFUSED


def test_zero_byte_docx_is_undecidable(envelope, put_file):
    (envelope / "원고.docx").write_bytes(b"")
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) == EXIT_UNDECIDABLE


def test_docx_with_truncated_zip_is_undecidable(envelope, builder, png, put_file):
    path = builder(envelope / "원고.docx", paragraphs=["본문"],
                   images={"a.png": png("a")}, anchored=["a.png"])
    data = open(path, "rb").read()
    open(path, "wb").write(data[: len(data) // 2])
    put_file(envelope, "S1_Table.pdf")
    assert main([str(envelope)]) == EXIT_UNDECIDABLE


def test_many_media_files_do_not_explode(envelope, builder, png, put_file):
    images = {f"i{n}.png": png(f"i{n}", 500) for n in range(200)}
    builder(envelope / "원고.docx", paragraphs=["본문"], images=images,
            anchored=list(images)[:100])
    put_file(envelope, "S1_Table.pdf")
    report = run_report(envelope)
    assert len(report.manuscript.orphans) == 100
    assert report.exit_code == EXIT_CRITICAL


def test_markdown_renders_for_every_example_shape(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["Body cites Fig. 1."],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    text = markdown(run_report(envelope))
    assert text.startswith("# 투고 봉투 점검")
    assert "커버리지 자백" in text


def test_supplementary_container_satisfies_promise(envelope, builder, put_file):
    make_manuscript(envelope, paragraphs=["Body", "See S1 Table and S2 Table."])
    builder(envelope / "Supplementary_Material.docx",
            paragraphs=["Supplementary Material",
                        "Table S1. First.", "Table S2. Second."])
    report = run_report(envelope, "원고.docx")
    assert {row.verdict for row in report.promises} == {"있음(통합본)"}


def test_supplementary_container_missing_item_stays_unverified(envelope, builder):
    make_manuscript(envelope, paragraphs=["Body", "See S1 Table and S9 Table."])
    builder(envelope / "Supplementary_Material.docx",
            paragraphs=["Supplementary Material", "Table S1. First."])
    report = run_report(envelope, "원고.docx")
    verdicts = {row.token: row.verdict for row in report.promises}
    assert verdicts["S1 Table"] == "있음(통합본)"
    assert verdicts["S9 Table"] == "대조불가"


def test_windows_style_backslash_relationship_target(envelope, png):
    """일부 편집기가 남기는 `..\\media\\x.png` 형태도 고아로 오판하지 않는다."""
    path = envelope / "원고.docx"
    document = ('<?xml version="1.0"?><w:document '
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<w:body><w:p><w:r><w:drawing r:embed="rId9"/></w:r></w:p></w:body></w:document>')
    rels = ('<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/a.png"/></Relationships>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("word/_rels/document.xml.rels", rels)
        zf.writestr("word/media/a.png", png("a"))
    info = read_docx(path, "원고.docx")
    assert info.orphans == []
