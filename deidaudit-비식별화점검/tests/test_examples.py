"""번들 예제에 대한 회귀 테스트 — 규칙이 넓어지면 여기서 먼저 깨집니다."""

from __future__ import annotations

from deidaudit.audit import run_audit
from deidaudit.cli import EXIT_CRITICAL, EXIT_OK, run
from deidaudit.findings import CRITICAL, WARNING
from deidaudit.safety import file_sha256


def test_clean_example_is_completely_silent(examples_dir, capsys):
    """깨끗한 합성 파일에 치명·경고가 하나라도 나오면 규칙이 넓은 것입니다."""
    code = run([str(examples_dir / "깨끗한_분석용.csv"), "--quasi", "age_group,sex"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "[치명] 없음" in out and "[경고] 없음" in out


def test_clean_example_reaches_target_k(examples_dir):
    result = run_audit([examples_dir / "깨끗한_분석용.csv"], ["age_group", "sex"], ["subject_id"], 10**9, 5)
    assert len(result.k_results) == 1
    assert result.k_results[0].min_k == 5
    assert result.k_results[0].unit == "사람"


def test_dirty_example_finds_every_expected_kind(examples_dir, capsys):
    code = run([str(examples_dir / "수면일기_원본.csv"), str(examples_dir / "UT로그.xlsx"),
                "--quasi", "birth,sex,visit_date"])
    out = capsys.readouterr().out
    assert code == EXIT_CRITICAL
    for expected in [
        "성명", "휴대전화", "생년월일", "주민등록번호(체크섬 통과)",
        "숨김 시트", "숨김 열", "셀 주석", "문서 메타데이터(작성자)", "정의된 이름",
        "정확 날짜 열", "자유텍스트 내 인명",
    ]:
        assert expected in out, expected


def test_examples_are_never_modified(examples_dir, tmp_path, capsys):
    files = sorted(examples_dir.glob("*.csv")) + sorted(examples_dir.glob("*.xlsx"))
    before = {p: file_sha256(p) for p in files}
    run([str(p) for p in files] + ["--quasi", "birth,sex,visit_date", "--link-id", "subject_id",
                                   "--pseudonymize", "--shift-dates",
                                   "--drop-columns", "name,phone,birth",
                                   "--out-dir", str(tmp_path / "out"),
                                   "--key-out", str(tmp_path / "보안" / "키.csv"), "--salt", "t"])
    capsys.readouterr()
    for path, digest in before.items():
        assert file_sha256(path) == digest, path.name


def test_hidden_sheet_rrn_passes_checksum(examples_dir):
    """예제의 주민등록번호는 체크섬을 통과해야 치명 경로를 실제로 시험합니다."""
    result = run_audit([examples_dir / "UT로그.xlsx"], [], ["subject_id"], 10**9, 5)
    kinds = [f.kind for f in result.findings]
    assert "주민등록번호(체크섬 통과)" in kinds


def test_example_rrn_cannot_be_a_real_issued_number(examples_dir):
    """생년 88 + 성별자리 3 → 2088년 출생. 현실에서 발급될 수 없는 번호입니다."""
    from deidaudit.detect import rrn_checksum_ok, rrn_date_ok

    digits = "8804023123454"
    assert rrn_checksum_ok(digits) and rrn_date_ok(digits)
    assert int(digits[6]) in (3, 4)  # 2000년대 출생 자리


def test_free_text_columns_are_confessed(examples_dir):
    result = run_audit(
        [examples_dir / "수면일기_원본.csv", examples_dir / "UT로그.xlsx"], [], ["subject_id"], 10**9, 5
    )
    names = {col for _, col, _ in result.coverage.free_text_columns}
    assert {"비고", "자유응답"} <= names


def test_repeated_measures_diary_counts_people_not_rows(examples_dir):
    result = run_audit([examples_dir / "수면일기_원본.csv"], ["birth", "sex"], ["subject_id"], 10**9, 5)
    k = result.k_results[0]
    assert k.unit == "사람"
    assert k.n_units == 8      # 24행이지만 8명
    assert k.min_k == 1
