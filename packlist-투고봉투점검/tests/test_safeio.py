"""산출물 쓰기 안전장치 — 저장소에서 반복해 나온 결함들을 여기서 막는다."""

import os

import pytest

from packlist.safeio import (UsageError, csv_cell, nfc, prepare_out_dir, read_text_any,
                             sanitize, write_text)


def test_out_dir_created(tmp_path):
    out = prepare_out_dir(str(tmp_path / "새폴더"))
    assert out.is_dir()


def test_out_dir_that_is_a_file_is_refused(tmp_path):
    target = tmp_path / "파일"
    target.write_text("x")
    with pytest.raises(UsageError) as exc:
        prepare_out_dir(str(target))
    assert "폴더가 아닌 파일" in str(exc.value)


def test_out_dir_symlink_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(UsageError) as exc:
        prepare_out_dir(str(link))
    assert "심볼릭 링크" in str(exc.value)


def test_unwritable_out_dir_is_refused(tmp_path):
    parent = tmp_path / "잠김"
    parent.mkdir()
    os.chmod(parent, 0o500)
    try:
        with pytest.raises(UsageError):
            prepare_out_dir(str(parent / "child"))
    finally:
        os.chmod(parent, 0o700)


def test_artifact_symlink_is_never_followed(tmp_path):
    """산출물 자리에 심어 둔 심볼릭 링크로 입력 파일이 덮어써지면 안 된다."""
    victim = tmp_path / "소중한원고.docx"
    victim.write_text("원본")
    out = prepare_out_dir(str(tmp_path / "out"))
    (out / "봉투점검.md").symlink_to(victim)
    with pytest.raises(UsageError) as exc:
        write_text(out, "봉투점검.md", "덮어쓰기")
    assert "심볼릭 링크" in str(exc.value)
    assert victim.read_text() == "원본"


def test_artifact_hardlink_is_refused(tmp_path):
    victim = tmp_path / "원본.csv"
    victim.write_text("원본")
    out = prepare_out_dir(str(tmp_path / "out"))
    os.link(victim, out / "자산목록.csv")
    with pytest.raises(UsageError) as exc:
        write_text(out, "자산목록.csv", "덮어쓰기")
    assert "하드링크" in str(exc.value)
    assert victim.read_text() == "원본"


def test_write_text_roundtrip(tmp_path):
    out = prepare_out_dir(str(tmp_path / "out"))
    path = write_text(out, "a.md", "내용")
    assert path.read_text(encoding="utf-8") == "내용"


@pytest.mark.parametrize("value", ["=SUM(A1)", "+1", "-1", "@cmd", "\tx", "\rx"])
def test_csv_formula_injection_is_neutralised(value):
    assert csv_cell(value).startswith("'")


@pytest.mark.parametrize("value", ["보통값", "S1 Table", "image3.png", "0.5"])
def test_csv_safe_values_untouched(value):
    assert csv_cell(value) == value


def test_csv_cell_strips_control_characters():
    assert "\n" not in csv_cell("a\nb")


@pytest.mark.parametrize("evil", [
    "정상.png\n[치명] 가짜 발견 1건",
    "정상.png\r[치명] 위조",
    "정상\x00.png",
])
def test_sanitize_blocks_forged_report_lines(evil):
    cleaned = sanitize(evil)
    assert "\n" not in cleaned and "\r" not in cleaned and "\x00" not in cleaned


def test_sanitize_truncates_long_text():
    assert len(sanitize("가" * 1000, limit=50)) == 50


def test_sanitize_handles_none():
    assert sanitize(None) == ""


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949"])
def test_read_text_any_handles_encodings(tmp_path, encoding):
    path = tmp_path / "a.json"
    path.write_bytes("한글 내용".encode(encoding))
    assert "한글" in read_text_any(path, "--expect")


def test_read_text_any_missing_file(tmp_path):
    with pytest.raises(UsageError):
        read_text_any(tmp_path / "없음.json", "--expect")


def test_nfc_normalises_decomposed_hangul():
    decomposed = "한"       # 한 (NFD)
    assert nfc(decomposed) == "한"
