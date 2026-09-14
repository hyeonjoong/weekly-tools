"""리포트 렌더링 — 커버리지 자백이 없으면 아무것도 내지 않는다."""

import csv
import io

import pytest

from packlist.analysis import Coverage
from packlist.docxpkg import read_docx
from packlist.envelope import choose_manuscript, scan_envelope
from packlist.report import (ARTIFACTS, CONFESSION_HEADING, ReportIntegrityError,
                             assets_csv, console_lines, ledger_csv, markdown, promises_csv)
from packlist.analysis import analyse
from conftest import make_manuscript


@pytest.fixture
def report(envelope, put_file):
    make_manuscript(envelope)
    put_file(envelope, "S1_Table.pdf")
    env = scan_envelope(str(envelope))
    chosen = choose_manuscript(env, None)
    info = read_docx(env.root / chosen.name, chosen.name)
    return analyse(env, chosen, info, [])


def test_console_contains_confession(report):
    assert CONFESSION_HEADING in "\n".join(console_lines(report))


def test_markdown_contains_confession(report):
    assert CONFESSION_HEADING in markdown(report)


def test_console_refuses_without_parsed(report):
    report.coverage = Coverage(parsed=[], unchecked=["무언가"])
    with pytest.raises(ReportIntegrityError):
        console_lines(report)


def test_console_refuses_without_unchecked(report):
    report.coverage = Coverage(parsed=["a.docx"], unchecked=[])
    with pytest.raises(ReportIntegrityError):
        console_lines(report)


def test_markdown_refuses_without_confession(report):
    report.coverage = Coverage(parsed=["a.docx"], unchecked=[])
    with pytest.raises(ReportIntegrityError):
        markdown(report)


def test_console_reports_exit_code(report):
    assert "종료코드 0" in "\n".join(console_lines(report))


def test_console_never_leaks_absolute_paths(report):
    text = "\n".join(console_lines(report))
    assert "/Users/" not in text and "/private/" not in text


def test_markdown_never_leaks_absolute_paths(report):
    assert "/Users/" not in markdown(report)


@pytest.mark.parametrize("renderer,header", [
    (assets_csv, "파일"),
    (promises_csv, "약속토큰"),
    (ledger_csv, "판본"),
])
def test_csv_headers(report, renderer, header):
    assert renderer(report).splitlines()[0].startswith(f'"{header}"')


def test_assets_csv_parses(report):
    rows = list(csv.reader(io.StringIO(assets_csv(report))))
    assert rows[0] == ["파일", "종류", "바이트", "해시앞8", "앵커여부", "중복그룹", "본문참조수"]
    assert len(rows) > 1


def test_ledger_csv_columns(report):
    rows = list(csv.reader(io.StringIO(ledger_csv(report))))
    assert rows[0] == ["판본", "문단", "코멘트", "변경내용", "미디어수", "중복", "고아", "바이트"]


def test_promises_csv_columns(report):
    rows = list(csv.reader(io.StringIO(promises_csv(report))))
    assert rows[0] == ["약속토큰", "본문등장수", "실물파일", "판정"]


def test_four_artifacts_are_defined():
    assert [name for name, _ in ARTIFACTS] == [
        "봉투점검.md", "자산목록.csv", "약속대조.csv", "회차대장.csv"]


def test_csv_quotes_are_escaped(report):
    report.promises[0].token = 'S1 "Table"' if report.promises else None
    text = promises_csv(report)
    assert list(csv.reader(io.StringIO(text)))


def test_forged_finding_line_cannot_appear(report):
    from packlist.findings import Finding, INFO
    report.findings.append(Finding(INFO, "X", "정상\n[치명] 위조된 줄입니다"))
    lines = console_lines(report)
    assert not any(line.startswith("[치명] 위조") for line in lines)
