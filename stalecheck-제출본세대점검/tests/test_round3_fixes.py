# -*- coding: utf-8 -*-
"""3라운드(엣지케이스) 회귀 테스트.

여기 있는 것들은 전부 같은 결말이었다: **트레이스백이 사용자에게 도달**하거나,
**깨끗하다는 종료코드로 낡은 봉투를 통과**시키거나, **영원히 멈춘다.**
"""

import io
import os
import subprocess
import sys
import zipfile

import pytest

from conftest import T0, T1, write
from stalecheck import cli, engine, normalize, scanning
from stalecheck.errors import (EXIT_CRITICAL, EXIT_OK, EXIT_REFUSED,
                               EXIT_UNDECIDABLE)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def call(args):
    out, err = io.StringIO(), io.StringIO()
    return cli.main(args, stdout=out, stderr=err), out.getvalue(), err.getvalue()


def tree(tmp_path, work_data="새", pkg_data="옛", wt=T1, pt=T0, name="a.md"):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg), exist_ok=True)
    write(work / name, work_data, wt)
    write(pkg / name, pkg_data, pt)
    return str(work), str(pkg)


def run_cli(args, env=None, **kw):
    e = dict(os.environ)
    e.setdefault("PYTHONPATH", ROOT)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, "-m", "stalecheck"] + args,
                          cwd=ROOT, env=e, capture_output=True, **kw)


# ============================================ 출력이 막혀도 종료코드는 살아남는다
def test_closed_stdout_does_not_change_exit_code(tmp_path):
    work, pkg = tree(tmp_path, "같음", "같음", wt=T0)
    proc = subprocess.run(
        "%s -m stalecheck --work %r --package %r >&-"
        % (sys.executable, work, pkg),
        shell=True, cwd=ROOT, capture_output=True, executable="/bin/bash")
    assert proc.returncode == EXIT_OK
    assert b"Traceback" not in proc.stderr


def test_closed_stderr_keeps_refusal_code(tmp_path):
    proc = subprocess.run(
        "%s -m stalecheck --work /없는폴더 --package /없는폴더/s 2>&-"
        % sys.executable,
        shell=True, cwd=ROOT, capture_output=True, executable="/bin/bash")
    assert proc.returncode == EXIT_REFUSED


def test_ascii_stdout_encoding_does_not_crash(tmp_path):
    work, pkg = tree(tmp_path)
    proc = run_cli(["--work", work, "--package", pkg],
                   env={"PYTHONIOENCODING": "ascii"})
    assert proc.returncode in (EXIT_OK, EXIT_CRITICAL, EXIT_UNDECIDABLE)
    assert b"Traceback" not in proc.stderr


def test_safe_stream_swallows_none():
    stream = cli._SafeStream(None)
    stream.write("무언가")
    stream.flush()
    assert stream.failed


def test_safe_stream_downgrades_unencodable(tmp_path):
    raw = open(str(tmp_path / "out.txt"), "w", encoding="ascii")
    stream = cli._SafeStream(raw)
    stream.write("한글")
    raw.close()
    assert "\\u" in open(str(tmp_path / "out.txt"), encoding="ascii").read()


