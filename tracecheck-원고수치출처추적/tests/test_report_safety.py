"""리포트 무결성 + 보안.

여기 있는 테스트는 '이 툴이 조용히 통과시키지 않는다'와 '남의 파일을 건드리지
않는다' 두 가지 약속을 지킵니다. 둘 다 문서의 문장이 아니라 코드로 강제돼야
의미가 있습니다.
"""

import os

import pytest
from conftest import make_bundle, write

from tracecheck.analyze import analyze, parse_sections
from tracecheck.bundle import collect
from tracecheck.cli import main
from tracecheck.manuscript import read_manuscript
from tracecheck.report import (COVERAGE_MARKER, OUT_TABLE, ReportIntegrityError,
                               _require_coverage, coverage_lines,
                               render_console, render_markdown,
                               render_summary, render_table_csv, sentences,
                               write_outputs)
from tracecheck.safety import InputError, csv_safe, safe_out_path


@pytest.fixture
def analysis(simple_case):
    manuscript_path, bundle_dir = simple_case
    return analyze(read_manuscript(manuscript_path),
                   collect([bundle_dir], "현재"), None,
                   sections=parse_sections(""))


# --------------------------------------------------------------------------- #
# 커버리지 자백은 선택이 아닙니다
# --------------------------------------------------------------------------- #

def test_every_rendered_report_contains_the_coverage_block(analysis):
    for text in (render_console(analysis), render_markdown(analysis),
                 render_summary(analysis)):
        assert COVERAGE_MARKER in text


def test_report_without_coverage_block_is_refused():
    with pytest.raises(ReportIntegrityError):
        _require_coverage("치명 0건입니다. (커버리지 자백 없음)")


def test_coverage_block_is_refused_even_when_verdict_is_clean(analysis,
                                                              monkeypatch):
    """자백 블록이 사라지면 '이상 없음' 리포트도 나가면 안 됩니다."""
    monkeypatch.setattr("tracecheck.report.coverage_lines", lambda _a: [])
    with pytest.raises(ReportIntegrityError):
        render_console(analysis)


def test_cli_returns_3_if_report_integrity_breaks(simple_case, monkeypatch,
                                                  capsys):
    monkeypatch.setattr("tracecheck.report.coverage_lines", lambda _a: [])
    manuscript, bundle = simple_case
    assert main([manuscript, "--outputs", bundle, "--no-files"]) == 3
    assert "무결성" in capsys.readouterr().err


def test_coverage_numbers_add_up(analysis):
    cov = analysis.coverage
    assert cov.extracted == cov.compared + cov.skipped
    assert cov.compared == cov.matched + cov.unmatched
    assert sum(cov.skip_counts.values()) == cov.skipped


def test_coverage_lines_list_every_skip_reason_used(tmp_path):
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n"
                       "Table 1 은 2026-08-18 자료다 [3]. 8주 시점, 1:1 배정, "
                       "p < 0.05, 2명 탈락, 2019 이후, NCT01234567.\n"
                       "평균 12.44, 15.91, -3.47, 0.0021, 4.08 이었다.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "a,b,c,d,e\n12.44,15.91,-3.47,0.0021,4.08\n"})
    result = analyze(read_manuscript(manuscript), collect([bundle], "현재"),
                     None, sections=parse_sections(""))
    text = "\n".join(coverage_lines(result))
    for reason, count in result.coverage.skip_counts.items():
        assert reason in text
        assert str(count) in text


def test_previous_absence_is_stated_prominently(analysis):
    assert any("구버전 잔존 검사는 수행되지 않았습니다" in note
               for note in analysis.warnings)
    assert "구버전 잔존 검사는 수행되지 않았습니다" in render_console(analysis)


def test_sentences_are_bilingual_and_honest(analysis):
    kr, en = sentences(analysis)
    assert "라벨" in kr and "not verified" in en
    assert str(analysis.coverage.compared) in kr


# --------------------------------------------------------------------------- #
# CSV 안전
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,expected", [
    ("=cmd|' /c calc'!A1", "'=cmd|' /c calc'!A1"),
    ("+SUM(A1)", "'+SUM(A1)"),
    ("@import", "'@import"),
    ("-3.47", "-3.47"),            # 음수는 수식이 아니라 숫자입니다
    ("-1.5%", "-1.5%"),
    ("12.44", "12.44"),
    ("-cmd", "'-cmd"),
    ("", ""),
])
def test_csv_formula_injection_guard(value, expected):
    assert csv_safe(value) == expected


