"""CLI — 종료코드, 출력 폴더, 읽기 전용 보장, 인자 검증."""

from __future__ import annotations

import json
import os

import pytest

from conftest import EXAMPLES
from numcheck.cli import main

CLEAN = str(EXAMPLES / "clean_manuscript.md")
FLAWED = str(EXAMPLES / "flawed_manuscript.md")
SERENE = str(EXAMPLES / "serene_style.docx")


def test_clean_example_exits_zero(capsys):
    assert main([CLEAN]) == 0
    assert "종료코드 0" in capsys.readouterr().out


def test_flawed_example_exits_one(capsys):
    assert main([FLAWED]) == 1
    out = capsys.readouterr().out
    assert "치명" in out


def test_docx_example_runs(capsys):
    assert main([SERENE]) == 1
    assert "GRIM" in capsys.readouterr().out


def test_out_dir_creates_three_files(tmp_path, capsys):
    out = tmp_path / "검토"
    assert main([FLAWED, "--out-dir", str(out)]) == 1
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(["문제목록.csv", "재계산표.csv", "요약.txt"])
    assert "출력:" in capsys.readouterr().out


def test_manuscript_file_is_never_modified(tmp_path, capsys):
    source = tmp_path / "원고.md"
    source.write_text("## Results\n반응자는 23/48 (45.2%) 이었다.\n", encoding="utf-8")
    before = source.read_bytes()
    mtime = source.stat().st_mtime_ns
    main([str(source), "--out-dir", str(tmp_path / "out")])
    assert source.read_bytes() == before
    assert source.stat().st_mtime_ns == mtime
    capsys.readouterr()


def test_out_dir_that_is_a_file_is_refused(tmp_path, capsys):
    blocker = tmp_path / "notadir"
    blocker.write_text("x", encoding="utf-8")
    assert main([CLEAN, "--out-dir", str(blocker)]) == 3
    assert "출력 폴더" in capsys.readouterr().err


def test_quiet_prints_one_line(capsys):
    main([CLEAN, "--quiet"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert "종료코드" in out[0]


def test_dump_text_shows_sections(capsys):
    assert main([CLEAN, "--dump-text"]) == 0
    out = capsys.readouterr().out
    assert "Results" in out and "Abstract" in out


def test_lang_en(capsys):
    main([CLEAN, "--lang", "en"])
    assert "candidates" in capsys.readouterr().out


def test_scale_option_enables_grim(tmp_path, capsys):
    source = tmp_path / "m.md"
    source.write_text("## Results\n단어인지도 평균은 62.40% (N = 7) 이었다.\n"
                      "반응자 23/48 (47.9%), 14/23 (60.9%), 6/23 (26.1%), "
                      "24/48 (50.0%), 12/48 (25.0%).\n", encoding="utf-8")
    code = main([str(source), "--scale", "단어인지도=0:100:50",
                 "--percent-of-count", "단어인지도"])
    assert code == 1
    assert "GRIM" in capsys.readouterr().out


def test_percent_of_count_without_scale_is_an_error(capsys):
    assert main([CLEAN, "--percent-of-count", "없는척도"]) == 3
    assert "정의되지 않" in capsys.readouterr().err


def test_scale_config_file(tmp_path, capsys):
    config = tmp_path / "scales.json"
    config.write_text(json.dumps({"WRSX": {"min": 0, "max": 100, "items": 50,
                                           "percent_of_count": True}}),
                      encoding="utf-8")
    source = tmp_path / "m.md"
    source.write_text("## Results\nWRSX 평균은 62.40 (N = 7) 이었다.\n"
                      "반응자 23/48 (47.9%), 14/23 (60.9%), 6/23 (26.1%), "
                      "24/48 (50.0%), 12/48 (25.0%).\n", encoding="utf-8")
    assert main([str(source), "--scale-config", str(config)]) == 1
    assert "WRSX" in capsys.readouterr().out


def test_bad_scale_config_exits_three(tmp_path, capsys):
    assert main([CLEAN, "--scale-config", str(tmp_path / "nope.json")]) == 3
    assert "척도 설정" in capsys.readouterr().err


def test_missing_manuscript_exits_three(tmp_path, capsys):
    assert main([str(tmp_path / "nope.md")]) == 3
    assert "읽을 수 없습니다" in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    ["--alpha", "0"], ["--alpha", "1"], ["--alpha", "-0.1"],
    ["--tolerance", "0"], ["--tolerance", "9"], ["--min-checked", "-1"],
])
def test_out_of_range_arguments_are_rejected(args, capsys):
    """인자 오류는 **3** 이다. 2 는 '경고만 있음'이라 CI 가 통과로 읽는다."""
    with pytest.raises(SystemExit) as excinfo:
        main([CLEAN] + args)
    assert excinfo.value.code == 3
    capsys.readouterr()


