"""분석 전 변환: 대표주제 한정 / 키워드 보강 / 연도 필터."""

from pubgap.records import (
    Article,
    apply_include_keywords,
    apply_major_only,
    filter_years,
)


def _mk(pmid, year, mesh, major=None, kw=None):
    return Article(
        pmid=pmid, year=year, journal="J", title="t",
        mesh=list(mesh), mesh_major=list(major or []), keywords=list(kw or []),
    )


def test_apply_major_only_replaces_mesh():
    arts = [_mk("1", 2020, ["Sleep", "Respiration", "EEG"], major=["Sleep"])]
    out = apply_major_only(arts)
    assert out[0].mesh == ["Sleep"]
    # 원본은 불변(dataclasses.replace 로 새 객체)
    assert arts[0].mesh == ["Sleep", "Respiration", "EEG"]


def test_apply_major_only_empty_when_no_major():
    arts = [_mk("1", 2020, ["Sleep", "EEG"], major=[])]
    assert apply_major_only(arts)[0].mesh == []


def test_include_keywords_merges_and_dedups_caseinsensitive():
    arts = [_mk("1", 2020, ["Sleep"], kw=["sleep", "Vagal Tone", "vagal tone"])]
    out = apply_include_keywords(arts)
    # 'sleep' 는 MeSH 'Sleep' 과 대소문자만 다르므로 제외, 'Vagal Tone' 만 추가,
    # 키워드 내부 중복('vagal tone')도 제거.
    assert out[0].mesh == ["Sleep", "Vagal Tone"]


def test_include_keywords_no_keywords_is_noop():
    arts = [_mk("1", 2020, ["Sleep"], kw=[])]
    assert apply_include_keywords(arts)[0].mesh == ["Sleep"]


def test_include_keywords_global_case_canonicalization():
    # 회귀(라운드2 버그): MeSH 'Sleep'(논문1,2) 와 키워드 'sleep'(논문3,4) 가
    # 대소문자만 달라도 하나의 주제('Sleep')로 합쳐져야 한다 — 코퍼스 전역 표준화.
    from pubgap.analyze import top_mesh

    arts = [
        _mk("1", 2020, ["Sleep"]),
        _mk("2", 2020, ["Sleep"]),
        _mk("3", 2021, [], kw=["sleep", "Exercise"]),
        _mk("4", 2021, [], kw=["sleep", "exercise"]),
    ]
    merged = apply_include_keywords(arts)
    tm = dict(top_mesh(merged))
    assert tm.get("Sleep") == 4          # sleep 키워드가 MeSH Sleep 로 병합
    assert "sleep" not in tm             # 소문자 별도 주제로 쪼개지지 않음
    assert tm.get("Exercise") == 2       # Exercise/exercise 도 하나로
    assert "exercise" not in tm


def test_filter_years_inclusive_bounds():
    arts = [
        _mk("1", 2015, ["A"]),
        _mk("2", 2018, ["B"]),
        _mk("3", 2020, ["C"]),
        _mk("4", None, ["D"]),
    ]
    out = filter_years(arts, min_year=2018, max_year=2020)
    assert [a.pmid for a in out] == ["2", "3"]  # 미상(None)은 제외


def test_filter_years_none_bounds_keeps_all():
    arts = [_mk("1", 2015, ["A"]), _mk("2", None, ["B"])]
    out = filter_years(arts, None, None)
    assert len(out) == 2


def test_filter_years_only_min():
    arts = [_mk("1", 2015, ["A"]), _mk("2", 2020, ["B"]), _mk("3", None, ["C"])]
    out = filter_years(arts, min_year=2018)
    assert [a.pmid for a in out] == ["2"]  # None 은 경계 있으면 제외


def test_filter_years_only_max():
    arts = [_mk("1", 2015, ["A"]), _mk("2", 2020, ["B"]), _mk("3", None, ["C"])]
    out = filter_years(arts, max_year=2018)
    assert [a.pmid for a in out] == ["1"]  # None 제외, 2020 제외
