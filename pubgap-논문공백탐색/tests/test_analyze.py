"""analyze.py 순수 함수들을 손으로 계산한 기대값과 대조."""

from pubgap.analyze import (
    GapPair,
    declining,
    emerging,
    gap_pairs,
    growth_summary,
    split_point,
    term_trends,
    top_journals,
    top_mesh,
    year_span,
    yearly_counts,
)
from pubgap.records import Article


def _mk(pmid, year, journal, mesh):
    return Article(pmid=pmid, year=year, journal=journal, title=f"t{pmid}", mesh=mesh)


# 작고 손으로 계산 가능한 코퍼스.
def sample():
    return [
        _mk("1", 2016, "A", ["Sleep", "Respiration"]),
        _mk("2", 2016, "A", ["Sleep", "EEG"]),
        _mk("3", 2017, "B", ["Sleep", "Heart Rate"]),
        _mk("4", 2021, "A", ["Sleep", "Respiration"]),
        _mk("5", 2022, "B", ["EEG", "Heart Rate"]),
        _mk("6", 2022, "C", ["Sleep", "Respiration"]),
    ]


def test_yearly_counts_and_span():
    arts = sample()
    assert yearly_counts(arts) == {2016: 2, 2017: 1, 2021: 1, 2022: 2}
    assert year_span(arts) == (2016, 2022)


def test_year_span_none_when_no_years():
    arts = [_mk("1", None, "A", ["Sleep"])]
    assert year_span(arts) is None
    assert split_point(arts) is None
    assert yearly_counts(arts) == {}


def test_top_journals_and_mesh():
    arts = sample()
    assert top_journals(arts, 2) == [("A", 3), ("B", 2)]
    tm = dict(top_mesh(arts))
    # Sleep 5편, Respiration 3, EEG 2, Heart Rate 2
    assert tm["Sleep"] == 5
    assert tm["Respiration"] == 3
    assert tm["EEG"] == 2
    assert tm["Heart Rate"] == 2


def test_top_mesh_counts_article_once_even_if_dupe_term():
    arts = [_mk("1", 2020, "A", ["Sleep", "Sleep"])]  # 방어적: 중복 주제어
    assert dict(top_mesh(arts))["Sleep"] == 1


def test_split_point_midpoint():
    # (2016 + 2022 + 1)//2 = 2019 → year>=2019 이 '최근'
    assert split_point(sample()) == 2019


def test_term_trends_shares_handcomputed():
    arts = sample()
    # 초기(<2019): pmid 1,2,3 → 3편. 최근(>=2019): 4,5,6 → 3편.
    # Sleep: 초기 3(1,2,3), 최근 2(4,6). share 3/3=1.0 → 2/3=0.667. delta=-0.333
    # Respiration: 초기 1(1), 최근 2(4,6). 1/3=0.333 → 2/3=0.667. delta=+0.333
    trends = {t.term: t for t in term_trends(arts, min_total=1)}
    assert trends["Sleep"].early_count == 3 and trends["Sleep"].recent_count == 2
    assert abs(trends["Sleep"].delta - (2 / 3 - 1.0)) < 1e-9
    assert trends["Respiration"].early_count == 1 and trends["Respiration"].recent_count == 2
    assert abs(trends["Respiration"].delta - (2 / 3 - 1 / 3)) < 1e-9


def test_emerging_declining_ordering():
    arts = sample()
    trends = term_trends(arts, min_total=1)
    em = emerging(trends)
    dec = declining(trends)
    # Respiration/EEG/Heart Rate 는 부상, Sleep 는 쇠퇴
    assert em[0].term == "Respiration"  # 가장 큰 +delta
    assert all(t.delta > 0 for t in em)
    assert dec[0].term == "Sleep"
    assert all(t.delta < 0 for t in dec)


def test_term_trends_min_total_filters_noise():
    arts = sample()
    # Sleep=5, Respiration=3, EEG=2, HeartRate=2. min_total=4 → Sleep만 남는다.
    trends = term_trends(arts, min_total=4)
    terms = {t.term for t in trends}
    assert terms == {"Sleep"}


def test_gap_pairs_handcomputed():
    arts = sample()
    # 상위 주제: Sleep5, Respiration3, EEG2, HeartRate2, N=6
    # Sleep&Respiration: obs=3(1,4,6) expected=5*3/6=2.5 lift=1.2 → 제외(>max_lift)
    # Sleep&EEG: obs=1(2) expected=5*2/6=1.667 lift=0.6 → min_expected 2.0 미만? 1.667<2 → 제외
    # Sleep&HeartRate: obs=1(3) expected=1.667 → 제외(<min_expected)
    # 낮은 min_expected 로 강제 확인:
    gaps = gap_pairs(arts, top_k=4, min_expected=1.0, max_lift=1.0)
    d = {(g.term_a, g.term_b): g for g in gaps}
    assert ("Sleep", "EEG") in d
    g = d[("Sleep", "EEG")]
    assert g.observed == 1
    assert abs(g.expected - 5 * 2 / 6) < 1e-9
    assert abs(g.lift - (1 / (5 * 2 / 6))) < 1e-9


def test_gap_pairs_min_expected_excludes_rare():
    arts = sample()
    # min_expected=2.0 이면 기대<2 인 조합은 전부 빠진다.
    gaps = gap_pairs(arts, top_k=4, min_expected=2.0, max_lift=1.0)
    for g in gaps:
        assert g.expected >= 2.0


