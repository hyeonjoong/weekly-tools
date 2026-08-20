"""CLI 통합 — 종료코드 4종, 원본 무수정, 산출물 격리, 예제 회귀."""

import csv
import hashlib
import json
import os
import subprocess
import sys

import pytest

from tests.conftest import EXAMPLES, PROTOCOL_JSON, run_cli, write_csv

CLEAN_VISITS = (
    "피험자ID,방문명,방문일,상태\n"
    "S01,Screening,2026-02-16,완료\n"
    "S01,Baseline,2026-03-02,완료\n"
    "S01,V1,2026-03-30,완료\n"
    "S01,V2,2026-04-27,완료\n"
    "S01,EOT,2026-05-25,완료\n"
)


def _hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ── exit 2: 입력·프로토콜 오류 ──────────────────────────────────────
def test_exit2_without_protocol_flag(tmp_path, capsys):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    assert run_cli([v]) == 2
    err = capsys.readouterr().err
    assert "프로토콜" in err
    assert "joinaudit" in err      # 경계 강제 장치의 안내문


def test_exit2_protocol_file_missing(tmp_path):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    assert run_cli([v, "--protocol", str(tmp_path / "없음.json"), "--no-files"]) == 2


def test_exit2_broken_protocol_json(tmp_path):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    p = write_csv(tmp_path, "p.json", "{깨짐")
    assert run_cli([v, "--protocol", p, "--no-files"]) == 2


def test_exit2_visits_missing(tmp_path, protocol_file):
    assert run_cli([str(tmp_path / "없음.csv"), "--protocol", protocol_file, "--no-files"]) == 2


def test_exit2_bad_asof(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    assert run_cli([v, "--protocol", protocol_file, "--as-of", "언젠가", "--no-files"]) == 2


def test_exit2_bad_min_coverage(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    assert run_cli([v, "--protocol", protocol_file, "--min-coverage", "150", "--no-files"]) == 2


def test_exit2_input_inside_outdir(tmp_path, protocol_file):
    sub = tmp_path / "결과"
    sub.mkdir()
    v = write_csv(sub, "v.csv", CLEAN_VISITS)
    assert run_cli([v, "--protocol", protocol_file, "--out-dir", str(sub)]) == 2


def test_argparse_usage_error_is_2(tmp_path):
    # 인자 자체가 틀리면 argparse 가 SystemExit(2) — 스펙의 '입력 오류 = 2' 와 일치
    with pytest.raises(SystemExit) as e:
        run_cli(["--이상한옵션"])
    assert e.value.code == 2


# ── exit 0: 이탈 없음 ───────────────────────────────────────────────
def test_exit0_clean(tmp_path, protocol_file, capsys):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    assert code == 0
    out = capsys.readouterr().out
    assert "exit 0" in out
    assert "[커버리지 자백]" in out


# ── exit 1: 이탈 발견 ───────────────────────────────────────────────
def test_exit1_deviation(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS.replace("2026-03-30", "2026-04-07"))  # V1 +5일
    assert run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"]) == 1


# ── exit 3: 판정률 임계 미만 ────────────────────────────────────────
def _low_coverage_visits():
    # S01 은 정상 판정(창이탈 1건 포함), S02·S03 은 기준방문 없음 → 판정불가
    return (
        "피험자ID,방문명,방문일,상태\n"
        "S01,Screening,2026-02-16,완료\n"
        "S01,Baseline,2026-03-02,완료\n"
        "S01,V1,2026-04-07,완료\n"          # +5일 이탈
        "S01,V2,2026-04-27,완료\n"
        "S01,EOT,2026-05-25,완료\n"
        "S02,V1,2026-03-30,완료\n"
        "S03,V1,2026-03-30,완료\n"
    )


def test_exit3_low_coverage(tmp_path, protocol_file, capsys):
    v = write_csv(tmp_path, "v.csv", _low_coverage_visits())
    # 판정완료 5 / 판정불가 10 → 판정률 33.3% < 70%
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    assert code == 3
    out = capsys.readouterr().out
    assert "exit 3" in out


def test_exit3_takes_precedence_over_deviations(tmp_path, protocol_file, capsys):
    # 위 데이터에는 이탈 1건이 있지만, 판정률 미달이면 그 개수를 믿을 수 없다 → 3
    v = write_csv(tmp_path, "v.csv", _low_coverage_visits())
    assert run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"]) == 3
    assert "신뢰할 수 없습니다" in capsys.readouterr().out


def test_min_coverage_flag_lowers_threshold(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv", _low_coverage_visits())
    # 임계 30% 로 낮추면 커버리지는 통과 → 이탈 1건으로 exit 1
    assert run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14",
                    "--min-coverage", "30", "--no-files"]) == 1


# ── 산출물 격리·원본 무수정 ─────────────────────────────────────────
def test_outputs_only_in_outdir_and_inputs_untouched(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    before = {name: _hash(os.path.join(str(tmp_path), name)) for name in os.listdir(tmp_path)}
    out = tmp_path / "산출"
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14",
                    "--out-dir", str(out)])
    assert code == 0
    assert sorted(os.listdir(out)) == sorted(["진행점검.md", "이탈목록.csv", "피험자별요약.csv", "CONSORT.txt"])
    after = {name: _hash(os.path.join(str(tmp_path), name))
             for name in os.listdir(tmp_path) if name != "산출"}
    assert before == after         # 입력 파일은 1바이트도 안 바뀐다