def test_injection_from_manuscript_text_is_neutralized(tmp_path):
    manuscript = write(str(tmp_path / "m.md"),
                       "## Results\n=HYPERLINK(\"x\") 평균 12.44, 15.91, -3.47, "
                       "0.0021, 4.08, 4.63 이었다.\n")
    bundle = make_bundle(tmp_path / "out",
                         {"a.csv": "a,b,c,d,e,f\n12.44,15.91,-3.47,0.0021,4.08,4.63\n"})
    result = analyze(read_manuscript(manuscript), collect([bundle], "현재"),
                     None, sections=parse_sections(""))
    csv_text = render_table_csv(result)
    for line in csv_text.splitlines()[1:]:
        assert not line.lstrip('"').startswith("=")


# --------------------------------------------------------------------------- #
# 경로 안전
# --------------------------------------------------------------------------- #

def test_safe_out_path_rejects_traversal(tmp_path):
    for name in ("../탈출.csv", "/etc/passwd", "sub/x.csv"):
        with pytest.raises(InputError):
            safe_out_path(str(tmp_path), name, [])


def test_safe_out_path_refuses_to_overwrite_an_input(tmp_path):
    target = write(str(tmp_path / OUT_TABLE), "기존 파일")
    with pytest.raises(InputError) as exc:
        safe_out_path(str(tmp_path), OUT_TABLE, [os.path.realpath(target)])
    assert "덮어쓰게" in str(exc.value)


def test_symlinked_output_target_is_refused(tmp_path, analysis):
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    victim = write(str(tmp_path / "중요.csv"), "지우면 안 되는 파일")
    os.symlink(victim, str(out_dir / OUT_TABLE))
    with pytest.raises(InputError):
        write_outputs(analysis, str(out_dir))
    with open(victim, encoding="utf-8") as handle:
        assert handle.read() == "지우면 안 되는 파일"


def test_symlinked_manuscript_is_refused(tmp_path, capsys, simple_case):
    manuscript, bundle = simple_case
    link = str(tmp_path / "링크.md")
    os.symlink(manuscript, link)
    assert main([link, "--outputs", bundle, "--no-files"]) == 2
    assert "심볼릭 링크" in capsys.readouterr().err


def test_hardlinked_manuscript_is_refused(tmp_path, capsys, simple_case):
    manuscript, bundle = simple_case
    link = str(tmp_path / "하드링크.md")
    os.link(manuscript, link)
    assert main([link, "--outputs", bundle, "--no-files"]) == 2
    assert "하드 링크" in capsys.readouterr().err


def test_input_files_are_never_modified(tmp_path, simple_case):
    manuscript, bundle = simple_case
    before = {}
    for root, _dirs, files in os.walk(bundle):
        for name in files:
            path = os.path.join(root, name)
            before[path] = (os.path.getmtime(path), open(path, "rb").read())
    with open(manuscript, "rb") as handle:
        manuscript_bytes = handle.read()
    main([manuscript, "--outputs", bundle, "--out-dir", str(tmp_path / "결과")])
    for path, (mtime, data) in before.items():
        assert os.path.getmtime(path) == mtime
        with open(path, "rb") as handle:
            assert handle.read() == data
    with open(manuscript, "rb") as handle:
        assert handle.read() == manuscript_bytes


def test_error_messages_do_not_leak_manuscript_text(tmp_path, capsys):
    secret = "환자 홍길동의 ISI 는 21점이다"
    manuscript = write(str(tmp_path / "비밀.docx"), secret)   # docx 가 아닌 내용
    bundle = make_bundle(tmp_path / "out", {"a.csv": "m\n1.5\n"})
    main([manuscript, "--outputs", bundle, "--no-files"])
    err = capsys.readouterr().err
    assert "홍길동" not in err and "ISI" not in err


def test_report_files_are_utf8_bom_for_excel(analysis, tmp_path):
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    written = write_outputs(analysis, str(out_dir))
    for path in written:
        with open(path, "rb") as handle:
            assert handle.read(3) == b"\xef\xbb\xbf"
