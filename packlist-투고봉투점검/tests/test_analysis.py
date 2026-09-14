"""판정 규칙 — 무엇이 치명이고 무엇이 경고인가."""

import pytest

from packlist.analysis import analyse
from packlist.docxpkg import read_docx
from packlist.envelope import choose_manuscript, scan_envelope
from packlist.findings import CRITICAL, INFO, WARNING
from packlist.spec import Expectations, Limits
from conftest import make_manuscript


def run(folder, manuscript=None, **kwargs):
    env = scan_envelope(str(folder))
    chosen = choose_manuscript(env, manuscript)
    info = read_docx(env.root / chosen.name, chosen.name)
    others = [read_docx(env.root / f.name, f.name)
              for f in env.docx_files if f.name != chosen.name]
    return analyse(env, chosen, info, others, **kwargs)


def codes(report, severity=None):
    return [f.code for f in report.findings if severity is None or f.severity == severity]


def test_clean_envelope_has_no_critical(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    report = run(envelope)
    assert report.critical_count == 0
    assert report.exit_code == 0


def test_orphan_is_critical(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["본문 Fig. 1 참조"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    report = run(envelope)
    assert "ORPHAN_MEDIA" in codes(report, CRITICAL)
    assert report.exit_code == 1


def test_duplicate_is_warning_never_critical(envelope, builder, png, put_file):
    blob = png("twin")
    builder(envelope / "원고.docx", paragraphs=["본문"],
            images={"x.png": blob, "y.png": blob}, anchored=["x.png", "y.png"])
    put_file(envelope, "S1_Table.pdf")
    report = run(envelope)
    assert "DUP_MEDIA" in codes(report, WARNING)
    assert report.critical_count == 0


def test_broken_relationship_is_critical(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["본문"], images={"a.png": png("a")},
            anchored=["a.png"], broken_targets=["gone.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "BROKEN_REL" in codes(run(envelope), CRITICAL)


def test_external_image_is_critical(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["본문"], images={"a.png": png("a")},
            anchored=["a.png"], external_images=["https://example.invalid/f.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "EXTERNAL_IMAGE" in codes(run(envelope), CRITICAL)


def test_promise_without_expect_is_unverified_not_critical(envelope, put_file):
    make_manuscript(envelope, paragraphs=[
        "Body", "Numbers appear in S1 Table and S2 Table."])
    put_file(envelope, "Cover_Letter.pdf")
    report = run(envelope)
    assert report.critical_count == 0
    assert "PROMISE_UNVERIFIED" in codes(report, WARNING)
    assert {row.verdict for row in report.promises} == {"대조불가"}


def test_promise_with_expect_becomes_critical(envelope, put_file):
    make_manuscript(envelope, paragraphs=["Body", "See S1 Table."])
    put_file(envelope, "Cover_Letter.pdf")
    report = run(envelope, expect=Expectations(required=["S1 Table"]))
    assert "PROMISE_MISSING" in codes(report, CRITICAL)
    assert report.promises[0].verdict == "없음"


def test_promise_found_in_envelope(envelope, put_file):
    make_manuscript(envelope, paragraphs=["Body", "See S1 Table."])
    put_file(envelope, "S1_Table.pdf")
    report = run(envelope, expect=Expectations(required=["S1 Table"]))
    assert report.critical_count == 0
    assert report.promises[0].verdict == "있음"


def test_expect_item_never_mentioned_in_body_is_critical(envelope, put_file):
    make_manuscript(envelope, paragraphs=["Body only."])
    put_file(envelope, "S1_Table.pdf")
    report = run(envelope, expect=Expectations(required=["Cover Letter"]))
    assert "EXPECT_MISSING" in codes(report, CRITICAL)


def test_stray_file_is_warning(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "SubjectInfoExtra.pptx")
    assert "STRAY_FILE" in codes(run(envelope), WARNING)


def test_standard_part_is_not_stray(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "Cover_Letter.pdf")
    assert "STRAY_FILE" not in codes(run(envelope))


def test_junk_file_is_warning(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    (envelope / ".DS_Store").write_bytes(b"\x00")
    assert "JUNK_FILE" in codes(run(envelope), WARNING)


def test_uncited_figure_caption_is_warning(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body text.", ("Fig. 1. Caption.", "a.png")],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "FIG_UNCITED" in codes(run(envelope), WARNING)


def test_cited_figure_without_caption_is_warning(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["Body cites Fig. 7.", ("Fig. 1. Caption.", "a.png")],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "FIG_NO_CAPTION" in codes(run(envelope), WARNING)


def test_figure_number_gap_is_warning(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body cites Fig. 1 and Fig. 3.",
                        ("Fig. 1. one.", "a.png"), "Fig. 3. three."],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "FIG_GAP" in codes(run(envelope), WARNING)


def test_figure_order_reversal_is_warning(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body cites Fig. 2 before Fig. 1.",
                        ("Fig. 1. one.", "a.png"), "Fig. 2. two."],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "FIG_ORDER" in codes(run(envelope), WARNING)


def test_panel_missing_from_caption_is_warning(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body cites Fig. 1c.", ("Fig. 1. Caption. (a) left. (b) right.", "a.png")],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "PANEL_MISSING" in codes(run(envelope), WARNING)


def test_panel_present_in_caption_is_silent(envelope, builder, png, put_file):
    builder(envelope / "원고.docx",
            paragraphs=["Body cites Fig. 1b.", ("Fig. 1. Caption. (a–c) panels.", "a.png")],
            images={"a.png": png("a")}, anchored=["a.png"])
    put_file(envelope, "S1_Table.pdf")
    assert "PANEL_MISSING" not in codes(run(envelope))


def test_doc_props_record_is_info_not_warning(envelope, put_file):
    """작성자 기록은 모든 Word 파일에 있다 — 경고로 올리면 매 회차 운다."""
    make_manuscript(envelope, creator="김 아무개")
    put_file(envelope, "S1_Table.pdf")
    assert "DOC_PROPS" in codes(run(envelope), INFO)
    assert "DOC_PROPS" not in codes(run(envelope), WARNING)


def test_limits_over_total_is_warning_not_critical(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "big.pdf", b"x" * 200000)
    report = run(envelope, limits=Limits(max_total_mb=0.01))
    assert "LIMIT_TOTAL" in codes(report, WARNING)
    assert report.critical_count == 0


def test_limits_per_file(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "big.pdf", b"x" * 200000)
    assert "LIMIT_FILE" in codes(run(envelope, limits=Limits(max_file_mb=0.01)), WARNING)


def test_limits_file_count(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "a.pdf")
    assert "LIMIT_COUNT" in codes(run(envelope, limits=Limits(max_files=1)), WARNING)


def test_triad_info_always_present(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    assert "TRIAD" in codes(run(envelope), INFO)


def test_size_info_always_present(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    assert "SIZE" in codes(run(envelope), INFO)


# ---------------------------------------------------------------- baseline
def _baseline_pair(envelope, builder, png):
    blob_a, blob_b = png("a", 3000), png("b", 3000)
    base = envelope.parent / "기준"
    base.mkdir()
    builder(base / "원고.docx", paragraphs=["Body cites Fig. 1 and Fig. 2."],
            images={"a.png": blob_a, "b.png": blob_b}, anchored=["a.png", "b.png"])
    builder(envelope / "원고.docx", paragraphs=["Body cites Fig. 1 and Fig. 2."],
            images={"a.png": blob_a, "b.png": blob_b}, anchored=["a.png"])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    (base / "S1_Table.pdf").write_bytes(b"%PDF")
    return read_docx(base / "원고.docx", "원고.docx")


def test_anchor_regression_is_critical(envelope, builder, png):
    base_info = _baseline_pair(envelope, builder, png)
    report = run(envelope, baseline=("기준_원고.docx", base_info, 6000))
    assert "ANCHOR_REGRESSION" in codes(report, CRITICAL)
    assert report.exit_code == 1


def test_no_regression_when_anchors_stable(envelope, builder, png):
    blob = png("a", 3000)
    base = envelope.parent / "기준"
    base.mkdir()
    builder(base / "원고.docx", paragraphs=["Body cites Fig. 1."],
            images={"a.png": blob}, anchored=["a.png"])
    builder(envelope / "원고.docx", paragraphs=["Body cites Fig. 1."],
            images={"a.png": blob}, anchored=["a.png"])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    base_info = read_docx(base / "원고.docx", "원고.docx")
    report = run(envelope, baseline=("기준", base_info, 3000))
    assert "ANCHOR_REGRESSION" not in codes(report)


def test_media_lost_is_warning(envelope, builder, png):
    base = envelope.parent / "기준"
    base.mkdir()
    builder(base / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png", "b.png"])
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a")}, anchored=["a.png"])
    (envelope / "S1_Table.pdf").write_bytes(b"%PDF")
    base_info = read_docx(base / "원고.docx", "원고.docx")
    assert "MEDIA_LOST" in codes(run(envelope, baseline=("기준", base_info, 10)), WARNING)


def test_ledger_has_two_rows_with_baseline(envelope, builder, png):
    base_info = _baseline_pair(envelope, builder, png)
    report = run(envelope, baseline=("기준_원고.docx", base_info, 6000))
    assert len(report.ledger) == 2


def test_ledger_has_one_row_without_baseline(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    assert len(run(envelope).ledger) == 1


def test_assets_include_media_rows(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    report = run(envelope)
    media_rows = [r for r in report.assets if r.kind == "미디어"]
    assert len(media_rows) == 2
    assert {r.anchored for r in media_rows} == {"본문앵커"}


def test_asset_rows_use_basenames_only(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    for row in run(envelope).assets:
        assert not row.name.startswith("/")


def test_coverage_always_lists_unchecked(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    assert run(envelope).coverage.unchecked


def test_findings_sorted_critical_first(envelope, builder, png, put_file):
    builder(envelope / "원고.docx", paragraphs=["Body"],
            images={"a.png": png("a"), "b.png": png("b")}, anchored=["a.png"])
    put_file(envelope, "SubjectInfoExtra.pptx")
    severities = [f.severity for f in run(envelope).findings]
    assert severities == sorted(severities, key=lambda s: {CRITICAL: 0, WARNING: 1, INFO: 2}[s])