# ============================================ zip 폭탄 · 손상 zip
def test_zip_bomb_does_not_blow_up_the_cli(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    for target in (work / "본문.docx", pkg / "본문.docx"):
        with zipfile.ZipFile(str(target), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("docProps/core.xml", "<c/>")
            zf.writestr("word/big.bin", b"\0" * (8 * 1024 * 1024))
    os.utime(str(work / "본문.docx"), (T1, T1))
    os.utime(str(pkg / "본문.docx"), (T0, T0))
    original = normalize.MAX_ZIP_UNCOMPRESSED
    normalize.MAX_ZIP_UNCOMPRESSED = 64 * 1024
    try:
        code, _, _ = call(["--work", str(work), "--package", str(pkg), "--quiet"])
    finally:
        normalize.MAX_ZIP_UNCOMPRESSED = original
    assert code in (EXIT_OK, EXIT_CRITICAL, EXIT_UNDECIDABLE)


def test_corrupt_docx_does_not_traceback(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    good = str(work / "본문.docx")
    with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docProps/core.xml", "<c/>")
        zf.writestr("word/document.xml", "<x/>" * 3000)
    data = bytearray(open(good, "rb").read())
    for i in range(len(data) // 3, len(data) // 3 + 8):
        data[i] ^= 0xFF
    with open(str(pkg / "본문.docx"), "wb") as fh:
        fh.write(bytes(data))
    os.utime(good, (T1, T1))
    os.utime(str(pkg / "본문.docx"), (T0, T0))
    proc = run_cli(["--work", str(work), "--package", str(pkg)])
    assert b"Traceback" not in proc.stderr
    assert proc.returncode in (EXIT_OK, EXIT_CRITICAL, EXIT_UNDECIDABLE)


# ============================================ FIFO / 특수 파일
def test_fifo_swapped_in_after_scan_does_not_hang(tmp_path):
    from stalecheck import pairing
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    target = write(work / "zzz.md", "새", T1)
    write(pkg / "zzz.md", "옛", T0)
    w = scanning.scan_tree(str(work), skip_subtrees=[str(pkg)])
    p = scanning.scan_tree(str(pkg), package_mode=True)
    os.remove(target)
    os.mkfifo(target)
    cache = pairing.HashCache()
    result = pairing.pair_files(p.files, w.files, cache)   # 멈추면 테스트가 끝나지 않는다
    assert cache.unreadable
    assert result.pairs[0].work_hash is None


def test_fifo_pair_is_undecidable_not_critical(tmp_path):
    from stalecheck import pairing, verdicts
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    target = write(work / "zzz.md", "새", T1)
    write(pkg / "zzz.md", "옛", T0)
    w = scanning.scan_tree(str(work), skip_subtrees=[str(pkg)])
    p = scanning.scan_tree(str(pkg), package_mode=True)
    os.remove(target)
    os.mkfifo(target)
    result = pairing.pair_files(p.files, w.files, pairing.HashCache())
    assert verdicts.judge(result.pairs[0]).label == verdicts.UNDECIDABLE


# ============================================ 읽지 못한 디렉터리
def test_unreadable_directory_is_confessed(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root 는 권한을 무시합니다")
    work, pkg = tree(tmp_path, "같음", "같음", wt=T0)
    locked = tmp_path / "논문" / "locked"
    os.makedirs(str(locked))
    write(locked / "paper.md", "숨은 파일", T1)
    os.chmod(str(locked), 0o000)
    try:
        a = engine.analyze(work, [pkg])
        assert any("locked" in rel for rel, _ in a.unreadable)
    finally:
        os.chmod(str(locked), 0o755)


def test_unreadable_directory_yields_exit_three(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root 는 권한을 무시합니다")
    work, pkg = tree(tmp_path, "같음", "같음", wt=T0)
    locked = tmp_path / "논문" / "locked"
    os.makedirs(str(locked))
    write(locked / "paper.md", "숨은 파일", T1)
    os.chmod(str(locked), 0o000)
    try:
        code, _, _ = call(["--work", work, "--package", pkg, "--quiet"])
        assert code == EXIT_UNDECIDABLE
    finally:
        os.chmod(str(locked), 0o755)


# ============================================ 판정불가 쌍의 종료코드
def test_undecidable_pairs_exit_three_not_zero(tmp_path):
    """mtime 이 보존된 복사(cp -p·Dropbox·rsync --times)가 정확히 이 상황이다."""
    work, pkg = tree(tmp_path, "새 내용", "옛 내용", wt=T0, pt=T0)
    code, out, err = call(["--work", work, "--package", pkg])
    assert code == EXIT_UNDECIDABLE
    assert "[판정불가] — 1쌍" in out
    assert "방향을 말할 수 없는 쌍" in err


def test_undecidable_outranks_critical(tmp_path):
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "치명.md", "새", T1)
    write(pkg / "치명.md", "옛", T0)
    write(work / "동률.md", "새", T0)
    write(pkg / "동률.md", "옛", T0)
    code, out, _ = call(["--work", str(work), "--package", str(pkg)])
    assert code == EXIT_UNDECIDABLE
    assert "[치명] 봉투가 구세대 — 1쌍" in out, "치명 사실 자체는 여전히 보고한다"


def test_clean_tree_is_still_zero(tmp_path):
    work, pkg = tree(tmp_path, "같음", "같음", wt=T0)
    code, _, _ = call(["--work", work, "--package", pkg, "--quiet"])
    assert code == EXIT_OK


# ============================================ --package 심볼릭 링크 탈출
def test_package_symlink_pointing_outside_work_refused(tmp_path):
    work = tmp_path / "논문"
    outside = tmp_path / "밖"
    os.makedirs(str(work))
    os.makedirs(str(outside))
    write(work / "a.md", "새", T1)
    write(outside / "a.md", "옛", T0)
    link = str(work / "submission_link")
    os.symlink(str(outside), link)
    code, _, err = call(["--work", str(work), "--package", link, "--quiet"])
    assert code == EXIT_REFUSED
    assert "안에 있지 않습니다" in err


def test_package_symlink_pointing_inside_work_is_allowed(tmp_path):
    work = tmp_path / "논문"
    real = work / "실제봉투"
    os.makedirs(str(real))
    write(work / "a.md", "새", T1)
    write(real / "a.md", "옛", T0)
    link = str(work / "submission_link")
    os.symlink(str(real), link)
    code, _, _ = call(["--work", str(work), "--package", link, "--quiet"])
    assert code == EXIT_CRITICAL


# ============================================ 비정상 인자
@pytest.mark.parametrize("value", ["inf", "nan", "-1", "-0.5"])
def test_non_finite_mtime_tolerance_refused(tmp_path, value):
    work, pkg = tree(tmp_path)
    code, _, err = call(["--work", work, "--package", pkg,
                         "--mtime-tolerance", value, "--quiet"])
    assert code == EXIT_REFUSED, value
    assert "0 이상" in err


def test_negative_infinity_tolerance_refused(tmp_path):
    """`-inf` 는 argparse 가 옵션으로 읽어 먼저 거절한다 — 그래도 2 여야 한다."""
    work, pkg = tree(tmp_path)
    code, _, _ = call(["--work", work, "--package", pkg,
                       "--mtime-tolerance=-inf", "--quiet"])
    assert code == EXIT_REFUSED


@pytest.mark.parametrize("value", ["inf", "nan", "2", "-0.5"])
def test_bad_min_coverage_refused(tmp_path, value):
    work, pkg = tree(tmp_path)
    code, _, err = call(["--work", work, "--package", pkg,
                         "--min-coverage", value, "--quiet"])
    assert code == EXIT_REFUSED, value


# ============================================ 보지 않은 것을 전부 자백한다
def test_hidden_files_are_counted_in_confession(tmp_path):
    """`.숨은원고.md` 가 흔적 없이 사라지고 '읽음 2/2' 가 인쇄되면 안 된다."""
    from stalecheck import report
    work, pkg = tree(tmp_path, "같음", "같음", wt=T0)
    write(tmp_path / "논문" / ".숨은원고.md", "새", T1)
    write(tmp_path / "논문" / "submission" / ".숨은원고.md", "옛", T0)
    a = engine.analyze(work, [pkg])
    assert len(a.hidden) == 2
    text = report.render_console(a)
    assert "숨김 파일이라 보지 않음: 2개" in text


def test_hidden_files_outside_scan_extensions_are_not_counted(tmp_path):
    """`.DS_Store` 까지 세면 자백이 잡음이 된다 — 대상 확장자인 것만 센다."""
    work, pkg = tree(tmp_path, "같음", "같음", wt=T0)
    write(tmp_path / "논문" / ".DS_Store", b"x", T1)
    assert engine.analyze(work, [pkg]).hidden == []


def test_symlinks_inside_the_envelope_are_confessed(tmp_path):
    """봉투 그림이 전부 심볼릭 링크면 한 쌍도 비교되지 않는다 — 그 사실이 보여야 한다."""
    from stalecheck import report
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "a.md", "같음", T0)
    write(pkg / "a.md", "같음", T0)
    real = write(tmp_path / "바깥" / "그림.png", "낡음".encode("utf-8"), T0)
    os.symlink(real, str(pkg / "그림.png"))
    a = engine.analyze(str(work), [str(pkg)])
    assert any("submission" in s for s in a.symlinks)
    assert "따라가지 않은 심볼릭 링크" in report.render_console(a)


def test_ambiguous_basename_does_not_exit_zero(tmp_path):
    """같은 이름의 작업본이 둘이고 내용이 달라 판정하지 못했다면 '깨끗함'이 아니다."""
    work = tmp_path / "논문"
    pkg = work / "submission"
    os.makedirs(str(pkg))
    write(work / "정상.md", "같음", T0)
    write(pkg / "정상.md", "같음", T0)
    write(work / "figures" / "Fig1.md", "새 내용", T1)
    write(work / "manuscript" / "Fig1.md", "다른 내용", T1)
    write(pkg / "Fig1.md", "옛 내용", T0)
    code, out, err = call(["--work", str(work), "--package", str(pkg)])
    assert code == EXIT_UNDECIDABLE
    assert "판정하지 못한 봉투 파일이 1개" in err
    assert "판정하지 않은** 봉투 파일: 1개" in out


def test_zip_bomb_fallback_actually_fires(tmp_path):
    """상한을 넘긴 파일이 **원시 바이트로** 비교됐는지 태그로 확인한다."""
    p = str(tmp_path / "bomb.docx")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docProps/core.xml", "<c/>")
        zf.writestr("word/big.bin", b"\0" * (2 * 1024 * 1024))
    original = normalize.MAX_ZIP_UNCOMPRESSED
    normalize.MAX_ZIP_UNCOMPRESSED = 1024
    try:
        h, tag = normalize.content_hash(p)
    finally:
        normalize.MAX_ZIP_UNCOMPRESSED = original
    assert tag == normalize.NORM_NONE
    assert h == normalize.raw_hash(p)
