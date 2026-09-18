# -*- coding: utf-8 -*-
"""리포트 — 일치는 한 줄, 치명은 최대 5건, 자백 없으면 출력 금지."""

import os

import pytest

from conftest import T0, T1, write
from stalecheck import engine, report, verdicts
from stalecheck.errors import ReportIntegrityError


def run(work, pkg):
    return engine.analyze(str(work), [str(pkg)])


def build(tmp_path, n_same=0, n_stale=0, n_warn=0, n_tie=0):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    for i in range(n_same):
        write(work / ("s%d.md" % i), "x", T1)
        write(pkg / ("s%d.md" % i), "x", T0)
    for i in range(n_stale):
        write(work / ("c%d.md" % i), "새 내용 %d" % i, T1)
        write(pkg / ("c%d.md" % i), "옛 내용 %d" % i, T0)
    for i in range(n_warn):
        write(work / ("w%d.md" % i), "작업 %d" % i, T0)
        write(pkg / ("w%d.md" % i), "봉투 %d" % i, T1)
    for i in range(n_tie):
        write(work / ("t%d.md" % i), "A%d" % i, T0)
        write(pkg / ("t%d.md" % i), "B%d" % i, T0)
    return run(work, pkg)


def test_matches_are_one_line(tmp_path):
    a = build(tmp_path, n_same=196)
    text = report.render_console(a)
    assert "일치 196쌍" in text
    assert text.count("s0.md") == 0, "일치 목록을 콘솔에 쏟지 않는다"


def test_many_matches_keep_report_short(tmp_path):
    a = build(tmp_path, n_same=196)
    assert len(report.render_console(a).splitlines()) < 30


def test_critical_console_capped_at_five(tmp_path):
    a = build(tmp_path, n_stale=21)
    text = report.render_console(a)
    shown = sum(1 for line in text.splitlines()
                if line.startswith("  c") and line.endswith(".md"))
    assert shown == report.CONSOLE_MAX_CRITICAL_ITEMS
    assert "전체 21건은 세대불일치.csv" in text


def test_critical_count_printed(tmp_path):
    a = build(tmp_path, n_stale=3)
    assert "[치명] 봉투가 구세대 — 3쌍" in report.render_console(a)


def test_zero_critical_still_prints_the_header(tmp_path):
    a = build(tmp_path, n_same=2)
    assert "[치명] 봉투가 구세대 — 0쌍" in report.render_console(a)


def test_warning_section_present(tmp_path):
    a = build(tmp_path, n_warn=2)
    text = report.render_console(a)
    assert "[경고] 봉투가 더 최신 — 2쌍" in text
    assert "손으로 고쳤을 수 있고" in text


def test_undecidable_section_present(tmp_path):
    a = build(tmp_path, n_tie=2)
    assert "[판정불가] — 2쌍" in report.render_console(a)


def test_coverage_confession_present(tmp_path):
    a = build(tmp_path, n_same=1)
    text = report.render_console(a)
    assert report.COVERAGE_HEADER in text
    assert "읽음:" in text and "셈의 범위:" in text and "검사 안 함:" in text


def test_report_refuses_without_confession(tmp_path, monkeypatch):
    a = build(tmp_path, n_same=1)
    monkeypatch.setattr(report, "coverage_block", lambda *a, **k: [])
    with pytest.raises(ReportIntegrityError):
        report.render_console(a)


def test_report_refuses_with_stub_confession(tmp_path, monkeypatch):
    a = build(tmp_path, n_same=1)
    monkeypatch.setattr(report, "coverage_block",
                        lambda *a, **k: [report.COVERAGE_HEADER])
    with pytest.raises(ReportIntegrityError):
        report.render_console(a)


def test_confession_names_the_tools_not_used(tmp_path):
    a = build(tmp_path, n_same=1)
    text = report.render_console(a)
    for tool in ("tracecheck", "packlist", "revcheck"):
        assert tool in text


def test_confession_disclaims_guarantee(tmp_path):
    a = build(tmp_path, n_same=1)
    assert "보증하지 않습니다" in report.render_console(a)


def test_line_diff_preview_in_console(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "a.py", "x = 1\ny = 2\n", T1)
    write(pkg / "a.py", "x = 1\n", T0)
    text = report.render_console(run(work, pkg))
    assert "(+1행 / -0행)" in text
    assert "+ y = 2" in text


