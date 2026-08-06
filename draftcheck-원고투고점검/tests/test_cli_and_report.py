"""CLI 동작(종료 코드·출력 파일)과 리포트 계층(콘솔/마크다운/CSV) — 그리고 안전성."""

from __future__ import annotations

import csv
import hashlib
import json

import pytest
from conftest import EXAMPLES, LIMITS, analyse

from draftcheck.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, EXIT_UNVERIFIABLE, main
from draftcheck.report import (
    ISSUES_CSV,
    REF_HEADER,
    REFERENCES_CSV,
    REPORT_MD,
    console_report,
    csv_safe,
    markdown_report,
    reference_rows,
    write_outputs,
)

FLAWED = str(EXAMPLES / "manuscript_flawed.md")
CLEAN = str(EXAMPLES / "manuscript_clean.md")
FLAWED_DOCX = str(EXAMPLES / "manuscript_flawed.docx")


def write(tmp_path, text, name="m.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# ── 종료 코드 ────────────────────────────────────────────────────────────────


def test_exit_zero_without_strict_even_when_findings_exist(capsys):
    assert main([FLAWED]) == EXIT_OK
    assert "치명 5" in capsys.readouterr().out


def test_strict_returns_one_when_findings_exist(capsys):
    assert main([FLAWED, "--strict"]) == EXIT_FINDINGS


def test_strict_returns_zero_on_a_clean_manuscript(capsys):
    assert main([CLEAN, "--strict"]) == EXIT_OK


def test_strict_returns_three_when_unverifiable(tmp_path, capsys):
    path = write(tmp_path, "Title\n\n## Introduction\nProse citing [1] and [2] and [3].\n")
    assert main([path, "--strict"]) == EXIT_UNVERIFIABLE
    out = capsys.readouterr().out
    assert "점검 불가" in out
    assert "참고문헌" in out


def test_missing_file_returns_two(tmp_path, capsys):
    assert main([str(tmp_path / "nope.md")]) == EXIT_ERROR
    assert "오류" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "draftcheck" in capsys.readouterr().out


# ── 저널 한도 파일 ───────────────────────────────────────────────────────────


def test_limits_file_is_applied(capsys):
    main([FLAWED, "--limits", str(LIMITS)])
    out = capsys.readouterr().out
    assert "Sleep Medicine" in out
    assert "초과" in out


def test_broken_json_limits(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main([CLEAN, "--limits", str(bad)]) == EXIT_ERROR
    assert "JSON" in capsys.readouterr().err


def test_limits_must_be_an_object(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    assert main([CLEAN, "--limits", str(bad)]) == EXIT_ERROR


def test_negative_limit_is_refused(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"abstract_words_max": -5}), encoding="utf-8")
    assert main([CLEAN, "--limits", str(bad)]) == EXIT_ERROR
    assert "0보다 크고" in capsys.readouterr().err


def test_non_numeric_limit_is_refused(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"abstract_words_max": "250"}), encoding="utf-8")
    assert main([CLEAN, "--limits", str(bad)]) == EXIT_ERROR


def test_unknown_limit_key_is_ignored_with_a_note(tmp_path, capsys):
    partial = tmp_path / "p.json"
    partial.write_text(json.dumps({"journal": "X", "made_up": 3}), encoding="utf-8")
    assert main([CLEAN, "--limits", str(partial)]) == EXIT_OK
    assert "made_up" in capsys.readouterr().err


def test_missing_limits_file(tmp_path, capsys):
    assert main([CLEAN, "--limits", str(tmp_path / "gone.json")]) == EXIT_ERROR


# ── 출력 파일 ────────────────────────────────────────────────────────────────


def test_no_files_are_written_without_out_dir(tmp_path, capsys):
    main([FLAWED])
    assert list(tmp_path.iterdir()) == []


def test_out_dir_writes_exactly_three_files(tmp_path, capsys):
    out = tmp_path / "점검_20260806"
    main([FLAWED, "--out-dir", str(out)])
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [REPORT_MD, ISSUES_CSV, REFERENCES_CSV]
    )


