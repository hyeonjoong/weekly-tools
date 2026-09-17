"""가장자리 — 빈 입력, 한 행, 이상한 값, 큰 파일, 시간대."""

import datetime

import pytest

from doseaudit.audit import Config, run_audit
from doseaudit.dose import Mean3, subject_exposure
from doseaudit.logs import load_bundle
from doseaudit.report import render_console
from doseaudit.rules import ActiveDayRule, Window


def audit(logs, tracker=None, rule="any-row", window="fixed-weeks=8", cut=None,
          target=3.0, cap=None, criterion=None, pool=False):
    return run_audit(Config(
        logs=logs, tracker_path=tracker, rule=ActiveDayRule.parse(rule),
        window=Window.parse(window, cut), enroll_source=None, cap=cap,
        target_per_week=target, pool_types=pool, criterion=criterion, out_dir=None))


def test_모든_모듈이_빈_워크북(make_workbook, tmp_path):
    """벤더가 아무것도 안 준 경우 — 평균 0 이 아니라 데이터 없음이 여섯 번."""
    make_workbook("AB12CD34", {})
    result = audit(str(tmp_path / "logs"))
    assert result.bundle.n_data_rows == 0
    assert sum(1 for f in result.findings if "데이터 없음" in f.title) == 6
    assert "[커버리지 자백]" in "\n".join(render_console(result))


def test_데이터가_한_행뿐인_워크북(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    result = audit(str(tmp_path / "logs"))
    exposure = result.exposures[0]
    assert exposure.n_active == 1
    # 활동일이 하루뿐이어도 **창의 끝까지 이어진 침묵은 공백이다.**
    # fixed-weeks=8 → 창 03-02 ~ 04-26(56일). 마지막 활동 03-02 이후 55일이 공백.
    assert exposure.longest_gap == 55
    assert exposure.first == exposure.last


def test_피험자가_한_명뿐이면_중앙값은_그_값이다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.%02d" % d, 1, "REGULAR", 1, 5)
                                        for d in (2, 3, 4)]})
    result = audit(str(tmp_path / "logs"))
    assert all(r["n"] == 1 for r in result.sensitivity)
    assert result.sensitivity[0]["median"] == result.sensitivity[0]["min"]


def test_같은_날_같은_모듈에_여러_행이어도_활동일은_하루다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", i, "REGULAR", 1, i)
                                        for i in range(1, 21)]})
    result = audit(str(tmp_path / "logs"))
    assert result.exposures[0].n_active == 1
    assert result.exposures[0].n_rows == 20


def test_날짜가_뒤죽박죽이어도_첫날과_마지막날은_맞는다(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [
        ("2026.05.20", 1, "REGULAR", 1, 5),
        ("2026.01.02", 2, "REGULAR", 1, 5),
        ("2026.03.11", 3, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-01-02", 3, 200)])
    exposure = audit(str(tmp_path / "logs"), tracker,
                     window="enroll-to-cut", cut="2026-06-30").exposures[0]
    assert exposure.first == datetime.date(2026, 1, 2)
    assert exposure.last == datetime.date(2026, 5, 20)
    assert exposure.longest_gap == 69        # 03-11 ~ 05-20 사이
    assert exposure.n_outside_window == 0


def test_훈련유형_칸이_비어_있어도_죽지_않는다(make_workbook, tmp_path):
    from doseaudit.dose import TYPE_BLANK
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "", 1, 5),
                                        ("2026.03.03", 1, "REGULAR", 1, 5)]})
    result = audit(str(tmp_path / "logs"))
    module = result.modules["음소쌍"]
    # 빈 유형도 **한 무리로 센다.** 빼 버리면 유형별 행수의 합(1)이 모듈 행수(2)와
    # 달라지는데, 그 차이를 설명하는 줄이 산출물 어디에도 없다.
    assert module.by_type[TYPE_BLANK]["n_rows"] == 1
    assert module.by_type["REGULAR"]["n_rows"] == 1
    assert module.n_rows == 2
    assert sum(t["n_rows"] for t in module.by_type.values()) == module.n_rows


