# -*- coding: utf-8 -*-
"""CLI — 종료코드와 거절."""

import io
import os
import subprocess
import sys

import pytest

from conftest import T0, T1, write
from stalecheck import cli
from stalecheck.errors import (EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED,
                               EXIT_UNDECIDABLE)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def call(args):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


# ------------------------------------------------------------ 종료코드
def test_clean_package_exits_zero(clean_pair):
    work, pkg = clean_pair
    code, out, _ = call(["--work", work, "--package", pkg])
    assert code == EXIT_OK
    assert "[치명] 봉투가 구세대 — 0쌍" in out


def test_stale_package_exits_one(stale_pair):
    work, pkg = stale_pair
    code, out, _ = call(["--work", work, "--package", pkg])
    assert code == EXIT_CRITICAL
    assert "[치명] 봉투가 구세대 — 2쌍" in out


def test_missing_package_exits_two(stale_pair):
    work, _ = stale_pair
    code, out, err = call(["--work", work])
    assert code == EXIT_REFUSED
    assert "봉투 후보" in out and "submission" in out
    assert "--package" in err


def test_missing_work_exits_two(tmp_path):
    code, _, err = call(["--work", str(tmp_path / "없음")])
    assert code == EXIT_REFUSED and "없습니다" in err


def test_package_outside_work_exits_two(tmp_path, stale_pair):
    work, _ = stale_pair
    other = tmp_path / "다른곳"
    os.makedirs(str(other))
    code, _, err = call(["--work", work, "--package", str(other)])
    assert code == EXIT_REFUSED
    assert "--package 가 --work 안에 있지 않습니다" in err


def test_package_equal_to_work_exits_two(stale_pair):
    work, _ = stale_pair
    code, _, err = call(["--work", work, "--package", work])
    assert code == EXIT_REFUSED and "같은 폴더" in err


def test_nested_packages_exit_two(stale_pair):
    work, pkg = stale_pair
    inner = os.path.join(pkg, "analysis")
    code, _, err = call(["--work", work, "--package", pkg, "--package", inner])
    assert code == EXIT_REFUSED and "포함관계" in err


def test_nonexistent_package_exits_two(stale_pair):
    work, pkg = stale_pair
    code, _, err = call(["--work", work, "--package", os.path.join(work, "없음")])
    assert code == EXIT_REFUSED


def test_unreadable_file_exits_three_over_one(tmp_path):
    """읽지 못한 파일이 있으면 치명이 있어도 exit 3 — 3이 1보다 우선한다."""
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "a.md", "새 내용", T1)
    write(pkg / "a.md", "옛 내용", T0)
    locked = write(work / "b.md", "x", T1)
    write(pkg / "b.md", "y", T0)
    os.chmod(locked, 0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root 는 권한을 무시합니다")
        code, out, err = call(["--work", str(work), "--package", str(pkg)])
        assert code == EXIT_UNDECIDABLE
        assert "[치명]" in out, "불일치 사실 자체는 여전히 보고한다"
    finally:
        os.chmod(locked, 0o600)


def test_min_coverage_failure_exits_three(stale_pair):
    work, pkg = stale_pair
    code, _, err = call(["--work", work, "--package", pkg, "--min-coverage", "0.99"])
    assert code == EXIT_UNDECIDABLE and "짝지음 비율" in err


def test_min_coverage_default_is_off(clean_pair):
    work, pkg = clean_pair
    code, _, _ = call(["--work", work, "--package", pkg])
    assert code == EXIT_OK


def test_min_coverage_satisfied_passes(clean_pair):
    work, pkg = clean_pair
    code, _, _ = call(["--work", work, "--package", pkg, "--min-coverage", "0.5"])
    assert code == EXIT_OK


def test_bad_arguments_exit_two():
    code, _, _ = call(["--nonexistent-flag"])
    assert code == EXIT_REFUSED


def test_help_exits_zero():
    code, _, _ = call(["--help"])
    assert code == EXIT_OK


# ------------------------------------------------------------ --inspect
def test_inspect_does_not_judge(stale_pair):
    """--inspect 는 판정하지 않는다. 그래서 0(=치명 없음)으로 끝내지 않는다 —
    `stalecheck --inspect && 제출` 이 낡은 봉투를 통과시키면 안 된다."""
    work, pkg = stale_pair
    code, out, err = call(["--work", work, "--package", pkg, "--inspect"])
    assert code == EXIT_REFUSED
    assert "판정하지 않았습니다" in out
    assert "판정하지 않습니다" in err
    assert "[치명]" not in out


def test_inspect_prints_extension_counts(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg, "--inspect"])
    assert "py " in out and "png " in out