def test_gap_pairs_sorted_by_lift_then_expected():
    # 두 개의 lift=0 조합이 있을 때 기대 큰 쪽이 먼저 오는지 실제로 확인.
    arts = [
        _mk("1", 2020, "A", ["X", "W"]),   # X&Z 는 절대 안 겹침 (기대 큼)
        _mk("2", 2020, "A", ["X", "W"]),
        _mk("3", 2020, "A", ["X", "W"]),
        _mk("4", 2020, "A", ["Z"]),
        _mk("5", 2020, "A", ["Z"]),
        _mk("6", 2020, "A", ["Z", "Y"]),   # Z&Y 겹침 없음 대상 만들기용
        _mk("7", 2020, "A", ["Y"]),
        _mk("8", 2020, "A", ["W"]),
    ]
    # 빈도: X=3, W=4, Z=3, Y=2 ; N=8
    # X&Z: obs0, expected=3*3/8=1.125
    # Y&Z: obs1, ... (겹침 있어 lift>0)
    # X&Y: obs0, expected=3*2/8=0.75
    gaps = gap_pairs(arts, top_k=4, min_expected=0.5, max_lift=0.0)
    pairs = [(g.term_a, g.term_b) for g in gaps]
    # lift 0 조합만 남고, 기대 큰 X&Z 가 기대 작은 X&Y 보다 앞에 온다.
    assert all(g.lift == 0 for g in gaps)
    assert pairs.index(("X", "Z")) < pairs.index(("X", "Y"))


def test_gap_pairs_deterministic_with_ties():
    # 동률 빈도 주제(둘 다 3편)가 top_k 선택/정렬에서 항상 같은 순서여야 한다.
    arts = [
        _mk("1", 2020, "A", ["Bbb", "Zzz"]),
        _mk("2", 2020, "A", ["Aaa", "Zzz"]),
        _mk("3", 2020, "A", ["Bbb", "Aaa"]),
        _mk("4", 2020, "A", ["Bbb"]),
        _mk("5", 2020, "A", ["Aaa"]),
        _mk("6", 2020, "A", ["Zzz"]),
    ]
    first = [(g.term_a, g.term_b) for g in gap_pairs(arts, top_k=2, min_expected=0.1, max_lift=2.0)]
    for _ in range(5):
        again = [(g.term_a, g.term_b) for g in gap_pairs(arts, top_k=2, min_expected=0.1, max_lift=2.0)]
        assert again == first


def test_top_mesh_deterministic_tiebreak():
    # Respiration/Sleep 동률(각 1편)이면 알파벳 순으로 안정적.
    arts = [_mk("1", 2020, "A", ["Sleep"]), _mk("2", 2020, "A", ["Respiration"])]
    assert top_mesh(arts) == [("Respiration", 1), ("Sleep", 1)]


def test_hypergeom_lower_tail_known_values():
    from pubgap.analyze import hypergeom_lower_tail

    # 관측이 가능한 최대면 확률 1.0
    assert abs(hypergeom_lower_tail(10, 5, 5, 5) - 1.0) < 1e-12
    # N=18,K=8,n=6,k=0 : P(X=0)=C(8,0)C(10,6)/C(18,6)=210/18564≈0.01131
    p = hypergeom_lower_tail(18, 8, 6, 0)
    assert abs(p - 210 / 18564) < 1e-9
    # 손계산: 대칭성 K/n 바꿔도 동일
    assert abs(hypergeom_lower_tail(18, 6, 8, 0) - p) < 1e-9


def test_gap_pair_has_pvalue():
    arts = sample()
    gaps = gap_pairs(arts, top_k=4, min_expected=1.0, max_lift=1.0)
    for g in gaps:
        assert 0.0 <= g.p_value <= 1.0


def test_hypergeom_clamps_out_of_range_k():
    from pubgap.analyze import hypergeom_lower_tail

    # k > min(K,n) 이면 사실상 P=1 로 클램프(예외 없이)
    assert hypergeom_lower_tail(10, 5, 3, 5) == 1.0
    # 음수 k 는 0.0
    assert hypergeom_lower_tail(10, 5, 3, -1) == 0.0
    # 경계: k == min(K,n) → 전체 확률 1.0
    assert abs(hypergeom_lower_tail(10, 4, 3, 3) - 1.0) < 1e-12


def test_growth_summary_empty():
    assert growth_summary({}) == {
        "total": 0,
        "recent_share": 0.0,
        "ratio": 0.0,
        "split": None,
        "cagr": None,
    }


def test_growth_summary_uses_given_split_consistently():
    counts = {2016: 2, 2017: 1, 2021: 1, 2022: 2}
    # 명시 split=2019 → early(<2019)=3, recent(>=2019)=3
    g = growth_summary(counts, split=2019)
    assert g["split"] == 2019
    assert g["early_total"] == 3 and g["recent_total"] == 3


def test_growth_summary_values():
    counts = {2016: 2, 2017: 1, 2021: 1, 2022: 2}
    g = growth_summary(counts)
    # 기본 split=(2016+2022+1)//2=2019. early(<2019)=3, recent(>=2019)=3
    assert g["split"] == 2019
    assert g["early_total"] == 3 and g["recent_total"] == 3
    assert g["total"] == 6
    assert abs(g["recent_share"] - 0.5) < 1e-9
    assert abs(g["ratio"] - 1.0) < 1e-9


def test_growth_summary_infinite_ratio():
    g = growth_summary({2021: 4})  # early_total=0
    assert g["ratio"] == float("inf")