def test_낯선_훈련유형도_그대로_센다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "NEWTYPE", 1, 5),
                                        ("2026.03.03", 1, "REGULAR", 1, 5)]})
    result = audit(str(tmp_path / "logs"))
    assert set(result.modules["음소쌍"].by_type) == {"NEWTYPE", "REGULAR"}


def test_음수_소요시간은_평균에_들어가지_않고_해석실패로_센다(make_workbook, tmp_path):
    """`-100초` 가 평균에 들어가면 '평균 소요시간 -21.67초' 가 인쇄된다."""
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, -5),
                                        ("2026.03.03", 1, "REGULAR", 1, 15)]})
    result = audit(str(tmp_path / "logs"))
    assert result.modules["음소쌍"].seconds.total_mean == pytest.approx(15.0)
    assert result.modules["음소쌍"].seconds.n == 1
    assert any("음수" in row[3] for row in result.bundle.parse_fail_rows)


def test_분_단위가_섞이면_60배_줄여_읽지_않는다(make_workbook, tmp_path):
    """`소요시간(초)` 열의 `'5분'` 은 5초가 아니다 — 환산하지 않고 해석 실패로 센다."""
    folder = tmp_path / "logs"
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 30)]},
                  folder=str(folder))
    import zipfile
    path = str(folder / "AB12CD34_user_1.xlsx")
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    key = [n for n in members if n.endswith("sheet5.xml")][0]
    members[key] = members[key].replace(b"<v>30\xec\xb4\x88</v>",
                                        "<v>5분</v>".encode("utf-8"))
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    result = audit(str(folder))
    assert any("단위가 다름" in row[3] for row in result.bundle.parse_fail_rows)


def test_아주_큰_소요시간도_그대로(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 10 ** 9)]})
    result = audit(str(tmp_path / "logs"))
    assert result.modules["음소쌍"].seconds.total_mean == 10.0 ** 9


def test_피험자가_많아도_돈다(make_workbook, tmp_path):
    for i in range(40):
        make_workbook("CODE%04d" % i,
                      {"음소쌍": [("2026.03.%02d" % (d + 1), 1, "REGULAR", 1, 5)
                                for d in range(i % 10 + 1)]}, seq=i)
    result = audit(str(tmp_path / "logs"))
    assert len(result.exposures) == 40
    assert all(r["n"] == 40 for r in result.sensitivity)


def test_행이_많아도_돈다(make_workbook, tmp_path):
    rows = [("2026.%02d.%02d" % (1 + i // 28, 1 + i % 28), i, "REGULAR", 1, i % 7)
            for i in range(3000)]
    make_workbook("AB12CD34", {"음소쌍": rows})
    result = audit(str(tmp_path / "logs"))
    assert result.bundle.n_data_rows == 3000


def test_관찰일수를_셀_수_없으면_노출률도_없다(make_workbook, tmp_path):
    """가입일을 모르면 분모가 없다 — 추측해서 채우지 않는다."""
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    exposure = subject_exposure(bundle.subjects[0], ActiveDayRule.parse("any-row"),
                                Window.parse("enroll-to-cut", "2026-04-27"), None, 3.0, None)
    assert exposure.observed_days is None
    assert exposure.adherence_pct is None
    assert exposure.days_per_week is None


def test_컷이_가입일보다_앞이면_노출률을_내지_않는다(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 1, 10)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-01-01")
    assert result.exposures[0].adherence_pct is None
    assert result.sensitivity[0]["n"] == 0


def test_활동일이_0인_피험자(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [("언제였더라", 1, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 0, 57)])
    result = audit(str(tmp_path / "logs"), tracker, window="fixed-weeks=8")
    assert result.exposures[0].n_active == 0
    assert result.exposures[0].adherence_pct == 0.0
    assert result.comparison["agree"] == ["AB12CD34"]


def test_enroll_to_last_는_활동이_없으면_분모가_없다(make_workbook, tmp_path, make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [("깨진날짜", 1, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-02", 0, 57)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-last")
    assert result.exposures[0].observed_days is None


def test_첫활동일을_시작으로_쓸_수도_있다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5),
                                        ("2026.03.09", 2, "REGULAR", 1, 5)]})
    result = run_audit(Config(
        logs=str(tmp_path / "logs"), tracker_path=None,
        rule=ActiveDayRule.parse("any-row"),
        window=Window.parse("enroll-to-last", None), enroll_source="first-activity",
        cap=None, target_per_week=3.0, pool_types=False, criterion=None, out_dir=None))
    assert result.exposures[0].observed_days == 8.0
    assert result.enroll_source == "first-activity"
    assert "첫 활동일" in "\n".join(render_console(result))


