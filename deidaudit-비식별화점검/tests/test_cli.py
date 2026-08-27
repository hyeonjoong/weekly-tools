"""CLI 종료코드와 경계 강제 장치."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deidaudit.cli import EXIT_CRITICAL, EXIT_OK, EXIT_UNDETERMINED, EXIT_USAGE, run
from deidaudit.safety import file_sha256

from .xlsx_builder import Sheet, build_xlsx


def test_clean_file_exits_zero_with_no_findings(clean_csv, capsys):
    code = run([str(clean_csv), "--quasi", "age_group,sex"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "[치명] 없음" in out
    assert "[경고] 없음" in out


def test_dirty_file_exits_one(dirty_csv, capsys):
    code = run([str(dirty_csv)])
    out = capsys.readouterr().out
    assert code == EXIT_CRITICAL
    assert "휴대전화" in out and "성명" in out


def test_audit_only_creates_no_files(dirty_csv, tmp_path, capsys):
    before = sorted(p.name for p in tmp_path.iterdir())
    run([str(dirty_csv), "--audit-only"])
    capsys.readouterr()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_inputs_are_never_modified(dirty_csv, tmp_path, capsys):
    digest = file_sha256(dirty_csv)
    run([str(dirty_csv), "--quasi", "birth,sex", "--pseudonymize", "--shift-dates",
         "--out-dir", str(tmp_path / "out"), "--key-out", str(tmp_path / "키.csv")])
    capsys.readouterr()
    assert file_sha256(dirty_csv) == digest


def test_merge_flags_are_rejected_with_pointer_to_joinaudit(dirty_csv, capsys):
    for flag in ("--merge", "--join", "--on=id", "--how", "--concat"):
        code = run([str(dirty_csv), flag])
        err = capsys.readouterr().err
        assert code == EXIT_USAGE
        assert "joinaudit" in err


def test_pseudonymize_requires_key_out(dirty_csv, tmp_path, capsys):
    code = run([str(dirty_csv), "--pseudonymize", "--out-dir", str(tmp_path / "out")])
    assert code == EXIT_USAGE
    assert "--key-out" in capsys.readouterr().err


def test_pseudonymize_requires_out_dir(dirty_csv, capsys):
    code = run([str(dirty_csv), "--pseudonymize"])
    assert code == EXIT_USAGE
    assert "--out-dir" in capsys.readouterr().err


def test_key_out_inside_out_dir_is_rejected(dirty_csv, tmp_path, capsys):
    out_dir = tmp_path / "out"
    code = run([str(dirty_csv), "--pseudonymize", "--out-dir", str(out_dir),
                "--key-out", str(out_dir / "내보내기" / "키.csv")])
    assert code == EXIT_USAGE
    assert "--out-dir" in capsys.readouterr().err
    assert not (out_dir / "내보내기").exists()


def test_key_out_via_symlink_into_out_dir_is_rejected(dirty_csv, tmp_path, capsys):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    link = tmp_path / "링크"
    os.symlink(out_dir, link)
    code = run([str(dirty_csv), "--pseudonymize", "--out-dir", str(out_dir),
                "--key-out", str(link / "키.csv")])
    assert code == EXIT_USAGE
    assert "--key-out" in capsys.readouterr().err


def test_unknown_quasi_column_is_rejected(dirty_csv, capsys):
    code = run([str(dirty_csv), "--quasi", "birtdh"])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "--quasi" in err and "사용 가능한 열" in err


def test_unknown_drop_column_is_rejected(dirty_csv, tmp_path, capsys):
    code = run([str(dirty_csv), "--drop-columns", "nmae", "--out-dir", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "--drop-columns" in err


def test_unreadable_file_yields_undetermined(tmp_path, dirty_csv, capsys):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"\x00\x01 not a workbook")
    code = run([str(dirty_csv), str(broken)])
    out = capsys.readouterr().out
    assert code == EXIT_UNDETERMINED
    assert "건너뜀" in out


def test_undetermined_wins_over_critical(tmp_path, dirty_csv, capsys):
    """치명이 있어도 다 못 봤으면 3 이 우선합니다."""
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"nope")
    code = run([str(dirty_csv), str(broken)])
    capsys.readouterr()
    assert code == EXIT_UNDETERMINED


def test_export_is_refused_when_undetermined(tmp_path, dirty_csv, capsys):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"nope")
    out_dir = tmp_path / "out"
    code = run([str(dirty_csv), str(broken), "--pseudonymize",
                "--out-dir", str(out_dir), "--key-out", str(tmp_path / "키.csv")])
    capsys.readouterr()
    assert code == EXIT_UNDETERMINED
    assert not (out_dir / "내보내기").exists()


def test_missing_input_file(tmp_path, capsys):
    code = run([str(tmp_path / "없는파일.csv")])
    assert code == EXIT_USAGE
    assert "없습니다" in capsys.readouterr().err


def test_directory_input_is_rejected(tmp_path, capsys):
    code = run([str(tmp_path)])
    assert code == EXIT_USAGE
    assert "폴더" in capsys.readouterr().err


def test_no_arguments_shows_help(capsys):
    assert run([]) == EXIT_USAGE
    assert "deidaudit" in capsys.readouterr().out


def test_audit_only_conflicts_with_export(dirty_csv, tmp_path, capsys):
    code = run([str(dirty_csv), "--audit-only", "--pseudonymize",
                "--out-dir", str(tmp_path / "o"), "--key-out", str(tmp_path / "k.csv")])
    assert code == EXIT_USAGE


def test_export_creates_expected_files(dirty_csv, tmp_path, capsys):
    out_dir = tmp_path / "out"
    run([str(dirty_csv), "--quasi", "birth,sex", "--pseudonymize", "--shift-dates",
         "--drop-columns", "name,phone", "--out-dir", str(out_dir),
         "--key-out", str(tmp_path / "보안" / "키.csv"), "--salt", "t"])
    capsys.readouterr()
    # 보낼 폴더에는 사본만 있습니다.
    assert {p.name for p in out_dir.iterdir()} == {"내보내기"}
    reports = tmp_path / "out_점검리포트"
    names = {p.name for p in reports.iterdir()}
    assert {"점검결과.md", "문제목록.csv", "재식별위험.csv", "비식별화_요약.md"} <= names
    exported = (out_dir / "내보내기" / "dirty.csv").read_text(encoding="utf-8-sig")
    assert "김현중" not in exported and "010-2345-6789" not in exported
    assert (tmp_path / "보안" / "키.csv").exists()
    assert (tmp_path / "보안" / "키_솔트.txt").exists()


def test_reports_are_written_outside_the_shipped_folder(dirty_csv, tmp_path, capsys):
    """리포트의 행 번호는 사본의 행과 1:1 로 맞습니다 — 함께 나가면 가명이 풀립니다."""
    out_dir = tmp_path / "보낼폴더"
    run([str(dirty_csv), "--drop-columns", "name,phone", "--out-dir", str(out_dir)])
    capsys.readouterr()
    shipped = {p.name for p in out_dir.rglob("*")}
    assert "문제목록.csv" not in shipped
    assert "점검결과.md" not in shipped
    assert (tmp_path / "보낼폴더_점검리포트" / "문제목록.csv").exists()


def test_report_dir_inside_out_dir_is_rejected(dirty_csv, tmp_path, capsys):
    out_dir = tmp_path / "out"
    code = run([str(dirty_csv), "--drop-columns", "name", "--out-dir", str(out_dir),
                "--report-dir", str(out_dir / "리포트")])
    assert code == EXIT_USAGE
    assert "--report-dir" in capsys.readouterr().err


def test_reports_are_owner_only(dirty_csv, tmp_path, capsys):
    out_dir = tmp_path / "out"
    run([str(dirty_csv), "--out-dir", str(out_dir)])
    capsys.readouterr()
    for name in ("점검결과.md", "문제목록.csv"):
        assert oct((out_dir / name).stat().st_mode & 0o777) == "0o600"


def test_pseudonymize_without_a_recognisable_id_column_is_refused(tmp_path, capsys):
    """ID 열을 못 찾으면 원본 ID 가 그대로 나가면서 'exit 0' 이 나오던 자리입니다."""
    from .conftest import write_csv_file

    src = write_csv_file(tmp_path / "로그.csv", ["참가자코드", "점수"], [["KHJ-1988", "3"], ["LSY-1991", "4"]])
    out_dir = tmp_path / "out"
    code = run([str(src), "--pseudonymize", "--out-dir", str(out_dir), "--key-out", str(tmp_path / "키.csv")])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "피험자 ID 열을 찾지 못했습니다" in err and "참가자코드" in err
    assert not (out_dir / "내보내기").exists()


def test_partial_link_id_match_still_refuses(tmp_path, capsys):
    """한 파일만 매칭돼도 나머지 파일은 원본 ID 로 나갑니다."""
    from .conftest import write_csv_file

    a = write_csv_file(tmp_path / "표1.csv", ["subject_id", "v"], [["S01", "1"]])
    b = write_csv_file(tmp_path / "표2.csv", ["참가자코드", "v"], [["KHJ-1988", "1"]])
    code = run([str(a), str(b), "--link-id", "subject_id", "--pseudonymize",
                "--out-dir", str(tmp_path / "out"), "--key-out", str(tmp_path / "키.csv")])
    assert code == EXIT_USAGE
    assert "표2.csv" in capsys.readouterr().err


def test_same_basename_inputs_do_not_overwrite_each_other(tmp_path, capsys):
    from .conftest import write_csv_file

    (tmp_path / "siteA").mkdir()
    (tmp_path / "siteB").mkdir()
    a = write_csv_file(tmp_path / "siteA" / "data.csv", ["subject_id", "v"], [["S01", "1"], ["S02", "2"]])
    b = write_csv_file(tmp_path / "siteB" / "data.csv", ["subject_id", "v"], [["S91", "9"], ["S92", "8"]])
    out_dir = tmp_path / "out"
    run([str(a), str(b), "--link-id", "subject_id", "--pseudonymize",
         "--out-dir", str(out_dir), "--key-out", str(tmp_path / "키.csv"), "--salt", "t"])
    capsys.readouterr()
    exported = sorted(p.name for p in (out_dir / "내보내기").iterdir())
    assert len(exported) == 2
    rows = sum(
        len((out_dir / "내보내기" / n).read_text(encoding="utf-8-sig").strip().splitlines()) - 1
        for n in exported
    )
    assert rows == 4  # 어느 사이트도 사라지지 않았습니다


def test_out_dir_that_is_a_file_exits_two_not_one(dirty_csv, tmp_path, capsys):
    """종료코드 1 은 이 툴에서 '치명 발견'을 뜻합니다 — 크래시가 1 로 나가면 안 됩니다."""
    blocker = tmp_path / "notadir"
    blocker.write_text("x", encoding="utf-8")
    code = run([str(dirty_csv), "--out-dir", str(blocker)])
    assert code == EXIT_USAGE
    assert "폴더" in capsys.readouterr().err


def test_fifo_input_is_rejected_instead_of_hanging(tmp_path, capsys):
    fifo = tmp_path / "pipe.csv"
    os.mkfifo(fifo)
    code = run([str(fifo)])
    assert code == EXIT_USAGE
    assert "일반 파일이 아닙니다" in capsys.readouterr().err


def test_hardlinked_key_file_inside_out_dir_is_removed_and_refused(tmp_path, capsys):
    from .conftest import write_csv_file

    src = write_csv_file(tmp_path / "d.csv", ["subject_id", "v"], [["S01", "1"]])
    out_dir = tmp_path / "out"
    (out_dir / "내보내기").mkdir(parents=True)
    bait = out_dir / "내보내기" / "미끼.csv"
    bait.write_text("x\n", encoding="utf-8")
    key = tmp_path / "키.csv"
    os.link(bait, key)
    # 내보내기 폴더가 비어 있지 않으므로 먼저 정리하고 다시 시도합니다.
    bait_copy = tmp_path / "미끼보관.csv"
    os.link(bait, bait_copy)
    bait.unlink()
    os.link(bait_copy, out_dir / "내보내기" / "미끼.csv")
    code = run([str(src), "--pseudonymize", "--out-dir", str(out_dir), "--key-out", str(key)])
    assert code == EXIT_USAGE
    assert "하드링크" in capsys.readouterr().err or code == EXIT_USAGE


def test_key_out_differing_only_by_case_is_rejected(dirty_csv, tmp_path, capsys):
    """macOS 파일시스템은 대소문자를 구별하지 않습니다."""
    out_dir = tmp_path / "OUT"
    out_dir.mkdir()
    code = run([str(dirty_csv), "--pseudonymize", "--out-dir", str(out_dir),
                "--key-out", str(tmp_path / "out" / "키.csv")])
    assert code == EXIT_USAGE
    assert "--key-out" in capsys.readouterr().err


def test_key_out_differing_only_by_unicode_form_is_rejected(dirty_csv, tmp_path, capsys):
    import unicodedata

    nfc = tmp_path / unicodedata.normalize("NFC", "내보내기")
    nfd = tmp_path / unicodedata.normalize("NFD", "내보내기") / "키.csv"
    nfc.mkdir()
    code = run([str(dirty_csv), "--pseudonymize", "--out-dir", str(nfc), "--key-out", str(nfd)])
    assert code == EXIT_USAGE
    assert "--key-out" in capsys.readouterr().err


def test_shift_weeks_requires_shift_dates(dirty_csv, tmp_path, capsys):
    code = run([str(dirty_csv), "--shift-weeks", "--out-dir", str(tmp_path / "o")])
    assert code == EXIT_USAGE


def test_max_detail_caps_the_problem_list(tmp_path, capsys):
    from .conftest import write_csv_file

    rows = [[f"S{i:03d}", "010-1234-5678"] for i in range(40)]
    src = write_csv_file(tmp_path / "many.csv", ["subject_id", "phone"], rows)
    out_dir = tmp_path / "out"
    run([str(src), "--out-dir", str(out_dir), "--max-detail", "5"])
    capsys.readouterr()
    lines = (out_dir / "문제목록.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(lines) == 6  # 헤더 + 5행
    assert (out_dir / "문제목록_잘림안내.txt").exists()


def test_salt_file_is_owner_only(dirty_csv, tmp_path, capsys):
    key = tmp_path / "보안" / "키.csv"
    run([str(dirty_csv), "--pseudonymize", "--out-dir", str(tmp_path / "out"), "--key-out", str(key)])
    capsys.readouterr()
    salt = key.with_name(key.stem + "_솔트.txt")
    assert oct(salt.stat().st_mode & 0o777) == "0o600"


def test_key_file_permissions_are_owner_only(dirty_csv, tmp_path, capsys):
    key = tmp_path / "보안" / "키.csv"
    run([str(dirty_csv), "--pseudonymize", "--out-dir", str(tmp_path / "out"), "--key-out", str(key)])
    capsys.readouterr()
    assert oct(key.stat().st_mode & 0o777) == "0o600"


def test_export_refuses_non_empty_export_folder(dirty_csv, tmp_path, capsys):
    out_dir = tmp_path / "out"
    (out_dir / "내보내기").mkdir(parents=True)
    (out_dir / "내보내기" / "옛사본.csv").write_text("a\n", encoding="utf-8")
    code = run([str(dirty_csv), "--pseudonymize", "--out-dir", str(out_dir),
                "--key-out", str(tmp_path / "키.csv")])
    assert code == EXIT_USAGE
    assert "이미 파일이 있습니다" in capsys.readouterr().err


def test_hidden_sheet_is_not_exported(tmp_path, capsys):
    path = build_xlsx(
        tmp_path / "책.xlsx",
        [
            Sheet(name="응답", rows=[["subject_id", "점수"], ["S01", "3"], ["S02", "4"]]),
            Sheet(name="원본명단", rows=[["이름"], ["김현중"]], hidden=True),
        ],
    )
    out_dir = tmp_path / "out"
    run([str(path), "--pseudonymize", "--out-dir", str(out_dir), "--key-out", str(tmp_path / "키.csv")])
    capsys.readouterr()
    exported = sorted(p.name for p in (out_dir / "내보내기").iterdir())
    assert exported == ["책__응답.csv"]


def test_recheck_of_exported_copy_drives_exit_code(tmp_path, capsys):
    """이름·전화를 뺀 사본은 치명 0 → 종료코드 0 이어야 합니다."""
    from .conftest import write_csv_file

    src = write_csv_file(
        tmp_path / "a.csv",
        ["subject_id", "name", "phone", "sex", "week", "score"],
        [[f"S{i:02d}", "김현중", "010-1111-2222", "M" if i % 2 else "F", w, 10 + i]
         for i in range(1, 11) for w in (0, 4)],
    )
    code = run([str(src), "--drop-columns", "name,phone", "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert "[내보낸 사본 재감사]" in out
    assert code == EXIT_OK


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(["--version"])
    assert excinfo.value.code == 0
    assert "deidaudit" in capsys.readouterr().out


def test_partially_parseable_date_column_aborts_the_export(tmp_path, capsys):
    """`미상` 이 섞인 날짜 열은 이동되지 않은 채 원본 날짜로 나가던 자리입니다."""
    from .conftest import write_csv_file

    rows = [
        [f"S{i:02d}", "미상" if i in (2, 5, 9) else f"2026-01-{i:02d}", f"19{60 + i}-05-01"]
        for i in range(1, 11)
    ]
    src = write_csv_file(tmp_path / "mix.csv", ["subject_id", "visit_date", "birth"], rows)
    out_dir = tmp_path / "out"
    code = run([str(src), "--link-id", "subject_id", "--shift-dates", "--pseudonymize",
                "--out-dir", str(out_dir), "--key-out", str(tmp_path / "키.csv")])
    err = capsys.readouterr().err
    assert code == EXIT_UNDETERMINED
    assert "visit_date" in err and "이동하지 못했습니다" in err
    assert not list((out_dir / "내보내기").glob("*.csv"))


def test_duplicate_named_columns_are_both_dropped(tmp_path, capsys):
    """중복 헤더는 `이름#2` 로 바뀝니다 — --drop-columns 가 놓치면 그대로 나갑니다."""
    path = tmp_path / "dup.csv"
    path.write_text(
        "subject_id,이름,이름,memo\nS01,김현중,김현중,ok\nS02,이서연,이서연,ok\n", encoding="utf-8"
    )
    out_dir = tmp_path / "out"
    code = run([str(path), "--drop-columns", "이름", "--out-dir", str(out_dir)])
    capsys.readouterr()
    exported = (out_dir / "내보내기" / "dup.csv").read_text(encoding="utf-8-sig")
    assert "김현중" not in exported and "이서연" not in exported
    assert code == EXIT_OK


def test_hidden_columns_are_exported_and_that_is_stated(tmp_path, capsys):
    """숨김 시트는 빼고 숨김 열은 내보냅니다 — 비대칭이라 반드시 말해 줘야 합니다."""
    path = build_xlsx(
        tmp_path / "h.xlsx",
        [Sheet(name="S", rows=[["subject_id", "병록번호"], ["S01", "A-1"], ["S02", "A-2"]],
               hidden_columns=(1,))],
    )
    out_dir = tmp_path / "out"
    run([str(path), "--pseudonymize", "--out-dir", str(out_dir), "--key-out", str(tmp_path / "k.csv")])
    out = capsys.readouterr().out
    assert "숨김 열·행은 **일반 열/행으로 사본에 포함**됩니다" in out
    exported = (out_dir / "내보내기" / "h__S.csv").read_text(encoding="utf-8-sig")
    assert "병록번호" in exported


def test_free_text_detected_by_shape_not_just_header(tmp_path, capsys):
    """헤더가 `Q7` 이어도 내용이 자유기술이면 전 행을 봐야 합니다."""
    from deidaudit.audit import run_audit
    from .conftest import write_csv_file

    rows = [
        [f"S{i:02d}", f"어제는 잠들기까지 {i}0분쯤 걸렸고 중간에 한 번 깼습니다 기록 {i}"]
        for i in range(1, 11)
    ]
    rows[3][1] = "새벽에 깨서 ○○○ 간호사한테 얘기했습니다 그 뒤로 다시 잠들었어요"
    src = write_csv_file(tmp_path / "q7.csv", ["subject_id", "Q7"], rows)
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert any(col == "Q7" for _, col, _ in result.coverage.free_text_columns)
    assert "자유텍스트 내 인명" in {f.kind for f in result.findings}


def test_short_repetitive_column_is_not_free_text(tmp_path):
    from deidaudit.audit import run_audit
    from .conftest import write_csv_file

    src = write_csv_file(
        tmp_path / "cat.csv", ["subject_id", "arm"], [[f"S{i:02d}", "중재" if i % 2 else "대조"] for i in range(10)]
    )
    result = run_audit([src], [], ["subject_id"], 10**9, 5)
    assert not any(col == "arm" for _, col, _ in result.coverage.free_text_columns)


def test_output_cannot_overwrite_an_input_and_nothing_partial_is_left(tmp_path, capsys):
    """대상 경로를 먼저 전부 검증하므로 반쪽짜리 리포트가 남지 않습니다."""
    from .conftest import write_csv_file

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    victim = write_csv_file(out_dir / "문제목록.csv", ["a"], [["1"]])
    code = run([str(victim), "--out-dir", str(out_dir)])
    assert code == EXIT_USAGE
    assert not (out_dir / "점검결과.md").exists()
    assert victim.read_text(encoding="utf-8-sig").startswith("a")


def test_zip_bomb_is_refused(tmp_path, capsys):
    import zipfile

    path = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
        zf.writestr("big.bin", b"\0" * (700 * 1024 * 1024))
    code = run([str(path)])
    assert code == EXIT_UNDETERMINED
    assert "압축 해제 크기" in capsys.readouterr().out


def test_salt_file_cannot_be_symlinked_into_the_shipped_folder(tmp_path, capsys):
    """솔트만 있으면 모든 가명과 날짜 오프셋을 재생성할 수 있습니다."""
    from .conftest import write_csv_file

    src = write_csv_file(tmp_path / "일지.csv", ["subject_id", "v"], [["S01", "1"]])
    out_dir = tmp_path / "out"
    key_dir = tmp_path / "보안"
    key_dir.mkdir()
    target = out_dir / "내보내기" / "재현용_솔트.txt"
    target.parent.mkdir(parents=True)
    os.symlink(target, key_dir / "키_솔트.txt")
    target.parent.rmdir()
    run([str(src), "--pseudonymize", "--out-dir", str(out_dir), "--key-out", str(key_dir / "키.csv")])
    capsys.readouterr()
    assert not target.exists()


def test_relative_out_dir_still_puts_reports_outside(tmp_path, capsys, monkeypatch):
    """`--out-dir .` 은 기본 규칙만으로 리포트를 내보낼 폴더 안에 만들던 자리입니다."""
    from .conftest import write_csv_file

    work = tmp_path / "work"
    work.mkdir()
    src = write_csv_file(work / "일지.csv", ["subject_id", "v"], [["S01", "1"]])
    monkeypatch.chdir(work)
    run([src.name, "--pseudonymize", "--out-dir", ".", "--key-out", str(tmp_path / "키.csv")])
    capsys.readouterr()
    assert (tmp_path / "work_점검리포트" / "점검결과.md").exists()
    assert not (work / "_점검리포트").exists()
    assert not (work / "점검결과.md").exists()


def test_key_out_through_a_symlinked_subdir_of_out_dir_is_rejected(tmp_path, capsys):
    """`zip -r` 은 심볼릭 링크를 따라갑니다 — realpath 만 보면 통과합니다."""
    from .conftest import write_csv_file

    src = write_csv_file(tmp_path / "일지.csv", ["subject_id", "v"], [["S01", "1"]])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    outside = tmp_path / "보안real"
    outside.mkdir()
    os.symlink(outside, out_dir / "보안")
    code = run([str(src), "--pseudonymize", "--out-dir", str(out_dir),
                "--key-out", str(out_dir / "보안" / "키.csv")])
    assert code == EXIT_USAGE
    assert "--key-out" in capsys.readouterr().err
    assert not (outside / "키.csv").exists()


def test_failed_export_leaves_nothing_behind(tmp_path, capsys):
    """'취소합니다'라고 말했으면 실제로 지워야 합니다."""
    from .conftest import write_csv_file

    src = write_csv_file(tmp_path / "일지.csv", ["subject_id", "v"], [["S01", "1"]])
    blocker = tmp_path / "notadir"
    blocker.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    code = run([str(src), "--quiet", "--pseudonymize", "--out-dir", str(out_dir),
                "--key-out", str(blocker / "키.csv")])
    capsys.readouterr()
    assert code == EXIT_USAGE
    assert not list(out_dir.rglob("*.csv"))


def test_unwritable_export_folder_exits_two_not_one(tmp_path, capsys):
    """종료코드 1 은 '치명 발견'입니다 — 크래시가 1 로 나가면 스크립트가 오해합니다."""
    from .conftest import write_csv_file

    src = write_csv_file(tmp_path / "일지.csv", ["subject_id", "v"], [["S01", "1"]])
    out_dir = tmp_path / "out"
    (out_dir / "내보내기").mkdir(parents=True)
    os.chmod(out_dir / "내보내기", 0o500)
    try:
        code = run([str(src), "--quiet", "--pseudonymize", "--out-dir", str(out_dir),
                    "--key-out", str(tmp_path / "키.csv")])
    finally:
        if (out_dir / "내보내기").exists():
            os.chmod(out_dir / "내보내기", 0o700)
    assert code == EXIT_USAGE
    assert "쓰지 못했습니다" in capsys.readouterr().err
