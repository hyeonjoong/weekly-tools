"""노출량 계산 — 손으로 계산한 값과 맞춰 본다."""

import datetime

import pytest

from doseaudit.dose import (STATUS_ALL_ZERO, STATUS_NO_DATA, STATUS_OK, UNCOMPARABLE,
                            Mean3, check_vendor_summaries, day_buckets,
                            duplicate_suspects, longest_gap_days, module_summaries,
                            sensitivity_table, subject_exposure)
from doseaudit.logs import load_bundle
from doseaudit.rules import ActiveDayRule, Window


# ── Mean3 — 평균은 절대 혼자 다니지 않는다 ───────────────────────────────────
def test_Mean3_는_세_값을_낸다():
    mean = Mean3([2.0, 4.0, 0.0, 6.0])
    assert mean.total_mean == pytest.approx(3.0)      # (2+4+0+6)/4
    assert mean.nonzero_mean == pytest.approx(4.0)    # (2+4+6)/3
    assert mean.zero_ratio == pytest.approx(0.25)


def test_Mean3_전부_0이면_0제외평균은_대조불가():
    mean = Mean3([0.0, 0.0, 0.0])
    assert mean.total_mean == 0.0
    assert mean.nonzero_mean is None
    assert mean.zero_ratio == 1.0
    assert mean.triple()[1] == "%s(0행 100%%)" % UNCOMPARABLE


def test_Mean3_값이_없으면_전부_대조불가():
    assert Mean3([]).triple() == (UNCOMPARABLE,) * 3
    assert Mean3([None, None]).csv_fields() == (UNCOMPARABLE,) * 3


def test_Mean3_는_해석못한_값을_평균에_넣지_않는다():
    """`None` 을 0 으로 세면 평균이 조용히 내려간다."""
    mean = Mean3([10.0, None, 20.0])
    assert mean.n == 2
    assert mean.total_mean == pytest.approx(15.0)


@pytest.mark.parametrize("values,expected_zero_ratio", [
    ([0], 1.0), ([1], 0.0), ([0, 1], 0.5), ([0, 0, 0, 1], 0.75), ([1, 2, 3], 0.0),
])
def test_Mean3_0행비율(values, expected_zero_ratio):
    assert Mean3([float(v) for v in values]).zero_ratio == pytest.approx(expected_zero_ratio)


def test_Mean3_triple_은_언제나_3칸이다():
    for values in ([], [0.0], [1.0], [0.0, 5.0], [None]):
        assert len(Mean3(values).triple()) == 3
        assert len(Mean3(values).csv_fields()) == 3


# ── 활동일·공백 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("days,expected", [
    ([], None), (["2026-03-02"], None),
    (["2026-03-02", "2026-03-03"], 0),
    (["2026-03-02", "2026-03-04"], 1),
    (["2026-03-02", "2026-03-10"], 7),
    (["2026-03-02", "2026-03-04", "2026-03-20"], 15),
])
def test_최장_공백은_사이에_낀_비활동일수다(days, expected):
    dates = {datetime.date.fromisoformat(d) for d in days}
    assert longest_gap_days(dates) == expected


