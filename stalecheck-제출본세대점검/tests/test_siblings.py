# -*- coding: utf-8 -*-
"""형제 점검 — 소스가 산출물보다 최신이거나, 그림 두 포맷이 갈라진 경우."""

import pytest

from conftest import T0, T1, write
from stalecheck import scanning, siblings


def recs(tmp_path):
    return scanning.scan_tree(
        str(tmp_path), exts=(".md", ".docx", ".png", ".pdf", ".tex")).files


def test_md_newer_than_docx_is_build_lag(tmp_path):
    write(tmp_path / "본문.md", "x", T1)
    write(tmp_path / "본문.docx", b"x", T0)
    found = siblings.find_build_lag(recs(tmp_path))
    assert len(found) == 1
    assert found[0].kind == siblings.WARN_BUILD_STALE
    assert found[0].newer == "본문.md"


def test_docx_newer_than_md_is_not_flagged(tmp_path):
    write(tmp_path / "본문.md", "x", T0)
    write(tmp_path / "본문.docx", b"x", T1)
    assert siblings.find_build_lag(recs(tmp_path)) == []


def test_build_lag_within_threshold_ignored(tmp_path):
    write(tmp_path / "본문.md", "x", T0 + 30)
    write(tmp_path / "본문.docx", b"x", T0)
    assert siblings.find_build_lag(recs(tmp_path)) == []


def test_build_lag_threshold_configurable(tmp_path):
    write(tmp_path / "본문.md", "x", T0 + 30)
    write(tmp_path / "본문.docx", b"x", T0)
    assert len(siblings.find_build_lag(recs(tmp_path), lag=10)) == 1


def test_tex_pdf_pair_also_checked(tmp_path):
    write(tmp_path / "paper.tex", "x", T1)
    write(tmp_path / "paper.pdf", b"x", T0)
    found = siblings.find_build_lag(recs(tmp_path))
    assert [f.newer for f in found] == ["paper.tex"]


def test_different_directories_are_not_siblings(tmp_path):
    write(tmp_path / "a" / "본문.md", "x", T1)
    write(tmp_path / "b" / "본문.docx", b"x", T0)
    assert siblings.find_build_lag(recs(tmp_path)) == []


def test_stem_matching_is_case_insensitive(tmp_path):
    write(tmp_path / "Paper.md", "x", T1)
    write(tmp_path / "paper.docx", b"x", T0)
    assert len(siblings.find_build_lag(recs(tmp_path))) == 1


def test_png_pdf_far_apart_is_flagged(tmp_path):
    write(tmp_path / "그림1.png", b"x", T1)
    write(tmp_path / "그림1.pdf", b"x", T0)
    found = siblings.find_figure_sibling_lag(recs(tmp_path))
    assert len(found) == 1 and found[0].kind == siblings.WARN_FIGURE_SIBLING


def test_png_pdf_same_build_is_quiet(tmp_path):
    write(tmp_path / "그림1.png", b"x", T1)
    write(tmp_path / "그림1.pdf", b"x", T1 + 1)
    assert siblings.find_figure_sibling_lag(recs(tmp_path)) == []


def test_figure_lag_reports_newer_first(tmp_path):
    write(tmp_path / "그림1.png", b"x", T0)
    write(tmp_path / "그림1.pdf", b"x", T1)
    found = siblings.find_figure_sibling_lag(recs(tmp_path))
    assert found[0].newer == "그림1.pdf" and found[0].older == "그림1.png"


def test_figure_lag_threshold_configurable(tmp_path):
    write(tmp_path / "그림1.png", b"x", T0)
    write(tmp_path / "그림1.pdf", b"x", T0 + 120)
    assert siblings.find_figure_sibling_lag(recs(tmp_path)) == []
    assert len(siblings.find_figure_sibling_lag(recs(tmp_path), lag=60)) == 1


def test_lone_file_has_no_sibling(tmp_path):
    write(tmp_path / "그림1.png", b"x", T1)
    assert siblings.find_figure_sibling_lag(recs(tmp_path)) == []
    assert siblings.find_build_lag(recs(tmp_path)) == []


def test_findings_are_sorted_deterministically(tmp_path):
    for n in ("z", "a", "m"):
        write(tmp_path / (n + ".md"), "x", T1)
        write(tmp_path / (n + ".docx"), b"x", T0)
    found = siblings.find_build_lag(recs(tmp_path))
    assert [f.newer for f in found] == ["a.md", "m.md", "z.md"]


def test_lag_value_is_seconds(tmp_path):
    write(tmp_path / "본문.md", "x", T0 + 3600)
    write(tmp_path / "본문.docx", b"x", T0)
    assert siblings.find_build_lag(recs(tmp_path))[0].lag == pytest.approx(3600, abs=2)


def test_default_thresholds_documented():
    assert siblings.BUILD_LAG_SEC == 60.0
    assert siblings.FIGURE_LAG_SEC == 3600.0


def test_empty_record_list():
    assert siblings.find_build_lag([]) == []
    assert siblings.find_figure_sibling_lag([]) == []
