"""커버리지 자백 강제 — 자백을 못 만들면 리포트를 내지 않습니다."""

from __future__ import annotations

import pytest

from deidaudit.audit import run_audit
from deidaudit.coverage import MIN_SCAN_RATIO, Coverage, CoverageError
from deidaudit.report import console_report, markdown_report


def test_empty_coverage_refuses_to_render():
    coverage = Coverage()
    with pytest.raises(CoverageError):
        coverage.block()


def test_coverage_without_columns_refuses():
    coverage = Coverage(files_given=1, files_read=1, columns=0)
    with pytest.raises(CoverageError):
        coverage.validate()


def test_console_report_refuses_without_coverage():
    from deidaudit.audit import AuditResult

    result = AuditResult()
    with pytest.raises(CoverageError):
        console_report(result, 5)
    with pytest.raises(CoverageError):
        markdown_report(result, 5, False, "cmd", "now")


def test_confession_lists_free_text_columns(dirty_csv):
    result = run_audit([dirty_csv], [], ["subject_id"], 10**9, 5)
    block = "\n".join(result.coverage.block())
    assert "자유텍스트로 판정한 열" in block
    assert "비고" in block
    assert "건너뜀: 0개" in block


def test_confession_reports_skipped_files(tmp_path, dirty_csv):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"nope")
    result = run_audit([dirty_csv, broken], [], ["subject_id"], 10**9, 5)
    block = "\n".join(result.coverage.block())
    assert "broken.xlsx" in block
    assert result.coverage.file_ratio == 0.5
    assert result.coverage.undetermined


def test_not_computed_is_confessed_when_quasi_missing(dirty_csv):
    result = run_audit([dirty_csv], [], ["subject_id"], 10**9, 5)
    block = "\n".join(result.coverage.block())
    assert "계산 안 함" in block
    assert "--quasi" in block


def test_scan_ratio_threshold_is_a_named_constant():
    assert MIN_SCAN_RATIO == 0.80
    coverage = Coverage(files_given=1, files_read=1, columns=3, cells=79, cells_skipped=21)
    assert coverage.scan_ratio < MIN_SCAN_RATIO
    assert coverage.undetermined