def test_binary_shows_size_not_diff(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "f.png", b"aaaa", T1)
    write(pkg / "f.png", b"bb", T0)
    text = report.render_console(run(work, pkg))
    assert "대조불가(바이너리)" in text
    assert "4 B" in text and "2 B" in text


def test_markdown_contains_boundary_sentence(tmp_path):
    a = build(tmp_path, n_same=1)
    md = report.render_markdown(a)
    assert "packlist" in md and "한 세대 낡을 수 있다" in md


def test_markdown_wraps_console_in_code_fence(tmp_path):
    a = build(tmp_path, n_same=1)
    md = report.render_markdown(a)
    assert md.count("```") == 2


def test_markdown_records_command(tmp_path):
    a = build(tmp_path, n_same=1)
    assert "python3 -m stalecheck --work X" in report.render_markdown(
        a, command="python3 -m stalecheck --work X")


def test_mismatch_rows_exclude_matches(tmp_path):
    a = build(tmp_path, n_same=3, n_stale=2)
    rows = report.mismatch_rows(a)
    assert len(rows) == 2
    assert all(r[0] == verdicts.CRITICAL_STALE_PACKAGE for r in rows)


def test_mismatch_row_shape(tmp_path):
    a = build(tmp_path, n_stale=1)
    row = report.mismatch_rows(a)[0]
    assert len(row) == len(report.MISMATCH_HEADER)


def test_match_rows_cover_all_matches(tmp_path):
    a = build(tmp_path, n_same=5)
    assert len(report.match_rows(a)) == 5


def test_unpaired_rows_label_both_sides(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "작업만.md", "a", T1)
    write(pkg / "봉투만.md", "b", T0)
    labels = {r[0] for r in report.unpaired_rows(run(work, pkg))}
    assert labels == {"봉투에만 있음", "작업폴더에만 있음"}


def test_sibling_rows_shape(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "본문.md", "x", T1)
    write(work / "본문.docx", b"x", T0)
    rows = report.sibling_rows(run(work, pkg))
    assert rows and len(rows[0]) == len(report.SIBLING_HEADER)


def test_paths_in_rows_are_relative(tmp_path):
    """`~` 는 tmp_path 안에 원래 없다 — 실제로 샐 수 있는 문자열로 비교해야 한다."""
    a = build(tmp_path, n_stale=1)
    for row in report.mismatch_rows(a):
        for cell in row:
            assert str(tmp_path) not in str(cell)
            assert not str(cell).startswith(os.sep)


def test_console_has_no_absolute_home_path(tmp_path):
    a = build(tmp_path, n_stale=1)
    text = report.render_console(a)
    assert str(tmp_path) not in text


def test_oldest_package_mtime_summarised(tmp_path):
    a = build(tmp_path, n_stale=3)
    assert "이후 갱신되지 않았습니다" in report.render_console(a)


def test_no_summary_line_when_no_criticals(tmp_path):
    a = build(tmp_path, n_same=2)
    assert "이후 갱신되지 않았습니다" not in report.render_console(a)


def test_normalized_only_counted_separately(tmp_path, example_tree):
    a = engine.analyze(example_tree["stale"], [example_tree["stale_package"]])
    text = report.render_console(a)
    assert "메타데이터만 달랐던 것 2쌍" in text


def test_example_report_mentions_build_lag(example_tree):
    a = engine.analyze(example_tree["stale"], [example_tree["stale_package"]])
    assert "[경고] 빌드 누락 — 1건" in report.render_console(a)


def test_example_report_mentions_figure_sibling(example_tree):
    a = engine.analyze(example_tree["stale"], [example_tree["stale_package"]])
    assert "[경고] 그림 형제 세대 불일치 — 1건" in report.render_console(a)


def test_excluded_archive_dirs_confessed(example_tree):
    a = engine.analyze(example_tree["stale"], [example_tree["stale_package"]])
    assert "_이전" in report.render_console(a)


def test_skipped_large_confessed(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "big.csv", b"0" * 5000, T1)
    write(pkg / "small.csv", b"0", T0)
    a = engine.analyze(str(work), [str(pkg)], max_bytes=100)
    assert "크기초과 건너뜀 1" in report.render_console(a)