def test_트래커에_가입일이_없으면_분모를_만들지_않는다(make_workbook, tmp_path):
    from xlsxwrite import write_xlsx
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    tracker = str(tmp_path / "t.xlsx")
    write_xlsx(tracker, [("참여현황", [["Access Code", "완료일수"], ["AB12CD34", "1"]])])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-04-27")
    assert result.exposures[0].observed_days is None


def test_시각이_붙은_날짜도_하루로_묶인다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026-03-02 09:11", 1, "REGULAR", 1, 5),
                                        ("2026-03-02 21:40", 2, "REGULAR", 1, 5)]})
    result = audit(str(tmp_path / "logs"))
    assert result.exposures[0].n_active == 1


def test_Mean3_는_음수와_0을_구분한다():
    mean = Mean3([-1.0, 0.0, 1.0])
    assert mean.zero_ratio == pytest.approx(1 / 3)
    assert mean.nonzero_mean == pytest.approx(0.0)


def test_자정_넘김은_다루지_않는다고_자백에_적혀_있지_않다(make_workbook, tmp_path):
    """이 툴의 최소 단위는 **날짜**다 — 세션 추론은 `logflow` 의 일이다."""
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5)]})
    text = "\n".join(render_console(audit(str(tmp_path / "logs"))))
    assert "세션 단위 집계" in text and "logflow" in text


def test_출력_폴더에_이미_다른_파일이_있어도_괜찮다(flawed_logs, tmp_path):
    from doseaudit.artifacts import write_all
    out = tmp_path / "out"
    out.mkdir()
    (out / "메모.txt").write_text("사람이 써 둔 메모", encoding="utf-8")
    result = audit(flawed_logs)
    write_all(result, str(out), render_console(result))
    assert (out / "메모.txt").read_text(encoding="utf-8") == "사람이 써 둔 메모"


def test_두_번_돌려도_같은_결과가_나온다(flawed_logs, flawed_tracker):
    """결정론적이어야 Methods 문장을 믿을 수 있다."""
    first = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    second = audit(flawed_logs, flawed_tracker, window="enroll-to-cut", cut="2026-04-27")
    assert render_console(first) == render_console(second)


def test_파일_순서가_결과를_바꾸지_않는다(make_workbook, tmp_path):
    for idx, code in enumerate(["ZZ99AA11", "AA11ZZ99", "MM55MM55"]):
        make_workbook(code, {"음소쌍": [("2026.03.0%d" % (idx + 2), 1, "REGULAR", 1, 5)]},
                      seq=idx)
    result = audit(str(tmp_path / "logs"))
    from doseaudit.artifacts import exposure_rows
    _header, rows = exposure_rows(result)
    assert [r[0] for r in rows] == ["AA11ZZ99", "MM55MM55", "ZZ99AA11"]


