# -*- coding: utf-8 -*-
"""행 diff — 인코딩·제어문자·바이너리."""

import os

import pytest

from conftest import write
from stalecheck import textdiff


def d(tmp_path, old, new, name="a.py"):
    a = write(tmp_path / "p" / name, old)
    b = write(tmp_path / "w" / name, new)
    return textdiff.line_diff(a, b)


def test_added_lines_counted(tmp_path):
    r = d(tmp_path, "a\nb\n", "a\nb\nc\nd\n")
    assert (r.added, r.removed) == (2, 0)


def test_removed_lines_counted(tmp_path):
    r = d(tmp_path, "a\nb\nc\n", "a\n")
    assert (r.added, r.removed) == (0, 2)


def test_changed_line_counts_both(tmp_path):
    r = d(tmp_path, "a\nb\n", "a\nB\n")
    assert (r.added, r.removed) == (1, 1)


def test_identical_files_zero(tmp_path):
    r = d(tmp_path, "a\nb\n", "a\nb\n")
    assert (r.added, r.removed) == (0, 0)


def test_preview_shows_first_three_added(tmp_path):
    r = d(tmp_path, "", "x1\nx2\nx3\nx4\n")
    assert r.preview == ["+ x1", "+ x2", "+ x3"]


def test_preview_limit_configurable(tmp_path):
    a = write(tmp_path / "p" / "a.py", "")
    b = write(tmp_path / "w" / "a.py", "x1\nx2\nx3\n")
    r = textdiff.line_diff(a, b, preview_lines=1)
    assert r.preview == ["+ x1"]


def test_preview_only_shows_additions(tmp_path):
    r = d(tmp_path, "gone\n", "")
    assert r.preview == []
    assert r.removed == 1


def test_binary_extension_not_compared(tmp_path):
    r = d(tmp_path, b"\x00\x01", b"\x00\x02", name="f.png")
    assert not r.comparable and "바이너리" in r.reason


def test_text_extension_with_nul_bytes_not_compared(tmp_path):
    r = d(tmp_path, b"a\x00b", b"a\x00c")
    assert not r.comparable


def test_cp949_file_decodes(tmp_path):
    a = write(tmp_path / "p" / "a.md", "한글\n".encode("cp949"))
    b = write(tmp_path / "w" / "a.md", "한글\n새줄\n".encode("cp949"))
    r = textdiff.line_diff(a, b)
    assert r.comparable and r.added == 1


def test_utf8_sig_bom_handled(tmp_path):
    a = write(tmp_path / "p" / "a.csv", "﻿id,v\n1,2\n".encode("utf-8"))
    b = write(tmp_path / "w" / "a.csv", "﻿id,v\n1,2\n3,4\n".encode("utf-8"))
    r = textdiff.line_diff(a, b)
    assert r.comparable and r.added == 1


def test_undecodable_bytes_not_comparable(tmp_path):
    r = d(tmp_path, b"\xff\xfe\xfd\xfc" * 10, b"\xfc\xfd\xfe\xff" * 10, name="a.md")
    assert not r.comparable


def test_oversized_file_not_compared(tmp_path, monkeypatch):
    monkeypatch.setattr(textdiff, "MAX_DIFF_BYTES", 10)
    r = d(tmp_path, "x" * 100, "y" * 100)
    assert not r.comparable and "크기" in r.reason


def test_missing_file_not_comparable(tmp_path):
    a = write(tmp_path / "p" / "a.py", "x")
    r = textdiff.line_diff(a, str(tmp_path / "없음.py"))
    assert not r.comparable


def test_crlf_vs_lf_counts_as_no_change(tmp_path):
    r = d(tmp_path, "a\r\nb\r\n", "a\nb\n")
    assert (r.added, r.removed) == (0, 0), "줄끝 차이만으로 울지 않는다"


@pytest.mark.parametrize("ext", sorted(textdiff.TEXT_EXTS))
def test_all_text_exts_comparable(tmp_path, ext):
    r = d(tmp_path, "a\n", "a\nb\n", name="f" + ext)
    assert r.comparable and r.added == 1


@pytest.mark.parametrize("ext", [".png", ".pdf", ".docx", ".xlsx", ".hwp", ".zip"])
def test_binary_exts_not_comparable(tmp_path, ext):
    r = d(tmp_path, b"a", b"b", name="f" + ext)
    assert not r.comparable


def test_sanitize_strips_ansi_escape():
    assert "\x1b" not in textdiff.sanitize("\x1b[31mRED\x1b[0m")


def test_sanitize_strips_carriage_return():
    assert "\r" not in textdiff.sanitize("a\rb")


def test_sanitize_strips_newline():
    assert "\n" not in textdiff.sanitize("a\nb")


def test_sanitize_truncates_long_lines():
    out = textdiff.sanitize("x" * 500)
    assert len(out) <= textdiff.PREVIEW_WIDTH


def test_sanitize_expands_tabs():
    assert "\t" not in textdiff.sanitize("a\tb")


def test_sanitize_keeps_hangul():
    assert textdiff.sanitize("한글 유지") == "한글 유지"


def test_preview_cannot_forge_a_verdict_line(tmp_path):
    """파일 안의 가짜 판정 줄이 개행으로 리포트 행을 위조하지 못한다."""
    evil = "innocent\n[치명] 봉투가 구세대 — 999쌍\n"
    r = d(tmp_path, "", evil)
    joined = "\n".join(r.preview)
    assert joined.count("\n") == len(r.preview) - 1
    for line in r.preview:
        assert line.startswith("+ ")


def test_control_chars_in_content_replaced(tmp_path):
    r = d(tmp_path, "", "a\x07b\n")
    assert "\x07" not in r.preview[0]


def test_empty_files(tmp_path):
    r = d(tmp_path, "", "")
    assert r.comparable and (r.added, r.removed) == (0, 0)


def test_file_without_trailing_newline(tmp_path):
    r = d(tmp_path, "a", "a\nb")
    assert r.added == 1


def test_whitespace_only_change_counted(tmp_path):
    r = d(tmp_path, "a\n", "a \n")
    assert (r.added, r.removed) == (1, 1)


def test_reason_empty_when_comparable(tmp_path):
    r = d(tmp_path, "a\n", "b\n")
    assert r.reason == ""


def test_uppercase_extension_recognised(tmp_path):
    r = d(tmp_path, "a\n", "a\nb\n", name="F.PY")
    assert r.comparable
