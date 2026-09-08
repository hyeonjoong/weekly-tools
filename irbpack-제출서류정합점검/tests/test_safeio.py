"""출력 안전 — 입력을 절대 덮어쓰지 않는다."""
import os

import pytest

from irbpack import safeio


def test_out_dir_cannot_be_input_folder(tmp_path, clean_safeio):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    safeio.protect_inputs([str(f)])
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir(str(tmp_path))


def test_out_dir_cannot_be_input_file(tmp_path, clean_safeio):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    safeio.protect_inputs([str(f)])
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir(str(f))


def test_out_dir_symlink_refused(tmp_path, clean_safeio):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(str(real), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlink 불가")
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir(str(link))


def test_out_dir_created(tmp_path, clean_safeio):
    out = safeio.prepare_out_dir(str(tmp_path / "새폴더" / "하위"))
    assert os.path.isdir(out)


def test_empty_out_dir_refused(clean_safeio):
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir("  ")


def test_write_refuses_symlink_target(tmp_path, clean_safeio):
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    victim = tmp_path / "victim.md"
    victim.write_text("원본", encoding="utf-8")
    try:
        os.symlink(str(victim), os.path.join(out, "정합점검.md"))
    except (OSError, NotImplementedError):
        pytest.skip("symlink 불가")
    with pytest.raises(safeio.OutputError):
        safeio.write_text(out, "정합점검.md", "덮어쓰기")
    assert victim.read_text(encoding="utf-8") == "원본"


def test_write_refuses_hardlink_target(tmp_path, clean_safeio):
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    victim = tmp_path / "victim.md"
    victim.write_text("원본", encoding="utf-8")
    try:
        os.link(str(victim), os.path.join(out, "정합점검.md"))
    except (OSError, NotImplementedError):
        pytest.skip("hardlink 불가")
    with pytest.raises(safeio.OutputError):
        safeio.write_text(out, "정합점검.md", "덮어쓰기")
    assert victim.read_text(encoding="utf-8") == "원본"


def test_write_refuses_protected_input(tmp_path, clean_safeio):
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    victim = tmp_path / "out" / "정합점검.md"
    victim.write_text("입력", encoding="utf-8")
    safeio.protect_inputs([str(victim)])
    with pytest.raises(safeio.OutputError):
        safeio.write_text(out, "정합점검.md", "x")


def test_write_refuses_path_traversal(tmp_path, clean_safeio):
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    with pytest.raises(safeio.OutputError):
        safeio.write_text(out, "../정합점검.md", "x")


def test_written_file_is_owner_only(tmp_path, clean_safeio):
    out = safeio.prepare_out_dir(str(tmp_path / "out"))
    p = safeio.write_text(out, "a.md", "x")
    if os.name == "posix":
        assert oct(os.stat(p).st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("raw,expect", [("=1+1", "'=1+1"), ("+x", "'+x"), ("-x", "'-x"), ("@x", "'@x"), ("a\nb", "a b"), ("a\tb", "a b"), ("a\x07b", "a�b"), (None, "")])
def test_sanitize_cell(raw, expect):
    assert safeio.sanitize_cell(raw) == expect


def test_sanitize_cell_truncates():
    assert safeio.sanitize_cell("x" * 5000).endswith("(잘림)")


def test_sanitize_line():
    assert safeio.sanitize_line("a\r\nb\x00c") == "a␤b�c"


def test_nfd_and_case_variants_are_protected(tmp_path, clean_safeio):
    import unicodedata
    f = tmp_path / "동의서.md"
    f.write_text("x", encoding="utf-8")
    safeio.protect_inputs([str(f)])
    nfd = os.path.join(str(tmp_path), unicodedata.normalize("NFD", "동의서.md"))
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir(nfd)


def test_out_dir_symlink_with_trailing_slash_refused(tmp_path, clean_safeio):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(str(real), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlink 불가")
    with pytest.raises(safeio.OutputError):
        safeio.prepare_out_dir(str(link) + "/")