def test_inspect_prints_excluded_dirs(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg, "--inspect"])
    assert "_이전" in out


def test_inspect_prints_pair_counts(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg, "--inspect"])
    assert "짝지음 5쌍" in out


def test_candidates_printed_with_reason(stale_pair):
    work, _ = stale_pair
    _, out, _ = call(["--work", work])
    assert "'submission' 포함" in out


def test_candidates_include_copyable_command(stale_pair):
    work, _ = stale_pair
    _, out, _ = call(["--work", work])
    assert "python3 -m stalecheck --work" in out


# ------------------------------------------------------------ 산출물
def test_out_dir_writes_five_artifacts(stale_pair, tmp_path):
    work, pkg = stale_pair
    out_dir = str(tmp_path / "리포트")
    call(["--work", work, "--package", pkg, "--out-dir", out_dir])
    assert sorted(os.listdir(out_dir)) == sorted(
        ["세대점검.md", "세대불일치.csv", "짝없음.csv", "일치목록.csv", "형제점검.csv"])


def test_out_dir_inside_work_refused(stale_pair):
    work, pkg = stale_pair
    code, _, err = call(["--work", work, "--package", pkg,
                         "--out-dir", os.path.join(work, "리포트")])
    assert code == EXIT_REFUSED and "입력 폴더 안" in err


def test_out_dir_as_file_refused(stale_pair, tmp_path):
    work, pkg = stale_pair
    f = write(tmp_path / "파일.txt", "x")
    code, _, err = call(["--work", work, "--package", pkg, "--out-dir", f])
    assert code == EXIT_REFUSED and "폴더가 아니라 파일" in err


def test_quiet_suppresses_console(stale_pair, tmp_path):
    work, pkg = stale_pair
    code, out, _ = call(["--work", work, "--package", pkg, "--quiet",
                         "--out-dir", str(tmp_path / "r")])
    assert code == EXIT_CRITICAL and out == ""


def test_no_out_dir_writes_nothing(stale_pair, tmp_path):
    work, pkg = stale_pair
    before = set(os.listdir(os.path.dirname(work)))
    call(["--work", work, "--package", pkg])
    assert set(os.listdir(os.path.dirname(work))) == before


# ------------------------------------------------------------ 옵션
def test_no_siblings_disables_sibling_checks(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg, "--no-siblings"])
    assert "[경고] 빌드 누락 — 0건" in out


def test_include_archives_makes_pairing_ambiguous(stale_pair):
    """아카이브를 작업본으로 세면 같은 이름이 둘이 되어 판정이 사라진다 — 기본 제외의 이유."""
    work, pkg = stale_pair
    plain_code, plain, _ = call(["--work", work, "--package", pkg])
    arch_code, arch, _ = call(["--work", work, "--package", pkg, "--include-archives"])
    assert "[치명] 봉투가 구세대 — 2쌍" in plain
    assert plain_code == EXIT_CRITICAL
    assert "[치명] 봉투가 구세대 — 1쌍" in arch
    assert "판정하지 않은** 봉투 파일: 1개" in arch
    # 판정하지 못한 파일이 생겼으므로 "치명 없음(0)" 으로 끝내면 안 된다.
    assert arch_code == EXIT_UNDECIDABLE


