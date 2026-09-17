"""산출물 4종 — 스키마·수식 주입·연계 호환."""

import csv
import os

import pytest

from doseaudit.artifacts import (EXPOSURE_CSV, MODULE_CSV, REPORT_MD, TRACKER_CSV,
                                 exposure_rows, module_rows, tracker_rows, write_all)
from doseaudit.audit import Config, run_audit
from doseaudit.csvout import escape_cell, write_csv
from doseaudit.dose import UNCOMPARABLE
from doseaudit.report import render_console
from doseaudit.rules import ActiveDayRule, Window


@pytest.fixture
def result(flawed_logs, flawed_tracker):
    return run_audit(Config(
        logs=flawed_logs, tracker_path=flawed_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-04-27"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))


@pytest.fixture
def written(result, tmp_path):
    out = str(tmp_path / "out")
    os.makedirs(out)
    names = write_all(result, out, render_console(result))
    return out, names


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def test_네_가지_산출물이_만들어진다(written):
    out, names = written
    assert set(names) == {EXPOSURE_CSV, MODULE_CSV, TRACKER_CSV, REPORT_MD}
    for name in names:
        assert os.path.getsize(os.path.join(out, name)) > 0


def test_피험자별노출량은_subject_id_를_키로_쓴다(result):
    """`joinaudit` 이 이 이름을 피험자 키로 자동 인식한다."""
    header, _rows = exposure_rows(result)
    assert header[0] == "subject_id"


@pytest.mark.parametrize("column", [
    "활동일수", "관찰일수", "주당활동일", "노출률", "상한절단", "최장공백일",
    "첫활동일", "마지막활동일", "창시작일", "창종료일", "창밖활동일수", "창적용",
])
def test_피험자별노출량_스키마(result, column):
    header, _rows = exposure_rows(result)
    assert column in header


def test_피험자별노출량은_모듈별_행수를_낸다(result):
    header, _rows = exposure_rows(result)
    for name in result.modules:
        assert "행수_%s_전체기간" % name in header


@pytest.mark.parametrize("column", [
    "총행수_전체기간", "소요시간0초행수_전체기간", "중복의심행수_전체기간",
    "날짜해석실패행수_전체기간",
])
def test_창과_무관한_열은_이름에_범위가_박혀_있다(result, column):
    """한 행에 창 기준 값과 워크북 전체 값이 나란히 있으므로, 이름으로 구분한다."""
    header, _rows = exposure_rows(result)
    assert column in header


def test_피험자별노출량_행수는_피험자_수와_같다(result):
    _header, rows = exposure_rows(result)
    assert len(rows) == len(result.exposures) == 6


def test_피험자별노출량은_코드순으로_정렬된다(result):
    _header, rows = exposure_rows(result)
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)


def test_모듈별요약은_평균을_세_칸으로만_낸다(result):
    header, _rows = module_rows(result)
    for prefix in ("소요시간", "반복횟수"):
        assert "%s_전체평균" % prefix in header
        assert "%s_0제외평균" % prefix in header
        assert "%s_0행비율" % prefix in header
    assert "소요시간_평균" not in header
    assert "평균" not in [h for h in header if h.count("평균") and "전체" not in h
                         and "0제외" not in h]


def test_0행비율_100퍼센트_모듈은_0제외평균이_대조불가다(result):
    header, rows = module_rows(result)
    idx = header.index("소요시간_0제외평균")
    row = next(r for r in rows if r[0] == "소리스케치북" and r[1] == "(전체)")
    assert row[idx] == UNCOMPARABLE


def test_데이터_없음_모듈은_상태가_적힌다(result):
    header, rows = module_rows(result)
    idx = header.index("상태")
    row = next(r for r in rows if r[0] == "발성훈련" and r[1] == "(전체)")
    assert "데이터 없음" == row[idx]
    assert row[header.index("소요시간_전체평균")] == UNCOMPARABLE


def test_모듈별요약은_유형별_행도_낸다(result):
    _header, rows = module_rows(result)
    types = {r[1] for r in rows}
    assert "REGULAR" in types and "INDIVIDUAL" in types


def test_트래커불일치는_불일치만_담는다(result):
    _header, rows = tracker_rows(result)
    codes = {r[0] for r in rows}
    assert "IJ11KL22" not in codes          # 일치한 사람은 나오지 않는다
    assert "QR33ST44" in codes


def test_트래커불일치에_부호가_붙는다(result):
    header, rows = tracker_rows(result)
    idx = header.index("차이")
    for row in rows:
        if row[idx]:
            assert row[idx][0] in "+-"


def test_트래커불일치에_한쪽에만_있는_코드도_사유와_함께_들어간다(result):
    header, rows = tracker_rows(result)
    reasons = {r[0]: r[header.index("사유")] for r in rows}
    assert "트래커에만" in reasons["ZZ99ZZ99"]
    assert "로그에만" in reasons["MN33OP44"]


def test_criterion_을_주면_충족_미달_칸이_생긴다(flawed_logs, flawed_tracker):
    result = run_audit(Config(
        logs=flawed_logs, tracker_path=flawed_tracker,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-cut", "2026-04-27"),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=20.0, out_dir=None))
    header, rows = exposure_rows(result)
    idx = header.index("선언기준충족")
    assert {r[idx] for r in rows} <= {"충족", "미달", UNCOMPARABLE}


def test_criterion_이_없으면_그_칸도_없다(result):
    header, _rows = exposure_rows(result)
    assert "선언기준충족" not in header


# ── CSV 수식 주입 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", [
    "=1+1", "=HYPERLINK(\"http://x\")", "+1+1", "@SUM(A1)", "-1+1",
    "=cmd|'/c calc'!A1", "\t=1", "\r=1", "=2+5+cmd|' /C calc'!A0",
])
def test_수식으로_읽힐_값은_따옴표로_막는다(raw):
    assert escape_cell(raw).startswith("'")


@pytest.mark.parametrize("raw", ["-3", "+5", "3.5", "-0.25", "52.0%", "0", "1e5", "-1e-5"])
def test_숫자는_손대지_않는다(raw):
    """`-3`(부호 붙은 차이)을 `'-3` 으로 바꾸면 표가 전부 문자열이 된다."""
    assert escape_cell(raw) == raw


def test_피험자_코드가_수식이어도_안전하다(tmp_path):
    out = str(tmp_path / "out")
    os.makedirs(out)
    write_csv(out, "t.csv", ["subject_id"], [["=cmd|calc"]])
    rows = read_csv(os.path.join(out, "t.csv"))
    assert rows[1][0] == "'=cmd|calc"


def test_CSV_는_엑셀이_한글을_깨지_않게_BOM_을_붙인다(written):
    out, _names = written
    with open(os.path.join(out, EXPOSURE_CSV), "rb") as fh:
        assert fh.read(3) == b"\xef\xbb\xbf"


def test_산출물에_절대경로가_없다(written):
    out, names = written
    for name in names:
        with open(os.path.join(out, name), encoding="utf-8-sig") as fh:
            text = fh.read()
        assert os.path.expanduser("~") not in text
        assert "/Users/" not in text


def test_산출물_파일명은_basename_이다(written):
    _out, names = written
    assert all(os.sep not in name for name in names)


def test_트래커가_없으면_불일치_CSV_를_만들지_않는다(flawed_logs, tmp_path):
    result = run_audit(Config(
        logs=flawed_logs, tracker_path=None,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("fixed-weeks=8", None),
        enroll_source=None, cap=None, target_per_week=3.0,
        pool_types=False, criterion=None, out_dir=None))
    out = str(tmp_path / "out")
    os.makedirs(out)
    names = write_all(result, out, render_console(result))
    assert TRACKER_CSV not in names


def test_CSV_의_모든_행이_헤더와_길이가_같다(written):
    out, names = written
    for name in names:
        if not name.endswith(".csv"):
            continue
        rows = read_csv(os.path.join(out, name))
        assert all(len(r) == len(rows[0]) for r in rows), name
