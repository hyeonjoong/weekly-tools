"""The bundled examples, the documented commands, and nasty-input robustness."""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from longistat.analyze import Options, analyze
from longistat.cli import main
from longistat.dataio import DataError, Panel, load_long, load_wide
from longistat.report import render_csv, render_json, render_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISI = os.path.join(ROOT, "examples", "isi_serene_예시.csv")
WOWFIT = os.path.join(ROOT, "examples", "와우핏_단어인지도_wide예시.csv")


# --------------------------------------------------------------------------
# bundled examples / documented commands
# --------------------------------------------------------------------------

def test_bundled_examples_exist():
    assert os.path.exists(ISI) and os.path.exists(WOWFIT)


def test_readme_headline_command_runs(capsys):
    code = main([ISI, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "--group", "군", "--time-order", "기저,4주,8주",
                 "--mcid", "6", "--direction", "lower",
                 "--reliability", "0.9", "--recovery-cutoff", "7"])
    out = capsys.readouterr().out
    assert code == 0
    assert "그룹 × 시점" in out and "반응자 분석" in out and "RCI" in out


def test_run_command_script_is_executable_and_self_consistent():
    script = os.path.join(ROOT, "실행.command")
    assert os.access(script, os.X_OK), "실행.command 에 실행 권한이 없습니다"
    body = open(script, encoding="utf-8").read()
    assert body.startswith("#!/bin/bash")
    assert 'cd "$(dirname "$0")"' in body
    assert "엔터를 누르면 창이 닫힙니다" in body
    for name in re.findall(r"examples/[^\s\\\"]+", body):
        assert os.path.exists(os.path.join(ROOT, name)), name


_SIBLING_TOOLS = ("statwise", "powerplan", "table1", "agreestat", "surveyscan",
                  "factorscan", "hrvkit", "eegband", "logflow", "paperforge",
                  "citecheck", "pubgap", "pip ")


def test_docs_reference_only_real_cli_flags():
    """Every ``--flag`` in the docs must exist, so the docs cannot rot.

    Lines that name a *sibling* tool are skipped — the README's "which tool do
    I want" table legitimately quotes ``statwise --values``.
    """
    from longistat.cli import build_parser
    known = set()
    for action in build_parser()._actions:
        known.update(action.option_strings)
    for doc in ("README.md", "사용법.md", "실행.command"):
        for lineno, line in enumerate(
                open(os.path.join(ROOT, doc), encoding="utf-8"), 1):
            if any(tool in line for tool in _SIBLING_TOOLS):
                continue
            for flag in set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", line)):
                assert flag in known, f"{doc}:{lineno} 에 없는 옵션 {flag}"


@pytest.mark.parametrize("fmt", ["text", "json", "csv"])
def test_every_output_format_works_on_the_example(fmt, capsys):
    assert main([ISI, "--id", "대상", "--time", "방문", "--value", "ISI",
                 "--group", "군", "--time-order", "기저,4주,8주",
                 "--format", fmt]) == 0
    assert capsys.readouterr().out.strip()