def test_out_dir_is_created_recursively(tmp_path, capsys):
    out = tmp_path / "a" / "b" / "c"
    main([FLAWED, "--out-dir", str(out)])
    assert (out / REPORT_MD).exists()


def test_out_dir_that_is_a_file_is_reported(tmp_path, capsys):
    target = tmp_path / "afile"
    target.write_text("x", encoding="utf-8")
    assert main([FLAWED, "--out-dir", str(target)]) == EXIT_ERROR


def test_report_markdown_contains_the_findings(tmp_path, capsys):
    out = tmp_path / "o"
    main([FLAWED, "--out-dir", str(out), "--limits", str(LIMITS)])
    text = (out / REPORT_MD).read_text(encoding="utf-8")
    assert "[27]" in text
    assert "그림 3" in text
    assert "citecheck references.csv" in text  # 다음 단계 안내
    assert "Sleep Medicine" in text


def test_issue_csv_columns_and_rows(tmp_path, capsys):
    out = tmp_path / "o"
    main([FLAWED, "--out-dir", str(out)])
    rows = list(csv.reader((out / ISSUES_CSV).read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == ["줄번호", "심각도", "유형", "대상", "설명", "권고"]
    assert len(rows) == 1 + len(analyse(EXAMPLES / "manuscript_flawed.md").findings)
    assert any(row[2] == "인용누락" for row in rows[1:])


# ── citecheck 연동: 열 이름이 정확히 같아야 한다 ────────────────────────────


def test_references_csv_uses_the_citecheck_schema(tmp_path, capsys):
    out = tmp_path / "o"
    main([FLAWED, "--out-dir", str(out)])
    rows = list(csv.reader((out / REFERENCES_CSV).read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == [
        "Study ID", "Authors", "Year", "Title", "Journal", "Article DOI", "PMID", "parse_ok",
    ]
    assert rows[0][:7] == REF_HEADER[:7]
    assert len(rows) == 27  # 헤더 + 참고문헌 26개


def test_references_csv_carries_dois_and_flags_parse_failures(flawed):
    rows = reference_rows(flawed)
    header, body = rows[0], rows[1:]
    doi_col = header.index("Article DOI")
    assert any(cell[doi_col].startswith("10.") for cell in body)
    assert all(row[-1] in ("yes", "no") for row in body)


# ── CSV 수식 인젝션 방어 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"),
        ("@SUM(A1:A9)", "'@SUM(A1:A9)"),
        ("+cmd|'/c calc'!A0", "'+cmd|'/c calc'!A0"),
        ("-2+3+cmd", "'-2+3+cmd"),
        ("-3", "-3"),  # 순수 숫자는 수식이 될 수 없다
        ("3.5", "3.5"),
        ("정상 문장", "정상 문장"),
        ("줄바꿈\n포함", "줄바꿈 포함"),
        (None, ""),
        (12, "12"),
    ],
)
def test_csv_safe(value, expected):
    assert csv_safe(value) == expected


def test_formula_from_a_manuscript_is_neutralised(tmp_path, capsys):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nBody cites [1] and [2].\n\n## References\n"
        '1. =HYPERLINK("http://evil.example","click"). A title. J Test. 2024.\n'
        "2. Kim H. Normal entry. J Test. 2024.\n",
    )
    out = tmp_path / "o"
    main([path, "--out-dir", str(out)])
    text = (out / REFERENCES_CSV).read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in text
    for row in csv.reader(text.splitlines()):
        for cell in row:
            assert not cell.startswith(("=", "@")), cell


def test_every_cell_of_both_csvs_is_safe(tmp_path, capsys):
    out = tmp_path / "o"
    main([FLAWED_DOCX, "--out-dir", str(out)])
    for name in (ISSUES_CSV, REFERENCES_CSV):
        text = (out / name).read_text(encoding="utf-8-sig")
        for row in csv.reader(text.splitlines()):
            for cell in row:
                if cell[:1] in ("=", "+", "@"):
                    pytest.fail(f"{name}: 수식으로 해석될 셀 {cell!r}")


# ── 원본 원고는 절대 바뀌지 않는다 ──────────────────────────────────────────


def test_running_the_cli_never_touches_the_manuscript(tmp_path, capsys):
    src = (EXAMPLES / "manuscript_flawed.docx").read_bytes()
    path = tmp_path / "copy.docx"
    path.write_bytes(src)
    before = (hashlib.sha256(src).hexdigest(), path.stat().st_mtime_ns)
    main([str(path), "--out-dir", str(tmp_path / "out"), "--limits", str(LIMITS)])
    after = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    assert before == after


def test_outputs_never_escape_the_out_dir(tmp_path, capsys):
    """산출물 이름은 상수 3개뿐 — 원고 내용이 경로에 관여할 수 없다."""
    path = write(
        tmp_path,
        "Title ../../escaped.md\n\n## Introduction\nBody [1] with ../../evil and /etc/passwd.\n\n"
        "## References\n1. ../../also.txt Kim H. T. J Test. 2024.\n",
    )
    out = tmp_path / "sandbox" / "o"
    main([path, "--out-dir", str(out)])
    produced = {p.name for p in out.iterdir()}
    assert produced == {REPORT_MD, ISSUES_CSV, REFERENCES_CSV}
    assert not (tmp_path / "escaped.md").exists()
    assert not (tmp_path / "sandbox" / "also.txt").exists()


# ── 그 밖의 플래그 ───────────────────────────────────────────────────────────


def test_dump_text_shows_line_numbers_and_writes_nothing(tmp_path, capsys):
    assert main([FLAWED_DOCX, "--dump-text"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[body]" in out and "docx" in out
    assert "[99]" not in out  # 추적 삭제분은 덤프에도 없다
    assert list(tmp_path.iterdir()) == []


def test_quiet_prints_one_line(capsys):
    main([FLAWED, "--quiet"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert "치명 5" in out[0]


def test_abbrev_ok_suppresses_a_warning(tmp_path, capsys):
    path = write(
        tmp_path,
        "Title\n\n## Introduction\nWe measured XYZ. Later the XYZ rose [1].\n"
        "The XYZ stayed high.\n\n"
        "## References\n1. Kim H. T. J Test. 2024.\n",
    )
    main([path])
    assert "XYZ" in capsys.readouterr().out
    main([path, "--abbrev-ok", "xyz"])
    assert "XYZ" not in capsys.readouterr().out


def test_forced_citation_style_is_shown(capsys):
    main([FLAWED, "--citation-style", "numeric"])
    assert "사용자 지정" in capsys.readouterr().out


# ── 리포트 계층 자체 ─────────────────────────────────────────────────────────


def test_console_report_mentions_style_and_counts(flawed):
    text = console_report(flawed)
    assert "numeric" in text
    assert "자동 판별" in text
    assert "치명 5" in text


def test_console_report_shows_the_unverifiable_box(tmp_path):
    result = analyse(write(tmp_path, "Title\n\nProse with no citations and no references.\n"))
    text = console_report(result)
    assert "점검 불가" in text
    assert "┏" in text and "┗" in text


def test_markdown_report_escapes_pipes(tmp_path):
    result = analyse(
        write(
            tmp_path,
            "Title\n\n## Introduction\nA table | pipe and Figure 4 [1].\n\n"
            "## References\n1. Kim H. T | with pipe. J Test. 2024.\n\n"
            "## Figure legends\n**Figure 1.** Caption.\n",
        )
    )
    text = markdown_report(result)
    for line in text.splitlines():
        if line.startswith("|") and "---" not in line:
            assert line.count("|") - line.count("\\|") >= 2


def test_write_outputs_is_idempotent(tmp_path, flawed):
    out = tmp_path / "o"
    first = write_outputs(flawed, out)
    second = write_outputs(flawed, out)
    assert [p.name for p in first] == [p.name for p in second]
    assert len(list(out.iterdir())) == 3
