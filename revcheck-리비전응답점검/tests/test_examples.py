"""번들 예제 3종 세트 — 거짓 양성 방어와 심어 둔 결함 검출을 함께 지킨다."""

from __future__ import annotations

import os
import stat

import pytest

from conftest import EXAMPLES, ROOT

CLEAN = EXAMPLES / "clean"
FLAWED = EXAMPLES / "flawed"
DOCX = EXAMPLES / "docx"


def _args(folder, old="제출본", new="개정본", resp="응답서", suffix=".md"):
    return (
        "--old", folder / f"{old}{suffix}",
        "--new", folder / f"{new}{suffix}",
        "--response", folder / f"{resp}{suffix}",
    )


# ── 거짓 양성 방어 ─────────────────────────────────────────────────────────


def test_clean_example_has_no_criticals_and_no_warnings(run_cli):
    """응답서대로 정확히 개정한 세트에서 치명이 뜨면 이 툴은 소음이다."""
    code, out = run_cli(*_args(CLEAN))
    assert code == 0, out
    assert "[치명 0건]" in out and "[경고 0건]" in out


def test_clean_docx_example_has_no_criticals(run_cli):
    """워드풍 .docx(런 쪼개짐·굽은 따옴표·변경내용 추적)에서도 치명 0건."""
    code, out = run_cli(*_args(DOCX, suffix=".docx"))
    assert "[치명 0건]" in out, out
    assert code in (0, 2)  # .docx 줄 번호 확인불가 경고만 남는다


def test_docx_example_announces_tracked_changes_at_the_top(run_cli):
    _code, out = run_cli(*_args(DOCX, suffix=".docx"))
    head = out.split("\n\n")[0]
    assert "변경내용 추적" in head and "수락" in head


# ── 심어 둔 결함 4가지 ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def flawed_output():
    from revcheck.cli import main
    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main([str(a) for a in _args(FLAWED)])
    return code, buffer.getvalue()


def test_flawed_example_exits_one(flawed_output):
    code, _out = flawed_output
    assert code == 1


def test_flawed_example_catches_the_missing_comment(flawed_output):
    _code, out = flawed_output
    assert "코멘트 2-3 에 대한 응답 블록이 없습니다" in out
    assert "2-2 다음이 2-4" in out


def test_flawed_example_catches_the_quote_number_mismatch(flawed_output):
    _code, out = flawed_output
    assert "숫자가 다릅니다" in out
    assert "42 participants" in out and "45 participants" in out


def test_flawed_example_catches_the_silent_number_change(flawed_output):
    _code, out = flawed_output
    assert "5.2" in out and "5.8" in out
    assert "숫자가 다른 값으로 바뀌었습니다." in out
    assert "연결되지 않은 수정입니다" in out


def test_flawed_example_catches_the_reference_count_mismatch(flawed_output):
    _code, out = flawed_output
    assert "참고문헌 3편 추가라고 했으나 실제 증가는 2편" in out


def test_flawed_example_catches_the_out_of_range_line_reference(flawed_output):
    _code, out = flawed_output
    assert "개정본 범위를 벗어납니다" in out


def test_flawed_example_catches_the_unverifiable_claim(flawed_output):
    _code, out = flawed_output
    assert "변경 주장을 기계로 확인할 수단이 없습니다" in out


def test_flawed_example_reports_exactly_three_criticals(flawed_output):
    """소음이 늘면 안 된다 — 심어 둔 치명 3건 그대로여야 한다."""
    _code, out = flawed_output
    assert "[치명 3건]" in out


def test_coverage_block_is_always_printed(flawed_output):
    _code, out = flawed_output
    assert "[커버리지 자백]" in out
    assert "리뷰어 코멘트 식별" in out
    assert "인용 문구 대조" in out
    assert "변경 문단" in out


# ── 하우스 구조 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "README.md", "사용법.md", "실행.command", "HARDENING.md",
        "pyproject.toml", "LICENSE", ".gitignore",
    ],
)
def test_house_files_exist(name):
    assert (ROOT / name).exists(), name


def test_run_command_is_executable_and_uses_the_examples():
    script = ROOT / "실행.command"
    assert os.stat(script).st_mode & stat.S_IXUSR
    text = script.read_text(encoding="utf-8")
    assert "examples/flawed" in text
    assert "cd \"$(dirname \"$0\")\"" in text
    assert "엔터" in text


def test_readme_has_the_house_order_and_the_boundary_table():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("## 목적", "## 설치", "## 사용법", "## 한계", "numcheck", "draftcheck", "citecheck"):
        assert needle in text, needle
    assert text.index("## 목적") < text.index("## 설치") < text.index("## 사용법")


def test_pyproject_declares_no_dependencies():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in text
    assert 'requires-python = ">=3.9"' in text
    assert "revcheck = " in text  # 콘솔 스크립트 등록


def test_readme_output_block_matches_the_real_output(flawed_output):
    """README 에 붙여 둔 '실제 출력'이 오래되면 그것도 결함이다."""
    _code, out = flawed_output
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("### 실제 출력")
    block = text[text.index("```", start) + 3 : text.index("```", text.index("```", start) + 3)]
    pasted = [
        line for line in block.splitlines()
        if line.strip() and not line.startswith(("$ revcheck", "           --response"))
    ]
    live = out.splitlines()
    for line in pasted:
        if line.endswith("저장"):  # --out-dir 을 준 실행에서만 나오는 줄
            continue
        assert line in live, f"README 의 이 줄이 실제 출력에 없습니다: {line}"