def test_module_entrypoint_runs_as_a_subprocess():
    proc = subprocess.run(
        [sys.executable, "-m", "longistat.cli", WOWFIT, "--wide", "--id", "환자",
         "--columns", "기저,4주,8주,12주", "--brief"],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "반복측정 추이 분석 리포트" in proc.stdout


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

def _panel(values, groups=None, times=("t1", "t2", "t3")):
    return Panel(subjects=[f"S{i}" for i in range(len(values))],
                 times=list(times), values=[list(v) for v in values],
                 groups=list(groups) if groups else None)


def test_two_subjects_is_the_minimum_and_still_reports():
    a = analyze(_panel([[1.0, 2.0, 3.0], [2.0, 4.0, 5.0]]))
    text = render_text(a)
    assert "longistat" in text
    assert a.anova is not None


def test_a_single_subject_cannot_be_analysed_but_fails_politely():
    a = analyze(_panel([[1.0, 2.0, 3.0]]))
    assert a.anova is None and a.anova_error
    assert "ANOVA 미수행" in render_text(a)


def test_constant_outcome_does_not_crash_any_renderer():
    a = analyze(_panel([[5.0, 5.0, 5.0]] * 6))
    for render in (render_text, render_json, render_csv):
        assert render(a)


def test_one_group_has_a_single_member(capsys):
    p = _panel([[1.0, 2.0, 3.0], [2.0, 3.0, 4.0], [9.0, 9.0, 9.0]],
               ["A", "A", "B"])
    a = analyze(p)
    assert render_text(a)
    assert any("셀의 관측 수" in w for w in a.warnings)


def test_group_with_only_one_subject_is_rejected_by_the_anova():
    p = _panel([[1.0, 2.0, 3.0], [9.0, 8.0, 7.0]], ["A", "B"])
    a = analyze(p)
    assert a.anova is None and a.anova_error is not None
    assert render_text(a)


def test_huge_and_tiny_magnitudes_survive():
    big = _panel([[1e12, 2e12, 3e12], [1.5e12, 2.5e12, 3.5e12],
                  [2e12, 3e12, 4.4e12], [1.1e12, 2.2e12, 3.3e12]])
    assert render_text(analyze(big))
    tiny = _panel([[1e-9, 2e-9, 3e-9], [1.5e-9, 2.5e-9, 3.6e-9],
                   [2e-9, 3e-9, 4e-9], [1.1e-9, 2.4e-9, 3.3e-9]])
    assert render_text(analyze(tiny))


def test_negative_values_and_zero_baselines_are_fine():
    p = _panel([[0.0, -3.0, -5.0], [0.0, -1.0, -2.0], [0.0, -4.0, -6.0],
                [0.0, -2.0, -1.0]])
    a = analyze(p, Options(mcid=1, direction="higher"))
    assert render_text(a)


def test_more_timepoints_than_subjects_disables_sphericity_not_the_report():
    p = _panel([[1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0, 5.0, 3.0, 7.0],
                [3.0, 1.0, 4.0, 6.0, 2.0]],
               times=("a", "b", "c", "d", "e"))
    a = analyze(p)
    assert not a.anova.sphericity.epsilon_ok
    assert "구형성" in render_text(a)


def test_many_timepoints_and_subjects_run_in_reasonable_time():
    import time
    rows = [[float((i * 7 + j * 13) % 47) for j in range(8)] for i in range(400)]
    groups = ["A" if i % 2 == 0 else "B" for i in range(400)]
    started = time.time()
    a = analyze(_panel(rows, groups, times=tuple(f"t{j}" for j in range(8))))
    assert render_text(a)
    assert time.time() - started < 20.0


def test_group_labels_that_look_like_formulas_or_are_unicode(tmp_path, capsys):
    p = tmp_path / "weird.csv"
    p.write_text(
        "id,t,v,g\n" + "".join(
            f"S{i},t{j},{i + j},{'=SUM(1)' if i % 2 else '군 A'}\n"
            for i in range(6) for j in range(3)),
        encoding="utf-8")
    assert main([str(p), "--id", "id", "--time", "t", "--value", "v",
                 "--group", "g", "--format", "csv"]) == 0
    out = capsys.readouterr().out
    assert "'=SUM(1)" in out
    assert not any(line.split(",")[1].startswith("=") for line in
                   out.splitlines()[1:] if len(line.split(",")) > 1)


def test_analysis_never_mutates_the_input_panel():
    values = [[1.0, 2.0, None], [2.0, 4.0, 5.0], [3.0, 5.0, 9.0],
              [4.0, 6.0, 7.0]]
    p = _panel(values, ["A", "A", "B", "B"])
    snapshot = [list(row) for row in p.values]
    subjects = list(p.subjects)
    analyze(p, Options(mcid=1, direction="lower"))
    assert p.values == snapshot
    assert p.subjects == subjects


def test_reading_never_writes_anything(tmp_path, capsys):
    src = tmp_path / "in.csv"
    src.write_text("id,t,v\nS1,a,1\nS1,b,2\nS2,a,2\nS2,b,4\n", encoding="utf-8")
    before = set(os.listdir(tmp_path))
    assert main([str(src), "--id", "id", "--time", "t", "--value", "v"]) == 0
    capsys.readouterr()
    assert set(os.listdir(tmp_path)) == before


def test_load_helpers_reject_nonsense_paths(tmp_path):
    with pytest.raises(DataError):
        load_long(str(tmp_path / "missing.csv"), "a", "b", "c")
    with pytest.raises(DataError):
        load_wide(str(tmp_path / "missing.csv"), ["a", "b"])
