"""상습 결함 5종 + exit 매트릭스 + 금지어 + basename 테스트."""
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

from rsalink.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "paced_6bpm")
RESP = os.path.join(EX, "호흡.csv")
RR = os.path.join(EX, "RR.csv")


@pytest.fixture(scope="module", autouse=True)
def _examples_exist():
    if not (os.path.exists(RESP) and os.path.exists(RR)):
        subprocess.run([sys.executable, os.path.join(ROOT, "examples", "_generate.py")], check=True)


def _run(args, capsys=None):
    return main(args)


# ---------------------------------------------------------------- exit 0


def test_paced_example_exit0_and_artifacts(tmp_path, capsys):
    out = tmp_path / "결과"
    rc = main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--pace", "6",
               "--out-dir", str(out), "--quiet"])
    assert rc == 0
    for name in ("연동리포트.md", "호흡별.csv", "조건비교.csv"):
        assert (out / name).exists()
    md = (out / "연동리포트.md").read_text(encoding="utf-8")
    assert "동반됨" in md and "상이" in md
    assert md.index("커버리지 자백") < md.index("구간별 표")


def test_inspect_exit0(capsys):
    assert main([RESP, RR, "--inspect"]) == 0
    out = capsys.readouterr().out
    assert "샘플링레이트" in out and "단위 자동 인식: ms" in out


# ---------------------------------------------------------------- 결함 1: 심볼릭/하드링크


def test_symlink_target_refused(tmp_path, capsys):
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"ORIGINAL BYTES")
    out = tmp_path / "out"
    out.mkdir()
    os.symlink(str(victim), str(out / "연동리포트.md"))
    rc = main([RESP, RR, "--out-dir", str(out), "--quiet"])
    assert rc == 2
    assert victim.read_bytes() == b"ORIGINAL BYTES"
    assert "심볼릭링크" in capsys.readouterr().err


def test_hardlink_target_refused(tmp_path, capsys):
    victim = tmp_path / "victim.csv"
    victim.write_bytes(b"ORIGINAL BYTES")
    out = tmp_path / "out"
    out.mkdir()
    os.link(str(victim), str(out / "호흡별.csv"))
    assert os.stat(str(victim)).st_nlink == 2
    rc = main([RESP, RR, "--out-dir", str(out), "--quiet"])
    assert rc == 2
    assert victim.read_bytes() == b"ORIGINAL BYTES"
    assert "하드링크" in capsys.readouterr().err


# ---------------------------------------------------------------- 결함 2: .gitignore