# ── 분자와 분모는 같은 창을 본다 (적대 패널 R1 · 정확성 감사 #1) ──────────────
def test_가입_이전과_데이터컷_이후_활동은_분자에_들어가지_않는다(make_workbook, tmp_path,
                                                             make_tracker):
    """분자만 워크북 전체에서 세면 노출률이 100%를 훌쩍 넘는 값으로 부풀어 오른다."""
    make_workbook("AB12CD34", {"음소쌍": [
        ("2026.02.20", 1, "REGULAR", 1, 5),   # 가입 이전
        ("2026.02.22", 2, "REGULAR", 1, 5),   # 가입 이전
        ("2026.03.02", 3, "REGULAR", 1, 5),   # 창 안
        ("2026.03.05", 4, "REGULAR", 1, 5),   # 창 안
        ("2026.03.20", 5, "REGULAR", 1, 5),   # 데이터컷 이후
        ("2026.03.24", 6, "REGULAR", 1, 5)]}) # 데이터컷 이후
    tracker = make_tracker([("AB12CD34", "2026-03-01", 2, 14)])
    exposure = audit(str(tmp_path / "logs"), tracker,
                     window="enroll-to-cut", cut="2026-03-14").exposures[0]
    assert exposure.n_active == 2                    # 03-02, 03-05 뿐
    assert exposure.n_outside_window == 4            # 버리지 않고 센다
    assert exposure.observed_days == 14.0
    assert exposure.days_per_week == pytest.approx(1.0)
    assert exposure.adherence_pct == pytest.approx(100 * 2 / (3 * 2.0))   # 33.33%
    assert exposure.first == datetime.date(2026, 3, 2)
    assert exposure.last == datetime.date(2026, 3, 5)


def test_창_밖_활동이_트래커_대조를_어긋나게_만들지_않는다(make_workbook, tmp_path,
                                                        make_tracker):
    make_workbook("AB12CD34", {"음소쌍": [
        ("2026.02.20", 1, "REGULAR", 1, 5),
        ("2026.03.02", 2, "REGULAR", 1, 5),
        ("2026.03.05", 3, "REGULAR", 1, 5),
        ("2026.03.20", 4, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-01", 2, 14)])
    result = audit(str(tmp_path / "logs"), tracker,
                   window="enroll-to-cut", cut="2026-03-14")
    assert result.comparison["diffs"] == []
    assert result.comparison["agree"] == ["AB12CD34"]


def test_창을_정할_수_없으면_전부_세고_그렇게_말한다(make_workbook, tmp_path):
    """가입일을 모르면 창이 없다 — 그때는 전부 세되 분모를 만들지 않는다."""
    from doseaudit.dose import subject_exposure
    from doseaudit.logs import load_bundle
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 5)
                                        for d in (2, 3, 4)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    exposure = subject_exposure(bundle.subjects[0], ActiveDayRule.parse("any-row"),
                                Window.parse("fixed-weeks=8", None), None, 3.0, None)
    assert exposure.n_active == 3
    assert exposure.n_outside_window == 0
    assert exposure.observed_days is None
    assert exposure.adherence_pct is None


def test_창밖_활동일수가_CSV_와_리포트에_인쇄된다(make_workbook, tmp_path, make_tracker):
    from doseaudit.artifacts import exposure_rows
    make_workbook("AB12CD34", {"음소쌍": [("2026.02.20", 1, "REGULAR", 1, 5),
                                        ("2026.03.02", 2, "REGULAR", 1, 5)]})
    tracker = make_tracker([("AB12CD34", "2026-03-01", 1, 14)])
    result = audit(str(tmp_path / "logs"), tracker, window="enroll-to-cut", cut="2026-03-14")
    header, rows = exposure_rows(result)
    assert rows[0][header.index("창밖활동일수")] == 1
    assert rows[0][header.index("창시작일")] == "2026-03-01"
    assert rows[0][header.index("창종료일")] == "2026-03-14"
    text = "\n".join(render_console(result))
    assert "창 밖" in text
