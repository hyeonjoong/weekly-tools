"""CLI — exit 코드 행렬(0/2/3)·산출물·게이트·--inspect·벤더 간 결과 동일성."""

import csv
import datetime as dt
import os

import pytest

from circadia.cli import run

ROOT = os.path.join(os.path.dirname(__file__), "..")
EXAMPLES = os.path.join(ROOT, "examples")


def ex(scenario, vendor, fname):
    return os.path.join(EXAMPLES, f"{scenario}_{vendor}", fname)


def make_low_wear(tmp_path):
    """7일 중 매일 2시간만 데이터 → 착용률 ≈ 8%."""
    rows = ["timestamp,hr"]
    for d in range(7):
        base = dt.datetime(2026, 8, 3, 10) + dt.timedelta(days=d)
        for m in range(0, 120, 10):
            rows.append(f"{base + dt.timedelta(minutes=m):%Y-%m-%d %H:%M:%S},70")
    p = tmp_path / "lowwear.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- exit 행렬

def test_exit_0_on_full_regular_scenario(capsys):
    rc = run([ex("규칙적_1주", "애플건강", "심박.csv"),
              "--steps", ex("규칙적_1주", "애플건강", "걸음.csv"),
              "--sleep", ex("규칙적_1주", "애플건강", "수면.csv")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "일주기리듬 분석 리포트" in out
    assert "액토그램" in out


def test_exit_2_missing_file(capsys):
    assert run(["없는파일.csv"]) == 2
    assert "오류" in capsys.readouterr().err


def test_exit_2_no_inputs(capsys):
    assert run([]) == 2
    assert "최소 1개" in capsys.readouterr().err


def test_exit_2_bad_min_wear(capsys):
    assert run([ex("규칙적_1주", "핏빗", "심박.csv"), "--min-wear", "1.5"]) == 2


def test_exit_2_ambiguous_columns(tmp_path, capsys):
    p = tmp_path / "amb.csv"
    p.write_text("timestamp,time,hr\n2026-08-03 00:00,x,70\n", encoding="utf-8")
    assert run([str(p)]) == 2
    assert "후보가 여러" in capsys.readouterr().err


def test_exit_3_low_wear(tmp_path, capsys):
    rc = run([make_low_wear(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 3
    assert "착용률" in err and "임계" in err


def test_low_wear_override_allows_analysis(tmp_path, capsys):
    rc = run([make_low_wear(tmp_path), "--min-wear", "0.05"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "데이터 부족" in out       # 유효일 0일 — IS 는 여전히 거부


# ---------------------------------------------------------------- 게이트

def test_min_days_gate_message_format(tmp_path, capsys):
    """수면 3밤 → SRI '데이터 부족(3일<5일)' — 값 출력 금지 (완료 기준)."""
    rows = ["start,end"]
    for d in (3, 4, 5):
        rows.append(f"2026-08-{d:02d} 23:00,2026-08-{d + 1:02d} 07:00")
    p = tmp_path / "sleep3.csv"
    p.write_text("\n".join(rows), encoding="utf-8")
    rc = run(["--sleep", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "데이터 부족(3일<5일)" in out
    sri_line = next(l for l in out.split("\n") if l.startswith("SRI"))
    assert "=" not in sri_line        # 숫자를 내지 않는다


def test_per_metric_gates_differ(capsys):
    """--min-days 7: 불규칙 주는 유효일 5일 → IS/IV만 부족, SRI(7밤)는 계산."""
    rc = run([ex("불규칙_1주", "삼성헬스", "심박.csv"),
              "--steps", ex("불규칙_1주", "삼성헬스", "걸음.csv"),
              "--sleep", ex("불규칙_1주", "삼성헬스", "수면.csv"),
              "--min-days", "7"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "데이터 부족(5일<7일)" in out          # IS/IV
    assert any(l.startswith("SRI = ") for l in out.split("\n"))   # SRI 는 7밤


def test_sleep_only_input_works(capsys):
    rc = run(["--sleep", ex("규칙적_1주", "핏빗", "수면.csv")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SRI" in out
    assert "심박 입력이 없어" in out


def test_hr_only_input_uses_hr_as_activity_with_caveat(capsys):
    rc = run([ex("규칙적_1주", "애플건강", "심박.csv")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "활동원: 심박" in out
    assert "방향 판정을 하지 않습니다" in out     # 심박 기반 IS/RA 참고범위 비적용


# ---------------------------------------------------------------- 산출물

def test_out_dir_writes_three_artifacts(tmp_path, capsys):
    out = tmp_path / "결과"
    rc = run([ex("규칙적_1주", "삼성헬스", "심박.csv"),
              "--steps", ex("규칙적_1주", "삼성헬스", "걸음.csv"),
              "--sleep", ex("규칙적_1주", "삼성헬스", "수면.csv"),
              "--out-dir", str(out)])
    assert rc == 0
    assert sorted(os.listdir(out)) == ["리듬리포트.md", "액토그램.txt", "지표.csv"]
    with open(out / "지표.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["구분", "날짜", "지표", "값", "단위", "비고"]
    names = {r[2] for r in rows[1:]}
    for expected in ("IS", "IV", "SRI", "RA", "cosinor_HR_MESOR",
                     "사회적시차", "야간심박강하율"):
        assert expected in names, f"지표.csv 누락: {expected}"
    daily = [r for r in rows[1:] if r[0] == "일별"]
    assert len({r[1] for r in daily}) >= 7      # 1행=1일×지표
    with open(out / "리듬리포트.md", encoding="utf-8") as fh:
        assert "의료기기가 아니" in fh.read()


def test_inspect_shows_columns_and_exits_clean(capsys):
    rc = run([ex("불규칙_1주", "애플건강", "심박.csv"), "--inspect"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "열 인식 결과" in out
    assert "startDate" in out
    assert "리포트" not in out            # 분석은 하지 않는다


def test_same_numbers_across_vendor_flavors(capsys):
    """애플/삼성 파일이 같은 내용이므로 리포트의 지표 줄도 같아야 한다."""
    picked = {}
    for vendor in ("애플건강", "삼성헬스"):
        rc = run([ex("규칙적_1주", vendor, "심박.csv"),
                  "--steps", ex("규칙적_1주", vendor, "걸음.csv"),
                  "--sleep", ex("규칙적_1주", vendor, "수면.csv")])
        assert rc == 0
        out = capsys.readouterr().out
        picked[vendor] = [l for l in out.split("\n")
                          if l.startswith(("IS(", "IV(", "SRI", "L5(", "[심박] MESOR"))]
    assert picked["애플건강"] == picked["삼성헬스"]
    assert len(picked["애플건강"]) >= 4