def test_day_buckets_는_날짜별_모듈별로_묶는다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {
        "음소쌍": [("2026.03.02", 1, "REGULAR", 1, 5), ("2026.03.02", 2, "REGULAR", 1, 6)],
        "사전자가진단": [("2026.03.02", 1, "REGULAR", 1, 4)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    buckets = day_buckets(bundle.subjects[0])
    day = datetime.date(2026, 3, 2)
    assert set(buckets) == {day}
    assert len(buckets[day]["음소쌍"]) == 2
    assert len(buckets[day]["사전자가진단"]) == 1


def test_중복의심은_세되_제거하지_않는다(make_workbook, tmp_path):
    same = ("2026.03.02", 1, "REGULAR", 2, 5)
    make_workbook("AB12CD34", {"음소쌍": [same, same, same,
                                        ("2026.03.03", 1, "REGULAR", 2, 5)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    subject = bundle.subjects[0]
    total, per_module = duplicate_suspects(subject)
    assert total == 2                       # 3번 나왔으니 반복은 2건
    assert per_module["음소쌍"] == 2
    assert subject.modules["음소쌍"].n_rows == 4    # 하나도 지우지 않았다


def test_노출량_전체를_손계산과_맞춘다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [
        ("2026.03.02", 1, "REGULAR", 1, 10),
        ("2026.03.02", 2, "REGULAR", 1, 0),
        ("2026.03.09", 3, "REGULAR", 1, 20),
        ("2026.03.16", 4, "REGULAR", 1, 30)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    exposure = subject_exposure(
        bundle.subjects[0], ActiveDayRule.parse("any-row"),
        Window.parse("enroll-to-cut", "2026-03-29"), datetime.date(2026, 3, 2), 3.0, None)
    assert exposure.n_active == 3                       # 03-02, 03-09, 03-16
    assert exposure.observed_days == 28.0               # 3/2 ~ 3/29, 양 끝 포함
    assert exposure.days_per_week == pytest.approx(3 / 4.0)
    # 최장공백은 **창의 양 끝도 공백으로 센다**. 손계산:
    #   창 시작(03-02) → 첫 활동(03-02)      =  0일
    #   03-02 ~ 03-09 사이                   =  6일
    #   03-09 ~ 03-16 사이                   =  6일
    #   마지막 활동(03-16) → 창 끝(03-29)     = 13일  ← 최댓값
    assert exposure.longest_gap == 13
    assert exposure.first == datetime.date(2026, 3, 2)
    assert exposure.last == datetime.date(2026, 3, 16)
    assert exposure.n_rows == 4
    assert exposure.n_zero_sec == 1
    # 3 / (3 * 4주) * 100 = 25%
    assert exposure.adherence_pct == pytest.approx(25.0)
    assert exposure.capped is False


def test_상한_절단은_줄_때만_한다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.%02d" % d, 1, "REGULAR", 1, 5)
                                        for d in range(2, 9)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    args = (bundle.subjects[0], ActiveDayRule.parse("any-row"),
            Window.parse("enroll-to-cut", "2026-03-08"), datetime.date(2026, 3, 2), 3.0)
    uncapped = subject_exposure(*args, None)
    assert uncapped.adherence_pct > 100
    assert uncapped.capped is False
    capped = subject_exposure(*args, 100)
    assert capped.adherence_pct == 100.0
    assert capped.capped is True


def test_처방을_선언하지_않으면_노출률을_내지_않는다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    exposure = subject_exposure(bundle.subjects[0], ActiveDayRule.parse("any-row"),
                                Window.parse("fixed-weeks=8", None), None, None, None)
    assert exposure.adherence_pct is None


# ── 모듈 요약 ────────────────────────────────────────────────────────────────
def test_데이터_0행_모듈은_평균_0_이_아니라_데이터_없음(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    summary = module_summaries(bundle)["발성훈련"]
    assert summary.status == STATUS_NO_DATA
    assert summary.n_rows == 0
    assert summary.seconds.triple() == (UNCOMPARABLE,) * 3


def test_전_행이_0초인_모듈은_단일_평균을_내보내지_않는다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.0%d" % d, 1, "REGULAR", 1, 0)
                                        for d in range(2, 5)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    summary = module_summaries(bundle)["음소쌍"]
    assert summary.status == STATUS_ALL_ZERO
    assert summary.seconds.nonzero_mean is None
    assert UNCOMPARABLE in summary.seconds.csv_fields()[1]


def test_정상_모듈은_데이터_있음(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    assert module_summaries(load_bundle(str(tmp_path / "logs")))["음소쌍"].status == STATUS_OK


def test_훈련유형을_분리해_센다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [
        ("2026.03.02", 1, "REGULAR", 1, 10),
        ("2026.03.03", 1, "REGULAR", 1, 20),
        ("2026.03.04", 1, "INDIVIDUAL", 1, 60)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    summary = module_summaries(bundle)["음소쌍"]
    assert summary.by_type["REGULAR"]["n_rows"] == 2
    assert summary.by_type["INDIVIDUAL"]["n_rows"] == 1
    assert summary.by_type["REGULAR"]["seconds"].total_mean == pytest.approx(15.0)
    assert summary.by_type["INDIVIDUAL"]["seconds"].total_mean == pytest.approx(60.0)


def test_pool_types_를_주면_유형을_합친다(make_workbook, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": [("2026.03.02", 1, "REGULAR", 1, 10),
                                        ("2026.03.03", 1, "INDIVIDUAL", 1, 20)]})
    bundle = load_bundle(str(tmp_path / "logs"))
    assert module_summaries(bundle, pool_types=True)["음소쌍"].by_type == {}
    assert module_summaries(bundle, pool_types=False)["음소쌍"].by_type != {}


def test_모듈_요약은_파일_수를_센다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows}, seq=1)
    make_workbook("EF56GH78", {}, seq=2)
    bundle = load_bundle(str(tmp_path / "logs"))
    summary = module_summaries(bundle)["음소쌍"]
    assert (summary.n_files, summary.n_files_with_data) == (2, 1)


# ── 벤더 [요약] 재계산 ───────────────────────────────────────────────────────
def test_벤더_요약이_맞으면_틀렸다고_말하지_않는다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    check = check_vendor_summaries(load_bundle(str(tmp_path / "logs")))
    assert check.mismatches == []
    assert check.n_agree == check.n_checked == 1


def test_벤더_요약이_실제로_어긋나면_잡는다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows}, vendor={"음소쌍": (999, 999)})
    check = check_vendor_summaries(load_bundle(str(tmp_path / "logs")))
    assert len(check.mismatches) == 2       # 소요시간·반복횟수 둘 다
    assert check.n_agree == 0


def test_데이터_0행_시트는_벤더_대조_대상이_아니다(make_workbook, basic_rows, tmp_path):
    """평균을 낼 대상이 없는 시트를 '불일치'로 세면 매번 우는 체커가 된다."""
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    check = check_vendor_summaries(load_bundle(str(tmp_path / "logs")))
    assert check.n_empty_with_block == 5
    assert check.n_no_block == 0


@pytest.mark.parametrize("raw,expected", [
    ("4.34초", 2), ("2회", 0), ("0초", 0), ("4.0초", 1), ("12", 0), ("1.234초", 3),
])
def test_인쇄된_자릿수를_읽는다(raw, expected):
    """벤더는 열마다 다른 자릿수로 인쇄한다(실측: 소요시간 2자리, 반복횟수 0자리)."""
    from doseaudit.dose import printed_decimals
    assert printed_decimals(raw) == expected


def test_벤더_대조는_인쇄된_자릿수로_한다(make_workbook, tmp_path):
    """`abs(차이) < 1.0` 로 보면 평균 2.9 와 인쇄값 2 가 '일치'가 된다."""
    rows = [("2026.03.0%d" % d, 1, "REGULAR", 3, 10) for d in range(2, 5)]
    rows += [("2026.03.0%d" % d, 1, "REGULAR", 1, 10) for d in range(5, 7)]
    make_workbook("AB12CD34", {"음소쌍": rows}, vendor={"음소쌍": (10, 2)})
    check = check_vendor_summaries(load_bundle(str(tmp_path / "logs")))
    # 실제 평균 반복횟수 = (3*3 + 1*2)/5 = 2.2 → 반올림 2 → 일치
    assert check.mismatches == []
    make_workbook("EF56GH78", {"음소쌍": rows}, vendor={"음소쌍": (10, 1)}, seq=2)
    check = check_vendor_summaries(load_bundle(str(tmp_path / "logs")))
    # 인쇄값 1 vs 재계산 2.2 → 옛 규칙(차이<1.0)은 놓치고, 지금은 잡는다
    assert any(m[0] == "EF56GH78" for m in check.mismatches)


def test_재계산할_값이_없으면_인쇄값_0을_일치로_세지_않는다(tmp_path):
    """'미측정'을 '0을 측정함'으로 읽는 것이 이 툴이 막으려는 바로 그 사고다."""
    from xlsxwrite import MODULES, module_sheet, write_xlsx
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = [["[요약]"], ["평균 소요시간", "0초"], ["평균 반복횟수", "0회"], None,
            ["날짜", "레벨", "훈련유형", "반복횟수", "소요시간(초)"],
            ["2026.03.02", "1", "REGULAR", "해석불가", "해석불가"]]
    write_xlsx(str(folder / "AB12CD34_x_1.xlsx"),
               [(MODULES[0], rows)] + [(m, module_sheet([], 0, 0)) for m in MODULES[1:]])
    check = check_vendor_summaries(load_bundle(str(folder)))
    assert check.n_agree == 0
    # 재계산할 값이 없으면 `대조불가` 다 — **`벤더가 틀렸다`(mismatches) 가 아니다.**
    # 남은 행으로 낸 평균을 근거로 벤더를 지목하는 것이 이 툴이 절대 하지 않겠다고 한 말이라,
    # 이 건은 [치명] 이 아니라 [경고]로 나가야 한다.
    assert any(UNCOMPARABLE in m[4] for m in check.uncomparable)
    assert not check.mismatches


# ── 정의 민감도 ──────────────────────────────────────────────────────────────
def test_민감도_표는_선언된_규칙과_대조_규칙들을_함께_낸다(make_workbook, tmp_path):
    for idx, code in enumerate(["AB12CD34", "EF56GH78", "IJ90KL12"]):
        make_workbook(code, {"음소쌍": [("2026.03.%02d" % (2 + d), 1, "REGULAR", 1, 5)
                                      for d in range(idx + 1)]}, seq=idx)
    bundle = load_bundle(str(tmp_path / "logs"))
    enrolls = {s.code: datetime.date(2026, 3, 2) for s in bundle.subjects}
    rows = sensitivity_table(bundle, ActiveDayRule.parse("any-row"),
                             Window.parse("fixed-weeks=4", None), enrolls, 3.0, None)
    assert len(rows) >= 4
    assert sum(1 for r in rows if r["declared"]) == 1
    assert any("cap" in r["label"] for r in rows)
    assert all(r["n"] == 3 for r in rows)


def test_민감도_표는_상한을_이미_준_경우_중복하지_않는다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    rows = sensitivity_table(bundle, ActiveDayRule.parse("any-row"),
                             Window.parse("fixed-weeks=4", None),
                             {"AB12CD34": datetime.date(2026, 3, 2)}, 3.0, 100)
    assert not any("cap" in r["label"] for r in rows)


def test_민감도_표에_권장_어휘가_없다(make_workbook, basic_rows, tmp_path):
    make_workbook("AB12CD34", {"음소쌍": basic_rows})
    bundle = load_bundle(str(tmp_path / "logs"))
    rows = sensitivity_table(bundle, ActiveDayRule.parse("any-row"),
                             Window.parse("fixed-weeks=4", None),
                             {"AB12CD34": datetime.date(2026, 3, 2)}, 3.0, None)
    text = " ".join(r["label"] for r in rows)
    for word in ("권장", "추천", "바람직", "best", "recommended"):
        assert word not in text