def test_no_files_creates_nothing(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    names_before = set(os.listdir(tmp_path))
    run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    assert set(os.listdir(tmp_path)) == names_before


# ── 축소 모드·형식 옵션 ─────────────────────────────────────────────
def test_no_subjects_mode_confesses(tmp_path, protocol_file, capsys):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14", "--no-files"])
    out = capsys.readouterr().out
    assert code == 0
    assert "피험자.csv 없음" in out          # 무엇을 못 했는지 자백


def test_wide_mode(tmp_path, protocol_file, capsys):
    v = write_csv(tmp_path, "w.csv",
                  "피험자ID,Screening,Baseline,V1,V2,EOT\n"
                  "S01,2026-02-16,2026-03-02,2026-03-30,2026-04-27,2026-05-25\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14",
                    "--wide", "--no-files"])
    assert code == 0
    assert "총 0건" in capsys.readouterr().out


def test_default_asof_banner(tmp_path, protocol_file, capsys):
    v = write_csv(tmp_path, "v.csv", CLEAN_VISITS)
    run_cli([v, "--protocol", protocol_file, "--no-files"])
    assert "--as-of 미지정" in capsys.readouterr().out


def test_column_override_flags(tmp_path, protocol_file):
    v = write_csv(tmp_path, "v.csv",
                  "환자,회차,일자\nS01,Screening,2026-02-16\nS01,Baseline,2026-03-02\n"
                  "S01,V1,2026-03-30\nS01,V2,2026-04-27\nS01,EOT,2026-05-25\n")
    code = run_cli([v, "--protocol", protocol_file, "--as-of", "2026-08-14",
                    "--id-col", "환자", "--visit-col", "회차", "--date-col", "일자",
                    "--no-files"])
    assert code == 0


# ── 번들 예제 회귀 (손으로 계산한 숫자와 전부 대조) ──────────────────
@pytest.fixture
def example_run(tmp_path, capsys):
    out = tmp_path / "결과예제"
    code = run_cli([
        os.path.join(EXAMPLES, "방문기록.csv"),
        "--protocol", os.path.join(EXAMPLES, "프로토콜.json"),
        "--subjects", os.path.join(EXAMPLES, "피험자.csv"),
        "--as-of", "2026-08-14", "--out-dir", str(out),
    ])
    return code, capsys.readouterr().out, out


