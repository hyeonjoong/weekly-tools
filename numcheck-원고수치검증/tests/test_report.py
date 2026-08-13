"""리포트·CSV — 커버리지 자백, 수식 인젝션 방어, 언어 전환."""

from __future__ import annotations

import csv

from conftest import analyze_text
from numcheck.report import OUTPUT_FILES, csv_safe, render_console, write_csvs

SAMPLE = (
    "## Results\n"
    "반응자는 23/48 (45.2%) 이었다.\n"
    "ISI 평균은 14.37 (N = 23) 이었다.\n"
    "총 48명 (능동 24, 대조 23) 이었다.\n"
    "하위군 반응자는 14/23 (60.9%), 6/23 (26.1%) 이었다.\n"
    "차이는 유의하였다, t(45) = 2.31, p = .026.\n"
    "순응도는 92.3% 였다.\n"
)


# ── 커버리지 자백 ────────────────────────────────────────────────────────────


def test_coverage_line_comes_first():
    text = render_console(analyze_text(SAMPLE))
    body = text.splitlines()
    assert any("검사 후보" in line for line in body[:6])
    assert any("건너뜀 사유" in line for line in body[:8])


def test_coverage_numbers_add_up():
    report = analyze_text(SAMPLE)
    assert report.n_checked + report.n_skipped == report.n_candidates
    assert sum(count for _r, count in report.skip_breakdown()) == report.n_skipped


def test_too_few_claims_is_loudly_reported():
    report = analyze_text("## Results\n특별한 숫자가 없는 문단이다.\n")
    text = render_console(report)
    assert report.exit_code() == 3
    assert "제대로" in text and "이상 없음" in text


def test_audit_csv_contains_every_candidate(tmp_path):
    report = analyze_text(SAMPLE)
    write_csvs(report, tmp_path, "ko", "요약")
    rows = list(csv.DictReader((tmp_path / "재계산표.csv").read_text(
        encoding="utf-8-sig").splitlines()))
    data_rows = [r for r in rows if r["절"] != "(요약)"]
    assert len(data_rows) == report.n_candidates
    summary = [r for r in rows if r["절"] == "(요약)"]
    assert summary and "후보" in summary[0]["보고값"]
    assert any(r["항목"] == "건너뜀 사유" for r in summary)


def test_issue_csv_matches_findings(tmp_path):
    report = analyze_text(SAMPLE)
    write_csvs(report, tmp_path, "ko", "요약")
    rows = list(csv.DictReader((tmp_path / "문제목록.csv").read_text(
        encoding="utf-8-sig").splitlines()))
    assert len(rows) == len(report.findings)
    assert set(rows[0]) >= {"줄번호", "절", "등급", "항목", "원문", "보고값",
                            "재계산값", "판정", "설명"}


def test_all_three_files_are_written(tmp_path):
    report = analyze_text(SAMPLE)
    written = write_csvs(report, tmp_path / "새폴더", "ko", "요약 본문")
    assert [p.name for p in written] == list(OUTPUT_FILES)
    assert (tmp_path / "새폴더" / "요약.txt").read_text(encoding="utf-8").strip() == "요약 본문"


# ── CSV 수식 인젝션 ──────────────────────────────────────────────────────────


def test_csv_safe_quotes_formula_starts():
    assert csv_safe("=SUM(A1:A9)") == "'=SUM(A1:A9)"
    assert csv_safe("+cmd|'/c calc'") == "'+cmd|'/c calc'"
    assert csv_safe("@import") == "'@import"
    assert csv_safe("-3+3") == "'-3+3"


def test_csv_safe_leaves_plain_numbers_alone():
    """-7.4 는 수식이 아니다. 전부 따옴표를 붙이면 표를 쓸 수 없게 된다."""
    assert csv_safe("-7.4") == "-7.4"
    assert csv_safe("+3") == "+3"
    assert csv_safe("47.9%") == "47.9%"


def test_csv_safe_strips_newlines():
    assert "\n" not in csv_safe("a\nb")
    assert "\r" not in csv_safe("a\rb")


def test_injection_payload_in_manuscript_is_neutralised(tmp_path):
    text = "## Results\n=HYPERLINK(\"http://x\") 23/48 (45.2%) 이었다.\n"
    report = analyze_text(text)
    write_csvs(report, tmp_path, "ko", "요약")
    raw = (tmp_path / "문제목록.csv").read_text(encoding="utf-8-sig")
    assert "=HYPERLINK" in raw          # 내용은 보존하고
    assert '"\'=HYPERLINK' in raw or "'=HYPERLINK" in raw  # 앞에 ' 를 붙여 무력화


# ── 언어·발췌 ────────────────────────────────────────────────────────────────


def test_english_report_uses_english_messages():
    report = analyze_text(SAMPLE)
    text = render_console(report, "en")
    assert "candidates" in text
    assert "CRITICAL" in text
    assert "unreachable under round/floor/ceil" in text


def test_no_quote_suppresses_manuscript_prose():
    """--no-quote 는 원고 **문장**을 빼는 스위치다.

    지적 문구에 남는 숫자(23/48 등)는 지적의 본체이므로 남는다 — 그것까지 빼면
    리포트가 '어디가 왜 틀렸는지'를 말하지 못한다.
    """
    report = analyze_text(SAMPLE, quote=False)
    text = render_console(report)
    assert "반응자는" not in text
    assert "원문 생략" in text
    assert all(f.quote == "" for f in report.findings)


def test_findings_are_sorted_by_severity_then_line():
    report = analyze_text(SAMPLE)
    ranks = [("치명", "경고", "정보").index(f.level) for f in report.sorted_findings()]
    assert ranks == sorted(ranks)


def test_exit_codes():
    assert analyze_text(SAMPLE).exit_code() == 1
    warn_only = analyze_text(
        "## Results\n총 48명 (능동 24, 대조 23) 이었다. 23/48 (47.9%) 이고 "
        "14/23 (60.9%) 이며 6/23 (26.1%) 이고 t(45) = 2.31, p = .026 이다.\n")
    assert warn_only.exit_code() == 2
    clean = analyze_text(
        "## Results\n23/48 (47.9%), 14/23 (60.9%), 6/23 (26.1%), "
        "24/48 (50.0%), 12/48 (25.0%), 총 48명 (능동 24, 대조 24) 이다.\n")
    assert clean.exit_code() == 0
