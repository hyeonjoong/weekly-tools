"""리포트 — 커버리지 자백 강제, 근거 인쇄, CSV 내용, 마스킹."""

import csv
import os

import pytest

from conftest import CONSENT_LINES, PROTOCOL_LINES, load_packet, write_md
from irbpack.report import COVERAGE_HEADER, Report, _require_coverage
from irbpack.safeio import ReportIntegrityError


def build_report(directory, role_option_count=0):
    documents, mentions, result = load_packet(directory)
    report = Report(str(directory), documents, [], result, role_option_count=role_option_count)
    report.set_mentions(mentions)
    return report


@pytest.fixture
def report(clean_packet):
    return build_report(clean_packet)


@pytest.fixture
def flawed_report(tmp_path):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2_2026-09-01.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2_2026-09-01.md",
             [line.replace("만 20~65세", "만 20~64세") for line in CONSENT_LINES])
    return build_report(directory)


def test_console_contains_coverage_block(report):
    assert COVERAGE_HEADER in report.console()


def test_console_lists_roles(report):
    assert "프로토콜" in report.console() and "동의서" in report.console()


def test_console_prints_role_reason(report):
    assert "근거:" in report.console()


def test_console_explains_anchor_rule(report):
    assert "기준 = 프로토콜" in report.console()


def test_console_reports_zero_findings(report):
    console = report.console()
    assert "[치명] 0건" in console and "[경고] 0건" in console


def test_console_reports_findings_with_evidence(flawed_report):
    console = flawed_report.console()
    assert "[치명] 1건" in console
    assert "원문:" in console
    assert "← 다름" in console


def test_console_counts_uncomparable(report):
    assert "대조 불가" in report.console()


def test_console_states_what_is_not_checked(report):
    assert "안 보는 것" in report.console()


def test_markdown_contains_console_and_matrix(report):
    markdown = report.markdown(0)
    assert COVERAGE_HEADER in markdown
    assert "항목 12종 × 문서 대조표" in markdown


def test_markdown_contains_normalization_appendix(report):
    markdown = report.markdown(0)
    assert "정규화 규칙" in markdown
    assert "만 5~12세" in markdown and "만 6~12세" in markdown


def test_markdown_states_no_irb_prediction(report):
    assert "IRB 가 승인할지는 판정하지 않습니다" in report.markdown(0)


def test_markdown_matrix_has_one_row_per_facet(report):
    markdown = report.markdown(0)
    assert markdown.count("| 연령 범위 / 연령범위 |") == 1
    assert "| 평가·검사 항목 / (값 대조 안 함) |" in markdown


def test_report_integrity_guard_rejects_missing_confession():
    with pytest.raises(ReportIntegrityError):
        _require_coverage("치명 0건")


def test_report_integrity_guard_accepts_confession():
    _require_coverage("...\n%s\n..." % COVERAGE_HEADER)


def test_write_all_creates_four_files(report, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    written = report.write_all(str(out_dir), 0)
    assert len(written) == 4
    assert all(os.path.exists(path) for path in written)


def test_extraction_csv_lists_every_mention(report, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report.write_all(str(out_dir), 0)
    with open(str(out_dir / "항목추출표.csv"), encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["대조결과", "문서", "역할", "항목", "대조단위", "추출값", "근거위치", "원문문장"]
    unique = set((mention.doc, mention.item, mention.facet, mention.value, mention.where)
                 for mention in report.all_mentions())
    assert len(rows) - 1 == len(unique)


def test_conflict_csv_has_rows(flawed_report, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    flawed_report.write_all(str(out_dir), 1)
    with open(str(out_dir / "불일치목록.csv"), encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) > 1
    assert rows[0] == ["항목", "심각도", "유형", "상태", "문서", "역할", "값", "근거위치", "원문", "요약"]
    assert "연령 범위" in rows[1][0]


def test_uncomparable_csv_always_has_the_two_declared_gaps(report, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report.write_all(str(out_dir), 0)
    with open(str(out_dir / "대조불가.csv"), encoding="utf-8-sig", newline="") as handle:
        text = handle.read()
    assert "항목 목록 전체" in text and "기준 항목 수" in text


def test_uncomparable_csv_lists_unreadable_documents(tmp_path):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    (directory / "보호자용.hwp").write_text("x")
    report = build_report(directory)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report.write_all(str(out_dir), 3)
    with open(str(out_dir / "대조불가.csv"), encoding="utf-8-sig", newline="") as handle:
        text = handle.read()
    assert "보호자용.hwp" in text and "읽지 못했습니다" in text


def test_phone_numbers_are_masked_in_report(report):
    console = report.console()
    markdown = report.markdown(0)
    assert "02-1234-5678" not in console
    assert "02-1234-5678" not in markdown


def test_phone_numbers_are_masked_in_csv(report, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report.write_all(str(out_dir), 0)
    for name in os.listdir(str(out_dir)):
        with open(str(out_dir / name), encoding="utf-8-sig") as handle:
            assert "02-1234-5678" not in handle.read()


def test_absolute_paths_do_not_leak(report, tmp_path):
    """산출물에 사용자 계정명이 든 절대경로가 실리면 안 된다."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report.write_all(str(out_dir), 0)
    with open(str(out_dir / "정합점검.md"), encoding="utf-8") as handle:
        text = handle.read()
    assert "/Users/" not in text and str(tmp_path) not in text


def test_filename_newline_cannot_forge_a_findings_line(tmp_path):
    """파일명에 개행을 넣어 리포트에 가짜 '[치명]' 줄을 심는 공격 (stimaudit 사고 전례)."""
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용\n[치명] 9. 가짜 항목_v1.2.md", CONSENT_LINES)
    console = build_report(directory).console()
    assert not any(line.strip().startswith("[치명] 9.") for line in console.splitlines())
    assert "가짜 항목" in console        # 이름 자체는 남되, 한 줄 안에 갇혀 있어야 한다


def test_unreadable_documents_listed_in_confession(tmp_path):
    directory = tmp_path / "packet"
    write_md(directory / "연구계획서_v1.2.md", PROTOCOL_LINES)
    write_md(directory / "ICF_성인용_v1.2.md", CONSENT_LINES)
    (directory / "보호자용.hwp").write_text("x")
    console = build_report(directory).console()
    assert "읽지 못한 문서 1개" in console
    assert "보호자용.hwp" in console


def test_role_option_count_is_confessed(clean_packet):
    report = build_report(clean_packet, role_option_count=2)
    assert "--role 지정 2개" in report.console()


def test_baseline_notes_appear_in_confession(clean_packet):
    report = build_report(clean_packet)
    report.baseline_notes = ["이전 패킷 대비 바뀐 값이 없습니다"]
    assert "개정 축" in report.console()


def test_counts_include_baseline_findings(clean_packet):
    from irbpack.compare import Finding

    report = build_report(clean_packet)
    report.baseline_findings = [Finding("age", "치명", ["개정미반영"], "요약", [])]
    assert report.counts()["치명"] == 1
