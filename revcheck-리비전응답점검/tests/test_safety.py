"""안전: 네트워크 0, CSV 수식 인젝션, 심볼릭 링크, 원본 보호, 정보 유출."""

from __future__ import annotations

import ast
import csv
import os
import unicodedata
from pathlib import Path

import pytest

from revcheck import report
from revcheck.docio import document_from_text
from revcheck.engine import Options, run_check
from revcheck.inventory import CITECHECK_HEADER
from revcheck.report import ISSUES_CSV, OutputError, csv_safe, write_outputs

PACKAGE = Path(__file__).resolve().parent.parent / "revcheck"
FORBIDDEN = {
    "socket", "ssl", "http", "urllib", "urllib2", "urllib3", "requests",
    "ftplib", "telnetlib", "smtplib", "xmlrpc", "asyncio", "subprocess",
    "os", "importlib", "ctypes", "webbrowser", "multiprocessing", "shutil",
    "tempfile", "pickle",
}
# 동적으로 모듈을 불러오거나 명령을 실행하는 우회로.
# 이름만으로 부르는 내장 함수와, 모듈에 붙여 부르는 함수를 나눠 본다
# (``re.compile`` 은 무해하지만 내장 ``compile()`` 은 아니다).
FORBIDDEN_BUILTINS = {"__import__", "eval", "exec", "compile"}
FORBIDDEN_METHODS = {
    "system", "popen", "import_module", "spawnv", "spawnl", "execv", "execve",
    "fork", "check_output", "urlopen", "connect",
}


def _sources():
    """하위 폴더까지 전부 본다 — ``glob('*.py')`` 는 revcheck/net/ 을 놓친다."""
    return sorted(PACKAGE.rglob("*.py"))


def test_no_network_or_subprocess_imports_anywhere_in_the_package():
    """정적 검증: 이 툴은 네트워크를 쓰지 않는다 — import 자체가 없다."""
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in FORBIDDEN:
                    offenders.append(f"{path.name}:{node.lineno} import {name}")
    assert not offenders, offenders


def test_no_dynamic_import_or_command_execution():
    """``__import__("so"+"cket")`` 같은 우회로도 막는다."""
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
                offenders.append(f"{path.name}:{node.lineno} {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_METHODS:
                offenders.append(f"{path.name}:{node.lineno} .{func.attr}()")
    assert not offenders, offenders


def test_runtime_network_is_never_touched(tmp_path, monkeypatch):
    """정적 검사를 우회하더라도, 실제로 소켓을 열면 여기서 터진다."""
    import socket

    def forbidden(*_args, **_kwargs):  # pragma: no cover - 터지면 테스트 실패
        raise AssertionError("이 툴은 네트워크를 쓰지 않는다")

    for attr in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, attr, forbidden)

    from conftest import EXAMPLES
    from revcheck.cli import main

    for folder, suffix in ((EXAMPLES / "clean", ".md"), (EXAMPLES / "docx", ".docx")):
        main([
            "--old", str(folder / f"제출본{suffix}"),
            "--new", str(folder / f"개정본{suffix}"),
            "--response", str(folder / f"응답서{suffix}"),
            "--out-dir", str(tmp_path / folder.name),
            "--quiet",
        ])


def test_package_never_opens_a_file_for_writing_outside_report_module():
    """원본 원고를 덮어쓰는 사고를 구조적으로 막는다 — 쓰기는 report.py 에만 있다."""
    writers = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == "report.py":
            continue
        source = path.read_text(encoding="utf-8")
        for marker in ('open(', '.write_text(', '.write_bytes('):
            if marker in source:
                writers.append(f"{path.name}: {marker}")
    # docio 의 open() 은 'rb' 전용이다 — 모드까지 확인한다.
    for entry in writers:
        assert entry.startswith("docio.py"), entry
    docio_source = (PACKAGE / "docio.py").read_text(encoding="utf-8")
    assert 'open(path, "rb")' in docio_source
    assert '"w"' not in docio_source and "'w'" not in docio_source