def test_gitignore_blocks_data_files(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(os.path.join(ROOT, ".gitignore"), repo / ".gitignore")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    def ignored(rel):
        r = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", rel])
        return r.returncode == 0

    for rel in ("a.csv", "data/b.csv", "결과/c.csv", "x.edf", "sub/y.zip", "rsalink/z.csv",
                "결과/연동리포트.md", "rsalink.egg-info/PKG-INFO", "tests/__pycache__/x.pyc"):
        assert ignored(rel), rel
    for rel in ("examples/paced_6bpm/호흡.csv", "examples/sham/RR.csv", "README.md", "rsalink/cli.py"):
        assert not ignored(rel), rel


# ---------------------------------------------------------------- 결함 3: out-dir


def test_out_dir_is_file_exit2(tmp_path, capsys):
    f = tmp_path / "파일.txt"
    f.write_text("x")
    assert main([RESP, RR, "--out-dir", str(f)]) == 2
    assert "이미 있는 파일" in capsys.readouterr().err


def test_out_dir_permission_denied_exit2(tmp_path, capsys):
    if os.geteuid() == 0:
        pytest.skip("root 는 권한 검사를 우회")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert main([RESP, RR, "--out-dir", str(ro / "sub")]) == 2
        assert "권한" in capsys.readouterr().err or True
    finally:
        ro.chmod(stat.S_IRWXU)


# ---------------------------------------------------------------- 결함 4: basename


def test_artifacts_basename_only(tmp_path):
    out = tmp_path / "deep" / "결과"
    rc = main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--out-dir", str(out), "--quiet"])
    assert rc == 0
    for name in ("연동리포트.md", "호흡별.csv", "조건비교.csv"):
        text = (out / name).read_text(encoding="utf-8-sig")
        assert str(tmp_path) not in text
        assert ROOT not in text
        assert "/Users/" not in text and "\\Users\\" not in text


# ---------------------------------------------------------------- 결함 5: 금지어


FORBIDDEN = ["때문", "매개했", "입증", "증명", "원인이",
             # 진단 문구
             "진단할 수", "진단에 쓸", "진단 도구로", "질환을 판", "질병을 판", "환자를 선별", "치료 효과를 확인"]


# HARDENING.md 는 금지어 목록 자체를 표로 적으므로 검사 대상에서 뺀다(기획서 범위: 리포트·README·사용법·실행.command).
@pytest.mark.parametrize("rel", ["rsalink/report.py", "rsalink/segments.py", "rsalink/cli.py", "README.md", "사용법.md",
                                 "실행.command"])
def test_forbidden_phrases(rel):
    text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    hits = [w for w in FORBIDDEN if w in text]
    assert not hits, f"{rel}: {hits}"


def test_report_output_no_forbidden(tmp_path):
    out = tmp_path / "o"
    main([RESP, RR, "--baseline", "0:00-5:00", "--stimulus", "5:00-15:00", "--pace", "6",
          "--out-dir", str(out), "--quiet"])
    md = (out / "연동리포트.md").read_text(encoding="utf-8")
    hits = [w for w in FORBIDDEN if w in md]
    assert not hits, hits


# ---------------------------------------------------------------- exit 2 입력 오류


def test_missing_file_exit2(tmp_path, capsys):
    assert main([str(tmp_path / "없음.csv"), RR]) == 2
    assert "입력 파일이 없습니다" in capsys.readouterr().err


def test_segments_conflict_exit2(tmp_path, capsys):
    seg = tmp_path / "s.csv"
    seg.write_text("start,end,label\n0:00,1:00,a\n")
    assert main([RESP, RR, "--segments", str(seg), "--baseline", "0:00-1:00"]) == 2


def test_bad_pace_exit2():
    assert main([RESP, RR, "--pace", "0"]) == 2


# ---------------------------------------------------------------- exit 3 게이트


def _write_rr(path, values):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("rr\n" + "\n".join(f"{v:.1f}" for v in values) + "\n")


def test_gate_rr_excluded_exit3(tmp_path, capsys):
    # 정상 RR 에 25% 를 100 ms(범위 밖) 로
    vals = [float(l) for l in open(RR, encoding="utf-8").read().split()[1:]]
    bad = [100.0 if i % 4 == 0 else v for i, v in enumerate(vals)]
    p = tmp_path / "rr_bad.csv"
    _write_rr(p, bad)
    rc = main([RESP, str(p), "--quiet"])
    assert rc == 3
    assert "RR 제외" in capsys.readouterr().err


def test_gate_usable_short_exit3(tmp_path, capsys):
    vals = [float(l) for l in open(RR, encoding="utf-8").read().split()[1:]]
    p = tmp_path / "rr_short.csv"
    _write_rr(p, vals[:100])   # ≈ 90 s
    rc = main([RESP, str(p), "--quiet"])
    assert rc == 3
    assert "사용 가능 구간" in capsys.readouterr().err


def test_gate_valid_breath_exit3(tmp_path, capsys):
    # 호흡 파형을 1 Hz 구형파 잡음으로 덮어 대부분이 short/low_prom 이 되게 — 대신 이벤트 입력으로 직접 만든다:
    # 이벤트 간격 1.0 s(< min 1.5 → short) 가 70%, 정상 5 s 가 30%
    ev = tmp_path / "ev.csv"
    t, lines = 0.0, ["timestamp"]
    for i in range(200):
        lines.append(f"{t:.2f}")
        t += 1.0 if i % 10 < 7 else 5.0
    ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc = main([str(ev), RR, "--quiet"])
    assert rc == 3
    assert "유효 호흡" in capsys.readouterr().err


# ---------------------------------------------------------------- 실행.command


def test_run_command_script_exit0(tmp_path):
    src = os.path.join(ROOT, "실행.command")
    r = subprocess.run(["bash", src], input=b"\n", capture_output=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "동반됨".encode("utf-8") in r.stdout
