"""안전 장치 — CSV 수식 인젝션, 경로 순회, 원본 덮어쓰기 방지."""

import os

import pytest

from conftest import make_rows, write_csv
from robustcheck.safety import (
    OutputPathError,
    assert_not_input,
    csv_safe,
    prepare_out_dir,
    safe_join,
)


# ------------------------------------------------------- CSV 수식 인젝션


@pytest.mark.parametrize("cell", [
    "=cmd|'/c calc'!A0",
    "+1+1",
    "@SUM(A1)",
    "-cmd|'/c calc'",
    "\t=1+1",
    "\r=1+1",
])
def test_dangerous_cells_are_neutralised(cell):
    assert csv_safe(cell).startswith("'")


@pytest.mark.parametrize("cell", [
    "-0.71", "+1.5", "-3", "0.043", "1e-5", "-1.2e3", "12",
])
def test_plain_numbers_are_left_alone(cell):
    assert csv_safe(cell) == cell


def test_text_cells_are_untouched():
    assert csv_safe("이상치=±3SD") == "이상치=±3SD"
    assert csv_safe("S001") == "S001"


def test_empty_and_none_cells():
    assert csv_safe("") == ""
    assert csv_safe(None) == ""


def test_non_string_cells_are_stringified():
    assert csv_safe(12) == "12"
    assert csv_safe(-0.5) == "-0.5"


def test_formula_disguised_as_number_prefix_is_escaped():
    assert csv_safe("-1+cmd").startswith("'")
    assert csv_safe("=1").startswith("'")


# ------------------------------------------------------------ 경로 안전


def test_prepare_out_dir_creates_directory(tmp_path):
    target = tmp_path / "결과" / "깊이"
    resolved = prepare_out_dir(str(target))
    assert os.path.isdir(resolved)


def test_prepare_out_dir_rejects_file(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(OutputPathError):
        prepare_out_dir(str(path))


def test_prepare_out_dir_rejects_null_byte():
    with pytest.raises(OutputPathError):
        prepare_out_dir("bad\x00dir")


def test_prepare_out_dir_rejects_empty():
    with pytest.raises(OutputPathError):
        prepare_out_dir("")


@pytest.mark.parametrize("name", [
    "../탈출.csv", "../../etc/passwd", "a/../../b.csv", "/절대/경로.csv",
])
def test_safe_join_blocks_traversal(tmp_path, name):
    out = prepare_out_dir(str(tmp_path / "out"))
    with pytest.raises(OutputPathError):
        safe_join(out, name)


def test_safe_join_allows_plain_names(tmp_path):
    out = prepare_out_dir(str(tmp_path / "out"))
    assert safe_join(out, "견고성점검.md").startswith(out)


def test_safe_join_rejects_null_byte(tmp_path):
    out = prepare_out_dir(str(tmp_path / "out"))
    with pytest.raises(OutputPathError):
        safe_join(out, "a\x00b.csv")


def test_safe_join_rejects_empty_name(tmp_path):
    out = prepare_out_dir(str(tmp_path / "out"))
    with pytest.raises(OutputPathError):
        safe_join(out, "")


# --------------------------------------------------------- 원본 보호


def test_output_cannot_overwrite_input(tmp_path):
    source = write_csv(tmp_path / "data.csv", make_rows(6))
    with pytest.raises(OutputPathError):
        assert_not_input([source], [source])


def test_output_detects_symlinked_input(tmp_path):
    source = write_csv(tmp_path / "data.csv", make_rows(6))
    link = tmp_path / "link.csv"
    os.symlink(source, link)
    with pytest.raises(OutputPathError):
        assert_not_input([str(link)], [source])


def test_output_detects_hardlinked_input(tmp_path):
    source = write_csv(tmp_path / "data.csv", make_rows(6))
    hard = tmp_path / "hard.csv"
    os.link(source, hard)
    with pytest.raises(OutputPathError):
        assert_not_input([str(hard)], [source])


def test_unrelated_paths_are_allowed(tmp_path):
    source = write_csv(tmp_path / "data.csv", make_rows(6))
    assert_not_input([str(tmp_path / "out" / "리포트.md")], [source])


def test_missing_input_is_ignored(tmp_path):
    assert_not_input([str(tmp_path / "a.md")], [str(tmp_path / "없음.csv")])
