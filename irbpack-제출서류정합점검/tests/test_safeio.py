"""출력 안전 — 링크 추적, CSV 수식 인젝션, PII 마스킹, 경로·파일명 위조."""

import csv
import os

import pytest

from irbpack.safeio import (OutputError, mask_pii, prepare_out_dir, safe_cell, safe_line,
                            safe_name, write_csv, write_text)


# ------------------------------------------------------------------ 마스킹

def test_mask_mobile_number():
    """가운데 자리는 첫 숫자만 남긴다 — 전부 가리면 다른 번호가 같아 보인다(라운드2 B-5)."""
    assert mask_pii("연락처: 010-9278-6844") == "연락처: 010-9***-6844"


def test_two_different_numbers_stay_distinguishable():
    assert mask_pii("010-1234-5678") != mask_pii("010-9999-5678")


def test_mask_office_number():
    assert mask_pii("전화 02-2135-2365") == "전화 02-2***-2365"


def test_mask_number_without_hyphen():
    assert "***" in mask_pii("전화 01092786844")


def test_mask_international_number():
    assert "***" in mask_pii("+82-10-9278-6844")


@pytest.mark.parametrize("raw,leak", [
    ("담당자 주민등록번호 890312–1234567", "1234567"),      # en 대시 (워드 자동고침)
    ("예비 890312.7654321", "7654321"),
    ("연락 010‑1234‑5678", "1234"),                        # 비분리 하이픈
    ("해외 82-10-1234-5678", "1234-5678"),
    ("대표 1588-1234", "1588-1234"),
])
def test_mask_covers_dash_variants(raw, leak):
    """canon() 이 en 대시를 '~' 로 바꾼 뒤라 마스킹이 안 걸리던 유출 (라운드2 B-1)."""
    from irbpack.normalize import canon
    assert leak not in mask_pii(canon(raw))


def test_mask_email_keeps_domain():
    assert mask_pii("snubhirb@snubh.org") == "sn****@snubh.org"


def test_mask_short_email_local():
    assert mask_pii("a@b.org").startswith("a****@")


def test_mask_resident_registration_number():
    """주민번호는 생년월일까지 가린다 — 리포트는 팀에 공유되는 파일이다."""
    assert mask_pii("880101-1234567") == "******-1******"


def test_mask_leaves_ordinary_numbers():
    assert mask_pii("총 90명, 3년간 보관") == "총 90명, 3년간 보관"


def test_mask_none_and_empty():
    assert mask_pii("") == ""
    assert mask_pii(None) is None


def test_mask_applies_to_report_lines():
    assert "***" in safe_line("문의 010-1111-2222")


# ------------------------------------------------------------------ CSV 인젝션

@pytest.mark.parametrize("payload", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=1+1", "  =1+1"])
def test_csv_formula_injection_is_quoted(payload):
    """앞의 공백·탭으로 감춘 수식도 막는다."""
    assert safe_cell(payload).startswith("'")


def test_csv_normal_cell_untouched():
    assert safe_cell("만 5~12세") == "만 5~12세"


def test_csv_cell_strips_newline():
    assert "\n" not in safe_cell("가\n[치명] 가짜")


def test_csv_cell_masks_pii():
    assert safe_cell("010-1234-5678") == "010-1***-5678"


def test_csv_cell_handles_none():
    assert safe_cell(None) == ""


# ------------------------------------------------------------------ 파일명 위조

def test_safe_name_uses_basename_only():
    assert safe_name("/Users/someone/secret/연구계획서.docx") == "연구계획서.docx"


def test_safe_name_strips_newline_injection():
    assert "\n" not in safe_name("a\n[치명] 가짜 항목.docx")


def test_safe_name_replaces_pipe():
    assert "|" not in safe_name("a|b.docx")


def test_safe_name_truncates():
    assert len(safe_name("가" * 300 + ".docx")) <= 120


def test_safe_name_empty():
    assert safe_name("") == "(이름 없음)"


# ------------------------------------------------------------------ 출력 경로

def test_prepare_out_dir_creates(tmp_path):
    target = prepare_out_dir(str(tmp_path / "새폴더"))
    assert os.path.isdir(target)


def test_prepare_out_dir_existing_file(tmp_path):
    path = tmp_path / "결과"
    path.write_text("x")
    with pytest.raises(OutputError) as error:
        prepare_out_dir(str(path))
    assert "파일이 이미" in str(error.value)


def test_prepare_out_dir_symlink_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(str(real), str(link))
    with pytest.raises(OutputError) as error:
        prepare_out_dir(str(link))
    assert "심볼릭" in str(error.value)


def test_prepare_out_dir_permission_denied(tmp_path):
    parent = tmp_path / "잠긴폴더"
    parent.mkdir()
    os.chmod(str(parent), 0o500)
    try:
        with pytest.raises(OutputError):
            prepare_out_dir(str(parent / "안쪽"))
    finally:
        os.chmod(str(parent), 0o700)


def test_write_text_refuses_symlinked_artifact(tmp_path):
    """--out-dir 에 산출물 이름의 심볼릭 링크를 심어 원본을 덮어쓰는 사고 차단."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    victim = tmp_path / "원본.docx"
    victim.write_text("소중한 원본")
    os.symlink(str(victim), str(out_dir / "정합점검.md"))
    with pytest.raises(OutputError) as error:
        write_text(str(out_dir), "정합점검.md", "새 내용")
    assert "심볼릭" in str(error.value)
    assert victim.read_text() == "소중한 원본"


def test_write_csv_refuses_symlinked_artifact(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    victim = tmp_path / "원본.csv"
    victim.write_text("원본")
    os.symlink(str(victim), str(out_dir / "불일치목록.csv"))
    with pytest.raises(OutputError):
        write_csv(str(out_dir), "불일치목록.csv", ["a"], [["b"]])
    assert victim.read_text() == "원본"


def test_write_text_refuses_hardlink(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    victim = tmp_path / "원본.md"
    victim.write_text("원본")
    os.link(str(victim), str(out_dir / "정합점검.md"))
    with pytest.raises(OutputError) as error:
        write_text(str(out_dir), "정합점검.md", "새 내용")
    assert "하드링크" in str(error.value)
    assert victim.read_text() == "원본"


def test_write_text_overwrites_own_artifact(tmp_path):
    out_dir = str(tmp_path)
    write_text(out_dir, "정합점검.md", "1회차")
    path = write_text(out_dir, "정합점검.md", "2회차")
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "2회차"


def test_write_csv_has_bom_for_excel(tmp_path):
    path = write_csv(str(tmp_path), "불일치목록.csv", ["항목"], [["값"]])
    with open(path, "rb") as handle:
        assert handle.read(3) == b"\xef\xbb\xbf"


def test_write_csv_roundtrip(tmp_path):
    path = write_csv(str(tmp_path), "a.csv", ["항목", "값"], [["연령", "만 5~12세"]])
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows == [["항목", "값"], ["연령", "만 5~12세"]]
