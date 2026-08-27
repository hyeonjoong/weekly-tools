"""라운드 1 적대적 패널 지적 회귀 테스트 (HARDENING.md 라운드 1 참조).

각 테스트는 패널이 실증한 공격/버그 하나를 그대로 재현해, 수정이
되돌아가면 즉시 죽도록 고정한다.
"""

import datetime as dt
import os
import shutil
import stat
import subprocess

import pytest

from circadia.cli import run
from circadia.cosinor import hours_to_clock
from circadia.nonparam import hourly_bin, is_iv
from circadia.parse import read_series

ROOT = os.path.join(os.path.dirname(__file__), "..")
EXAMPLES = os.path.join(ROOT, "examples")


def ex(scenario, vendor, fname):
    return os.path.join(EXAMPLES, f"{scenario}_{vendor}", fname)


def small_hr_csv(tmp_path, days=2):
    rows = ["timestamp,hr"]
    for d in range(days):
        for h in range(24):
            rows.append(f"{dt.datetime(2026, 8, 3 + d, h):%Y-%m-%d %H:%M:%S},70")
    p = tmp_path / "hr.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- C3 심볼릭링크

def test_c3_symlink_in_out_dir_refused_and_target_untouched(tmp_path, capsys):
    """out-dir 에 '지표.csv' 심볼릭링크를 심어도 링크 타깃(입력 데이터)이
    한 바이트도 바뀌지 않아야 한다 — exit 2."""
    victim = tmp_path / "건강데이터.csv"
    original = "timestamp,hr\n2026-08-03 00:00:00,70\n"
    victim.write_text(original, encoding="utf-8")
    out = tmp_path / "결과"
    out.mkdir()
    (out / "지표.csv").symlink_to(victim)
    rc = run([small_hr_csv(tmp_path), "--out-dir", str(out)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "심볼릭링크" in err
    assert victim.read_text(encoding="utf-8") == original   # 바이트 불변


def test_c3_symlink_report_md_also_refused(tmp_path, capsys):
    victim = tmp_path / "표적.md"
    victim.write_text("원본", encoding="utf-8")
    out = tmp_path / "결과"
    out.mkdir()
    (out / "리듬리포트.md").symlink_to(victim)
    rc = run([small_hr_csv(tmp_path), "--out-dir", str(out)])
    assert rc == 2
    assert victim.read_text(encoding="utf-8") == "원본"


def test_r2_hardlink_in_out_dir_refused_and_target_untouched(tmp_path, capsys):
    """라운드 2: 하드링크는 islink 로 안 잡힌다 — nlink>1 거부로 입력 파일을
    지켜야 한다 (심볼릭링크와 같은 공격, 같은 결과: exit 2 + 바이트 불변)."""
    victim = tmp_path / "건강데이터.csv"
    original = "timestamp,hr\n2026-08-03 00:00:00,70\n"
    victim.write_text(original, encoding="utf-8")
    out = tmp_path / "결과"
    out.mkdir()
    os.link(victim, out / "지표.csv")   # 하드링크 — islink() 는 False
    rc = run([small_hr_csv(tmp_path), "--out-dir", str(out)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "하드링크" in err
    assert victim.read_text(encoding="utf-8") == original   # 바이트 불변


# ---------------------------------------------------------------- C4 .gitignore

@pytest.mark.skipif(shutil.which("git") is None, reason="git 없음")
def test_c4_gitignore_blocks_real_data_allows_examples(tmp_path):
    """실데이터 CSV·export.xml·zip 은 무시, 합성 예시 CSV 는 추적."""
    repo = tmp_path / "scratch"
    (repo / "examples" / "규칙적_1주_애플건강").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, ".gitignore"), repo / ".gitignore")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    def ignored(rel):
        return subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", rel]).returncode == 0

    assert ignored("심박_실제.csv")                     # 루트 실데이터
    assert ignored("심박.csv")
    assert ignored("export.xml")                        # Apple 건강 원본
    assert ignored("백업.zip")
    assert not ignored("examples/규칙적_1주_애플건강/심박.csv")   # 합성 예시는 추적
    assert not ignored("README.md")


# ---------------------------------------------------------------- C5 out-dir 실패

def test_c5_out_dir_is_existing_file_exit_2_not_traceback(tmp_path, capsys):
    blocker = tmp_path / "이미파일"
    blocker.write_text("x", encoding="utf-8")
    rc = run([small_hr_csv(tmp_path), "--out-dir", str(blocker)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "산출물을 쓸 수 없습니다" in err


def test_c5_out_dir_permission_denied_exit_2(tmp_path, capsys):
    hr = small_hr_csv(tmp_path)
    locked = tmp_path / "잠김"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)     # 쓰기 불가
    try:
        rc = run([hr, "--out-dir", str(locked / "하위")])
        err = capsys.readouterr().err
        assert rc == 2
        assert "산출물을 쓸 수 없습니다" in err
    finally:
        locked.chmod(0o700)


# ---------------------------------------------------------------- M4 극단값

def test_m4_extreme_steps_excluded_not_overflow(tmp_path, capsys):
    rows = ["timestamp,steps"]
    for d in range(2):
        for h in range(24):
            rows.append(f"{dt.datetime(2026, 8, 3 + d, h):%Y-%m-%d %H:%M:%S},10")
    rows[5] = rows[5].rsplit(",", 1)[0] + ",1e154"      # 극단값 주입
    rows[9] = rows[9].rsplit(",", 1)[0] + ",9e307"
    p = tmp_path / "steps.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    s = read_series(str(p), "걸음")
    assert sum(v for k, v in s.meta.excluded.items() if "걸음 범위 밖" in k) == 2
    rc = run(["--steps", str(p)])                       # 크래시 없이 완주
    assert rc == 0
    assert "걸음 범위 밖" in capsys.readouterr().out


def test_m4_hours_to_clock_nan_guard():
    assert hours_to_clock(float("nan")) == "—"
    assert hours_to_clock(float("inf")) == "—"


# ---------------------------------------------------------------- M5 콤마

def test_m5_decimal_comma_not_silently_x10(tmp_path):
    """'3,5' 가 35로 읽히던 10배 오염 — 이제 자백 제외."""
    p = tmp_path / "hr.csv"
    p.write_text("timestamp,hr\n2026-08-03 00:00,70\n2026-08-03 00:05,\"3,5\"\n"
                 "2026-08-03 00:10,71\n", encoding="utf-8")
    s = read_series(str(p), "심박")
    assert [v for _, v in s.samples] == [70.0, 71.0]    # 35가 없어야 한다
    assert s.meta.excluded["숫자 아님"] == 1


def test_m5_thousands_comma_still_accepted(tmp_path):
    p = tmp_path / "steps.csv"
    p.write_text("timestamp,steps\n2026-08-03 00:00,\"1,234\"\n"
                 "2026-08-03 01:00,\"12,345.6\"\n", encoding="utf-8")
    s = read_series(str(p), "걸음")
    assert [v for _, v in s.samples] == [1234.0, 12345.6]


# ---------------------------------------------------------------- M7 사유 구분

def test_m7_short_recording_gets_specific_cosinor_message(tmp_path, capsys):
    rows = ["timestamp,hr"] + [
        f"{dt.datetime(2026, 8, 3, h):%Y-%m-%d %H:%M:%S},70" for h in range(12)]
    p = tmp_path / "half.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    rc = run([str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "24h 성분을 적합할 수 없음" in out
    assert "계산할 시계열이 없습니다" not in out.split("코사이너")[1].split("──")[0]


# ---------------------------------------------------------------- M9 수면 셈

def test_m9_sleep_coverage_counts_rows_and_intervals_separately(capsys):
    rc = run([ "--sleep", ex("규칙적_1주", "애플건강", "수면.csv")])
    out = capsys.readouterr().out
    assert rc == 0
    line = next(l for l in out.split("\n") if l.startswith("[수면]"))
    assert "원본 데이터 행" in line
    assert "→ 수면구간 7개" in line


# ---------------------------------------------------------------- M14 경로 유출

def test_m14_saved_md_contains_no_absolute_input_paths(tmp_path, capsys):
    hr_abs = small_hr_csv(tmp_path)          # tmp 절대경로 입력
    out = tmp_path / "결과"
    rc = run([hr_abs, "--out-dir", str(out)])
    assert rc == 0
    md = (out / "리듬리포트.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in md           # 절대경로·사용자명 미유출
    assert "hr.csv" in md                    # basename 은 남는다
    capsys.readouterr()


# ---------------------------------------------------------------- M15 ~ 확장

def test_m15_tilde_out_dir_expanded(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = run([small_hr_csv(tmp_path), "--out-dir", "~/리듬결과"])
    assert rc == 0
    assert (tmp_path / "리듬결과" / "지표.csv").exists()
    assert not os.path.exists(os.path.join(os.getcwd(), "~"))   # 리터럴 ~ 금지
    capsys.readouterr()


# ---------------------------------------------------------------- M16 IS 중간값

def test_m16_asymmetric_pattern_IS_hand_computed_23_47():
    """분자/분모 스왑에도 통과하던 IS=1 대칭성 보완 — 중간값 손계산.

    2일, 시간당 1표본: 1일차 h0=4 나머지 0, 2일차 전부 0.
      x̄ = 4/48 = 1/12
      Σ(x−x̄)² = (4−1/12)² + 47·(1/12)² = 2209/144 + 47/144 = 2256/144 = 47/3
      프로파일: h0 = (4+0)/2 = 2, 나머지 0
      Σ_h(prof−x̄)² = (2−1/12)² + 23·(1/12)² = 529/144 + 23/144 = 552/144 = 23/6
      IS = (48 · 23/6) / (24 · 47/3) = 184/376 = 23/47 ≈ 0.489362
    스왑하면 376/184 ≈ 2.04 — 즉시 구분된다.
    """
    samples = []
    for d in range(2):
        for h in range(24):
            v = 4.0 if (d == 0 and h == 0) else 0.0
            samples.append((dt.datetime(2026, 8, 3 + d, h), v))
    res = is_iv(hourly_bin(samples, "mean"), min_days=2)
    assert res.is_ == pytest.approx(23.0 / 47.0, rel=1e-12)