@pytest.mark.parametrize(
    "value,expected_prefix",
    [
        ("=HYPERLINK(\"http://evil\",\"x\")", "'="),
        ("+cmd|' /c calc'!A1", "'+"),
        ("-2+3+cmd", "'-"),
        ("@SUM(A1)", "'@"),
    ],
)
def test_csv_formula_injection_is_neutralised(value, expected_prefix):
    assert csv_safe(value).startswith(expected_prefix)


def test_plain_negative_numbers_are_left_alone():
    assert csv_safe("-3.5") == "-3.5"
    assert csv_safe(-3) == "-3"


def test_control_characters_never_reach_the_csv():
    assert "\x1b" not in csv_safe("safe\x1b[2Jtext")
    assert "\n" not in csv_safe("two\nlines")


def _result_with_manuscript_text(text: str):
    old = document_from_text("# T\n\n## Results\n\nBaseline text here.\n", "md", "old.md")
    new = document_from_text(f"# T\n\n## Results\n\n{text}\n", "md", "new.md")
    resp = document_from_text(
        "Comment 1-1: A point.\nResponse: We have revised the Results paragraph as suggested.\n"
        "Comment 1-2: Another.\nResponse: We have revised the Methods paragraph as suggested.\n"
        "Comment 1-3: Third.\nResponse: We have revised the Discussion paragraph as suggested.\n",
        "md",
        "resp.md",
        split_lines=True,
    )
    return run_check(old, new, resp, Options())


