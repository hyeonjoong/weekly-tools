"""트래커 읽기와 대조 — **이름은 열지 않는다**."""

import datetime

import pytest

from doseaudit.errors import RefuseError
from doseaudit.tracker import FORBIDDEN_PATTERNS, compare, load_tracker


class _Exposure(object):
    def __init__(self, code, n_active):
        self.code = code
        self.n_active = n_active


def test_트래커를_읽는다(make_tracker):
    path = make_tracker([("AB12CD34", "2026-03-02", 6, 57)])
    tracker = load_tracker(path)
    assert len(tracker.rows) == 1
    row = tracker.rows[0]
    assert row.code == "AB12CD34"
    assert row.enroll == datetime.date(2026, 3, 2)
    assert row.done_days == 6.0
    assert row.elapsed_days == 57.0


@pytest.mark.parametrize("column", [
    "환자명", "환 자 명", "성명", "이름", "피험자명", "Login ID", "login id",
    "NAME", "name", "user id", "user_id", "login_id", "연락처", "전화번호",
    "휴대폰", "생년월일", "주민등록번호", "email", "E-Mail", "이메일", "주소",
])
def test_개인식별_열은_열자마자_버린다(column):
    assert any(p.search(column) for p in FORBIDDEN_PATTERNS), column


def test_실명과_LoginID_는_읽히지_않는다(make_tracker):
    path = make_tracker([("AB12CD34", "2026-03-02", 6, 57)])
    tracker = load_tracker(path)
    assert set(tracker.dropped_columns) == {"환자명", "Login ID"}
    dumped = repr(vars(tracker)) + repr([vars(r) if hasattr(r, "__dict__") else
                                         {s: getattr(r, s) for s in r.__slots__}
                                         for r in tracker.rows])
    assert "홍길동" not in dumped
    assert "login0" not in dumped


def test_일자별_격자는_읽지_않는다(make_tracker):
    path = make_tracker([("AB12CD34", "2026-03-02", 6, 57)], grid_days=145)
    tracker = load_tracker(path)
    assert tracker.grid_columns == 145


def test_Access_Code_열이_없으면_거절(make_tracker, tmp_path):
    path = make_tracker([], extra_header=["환자명", "완료일수"])
    with pytest.raises(RefuseError) as info:
        load_tracker(path)
    assert "Access Code" in str(info.value)


def test_완료일수_열이_없으면_거절(tmp_path):
    from xlsxwrite import write_xlsx
    path = str(tmp_path / "t.xlsx")
    write_xlsx(path, [("참여현황", [["Access Code", "가입일"], ["AB12CD34", "2026-03-02"]])])
    with pytest.raises(RefuseError) as info:
        load_tracker(path)
    assert "완료일수" in str(info.value)


def test_같은_코드가_두_번이면_거절(make_tracker):
    path = make_tracker([("AB12CD34", "2026-03-02", 6, 57),
                         ("AB12CD34", "2026-03-02", 9, 57)])
    with pytest.raises(RefuseError) as info:
        load_tracker(path)
    assert "두 번" in str(info.value)


def test_없는_파일은_거절(tmp_path):
    with pytest.raises(RefuseError):
        load_tracker(str(tmp_path / "nope.xlsx"))


def test_지원하지_않는_확장자는_거절(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(RefuseError):
        load_tracker(str(path))


def test_읽지_못한_칸은_자백으로_남는다(tmp_path):
    from xlsxwrite import write_xlsx
    path = str(tmp_path / "t.xlsx")
    write_xlsx(path, [("참여현황", [
        ["Access Code", "가입일", "완료일수", "현재까지 일수", "진행률(%)"],
        ["AB12CD34", "언제였더라", "몇일", "57", "10"]])])
    tracker = load_tracker(path)
    fields = {f[1] for f in tracker.unread_fields}
    assert "가입일" in fields and "완료일수" in fields
    assert tracker.rows[0].done_days is None


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949"])
def test_CSV_트래커의_인코딩을_찾아_읽는다(tmp_path, encoding):
    path = tmp_path / "t.csv"
    text = "Access Code,가입일,완료일수,현재까지 일수,진행률(%)\nAB12CD34,2026-03-02,6,57,11\n"
    path.write_bytes(text.encode(encoding))
    tracker = load_tracker(str(path))
    assert tracker.rows[0].code == "AB12CD34"
    assert tracker.rows[0].done_days == 6.0


# ── 대조 ─────────────────────────────────────────────────────────────────────
def test_일치하면_불일치가_없다(make_tracker):
    tracker = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57)]))
    result = compare(tracker, [_Exposure("AB12CD34", 6)])
    assert result["diffs"] == []
    assert result["agree"] == ["AB12CD34"]


def test_부호_붙은_차이를_낸다(make_tracker):
    tracker = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57)]))
    result = compare(tracker, [_Exposure("AB12CD34", 11)])
    assert result["diffs"][0].delta == 5
    assert result["diffs"][0].tracker_done == 6
    assert result["diffs"][0].recomputed == 11


def test_전원_같은_방향이면_그렇게_말한다(make_tracker):
    tracker = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57),
                                         ("EF56GH78", "2026-03-02", 3, 57)]))
    result = compare(tracker, [_Exposure("AB12CD34", 11), _Exposure("EF56GH78", 5)])
    assert result["uniform_sign"] is True
    assert (result["delta_min"], result["delta_max"]) == (2, 5)


def test_방향이_섞이면_같은_방향이라고_말하지_않는다(make_tracker):
    tracker = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57),
                                         ("EF56GH78", "2026-03-02", 9, 57)]))
    result = compare(tracker, [_Exposure("AB12CD34", 11), _Exposure("EF56GH78", 5)])
    assert result["uniform_sign"] is False


def test_한쪽에만_있는_코드를_양쪽으로_센다(make_tracker):
    tracker = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57),
                                         ("ZZ99ZZ99", "2026-03-02", 3, 57)]))
    result = compare(tracker, [_Exposure("AB12CD34", 6), _Exposure("MN33OP44", 2)])
    assert result["only_tracker"] == ["ZZ99ZZ99"]
    assert result["only_logs"] == ["MN33OP44"]


def test_트래커_진행률의_내부_정합성을_본다(make_tracker):
    ok = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57, 11)]))
    assert compare(ok, [_Exposure("AB12CD34", 6)])["progress_bad"] == []
    bad = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57, 99)]))
    assert compare(bad, [_Exposure("AB12CD34", 6)])["progress_bad"][0][0] == "AB12CD34"


def test_반올림_1퍼센트_차이는_봐준다(make_tracker):
    """진행률은 정수로 반올림돼 들어온다 — 1%p 를 불일치로 세면 매번 운다."""
    tracker = load_tracker(make_tracker([("AB12CD34", "2026-03-02", 6, 57, 10)]))
    assert compare(tracker, [_Exposure("AB12CD34", 6)])["progress_bad"] == []


def test_완료일수를_못_읽은_사람은_대조하지_않는다(tmp_path):
    from xlsxwrite import write_xlsx
    path = str(tmp_path / "t.xlsx")
    write_xlsx(path, [("참여현황", [["Access Code", "완료일수"], ["AB12CD34", "미기입"]])])
    result = compare(load_tracker(path), [_Exposure("AB12CD34", 6)])
    assert result["diffs"] == [] and result["agree"] == []
