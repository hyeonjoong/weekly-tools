# -*- coding: utf-8 -*-
"""엔진 — 입력 검증, 집계, 정렬, 자백 수집."""

import os

import pytest

from conftest import T0, T1, write, 예제
from stalecheck import engine, verdicts
from stalecheck.errors import RefusedError


def make(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    return work, pkg


def test_package_must_be_inside_work(tmp_path):
    work, _ = make(tmp_path)
    other = tmp_path / "밖"
    os.makedirs(str(other))
    with pytest.raises(RefusedError):
        engine.analyze(str(work), [str(other)])


def test_work_must_exist(tmp_path):
    with pytest.raises(RefusedError):
        engine.analyze(str(tmp_path / "없음"), [])


def test_package_must_exist(tmp_path):
    work, _ = make(tmp_path)
    with pytest.raises(RefusedError):
        engine.analyze(str(work), [str(work / "없음")])


def test_work_and_package_identical_refused(tmp_path):
    work, _ = make(tmp_path)
    with pytest.raises(RefusedError):
        engine.analyze(str(work), [str(work)])


def test_duplicate_packages_refused(tmp_path):
    work, pkg = make(tmp_path)
    with pytest.raises(RefusedError):
        engine.analyze(str(work), [str(pkg), str(pkg)])


def test_sibling_prefix_not_confused_with_nesting(tmp_path):
    """'submission' 과 'submission_extra' 는 포함관계가 아니다."""
    work, pkg = make(tmp_path)
    other = work / "submission_extra"
    os.makedirs(str(other))
    write(work / "a.md", "x", T1)
    analysis = engine.analyze(str(work), [str(pkg), str(other)])
    assert len(analysis.packages) == 2


def test_package_label_is_relative(tmp_path):
    work, pkg = make(tmp_path)
    write(work / "a.md", "x", T1)
    write(pkg / "a.md", "x", T0)
    analysis = engine.analyze(str(work), [str(pkg)])
    assert analysis.packages[0].label == "submission"


def test_package_files_not_counted_as_work(tmp_path):
    work, pkg = make(tmp_path)
    write(pkg / "a.md", "x", T0)
    analysis = engine.analyze(str(work), [str(pkg)])
    assert analysis.work_scan.read_count == 0


def test_rows_sorted_critical_first(tmp_path):
    work, pkg = make(tmp_path)
    write(work / "z_same.md", "x", T1)
    write(pkg / "z_same.md", "x", T0)
    write(work / "a_stale.md", "새", T1)
    write(pkg / "a_stale.md", "옛", T0)
    analysis = engine.analyze(str(work), [str(pkg)])
    assert analysis.rows[0].verdict.label == verdicts.CRITICAL_STALE_PACKAGE


def test_rows_sorted_by_path_within_verdict(tmp_path):
    work, pkg = make(tmp_path)
    for name in ("c", "a", "b"):
        write(work / (name + ".md"), "새", T1)
        write(pkg / (name + ".md"), "옛", T0)
    analysis = engine.analyze(str(work), [str(pkg)])
    assert [r.work_rel for r in analysis.rows] == ["a.md", "b.md", "c.md"]


def test_raw_differs_true_for_metadata_only_pdf(tmp_path):
    work, pkg = make(tmp_path)
    write(work / "f.pdf", 예제.make_pdf("F", "20260731000000", "AA" * 16), T1)
    write(pkg / "f.pdf", 예제.make_pdf("F", "20260730000000", "BB" * 16), T0)
    analysis = engine.analyze(str(work), [str(pkg)])
    assert len(analysis.normalized_only_rows) == 1


def test_raw_differs_false_for_byte_identical(tmp_path):
    work, pkg = make(tmp_path)
    data = 예제.make_pdf("F", "20260731000000", "AA" * 16)
    write(work / "f.pdf", data, T1)
    write(pkg / "f.pdf", data, T0)
    analysis = engine.analyze(str(work), [str(pkg)])
    assert analysis.normalized_only_rows == []


def test_coverage_aggregates_across_packages(tmp_path):
    work = tmp_path / "논문"
    for name in ("SUBMISSION_A", "SUBMISSION_B"):
        os.makedirs(str(work / name))
        write(work / name / "a.md", "x", T0)
        write(work / name / "혼자.md", "y", T0)
    write(work / "src" / "a.md", "x", T1)
    analysis = engine.analyze(str(work), [str(work / "SUBMISSION_A"),
                                          str(work / "SUBMISSION_B")])
    assert analysis.coverage == pytest.approx(0.5)


def test_pair_count_sums_packages(tmp_path):
    work = tmp_path / "논문"
    for name in ("SUBMISSION_A", "SUBMISSION_B"):
        os.makedirs(str(work / name))
        write(work / name / "a.md", "옛", T0)
    write(work / "src" / "a.md", "새", T1)
    analysis = engine.analyze(str(work), [str(work / "SUBMISSION_A"),
                                          str(work / "SUBMISSION_B")])
    assert analysis.pair_count == 2 and len(analysis.critical_rows) == 2


def test_unreadable_collected_and_deduped(tmp_path):
    work, pkg = make(tmp_path)
    locked = write(work / "a.md", "x", T1)
    write(pkg / "a.md", "y", T0)
    os.chmod(locked, 0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root")
        analysis = engine.analyze(str(work), [str(pkg)])
        rels = [r for r, _ in analysis.unreadable]
        assert len(rels) == len(set(rels)) and rels
    finally:
        os.chmod(locked, 0o600)


def test_skipped_large_labelled_by_package(tmp_path):
    work, pkg = make(tmp_path)
    write(pkg / "big.csv", b"0" * 500, T0)
    analysis = engine.analyze(str(work), [str(pkg)], max_bytes=10)
    assert analysis.skipped_large[0][0] == os.path.join("submission", "big.csv")


def test_files_read_counts_both_sides(tmp_path):
    work, pkg = make(tmp_path)
    write(work / "a.md", "x", T1)
    write(pkg / "a.md", "x", T0)
    assert engine.analyze(str(work), [str(pkg)]).files_read == 2


def test_siblings_can_be_disabled(tmp_path):
    work, pkg = make(tmp_path)
    write(work / "본문.md", "x", T1)
    write(work / "본문.docx", b"x", T0)
    assert engine.analyze(str(work), [str(pkg)], check_siblings=False).build_lag == []
    assert engine.analyze(str(work), [str(pkg)]).build_lag


def test_siblings_only_look_at_work_tree(tmp_path):
    work, pkg = make(tmp_path)
    write(pkg / "본문.md", "x", T1)
    write(pkg / "본문.docx", b"x", T0)
    assert engine.analyze(str(work), [str(pkg)]).build_lag == []


def test_pkg_rel_includes_package_label(tmp_path):
    work, pkg = make(tmp_path)
    write(work / "a.md", "새", T1)
    write(pkg / "05_Figures" / "a.md", "옛", T0)
    row = engine.analyze(str(work), [str(pkg)]).rows[0]
    assert row.pkg_rel == os.path.join("submission", "05_Figures", "a.md")


def test_fmt_size_has_thousands_separator():
    assert engine.fmt_size(599103) == "599,103 B"


def test_fmt_time_format():
    import time
    assert engine.fmt_time(time.mktime((2026, 7, 31, 10, 43, 0, 0, 0, -1))) \
        == "2026-07-31 10:43"


def test_analysis_has_generated_at(tmp_path):
    work, pkg = make(tmp_path)
    assert engine.analyze(str(work), [str(pkg)]).generated_at > 0


def test_tolerance_passed_through(tmp_path):
    work, pkg = make(tmp_path)
    write(work / "a.md", "새", T0 + 10)
    write(pkg / "a.md", "옛", T0)
    assert engine.analyze(str(work), [str(pkg)]).critical_rows
    assert engine.analyze(str(work), [str(pkg)], tolerance=100).undecidable_rows


def test_example_tree_totals(example_tree):
    a = engine.analyze(example_tree["stale"], [example_tree["stale_package"]])
    assert a.pair_count == 5
    assert len(a.critical_rows) == 2
    assert len(a.same_rows) == 3
    assert len(a.normalized_only_rows) == 2
    assert len(a.renamed_identical) == 1


def test_clean_example_has_no_criticals(example_tree):
    a = engine.analyze(example_tree["clean"], [example_tree["clean_package"]])
    assert a.critical_rows == [] and a.pair_count == 3
