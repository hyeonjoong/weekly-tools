"""경로 안전성과 CSV 수식 인젝션 방어."""

from __future__ import annotations

import os

import pytest

from deidaudit.safety import (
    PathSafetyError,
    ensure_key_outside,
    ensure_not_input,
    ensure_output_target,
    file_sha256,
    is_within,
    safe_output_name,
    sanitize_cell,
    write_csv,
)


def test_key_inside_out_dir_is_rejected(tmp_path):
    out_dir = tmp_path / "내보내기"
    out_dir.mkdir()
    with pytest.raises(PathSafetyError):
        ensure_key_outside(out_dir / "키.csv", out_dir)
    with pytest.raises(PathSafetyError):
        ensure_key_outside(out_dir / "하위" / "키.csv", out_dir)


def test_key_outside_out_dir_is_allowed(tmp_path):
    out_dir = tmp_path / "내보내기"
    out_dir.mkdir()
    ensure_key_outside(tmp_path / "보안" / "키.csv", out_dir)


def test_symlink_cannot_smuggle_key_into_out_dir(tmp_path):
    """`--out-dir/링크` 가 밖을 가리켜도, 반대로 밖의 링크가 안을 가리켜도 막아야 합니다."""
    out_dir = tmp_path / "내보내기"
    out_dir.mkdir()
    secret_dir = tmp_path / "보안"
    secret_dir.mkdir()
    link = secret_dir / "링크"
    os.symlink(out_dir, link)
    with pytest.raises(PathSafetyError):
        ensure_key_outside(link / "키.csv", out_dir)


def test_symlinked_out_dir_still_matches(tmp_path):
    real_dir = tmp_path / "실제"
    real_dir.mkdir()
    link = tmp_path / "링크"
    os.symlink(real_dir, link)
    assert is_within(real_dir / "a.csv", link)


def test_output_outside_out_dir_is_rejected(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(PathSafetyError):
        ensure_output_target(tmp_path / "밖.csv", out_dir)
    assert ensure_output_target(out_dir / "안.csv", out_dir)


def test_output_cannot_overwrite_input(tmp_path):
    src = tmp_path / "입력.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(PathSafetyError):
        ensure_not_input(src, [src])


def test_formula_injection_is_escaped():
    assert sanitize_cell("=SUM(A1:A9)") == "'=SUM(A1:A9)"
    assert sanitize_cell("+1+1") == "'+1+1"
    assert sanitize_cell("@import") == "'@import"
    assert sanitize_cell("\tcmd") == "'\tcmd"
    assert sanitize_cell("-cmd|calc") == "'-cmd|calc"


def test_negative_numbers_are_not_mangled():
    """내보내기 사본의 음수를 조용히 바꾸면 분석이 깨집니다."""
    for value in ["-3.5", "-1", "-1e9", "-0.25", "3.5", "0"]:
        assert sanitize_cell(value) == value


def test_write_csv_sanitizes(tmp_path):
    path = tmp_path / "out.csv"
    write_csv(path, ["a"], [["=1+1"], ["-4.2"]])
    text = path.read_text(encoding="utf-8-sig")
    assert "'=1+1" in text
    assert "-4.2" in text and "'-4.2" not in text


def test_safe_output_name_strips_path_separators():
    assert "/" not in safe_output_name("../../etc/passwd")
    assert "\\" not in safe_output_name("a\\b")
    assert ".." not in safe_output_name("..hidden")


def test_file_hash_changes_only_on_change(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("a", encoding="utf-8")
    first = file_sha256(path)
    assert file_sha256(path) == first
    path.write_text("b", encoding="utf-8")
    assert file_sha256(path) != first
