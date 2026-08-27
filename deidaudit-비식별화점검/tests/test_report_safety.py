"""리포트 자체가 유출 경로가 되지 않는지 — 첫 커밋부터 못 박아 둡니다.

원문 식별자 문자열이 산출물 어디에도 등장하지 않아야 합니다.
(내보내기 사본은 예외 — 그건 데이터 자체입니다.)
"""

from __future__ import annotations

from pathlib import Path

from deidaudit.cli import run

from .conftest import write_csv_file

RAW_NAME = "김현중"
RAW_NAME2 = "이서연"
RAW_PHONE = "010-2345-6789"
RAW_EMAIL = "hong.gildong@bell.co.kr"
RAW_RRN = "880402-3123454"
RAW_FREE_TEXT = "새벽에 깨서 최민아 간호사한테 얘기함"
RAW_BIRTH = "1988-04-02"

REPORT_FILES = ("점검결과.md", "문제목록.csv", "재식별위험.csv", "비식별화_요약.md", "내보낸사본_문제목록.csv")


def _make_source(tmp_path: Path) -> Path:
    return write_csv_file(
        tmp_path / "원본.csv",
        ["subject_id", "name", "phone", "email", "주민등록번호", "birth", "sex", "visit_date", "비고"],
        [
            ["S01", RAW_NAME, RAW_PHONE, RAW_EMAIL, RAW_RRN, RAW_BIRTH, "M", "2026-03-14", RAW_FREE_TEXT],
            ["S02", RAW_NAME2, "010-9876-5432", "a@b.kr", "911127-4123456", "1991-11-27", "F", "2026-03-21", "특이사항 없음"],
        ],
    )


def test_no_raw_identifier_appears_in_any_report(tmp_path, capsys):
    src = _make_source(tmp_path)
    out_dir = tmp_path / "out"
    run([str(src), "--quasi", "birth,sex,visit_date", "--pseudonymize", "--shift-dates",
         "--drop-columns", "name,phone,email,주민등록번호,birth",
         "--out-dir", str(out_dir), "--key-out", str(tmp_path / "보안" / "키.csv"), "--salt", "t"])
    capsys.readouterr()
    reports = tmp_path / "out_점검리포트"

    forbidden = [RAW_NAME, RAW_NAME2, RAW_PHONE, "2345-6789", RAW_EMAIL, "gildong",
                 RAW_RRN, "3123454", "최민아", "새벽에 깨서"]
    for name in REPORT_FILES:
        path = reports / name
        assert path.exists(), name
        text = path.read_text(encoding="utf-8-sig")
        for needle in forbidden:
            assert needle not in text, f"{name} 에 원문 '{needle}' 이 남아 있습니다"


def test_console_output_contains_no_raw_identifier(tmp_path, capsys):
    src = _make_source(tmp_path)
    run([str(src), "--quasi", "birth,sex"])
    out = capsys.readouterr().out
    for needle in [RAW_NAME, RAW_PHONE, RAW_EMAIL, RAW_RRN, "최민아", "새벽에 깨서"]:
        assert needle not in out


def test_masked_evidence_still_identifies_the_location(tmp_path, capsys):
    src = _make_source(tmp_path)
    run([str(src), "--quasi", "birth,sex"])
    out = capsys.readouterr().out
    assert "010-****-**89" in out
    assert "김○○" in out
    assert "phone 열" in out


def test_risk_csv_never_contains_equivalence_class_values(tmp_path, capsys):
    """동치류의 실제 값(생년+성별 조합)을 쓰면 그 파일 자체가 재식별 자료가 됩니다."""
    src = _make_source(tmp_path)
    out_dir = tmp_path / "out"
    run([str(src), "--quasi", "birth,sex,visit_date", "--out-dir", str(out_dir)])
    capsys.readouterr()
    text = (out_dir / "재식별위험.csv").read_text(encoding="utf-8-sig")
    assert RAW_BIRTH not in text
    assert "2026-03-14" not in text
    assert "birth + sex + visit_date" in text  # 열 이름은 남습니다


def test_key_file_is_the_only_place_with_original_ids(tmp_path, capsys):
    src = _make_source(tmp_path)
    out_dir = tmp_path / "out"
    key = tmp_path / "보안" / "키.csv"
    run([str(src), "--pseudonymize", "--out-dir", str(out_dir), "--key-out", str(key), "--salt", "t"])
    capsys.readouterr()
    assert "S01" in key.read_text(encoding="utf-8-sig")
    exported = (out_dir / "내보내기" / "원본.csv").read_text(encoding="utf-8-sig")
    assert "S01" not in exported


HEADERLESS_ROWS = [
    "홍길동,010-1234-5678,1988-03-05,hong@snu.ac.kr,880305-1234567",
    "김서연,010-2222-3333,1975-07-21,kim@snu.ac.kr,750721-2345678",
    "박민수,010-4444-5555,1969-11-02,park@snu.ac.kr,691102-1456789",
]


def test_headerless_csv_leaks_nothing_into_reports(tmp_path, capsys):
    """헤더 없이 저장된 CSV 는 첫 행이 '열 이름'이 됩니다 — 리포트 전체에 남으면 안 됩니다."""
    src = tmp_path / "명단.csv"
    src.write_text("\n".join(HEADERLESS_ROWS) + "\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    run([str(src), "--out-dir", str(out_dir)])
    printed = capsys.readouterr().out
    blobs = [printed]
    for path in out_dir.rglob("*"):
        if path.is_file():
            blobs.append(path.read_text(encoding="utf-8-sig", errors="replace"))
    for needle in ["홍길동", "010-1234-5678", "hong@snu.ac.kr", "880305-1234567", "1988-03-05"]:
        for blob in blobs:
            assert needle not in blob, needle


def test_column_hint_in_error_messages_is_masked_too(tmp_path, capsys):
    src = tmp_path / "명단.csv"
    src.write_text("\n".join(HEADERLESS_ROWS) + "\n", encoding="utf-8")
    run([str(src), "--quasi", "없는열"])
    err = capsys.readouterr().err
    assert "사용 가능한 열" in err
    for needle in ["홍길동", "010-1234-5678", "hong@snu.ac.kr", "1988-03-05"]:
        assert needle not in err


def test_sheet_name_with_pii_never_becomes_a_shipped_filename(tmp_path, capsys):
    """엑셀 시트 이름은 사람이 짓습니다 — 그대로 두면 보낼 폴더의 파일 이름이 됩니다."""
    from .xlsx_builder import Sheet, build_xlsx

    path = build_xlsx(
        tmp_path / "코호트.xlsx",
        [
            Sheet(name="김철수 010-9876-5432",
                  rows=[["subject_id", "visit_date", "score"], ["S001", "2026-01-05", 7], ["S002", "2026-01-06", 8]]),
            Sheet(name="숨김_정하늘 주민 880305-1234567", rows=[["a"], ["1"]], hidden=True),
        ],
    )
    out_dir = tmp_path / "out"
    run([str(path), "--pseudonymize", "--link-id", "subject_id",
         "--out-dir", str(out_dir), "--key-out", str(tmp_path / "보안" / "키.csv")])
    printed = capsys.readouterr().out
    exported = [p.name for p in (out_dir / "내보내기").iterdir()]
    assert exported == ["코호트__시트1.csv"]
    blobs = [printed] + [
        p.read_text(encoding="utf-8-sig", errors="replace")
        for p in (tmp_path / "out_점검리포트").rglob("*") if p.is_file()
    ]
    for needle in ["김철수", "010-9876-5432", "정하늘", "880305-1234567"]:
        for blob in blobs:
            assert needle not in blob, needle
