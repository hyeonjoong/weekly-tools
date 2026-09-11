# -*- coding: utf-8 -*-
"""산출물 쓰기 안전장치 — 이 저장소에서 반복해서 났던 사고들."""

import csv
import os

import pytest

from jamoscore.safeio import (FORMULA_LEADERS, OutputError, base_name,
                              ensure_out_dir, read_text_any, sanitize_cell,
                              sanitize_line, write_csv, write_text)


@pytest.mark.parametrize("leader", ["=", "+", "-", "@"])
def test_formula_injection_is_neutralised(leader):
    assert sanitize_cell(leader + "SUM(A1)").startswith("'")


@pytest.mark.parametrize("raw", ["\tHYPERLINK", "\rcmd", "\x0bX", "\x0cY"])
def test_control_leaders_are_neutralised(raw):
    """탭·CR 로 시작하는 셀도 엑셀이 수식으로 먹는다."""
    out = sanitize_cell(raw)
    assert not out.startswith(tuple(FORMULA_LEADERS))


def test_sanitize_cell_strips_newlines():
    assert "\n" not in sanitize_cell("정상\n[치명] 가짜")


def test_sanitize_cell_keeps_ordinary_text():
    assert sanitize_cell("아라") == "아라"
    assert sanitize_cell("77.8") == "77.8"
    assert sanitize_cell(None) == ""
    assert sanitize_cell(12) == "12"


def test_sanitize_line_blocks_forged_finding_rows():
    """파일 이름·셀 값에 줄바꿈을 심어 가짜 [치명] 줄을 만들 수 없어야 한다."""
    forged = "보고서\n[치명] 있지도 않은 소견"
    assert "\n" not in sanitize_line(forged)
    assert sanitize_line(forged) == "보고서[치명] 있지도 않은 소견"


def test_base_name_strips_home_directory():
    assert base_name("/Users/someone/비밀/데이터.xlsx") == "데이터.xlsx"
    assert "/" not in base_name("/a/b/c")


def test_write_text_and_read_back(tmp_path):
    target = tmp_path / "리포트.md"
    write_text(str(target), "안녕\n")
    assert target.read_text(encoding="utf-8") == "안녕\n"


def test_write_csv_has_bom_for_excel(tmp_path):
    target = tmp_path / "표.csv"
    write_csv(str(target), ["가", "나"], [["1", "2"]])
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")


def test_write_csv_roundtrip(tmp_path):
    target = tmp_path / "표.csv"
    write_csv(str(target), ["심각도", "내용"], [["치명", "값 불일치"]])
    with open(str(target), encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows == [["심각도", "내용"], ["치명", "값 불일치"]]


def test_write_refuses_symlink_target(tmp_path):
    """--out-dir 에 산출물 이름의 심볼릭 링크를 심어 입력 원본을 덮어쓰는 사고."""
    victim = tmp_path / "원본.xlsx"
    victim.write_bytes(b"ORIGINAL")
    link = tmp_path / "채점검증.md"
    os.symlink(str(victim), str(link))
    with pytest.raises(OutputError) as exc:
        write_text(str(link), "덮어쓰기")
    assert "링크" in str(exc.value)
    assert victim.read_bytes() == b"ORIGINAL"


def test_write_csv_refuses_symlink_target(tmp_path):
    victim = tmp_path / "원본.csv"
    victim.write_bytes(b"ORIGINAL")
    link = tmp_path / "문제목록.csv"
    os.symlink(str(victim), str(link))
    with pytest.raises(OutputError):
        write_csv(str(link), ["a"], [["b"]])
    assert victim.read_bytes() == b"ORIGINAL"


def test_write_refuses_hardlinked_target(tmp_path):
    """하드링크도 같은 사고를 낸다 — nlink > 1 이면 거절."""
    victim = tmp_path / "원본.md"
    victim.write_bytes(b"ORIGINAL")
    hard = tmp_path / "채점검증.md"
    os.link(str(victim), str(hard))
    with pytest.raises(OutputError) as exc:
        write_text(str(hard), "덮어쓰기")
    assert "하드링크" in str(exc.value)
    assert victim.read_bytes() == b"ORIGINAL"


def test_write_overwrites_plain_file(tmp_path):
    target = tmp_path / "채점검증.md"
    target.write_text("옛날", encoding="utf-8")
    write_text(str(target), "새것")
    assert target.read_text(encoding="utf-8") == "새것"


def test_ensure_out_dir_creates(tmp_path):
    target = tmp_path / "결과" / "깊은곳"
    made = ensure_out_dir(str(target))
    assert os.path.isdir(made)


def test_ensure_out_dir_rejects_existing_file(tmp_path):
    target = tmp_path / "결과"
    target.write_text("파일입니다", encoding="utf-8")
    with pytest.raises(OutputError) as exc:
        ensure_out_dir(str(target))
    assert "파일" in str(exc.value)


def test_ensure_out_dir_rejects_symlink(tmp_path):
    real = tmp_path / "진짜"
    real.mkdir()
    link = tmp_path / "가짜"
    os.symlink(str(real), str(link))
    with pytest.raises(OutputError) as exc:
        ensure_out_dir(str(link))
    assert "심볼릭" in str(exc.value)


def test_ensure_out_dir_rejects_empty():
    with pytest.raises(OutputError):
        ensure_out_dir("   ")


def test_ensure_out_dir_rejects_unwritable(tmp_path):
    locked = tmp_path / "잠김"
    locked.mkdir()
    os.chmod(str(locked), 0o500)
    try:
        with pytest.raises(OutputError) as exc:
            ensure_out_dir(str(locked / "하위"))
        assert "권한" in str(exc.value)
    finally:
        os.chmod(str(locked), 0o700)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949", "euc-kr"])
def test_read_text_any_handles_korean_encodings(tmp_path, encoding):
    target = tmp_path / "입력.csv"
    target.write_bytes("제시,응답\n후드,푸드\n".encode(encoding))
    assert "후드" in read_text_any(str(target))


def test_read_text_any_never_raises_on_binary(tmp_path):
    target = tmp_path / "이상한.csv"
    target.write_bytes(b"\xff\xfe\x00\x01abc")
    assert isinstance(read_text_any(str(target)), str)