def test_example_exit_code_and_headline(example_run):
    code, out, _ = example_run
    assert code == 1
    assert "피험자 20명 중 19명 판정 / 1명 판정불가" in out
    assert "예상 방문 슬롯 100건 중 판정완료 76건 / 판정 제외 24건" in out
    assert "미도래(창 미마감): 13건" in out
    assert "탈락 후 해당없음: 4건" in out
    assert "판정률 91.6% = 판정완료 76 / 판정대상 83" in out
    assert "[이탈]  총 6건" in out


def test_example_deviation_details(example_run):
    _, out, _ = example_run
    assert "BELL-0003  V1" in out and "+5일" in out
    assert "BELL-0007  V2" in out and "-2일" in out
    assert "BELL-0009  V1" in out and "+26일" in out
    assert "BELL-0016  EOT" in out and "+8일" in out
    assert "BELL-0011  V2" in out                      # 결측
    assert "V2(2026-05-31) 가 V1(2026-06-02) 보다 앞섬" in out


def test_example_consort_and_pp(example_run):
    _, out, _ = example_run
    assert "스크리닝 26 → 제외 6 (기준미달 4 / 동의철회 1 / 기타 1)" in out
    assert "무작위배정 20" in out
    assert "PP 후보 14" in out
    assert "중복 1명 제거" in out
    assert "판정불가 1" in out
    # B7: BELL-0021(ISI 값 없음)은 후보로 남되 표시가 붙는다. 여기에 방문을 판정
    # 못 한 BELL-0013(중복 행)·BELL-0015(깨진 날짜)가 더해져 3명이다 — 판정 못 한
    # 방문을 품은 채로 아무 표시 없이 PP 후보가 되면 PP 숫자가 조용히 틀린다.
    assert "후보 14명 중 3명은 판정하지 못한 항목이 있음" in out
    assert "2028-12" in out                            # 등록 외삽 (손계산)


def test_example_csv_outputs(example_run):
    _, _, out = example_run
    with open(out / "이탈목록.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 1 + 6                          # 헤더 + 이탈 6건
    assert rows[0][-1] == "기준시점"
    days = {(r[0], r[1]): r[7] for r in rows[1:]}
    assert days[("BELL-0007", "V2")] == "-2"           # 부호 있는 정수, 따옴표 가드 없음
    assert days[("BELL-0016", "EOT")] == "8"           # 앞에 + 없음
    with open(out / "피험자별요약.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 1 + 20                         # 무작위배정 20명
    by_id = {r[0]: r for r in rows[1:]}
    assert by_id["BELL-0016"][5] == "제외"
    assert "선정기준위반" in by_id["BELL-0016"][6]
    assert by_id["BELL-0002"][7].startswith("불가")
    assert by_id["BELL-0001"][5] == "후보"
    assert by_id["BELL-0021"][5] == "후보(기준판정불가)"   # B7


def test_example_reproducible(tmp_path):
    """같은 입력 + 같은 --as-of → 같은 산출물 (툴의 존재 이유)."""
    outs = []
    for name in ("r1", "r2"):
        out = tmp_path / name
        run_cli([
            os.path.join(EXAMPLES, "방문기록.csv"),
            "--protocol", os.path.join(EXAMPLES, "프로토콜.json"),
            "--subjects", os.path.join(EXAMPLES, "피험자.csv"),
            "--as-of", "2026-08-14", "--out-dir", str(out),
        ])
        blob = b""
        for f in sorted(os.listdir(out)):
            with open(out / f, "rb") as fh:
                blob += fh.read()
        outs.append(hashlib.sha256(blob).hexdigest())
    assert outs[0] == outs[1]


def test_python_dash_m_entrypoint():
    """python -m visitaudit 이 실제 프로세스로 돈다 (--version)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run([sys.executable, "-m", "visitaudit", "--version"],
                          capture_output=True, text=True, cwd=root)
    assert proc.returncode == 0
    assert "visitaudit" in proc.stdout