def test_ext_filter_narrows_scan(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg, "--ext", "py"])
    assert "짝지음 2쌍" in out
    assert "대상 파일 2개" in out


def test_ext_accepts_dotted_form(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg, "--ext", ".py"])
    assert "짝지음 2쌍" in out


def test_mtime_tolerance_can_mute_direction(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg,
                      "--mtime-tolerance", "999999"])
    assert "[치명] 봉투가 구세대 — 0쌍" in out
    assert "[판정불가] — 2쌍" in out


def test_max_bytes_skips_and_confesses(stale_pair):
    work, pkg = stale_pair
    _, out, _ = call(["--work", work, "--package", pkg, "--max-bytes", "50"])
    assert "크기초과 건너뜀" in out


def test_multiple_packages_supported(tmp_path):
    work = tmp_path / "논문"
    for name in ("SUBMISSION_BRM", "SUBMISSION_JPBA"):
        os.makedirs(str(work / name))
        write(work / name / "본문.md", "옛 내용", T0)
    write(work / "manuscript" / "본문.md", "새 내용", T1)
    code, out, _ = call(["--work", str(work),
                         "--package", str(work / "SUBMISSION_BRM"),
                         "--package", str(work / "SUBMISSION_JPBA")])
    assert code == EXIT_CRITICAL
    assert "[치명] 봉투가 구세대 — 2쌍" in out


# ------------------------------------------------------------ 입력 불변
def _tree_hash(root):
    import hashlib
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            st = os.lstat(p)
            h.update(os.path.relpath(p, root).encode("utf-8", "surrogateescape"))
            h.update(str(st.st_size).encode())
            h.update(str(int(st.st_mtime)).encode())
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()


def test_input_tree_unchanged_after_run(stale_pair, tmp_path):
    work, pkg = stale_pair
    before = _tree_hash(work)
    call(["--work", work, "--package", pkg, "--out-dir", str(tmp_path / "r")])
    assert _tree_hash(work) == before, "이 툴은 입력을 절대 건드리지 않는다"


def test_input_tree_unchanged_on_refusal(stale_pair):
    work, pkg = stale_pair
    before = _tree_hash(work)
    call(["--work", work, "--package", pkg, "--out-dir", os.path.join(work, "r")])
    assert _tree_hash(work) == before


# ------------------------------------------------------------ 실제 프로세스
def test_module_entry_point_runs(stale_pair):
    work, pkg = stale_pair
    proc = subprocess.run([sys.executable, "-m", "stalecheck",
                           "--work", work, "--package", pkg],
                          cwd=ROOT, capture_output=True)
    assert proc.returncode == EXIT_CRITICAL
    assert "세대 점검".encode() in proc.stdout


def test_head_pipe_does_not_flip_exit_code(stale_pair):
    """`| head` 로 파이프가 끊겨도 종료코드가 뒤집히면 안 된다."""
    work, pkg = stale_pair
    proc = subprocess.run(
        "%s -m stalecheck --work %s --package %s 2>/dev/null | head -3; exit ${PIPESTATUS[0]}"
        % (sys.executable, _q(work), _q(pkg)),
        shell=True, cwd=ROOT, capture_output=True, executable="/bin/bash")
    assert proc.returncode == EXIT_CRITICAL


def _q(path):
    return "'" + path.replace("'", "'\\''") + "'"


def test_broken_pipe_is_not_a_traceback(stale_pair):
    work, pkg = stale_pair
    proc = subprocess.run(
        "%s -m stalecheck --work %s --package %s | head -1"
        % (sys.executable, _q(work), _q(pkg)),
        shell=True, cwd=ROOT, capture_output=True, executable="/bin/bash")
    assert b"Traceback" not in proc.stderr


def test_help_text_lists_exit_codes():
    parser = cli.build_parser()
    for code in ("0", "1", "2", "3"):
        assert code in parser.epilog
    assert "종료코드" in parser.epilog
