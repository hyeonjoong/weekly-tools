# -*- coding: utf-8 -*-
"""쓰기 — 입력을 덮어쓰는 모든 경로를 막는다."""

import os

import pytest

from conftest import write
from stalecheck import writer
from stalecheck.errors import RefusedError


# ------------------------------------------------------------ 수식 주입
@pytest.mark.parametrize("bad", ["=SUM(A1:A9)", "+1+1", "-1-1", "@SUM(1)",
                                 "\t=cmd", "\r=cmd"])
def test_formula_prefixes_quoted(bad):
    assert writer.sanitize_cell(bad).startswith("'")


@pytest.mark.parametrize("ok", ["figures/fig1.png", "2026-07-31", "한글", "0",
                                "[치명] 봉투가 구세대"])
def test_safe_cells_not_quoted(ok):
    assert not writer.sanitize_cell(ok).startswith("'")


def test_sanitize_cell_strips_newlines():
    assert "\n" not in writer.sanitize_cell("a\nb")


def test_sanitize_cell_strips_control_chars():
    assert "\x07" not in writer.sanitize_cell("a\x07b")


def test_sanitize_cell_none_is_empty():
    assert writer.sanitize_cell(None) == ""


def test_sanitize_cell_int():
    assert writer.sanitize_cell(42) == "42"


def test_sanitize_cell_float_trims_zeros():
    assert writer.sanitize_cell(3.5) == "3.5"


def test_sanitize_cell_keeps_tab_inside():
    assert "\t" in writer.sanitize_cell("a\tb")


# ------------------------------------------------------------ out-dir
def test_out_dir_created(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "새폴더"))
    assert os.path.isdir(out)


def test_out_dir_existing_file_refused(tmp_path):
    p = write(tmp_path / "파일", "x")
    with pytest.raises(RefusedError) as exc:
        writer.prepare_out_dir(p)
    assert "폴더가 아니라 파일" in exc.value.message


def test_out_dir_symlink_refused(tmp_path):
    os.makedirs(str(tmp_path / "real"))
    link = str(tmp_path / "link")
    os.symlink(str(tmp_path / "real"), link)
    with pytest.raises(RefusedError):
        writer.prepare_out_dir(link)


def test_out_dir_inside_input_refused(tmp_path):
    work = tmp_path / "논문"
    os.makedirs(str(work))
    with pytest.raises(RefusedError) as exc:
        writer.prepare_out_dir(str(work / "리포트"), forbidden_roots=[str(work)])
    assert "입력 폴더 안" in exc.value.message


def test_out_dir_equal_to_input_refused(tmp_path):
    work = tmp_path / "논문"
    os.makedirs(str(work))
    with pytest.raises(RefusedError):
        writer.prepare_out_dir(str(work), forbidden_roots=[str(work)])


def test_out_dir_sibling_of_input_allowed(tmp_path):
    work = tmp_path / "논문"
    os.makedirs(str(work))
    out = writer.prepare_out_dir(str(tmp_path / "논문_리포트"),
                                 forbidden_roots=[str(work)])
    assert os.path.isdir(out)


def test_out_dir_unwritable_refused(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root 는 권한을 무시합니다")
    base = tmp_path / "잠김"
    os.makedirs(str(base))
    os.chmod(str(base), 0o500)
    try:
        with pytest.raises(RefusedError):
            writer.prepare_out_dir(str(base / "하위"))
    finally:
        os.chmod(str(base), 0o700)


def test_out_dir_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = writer.prepare_out_dir("~/리포트")
    assert out.startswith(str(tmp_path))


# ------------------------------------------------------------ 산출물 쓰기
def test_write_text_roundtrip(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_text(out, "a.md", "내용\n")
    assert open(p, encoding="utf-8").read() == "내용\n"


def test_write_text_mode_is_private(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_text(out, "a.md", "x")
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


def test_write_csv_has_bom(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_csv(out, "a.csv", ("h",), [("v",)])
    assert open(p, "rb").read().startswith(b"\xef\xbb\xbf")


def test_write_csv_sanitizes_cells(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_csv(out, "a.csv", ("h",), [("=1+1",)])
    assert "'=1+1" in open(p, encoding="utf-8-sig").read()


def test_write_csv_sanitizes_header(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_csv(out, "a.csv", ("=evil",), [])
    assert open(p, encoding="utf-8-sig").read().startswith("'=evil")


def test_symlink_at_artifact_path_refused(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    victim = write(tmp_path / "소중한_입력.py", "원본")
    os.symlink(victim, os.path.join(out, "세대점검.md"))
    with pytest.raises(RefusedError) as exc:
        writer.write_text(out, "세대점검.md", "덮어씀")
    assert "심볼릭 링크" in exc.value.message
    assert open(victim, encoding="utf-8").read() == "원본"


def test_hardlink_at_artifact_path_refused(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    victim = write(tmp_path / "소중한_입력.py", "원본")
    os.link(victim, os.path.join(out, "세대불일치.csv"))
    with pytest.raises(RefusedError) as exc:
        writer.write_csv(out, "세대불일치.csv", ("h",), [])
    assert "하드 링크" in exc.value.message
    assert open(victim, encoding="utf-8").read() == "원본"


def test_dangling_symlink_refused(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    os.symlink(str(tmp_path / "없는파일"), os.path.join(out, "a.md"))
    with pytest.raises(RefusedError):
        writer.write_text(out, "a.md", "x")


def test_directory_at_artifact_path_refused(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    os.makedirs(os.path.join(out, "a.md"))
    with pytest.raises(RefusedError):
        writer.write_text(out, "a.md", "x")


@pytest.mark.parametrize("name", ["../탈출.md", "a/b.md", "..", "."])
def test_path_traversal_in_artifact_name_refused(tmp_path, name):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    with pytest.raises(RefusedError):
        writer.write_text(out, name, "x")


def test_existing_regular_file_is_overwritten(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    writer.write_text(out, "a.md", "첫번째")
    writer.write_text(out, "a.md", "두번째")
    assert open(os.path.join(out, "a.md"), encoding="utf-8").read() == "두번째"


def test_csv_rows_are_lf_terminated(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_csv(out, "a.csv", ("h",), [("1",), ("2",)])
    assert b"\r\n" not in open(p, "rb").read()


def test_write_csv_empty_rows(tmp_path):
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_csv(out, "a.csv", ("a", "b"), [])
    assert open(p, encoding="utf-8-sig").read().strip() == "a,b"


def test_csv_contains_exactly_what_was_written(tmp_path):
    """넣지 않은 절대 경로가 없다고 주장해 봐야 통과가 보장된다 —
    대신 **넣은 것과 정확히 같은지**를 본다."""
    out = writer.prepare_out_dir(str(tmp_path / "out"))
    p = writer.write_csv(out, "a.csv", ("경로",), [("figures/f.png",)])
    text = open(p, encoding="utf-8-sig").read()
    assert text == "경로\nfigures/f.png\n"
    assert str(tmp_path) not in text
