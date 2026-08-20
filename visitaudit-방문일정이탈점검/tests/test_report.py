"""리포트·CSV 산출물 — 수식 인젝션 가드, as-of 스탬프, 자백 최상단."""

import csv
import datetime as dt

import pytest

from tests.conftest import d, mini_protocol, rec, subj
from visitaudit.consort import build_consort, build_pp
from visitaudit.criteria import recheck
from visitaudit.enroll import build_enrollment
from visitaudit.judge import judge
from visitaudit.protocol import PPRules
from visitaudit.report import (guard_cell, render_drafts, render_report,
                               write_csv)

FAR = d(2026, 12, 31)


# ── 수식 인젝션 가드 ────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("=SUM(A1:A9)", "'=SUM(A1:A9)"),
    ("+뭔가", "'+뭔가"),
    ("@cmd", "'@cmd"),
    ("-창이탈", "'-창이탈"),
    ("-2", "-2"),            # 숫자는 그대로 — 창밖일수가 망가지면 안 된다
    ("-3.5", "-3.5"),
    ("+7", "+7"),            # float("+7") 성공 → 숫자로 보고 그대로
    ("", ""),
    ("BELL-0001", "BELL-0001"),
    ("정상 텍스트", "정상 텍스트"),
])
def test_guard_cell(raw, expected):
    assert guard_cell(raw) == expected


def test_write_csv_stamps_asof_and_guards(tmp_path):
    path = str(tmp_path / "out.csv")
    write_csv(path, ["a", "b"], [["=x", "-2"], ["y", "z"]], d(2026, 8, 14))
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["a", "b", "기준시점"]
    assert rows[1] == ["'=x", "-2", "2026-08-14"]    # 가드 O, 숫자 보존, as-of 스탬프
    assert rows[2] == ["y", "z", "2026-08-14"]


# ── 리포트 본문 ─────────────────────────────────────────────────────
def _full(records, subjects, proto, as_of=FAR, defaulted=False, min_cov=70.0):
    judged = judge(records, subjects, proto, as_of)
    cr = recheck(subjects, proto)
    c = build_consort(subjects, judged, proto, as_of)
    pp = build_pp(judged, cr, subjects, proto, as_of)
    e = build_enrollment(subjects, proto.target_n, as_of)
    text = render_report(proto, judged, cr, c, pp, e, as_of, defaulted,
                         "p.json", min_cov)
    return judged, text


def test_confession_is_first_section():
    proto = mini_protocol(pp=PPRules())
    _, text = _full([rec("S01", "Baseline", "2026-03-02")], [subj("S01")], proto)
    lines = text.splitlines()
    sections = [ln for ln in lines if ln.startswith("[")]
    assert sections[0] == "[커버리지 자백]"       # 항상 최상단


def test_asof_in_header_and_default_banner():
    proto = mini_protocol()
    _, text = _full([rec("S01", "Baseline", "2026-03-02")], None, proto,
                    as_of=d(2026, 8, 14), defaulted=True)
    assert "기준시점(as-of): 2026-08-14" in text
    assert "--as-of 미지정" in text
    _, text2 = _full([rec("S01", "Baseline", "2026-03-02")], None, proto,
                     as_of=d(2026, 8, 14), defaulted=False)
    assert "--as-of 미지정" not in text2


def test_coverage_below_threshold_says_exit3():
    proto = mini_protocol()
    # 판정완료 0 / 판정불가 2 → 판정률 0%
    _, text = _full([rec("S01", "V1", "2026-03-30")], [subj("S01")], proto)
    assert "미달 → exit 3" in text


def test_no_deviation_report():
    proto = mini_protocol()
    _, text = _full([rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-03-30")],
                    [subj("S01")], proto)
    assert "[이탈]  총 0건" in text
    assert "이탈 없음" in text


def test_deviation_lines_show_days_and_severity():
    proto = mini_protocol(pp=PPRules(max_days_out=7))
    _, text = _full([rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-04-15")],
                    [subj("S01")], proto)
    assert "+13일" in text and "[중대]" in text


def test_rate_none_message():
    proto = mini_protocol()
    _, text = _full([], None, proto)
    assert "판정률 계산 불가" in text


# ── 문장 초안 ───────────────────────────────────────────────────────
def test_drafts_kr_en_contain_numbers():
    proto = mini_protocol(pp=PPRules(missing_required=True, max_days_out=7))
    records = [rec("S01", "Baseline", "2026-03-02"), rec("S01", "V1", "2026-04-15"),
               rec("S02", "Baseline", "2026-03-02"), rec("S02", "V1", "2026-03-30")]
    subjects = [subj("S01"), subj("S02")]
    judged = judge(records, subjects, proto, FAR)
    cr = recheck(subjects, proto)
    c = build_consort(subjects, judged, proto, FAR)
    pp = build_pp(judged, cr, subjects, proto, FAR)
    text = render_drafts(proto, judged, cr, c, pp, d(2026, 8, 14))
    assert "프로토콜 이탈은 총 1건" in text
    # B13: 단수형이 문법에 맞게 나온다 ("1 protocol deviation was", "1 ... visit")
    assert "1 protocol deviation was identified" in text
    assert "1 out-of-window visit," in text
    assert "out-of-window visits" not in text
    assert "per-protocol" in text
    assert "2026-08-14" in text
    # PP 문장에 '후보' 명시 (판정이지 확정이 아님)
    assert "후보" in text and "candidate" in text
