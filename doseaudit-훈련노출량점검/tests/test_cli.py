"""CLI — 종료코드 규약과 번들 예제 전 구간."""

import os
import subprocess
import sys

import pytest

from doseaudit.cli import build_parser, main
from doseaudit.errors import (EXIT_CLEAN, EXIT_CRITICAL, EXIT_REFUSE, EXIT_UNJUDGEABLE,
                              RefuseError)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(args):
    """서브프로세스로 돌려 **진짜 종료코드**를 본다."""
    return subprocess.run([sys.executable, "-m", "doseaudit"] + args,
                          cwd=ROOT, capture_output=True, text=True)


# ── 종료코드 ─────────────────────────────────────────────────────────────────
def test_깨끗한_예제는_0(clean_logs, clean_tracker):
    proc = run_cli(["--logs", clean_logs, "--tracker", clean_tracker,
                    "--active-day", "any-row", "--window", "enroll-to-cut",
                    "--cut", "2026-04-27", "--target-per-week", "3", "--no-files"])
    assert proc.returncode == EXIT_CLEAN
    assert "치명 0건" in proc.stdout


def test_결함_예제는_1(flawed_logs, flawed_tracker):
    proc = run_cli(["--logs", flawed_logs, "--tracker", flawed_tracker,
                    "--active-day", "any-row", "--window", "enroll-to-cut",
                    "--cut", "2026-04-27", "--target-per-week", "3", "--no-files"])
    assert proc.returncode == EXIT_CRITICAL


def test_정의_미선언은_판정_없이_2(flawed_logs):
    proc = run_cli(["--logs", flawed_logs])
    assert proc.returncode == EXIT_REFUSE
    assert "--active-day" in proc.stderr
    assert "[치명]" not in proc.stdout


def test_창_미선언도_2(flawed_logs):
    proc = run_cli(["--logs", flawed_logs, "--active-day", "any-row"])
    assert proc.returncode == EXIT_REFUSE
    assert "--window" in proc.stderr


def test_규격_불일치는_2(examples_dir):
    proc = run_cli(["--logs", os.path.join(examples_dir, "규격불일치"),
                    "--active-day", "any-row", "--window", "enroll-to-last"])
    assert proc.returncode == EXIT_REFUSE
    assert "5열 규격" in proc.stderr


def test_단일_이벤트_CSV_는_logflow_로_보내고_2(examples_dir):
    proc = run_cli(["--logs", os.path.join(examples_dir, "이벤트로그csv"),
                    "--active-day", "any-row", "--window", "enroll-to-last"])
    assert proc.returncode == EXIT_REFUSE
    assert "logflow" in proc.stderr


def test_못_읽은_파일이_있으면_3(examples_dir):
    proc = run_cli(["--logs", os.path.join(examples_dir, "못읽는파일", "logs"),
                    "--tracker", os.path.join(examples_dir, "못읽는파일", "참여현황.xlsx"),
                    "--active-day", "any-row", "--window", "enroll-to-cut",
                    "--cut", "2026-04-27", "--no-files"])
    assert proc.returncode == EXIT_UNJUDGEABLE
    assert "못 읽음" in proc.stdout


def test_3은_1보다_먼저다(examples_dir, tmp_path, make_workbook):
    """치명이 있어도 못 읽은 파일이 있으면 3 — '치명 2건'이 전부로 읽히면 안 된다."""
    logs = tmp_path / "logs"
    src = os.path.join(examples_dir, "flawed", "logs")
    import shutil
    shutil.copytree(src, str(logs))
    (logs / "ZZ99ZZ99_x_9.xlsx").write_bytes(b"broken")
    proc = run_cli(["--logs", str(logs), "--tracker",
                    os.path.join(examples_dir, "flawed", "참여현황.xlsx"),
                    "--active-day", "any-row", "--window", "enroll-to-cut",
                    "--cut", "2026-04-27", "--no-files"])
    assert proc.returncode == EXIT_UNJUDGEABLE
    assert "[치명]" in proc.stdout           # 치명은 여전히 보여 준다
    assert "3은 1보다 먼저" in proc.stdout


def test_inspect_는_판정하지_않고_0(flawed_logs, flawed_tracker):
    proc = run_cli(["--inspect", "--logs", flawed_logs, "--tracker", flawed_tracker])
    assert proc.returncode == EXIT_CLEAN
    assert "판정하지 않습니다" in proc.stdout
    assert "[치명]" not in proc.stdout


def test_inspect_는_선언_가능한_규칙과_결과를_보여_준다(flawed_logs):
    proc = run_cli(["--inspect", "--logs", flawed_logs])
    for spec in ("any-row", "min-rows=5", "min-modules=2", "min-seconds=60"):
        assert spec in proc.stdout
    assert "--active-day" in proc.stdout