def test_tolerance_narrower_finds_at_least_as_much(tmp_path):
    """k = 0.5 는 반올림만 허용하므로 지적 **건수**가 줄어서는 안 된다."""
    from numcheck.engine import analyze
    from numcheck.options import Options
    source = tmp_path / "m.md"
    source.write_text(
        "## Results\n23/48 (47.8%), 14/23 (60.8%), 6/23 (26.0%), "
        "24/48 (49.9%), 12/48 (24.9%) 였다.\n", encoding="utf-8")
    loose = len(analyze(source, Options(k=1.0)).findings)
    tight = len(analyze(source, Options(k=0.5)).findings)
    assert tight > loose


def test_no_quote_flag(capsys):
    main([FLAWED, "--no-quote"])
    assert "원문 생략" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "numcheck" in capsys.readouterr().out


_FORBIDDEN_IMPORTS = {
    "socket", "ssl", "urllib", "http", "requests", "httpx", "ftplib", "smtplib",
    "telnetlib", "asyncio", "subprocess", "multiprocessing", "webbrowser",
    "ctypes", "tempfile", "shutil", "os",
}


def _package_modules():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "numcheck"
    return sorted(root.glob("*.py"))


def test_no_network_or_process_imports_anywhere_in_the_package():
    """네트워크 0 · 프로세스 실행 0 — **AST 로** 확인한다.

    예전 판은 모듈 __dict__ 의 값에서 __module__ 을 찾는 방식이었는데, 그건
    `import socket` 을 전혀 감지하지 못했다(실제로 심어 보니 통과했다).
    여기서는 import 문 자체를 파싱하므로 어떤 형태로 넣어도 걸린다.
    """
    import ast
    offenders = []
    for path in _package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                if name.split(".")[0] in _FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.name}:{node.lineno} import {name}")
    assert offenders == [], offenders


def test_no_dynamic_execution_in_the_package():
    """eval/exec/__import__ 로 우회할 여지도 두지 않는다."""
    import ast
    offenders = []
    for path in _package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
                    node.func.id in {"eval", "exec", "compile", "__import__", "open"}:
                # open 은 docio 의 읽기 전용 한 곳만 허용한다
                if node.func.id == "open" and path.name == "docio.py":
                    continue
                offenders.append(f"{path.name}:{node.lineno} {node.func.id}()")
    assert offenders == [], offenders


def test_the_only_open_call_is_read_only():
    """원고를 쓰기 모드로 여는 경로가 존재하지 않는다."""
    import ast
    import pathlib
    docio = pathlib.Path(__file__).resolve().parent.parent / "numcheck" / "docio.py"
    tree = ast.parse(docio.read_text(encoding="utf-8"))
    modes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open":
            assert len(node.args) >= 2, "open() 에 모드를 명시하세요"
            modes.append(ast.literal_eval(node.args[1]))
    assert modes == ["rb"]


def test_output_directory_is_the_only_thing_written(tmp_path, capsys):
    source = tmp_path / "원고.md"
    source.write_text("## Results\n23/48 (45.2%).\n", encoding="utf-8")
    out = tmp_path / "결과"
    before = set(os.listdir(tmp_path))
    main([str(source), "--out-dir", str(out)])
    capsys.readouterr()
    after = set(os.listdir(tmp_path))
    assert after - before == {"결과"}