def test_manuscript_derived_cells_are_escaped_in_every_csv(tmp_path):
    """원고 문장을 그대로 싣는 변경목록.csv·추가문헌.csv 까지 전부 본다."""
    result = _result_with_manuscript_text(
        "=cmd|' /c calc'!A1 and -2+3+cmd and @SUM(A1) was the observed value 9.9"
    )
    write_outputs(result, tmp_path, result.exit_code())
    escaped = 0
    for name in (ISSUES_CSV, report.CHANGES_CSV, report.ADDED_REFS_CSV):
        with open(tmp_path / name, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        for row in rows:
            for cell in row:
                if cell.startswith("'"):
                    escaped += 1
                    continue
                assert not cell.startswith(("=", "+", "-", "@", "\t", "\r")) or (
                    cell.lstrip("-").replace(".", "", 1).isdigit()
                ), (name, cell)
    assert escaped, "이스케이프된 셀이 하나도 없다 — 테스트가 빈 파일을 통과시켰다"


def test_symlinked_output_file_is_refused(tmp_path):
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    target = tmp_path / "somewhere_else.md"
    target.write_text("original", encoding="utf-8")
    (out_dir / report.REPORT_MD).symlink_to(target)
    result = _result_with_manuscript_text("A revised sentence with value 5.5.")
    with pytest.raises(OutputError) as exc:
        write_outputs(result, out_dir, result.exit_code())
    assert "심볼릭" in str(exc.value)
    assert target.read_text(encoding="utf-8") == "original"


def test_output_may_not_overwrite_an_input_file(tmp_path):
    manuscript = tmp_path / report.REPORT_MD
    manuscript.write_text("# 내 원고\n\n본문\n", encoding="utf-8")
    result = _result_with_manuscript_text("A revised sentence with value 5.5.")
    with pytest.raises(OutputError) as exc:
        write_outputs(result, tmp_path, result.exit_code(), sources=[manuscript])
    assert "원고" in str(exc.value)
    assert manuscript.read_text(encoding="utf-8").startswith("# 내 원고")


def test_manuscript_text_cannot_steer_the_output_path(tmp_path):
    """원고 안의 ``../`` 문자열이 파일 경로가 되는 일은 없다."""
    out_dir = tmp_path / "결과"
    result = _result_with_manuscript_text("../../etc/passwd was changed to 7.7 units.")
    written = write_outputs(result, out_dir, result.exit_code())
    assert all(path.parent == out_dir.resolve() for path in written)
    assert not (tmp_path.parent / "passwd").exists()


def test_input_files_are_never_modified(tmp_path, run_cli):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    resp = tmp_path / "resp.md"
    old.write_text("# T\n\n## Results\n\nISI fell by 5.2 points.\n", encoding="utf-8")
    new.write_text("# T\n\n## Results\n\nISI fell by 5.8 points.\n", encoding="utf-8")
    resp.write_text(
        "Comment 1-1: A point.\nResponse: We have revised the Results paragraph as suggested.\n"
        "Comment 1-2: Another.\nResponse: We have revised the Methods paragraph as suggested.\n"
        "Comment 1-3: Third.\nResponse: We have revised the Discussion paragraph as suggested.\n",
        encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in (old, new, resp)}
    run_cli("--old", old, "--new", new, "--response", resp, "--out-dir", tmp_path / "결과")
    for path, data in before.items():
        assert path.read_bytes() == data


def test_output_never_overwrites_an_input_via_hard_link(tmp_path):
    """경로 문자열이 달라도 **같은 파일**이면 덮어쓰면 안 된다(하드링크)."""
    manuscript = tmp_path / "원고.md"
    manuscript.write_text("# 내 원고\n\n미공개 본문\n", encoding="utf-8")
    out_dir = tmp_path / "결과"
    out_dir.mkdir()
    link = out_dir / report.REPORT_MD
    os.link(manuscript, link)
    result = _result_with_manuscript_text("A revised sentence with value 5.5.")
    with pytest.raises(OutputError):
        write_outputs(result, out_dir, result.exit_code(), sources=[manuscript])
    assert manuscript.read_text(encoding="utf-8").startswith("# 내 원고")


def test_output_never_overwrites_an_input_named_with_decomposed_hangul(tmp_path):
    """macOS 는 자모가 분리된(NFD) 파일명을 만든다 — 문자열 비교로는 못 막는다."""
    nfd_name = unicodedata.normalize("NFD", report.REPORT_MD)
    manuscript = tmp_path / nfd_name
    manuscript.write_text("# 내 원고\n\n미공개 본문\n", encoding="utf-8")
    result = _result_with_manuscript_text("A revised sentence with value 5.5.")
    try:
        with pytest.raises(OutputError):
            write_outputs(result, tmp_path, result.exit_code(), sources=[manuscript])
    finally:
        pass
    assert manuscript.read_text(encoding="utf-8").startswith("# 내 원고")


def test_symlinked_output_directory_is_refused(tmp_path):
    """결과 폴더 자체가 링크면, 사용자가 보지 않는 곳에 원고 문장이 적힌다."""
    real = tmp_path / "실제폴더"
    real.mkdir()
    link = tmp_path / "링크폴더"
    link.symlink_to(real, target_is_directory=True)
    result = _result_with_manuscript_text("A revised sentence with value 5.5.")
    with pytest.raises(OutputError) as exc:
        write_outputs(result, link, result.exit_code())
    assert "심볼릭" in str(exc.value)
    assert not list(real.iterdir())


def test_unwritable_output_directory_is_a_clean_error(tmp_path):
    out_dir = tmp_path / "읽기전용"
    out_dir.mkdir()
    out_dir.chmod(0o555)
    result = _result_with_manuscript_text("A revised sentence with value 5.5.")
    try:
        with pytest.raises(OutputError):
            write_outputs(result, out_dir, result.exit_code())
    finally:
        out_dir.chmod(0o755)


def test_undecidable_run_still_writes_documented_csv_headers(tmp_path):
    """판정불가로 일찍 끝나도 열 이름이 달라지면 다음 단계가 조용히 깨진다."""
    from revcheck.model import Result

    result = Result()
    result.undecidable = "번호 체계를 잡지 못했습니다"
    write_outputs(result, tmp_path, 3)
    with open(tmp_path / report.CHANGES_CSV, encoding="utf-8-sig", newline="") as fh:
        assert next(csv.reader(fh)) == report.CHANGE_HEADER
    with open(tmp_path / report.ADDED_REFS_CSV, encoding="utf-8-sig", newline="") as fh:
        assert next(csv.reader(fh)) == CITECHECK_HEADER