def test_inspect_는_헤더_규격을_인쇄한다(flawed_logs):
    proc = run_cli(["--inspect", "--logs", flawed_logs])
    assert "소요시간(초)" in proc.stdout
    assert "헤더 = 엑셀 5행" in proc.stdout


def test_inspect_는_LoginID_를_인쇄하지_않는다(flawed_logs):
    proc = run_cli(["--inspect", "--logs", flawed_logs])
    for login in ("echo", "foxtrot", "golf", "hotel", "india", "juliet"):
        assert "_%s_" % login not in proc.stdout


def test_버전을_인쇄한다():
    proc = run_cli(["--version", "--logs", "x"])
    assert proc.returncode == 0
    assert "doseaudit" in proc.stdout


def test_도움말에_예제와_종료코드가_있다():
    proc = run_cli(["--help"])
    assert "종료코드" in proc.stdout
    assert "--inspect" in proc.stdout
    for flag in ("--active-day", "--window", "--target-per-week", "--tracker"):
        assert flag in proc.stdout


# ── 인자 검증 ────────────────────────────────────────────────────────────────
def test_criterion_은_처방_없이는_뜻이_없다(flawed_logs):
    with pytest.raises(RefuseError):
        main(["--logs", flawed_logs, "--active-day", "any-row",
              "--window", "fixed-weeks=8", "--criterion", "80", "--no-files"])


@pytest.mark.parametrize("args", [
    ["--cap", "0"], ["--cap", "-1"], ["--target-per-week", "0"],
    ["--target-per-week", "-3"],
])
def test_말이_안_되는_값은_거절(flawed_logs, args):
    with pytest.raises(RefuseError):
        main(["--logs", flawed_logs, "--active-day", "any-row",
              "--window", "fixed-weeks=8", "--no-files"] + args)


def test_cut_은_enroll_to_cut_과만_쓴다(flawed_logs):
    with pytest.raises(RefuseError):
        main(["--logs", flawed_logs, "--active-day", "any-row",
              "--window", "enroll-to-last", "--cut", "2026-04-27", "--no-files"])


def test_enroll_tracker_는_트래커를_요구한다(flawed_logs):
    with pytest.raises(RefuseError):
        main(["--logs", flawed_logs, "--active-day", "any-row",
              "--window", "fixed-weeks=8", "--enroll", "tracker", "--no-files"])


def test_logs_는_필수다():
    proc = run_cli(["--active-day", "any-row"])
    assert proc.returncode == 2


# ── 산출물 ───────────────────────────────────────────────────────────────────
def test_out_dir_에_네_파일을_쓴다(flawed_logs, flawed_tracker, tmp_path):
    out = str(tmp_path / "결과")
    code = main(["--logs", flawed_logs, "--tracker", flawed_tracker,
                 "--active-day", "any-row", "--window", "enroll-to-cut",
                 "--cut", "2026-04-27", "--target-per-week", "3", "--out-dir", out])
    assert code == EXIT_CRITICAL
    assert sorted(os.listdir(out)) == sorted(
        ["노출량점검.md", "모듈별요약.csv", "트래커불일치.csv", "피험자별노출량.csv"])


def test_no_files_는_아무것도_쓰지_않는다(flawed_logs, tmp_path):
    out = str(tmp_path / "결과")
    main(["--logs", flawed_logs, "--active-day", "any-row",
          "--window", "fixed-weeks=8", "--out-dir", out, "--no-files"])
    assert not os.path.exists(out)


def test_out_dir_없이_돌리면_어떻게_쓰는지_알려_준다(flawed_logs, capsys):
    main(["--logs", flawed_logs, "--active-day", "any-row", "--window", "fixed-weeks=8"])
    assert "--out-dir" in capsys.readouterr().out


def test_트래커_없이도_돈다(flawed_logs, capsys):
    code = main(["--logs", flawed_logs, "--active-day", "any-row",
                 "--window", "fixed-weeks=8", "--target-per-week", "3", "--no-files"])
    out = capsys.readouterr().out
    assert "트래커 없음" in out
    assert code in (EXIT_CLEAN, EXIT_CRITICAL)


def test_pool_types_를_주면_합산했다고_인쇄한다(flawed_logs, capsys):
    main(["--logs", flawed_logs, "--active-day", "any-row", "--window", "fixed-weeks=8",
          "--pool-types", "--no-files"])
    assert "합산함" in capsys.readouterr().out


def test_파서가_모든_인자를_안다():
    parser = build_parser()
    args = parser.parse_args(["--logs", "x", "--active-day", "any-row",
                              "--window", "fixed-weeks=8", "--cap", "100",
                              "--criterion", "80", "--target-per-week", "3",
                              "--pool-types", "--enroll", "first-activity",
                              "--tracker", "t.xlsx", "--out-dir", "o", "--no-files"])
    assert args.logs == "x" and args.pool_types is True and args.cap == 100.0
