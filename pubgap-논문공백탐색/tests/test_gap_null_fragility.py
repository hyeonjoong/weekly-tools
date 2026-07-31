"""색인 밀도 보정 귀무모형(--gap-null degree)과 취약도(fragility)의 검증.

이 파일의 원칙: **닫힌 형태로 손계산이 가능한 경우**를 만들어 구현과 대조한다.
- 포아송이항 하단꼬리 ↔ 확률이 모두 같으면 이항분포 CDF(닫힌 형태).
- BHProbe ↔ `benjamini_hochberg` 전체 재계산(무차별 대조).
- degree 기대값 ↔ E = c_B · Σ(m_i − 1) / (M − c_A) 를 손으로 계산.
- 취약도 d ↔ obs+d−1 에서는 q≤α, obs+d 에서는 q>α (전체 BH 재계산으로 확인).
"""

from __future__ import annotations

import random
from math import comb

import pytest

from pubgap import analyze, report
from pubgap.records import Article


def _art(pmid: str, mesh, year: int = 2020) -> Article:
    return Article(pmid=pmid, title=f"t{pmid}", journal="J", year=year, mesh=list(mesh))


# --------------------------------------------------------------------------- #
# 포아송이항 하단꼬리
# --------------------------------------------------------------------------- #
def test_poisson_binomial_matches_binomial_when_probs_equal():
    """확률이 모두 같으면 이항분포 CDF 와 정확히 같아야 한다(닫힌 형태 대조)."""
    p, n = 0.27, 15
    for k in range(0, n + 1):
        expected = sum(comb(n, j) * p**j * (1 - p) ** (n - j) for j in range(k + 1))
        assert analyze.poisson_binomial_lower_tail([p] * n, k) == pytest.approx(
            expected, abs=1e-12
        )


def test_poisson_binomial_two_unequal_probs_by_hand():
    """p=[0.2, 0.5]: P(X<=0)=0.8*0.5=0.40, P(X<=1)=1-0.2*0.5=0.90."""
    probs = [0.2, 0.5]
    assert analyze.poisson_binomial_lower_tail(probs, 0) == pytest.approx(0.40)
    assert analyze.poisson_binomial_lower_tail(probs, 1) == pytest.approx(0.90)
    assert analyze.poisson_binomial_lower_tail(probs, 2) == 1.0


def test_poisson_binomial_edges():
    assert analyze.poisson_binomial_lower_tail([0.5, 0.5], -1) == 0.0
    assert analyze.poisson_binomial_lower_tail([], 0) == 1.0
    # p=0 인 논문(주제어가 하나뿐)은 성공에 기여하지 않는다.
    assert analyze.poisson_binomial_lower_tail([0.0, 0.0, 0.4], 0) == pytest.approx(0.6)
    # p=1 은 반드시 성공 → P(X<=0)=0
    assert analyze.poisson_binomial_lower_tail([1.0, 0.3], 0) == pytest.approx(0.0)


def test_poisson_binomial_is_monotone_and_bounded():
    random.seed(11)
    probs = [random.random() * 0.4 for _ in range(40)]
    tails = [analyze.poisson_binomial_lower_tail(probs, k) for k in range(0, 20)]
    assert all(0.0 <= t <= 1.0 for t in tails)
    assert tails == sorted(tails)


def test_poisson_binomial_large_input_is_fast_and_sane():
    """3000편 x k=5 절단 DP — 초기하와 같은 자릿수의 답을 즉시 내야 한다."""
    probs = [0.01] * 3000
    got = analyze.poisson_binomial_lower_tail(probs, 5)
    # 기대 30, 관측<=5 → 아주 작은 확률.
    assert 0.0 < got < 1e-6


# --------------------------------------------------------------------------- #
# BHProbe — 한 후보의 p 만 바꿔 q 다시 계산
# --------------------------------------------------------------------------- #
def test_bh_probe_matches_full_recomputation():
    random.seed(3)
    for _ in range(200):
        m = random.randint(1, 30)
        ps = [round(random.random(), 3) for _ in range(m)]
        i = random.randrange(m)
        probe = analyze.BHProbe(ps, i)
        for _ in range(5):
            # 동점(기존 값과 같은 p)도 일부러 섞는다 — 삽입 위치가 애매한 경우.
            p_new = random.choice([round(random.random(), 3), random.choice(ps)])
            modified = list(ps)
            modified[i] = p_new
            assert probe.q(p_new) == pytest.approx(
                analyze.benjamini_hochberg(modified)[i], abs=1e-12
            )


def test_bh_probe_single_candidate():
    probe = analyze.BHProbe([0.001], 0)
    assert probe.q(0.001) == pytest.approx(0.001)
    assert probe.q(0.9) == pytest.approx(0.9)
    assert probe.q(2.0) == 1.0  # q 는 1 을 넘지 않는다


# --------------------------------------------------------------------------- #
# 색인 밀도 보정 귀무모형
# --------------------------------------------------------------------------- #
def test_degree_null_expected_by_hand():
    """E = c_B · Σ_{i∈A}(m_i − 1) / (M − c_A) 를 손으로 계산해 대조."""
    sizes = [3, 5, 1]          # A 를 단 논문 세 편의 주제어 수
    total_headings = 40
    count_anchor, count_other = 3, 8
    expected, probs, saturated = analyze.degree_null_pair(
        sizes, [0, 1, 2], count_anchor, count_other, total_headings
    )
    assert saturated is False
    slots = 40 - 3
    hand = 8 * (2 / slots) + 8 * (4 / slots)   # m=1 인 논문은 기여 0
    assert expected == pytest.approx(hand)
    assert len(probs) == 2      # 주제어가 하나뿐인 논문은 확률 목록에서 빠진다


def test_degree_null_single_heading_articles_cannot_cooccur():
    """주제어가 하나뿐인 논문만 있으면 기대 동시등장은 정확히 0 이어야 한다."""
    expected, probs, _ = analyze.degree_null_pair([1, 1, 1], [0, 1, 2], 3, 5, 30)
    assert expected == 0.0
    assert probs == []


def test_degree_null_probabilities_are_clamped_to_one():
    """c_B 가 크면 확률이 1 을 넘을 수 있다 — 잘라내고, **포화를 신고**해야 한다."""
    expected, probs, saturated = analyze.degree_null_pair([10], [0], 1, 100, 12)
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert expected <= len(probs)
    assert saturated is True, "raw p_i > 1 이면 모형이 성립하지 않음을 알려야 한다"


def test_degree_null_marginal_is_preserved_over_whole_corpus():
    """전체 논문을 anchor 로 잡고 합하면 Σp = c_B (잘라내기가 걸리지 않는 범위에서)."""
    sizes = [4, 6, 3, 7, 5]
    total = sum(sizes)
    count_other = 2   # 확률이 1 을 넘지 않도록 작게 — 잘라내기가 개입하면 합이 줄어든다
    # anchor 가 '모든 논문에 등장하는 주제'라면 c_A = 논문 수.
    expected, _, saturated = analyze.degree_null_pair(
        sizes, list(range(len(sizes))), len(sizes), count_other, total
    )
    assert saturated is False
    assert expected == pytest.approx(count_other)


def test_degree_null_rejects_degenerate_totals():
    assert analyze.degree_null_pair([3], [0], 5, 4, 3) == (0.0, [], False)   # slots <= 0
    assert analyze.degree_null_pair([3], [0], 1, 0, 30) == (0.0, [], False)  # c_other = 0


def test_gap_pairs_degree_null_end_to_end():
    """모든 논문의 주제어 수가 같으면 degree 기대값은 손계산과 일치한다."""
    arts = [_art(str(i), ["Alpha", "Gamma", "Delta"]) for i in range(60)]
    arts += [_art(str(i), ["Beta", "Gamma", "Delta"]) for i in range(60, 120)]
    gaps = analyze.gap_pairs(arts, top_k=6, null="degree")
    ab = [g for g in gaps if {g.term_a, g.term_b} == {"Alpha", "Beta"}]
    assert ab, "Alpha × Beta 는 완전 배타이므로 반드시 공백으로 잡혀야 한다"
    g = ab[0]
    # m_i = 3, M = 360, c_A = c_B = 60 → E = 60 · 60·2 / (360−60) = 24
    assert g.expected == pytest.approx(24.0)
    assert g.expected_independent == pytest.approx(60 * 60 / 120)
    assert g.null_model == "degree"
    assert g.observed == 0
    assert g.p_value < 1e-6


def test_degree_null_is_more_conservative_on_thin_indexing():
    """얇게 색인된 코퍼스에서는 degree 기대값이 독립가정보다 작아야 한다.

    (독립가정은 '주제어 2개짜리 논문도 어떤 쌍이든 가질 수 있다'고 보므로 공백을
    과대평가한다. 이 보정이 이 옵션의 존재 이유다.)
    """
    arts = [_art(str(i), ["Alpha", "Gamma"]) for i in range(40)]
    arts += [_art(str(i), ["Beta", "Gamma"]) for i in range(40, 80)]
    ind = {(g.term_a, g.term_b): g for g in analyze.gap_pairs(arts, top_k=6)}
    deg = {(g.term_a, g.term_b): g for g in analyze.gap_pairs(arts, top_k=6, null="degree")}
    key = ("Alpha", "Beta")
    assert key in ind and key in deg
    assert deg[key].expected < ind[key].expected
    assert deg[key].p_value > ind[key].p_value


def test_gap_pairs_rejects_unknown_null():
    with pytest.raises(ValueError, match="귀무모형"):
        analyze.gap_pairs([_art("1", ["A", "B"])], null="bogus")


def test_independent_null_unchanged_by_default():
    """기본값은 예전 그대로여야 한다(회귀 방지)."""
    arts = [_art(str(i), ["Alpha", "Gamma", "Delta"]) for i in range(30)]
    arts += [_art(str(i), ["Beta", "Gamma", "Delta"]) for i in range(30, 60)]
    g = analyze.gap_pairs(arts, top_k=6)[0]
    assert g.null_model == "independent"
    assert g.expected == pytest.approx(g.expected_independent)
    assert g.p_value == pytest.approx(
        analyze.hypergeom_lower_tail(60, g.count_a, g.count_b, g.observed)
    )


# --------------------------------------------------------------------------- #
# 취약도(fragility)
# --------------------------------------------------------------------------- #
def _strong_gap_corpus(n_each: int = 60):
    arts = [_art(str(i), ["Alpha", "Gamma", "Delta"]) for i in range(n_each)]
    arts += [_art(str(i), ["Beta", "Gamma", "Delta"]) for i in range(n_each, 2 * n_each)]
    return arts


def test_fragility_boundary_verified_by_full_bh_recomputation():
    """취약도 d 는 'd−1 편까지는 살아남고 d 편에서 무너진다'를 정확히 만족해야 한다."""
    arts = _strong_gap_corpus()
    gaps = analyze.gap_pairs(arts, top_k=6)
    g = [x for x in gaps if {x.term_a, x.term_b} == {"Alpha", "Beta"}][0]
    assert g.fragility is not None and g.fragility >= 1

    # 같은 검정군을 다시 만들어(후보 = 이 코퍼스의 모든 쌍) 전체 BH 로 대조한다.
    all_gaps = analyze.gap_pairs(arts, top_k=6, max_lift=float("inf"))
    ps = [x.p_value for x in all_gaps]
    idx = [i for i, x in enumerate(all_gaps) if {x.term_a, x.term_b} == {"Alpha", "Beta"}][0]

    def q_at(k: int) -> float:
        v = list(ps)
        v[idx] = analyze.hypergeom_lower_tail(len(arts), g.count_a, g.count_b, k)
        return analyze.benjamini_hochberg(v)[idx]

    assert q_at(g.observed + g.fragility) > 0.05
    assert q_at(g.observed + g.fragility - 1) <= 0.05


def test_fragility_is_none_for_nonsignificant_gap():
    """q>0.05 인 줄에는 '몇 편이면 무너지나'를 물을 수 없다."""
    arts = [_art(str(i), ["Alpha", "Gamma"]) for i in range(5)]
    arts += [_art(str(i), ["Beta", "Gamma"]) for i in range(5, 10)]
    for g in analyze.gap_pairs(arts, top_k=6, min_expected=0.5):
        if g.q_value > 0.05:
            assert g.fragility is None
            assert g.fragility_capped is False


def test_fragility_capped_flag_for_very_robust_gap():
    """탐색 상한을 넘도록 견고한 공백은 값 대신 상한 표시를 단다."""
    arts = _strong_gap_corpus(n_each=400)
    gaps = analyze.gap_pairs(arts, top_k=6)
    g = [x for x in gaps if {x.term_a, x.term_b} == {"Alpha", "Beta"}][0]
    assert g.fragility is None
    assert g.fragility_capped is True
    assert g.fragility_tested == analyze.FRAGILITY_MAX_STEPS


def test_capped_label_never_claims_untested_depth():
    """관측 상한 때문에 40편을 다 못 얹었다면 그 사실대로 적어야 한다.

    (예전 구현은 무조건 '≥40편'이라고 적어, **시험하지 않은 범위까지 견고하다고
    주장**했다. 견고성 표시는 실제로 시험한 편수만 말해야 한다.)
    """
    gp = {"fragility_capped": True, "fragility_tested": 7}
    assert report._fragility_text(gp) == "≥7편"
    assert report._fragility_text({"fragility": 3}) == "+3편"
    assert report._fragility_text({}) == "–"


def test_fragility_not_claimed_when_at_observation_ceiling():
    """더 얹을 수 없는 쌍(관측 = min(cA,cB))에는 견고성을 주장하지 않는다."""
    arts = [_art(str(i), ["Alpha", "Beta", "Gamma"]) for i in range(2)]
    arts += [_art(str(i), ["Alpha", "Gamma", "Delta"]) for i in range(2, 50)]
    arts += [_art(str(i), ["Beta", "Gamma", "Delta"]) for i in range(50, 100)]
    for g in analyze.gap_pairs(arts, top_k=6, min_expected=0.5, max_lift=float("inf")):
        if g.observed >= min(g.count_a, g.count_b):
            assert g.fragility is None
            assert g.fragility_capped is False
            assert g.fragility_tested == 0


def test_fragility_never_exceeds_observation_ceiling():
    """동시등장은 min(cA,cB) 를 넘을 수 없다 — 그 위를 묻지 않아야 한다."""
    arts = [_art(str(i), ["Alpha", "Beta", "Gamma"]) for i in range(3)]
    arts += [_art(str(i), ["Alpha", "Gamma", "Delta"]) for i in range(3, 40)]
    arts += [_art(str(i), ["Beta", "Gamma", "Delta"]) for i in range(40, 80)]
    for g in analyze.gap_pairs(arts, top_k=6):
        if g.fragility is not None:
            assert g.observed + g.fragility <= min(g.count_a, g.count_b)


def test_fragility_computed_under_the_active_null():
    """degree 모형에서는 포아송이항으로 취약도를 재야 한다(값이 달라진다)."""
    arts = _strong_gap_corpus()
    ind = [g for g in analyze.gap_pairs(arts, top_k=6)
           if {g.term_a, g.term_b} == {"Alpha", "Beta"}][0]
    deg = [g for g in analyze.gap_pairs(arts, top_k=6, null="degree")
           if {g.term_a, g.term_b} == {"Alpha", "Beta"}][0]
    assert ind.fragility is not None and deg.fragility is not None
    # degree 는 더 보수적이라 더 적은 편수로 무너진다.
    assert deg.fragility < ind.fragility


def test_fragility_alpha_is_honoured():
    arts = _strong_gap_corpus()
    loose = [g for g in analyze.gap_pairs(arts, top_k=6, fragility_alpha=0.20)
             if {g.term_a, g.term_b} == {"Alpha", "Beta"}][0]
    tight = [g for g in analyze.gap_pairs(arts, top_k=6, fragility_alpha=0.001)
             if {g.term_a, g.term_b} == {"Alpha", "Beta"}][0]
    assert loose.fragility > tight.fragility


# --------------------------------------------------------------------------- #
# 리포트 배선(Markdown / JSON / CSV / CLI)
# --------------------------------------------------------------------------- #
def test_markdown_shows_fragility_column_and_null_note():
    arts = _strong_gap_corpus()
    rep = report.build_report(arts, "syn", gap_top_k=6)
    md = report.render_markdown(rep)
    assert "| 취약도 |" in md
    assert "fragility index" in md
    assert "독립가정(초기하)" in md
    assert "+" in md and "편 |" in md


def test_markdown_degree_null_note_and_pvalue_label():
    arts = _strong_gap_corpus()
    md = report.render_markdown(
        report.build_report(arts, "syn", gap_top_k=6, gap_null="degree")
    )
    assert "색인 밀도 보정" in md
    assert "포아송이항 하단꼬리" in md


def test_summary_reports_robustness_of_top_pick():
    arts = _strong_gap_corpus()
    md = report.render_markdown(report.build_report(arts, "syn", gap_top_k=6))
    assert "견고성(취약도)" in md


def test_summary_robustness_wording_for_capped_gap():
    arts = _strong_gap_corpus(n_each=400)
    md = report.render_markdown(report.build_report(arts, "syn", gap_top_k=6))
    assert f"**{analyze.FRAGILITY_MAX_STEPS}편** 더" in md


def test_json_carries_new_fields():
    arts = _strong_gap_corpus()
    rep = report.build_report(arts, "syn", gap_top_k=6, gap_null="degree")
    assert rep["gap_null"] == "degree"
    g = rep["gaps"][0]
    for key in ("fragility", "fragility_capped", "null_model", "expected_independent"):
        assert key in g
    assert g["null_model"] == "degree"


def test_csv_carries_new_columns():
    arts = _strong_gap_corpus()
    rep = report.build_report(arts, "syn", gap_top_k=6)
    text = report.render_csv(rep).lstrip("﻿")
    header = text.splitlines()[0].split(",")
    for col in ("null_model", "expected_independent", "fragility", "fragility_capped"):
        assert col in header
    row = text.splitlines()[1].split(",")
    assert row[header.index("null_model")] == "independent"


def test_cli_accepts_gap_null(tmp_path, capsys):
    from pubgap import cli

    rc = cli.main(["--from-file", "examples/sleep_pubmed.xml", "--gap-null", "degree"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "색인 밀도 보정" in out


def test_cli_rejects_bad_gap_null():
    from pubgap import cli

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--from-file", "x.xml", "--gap-null", "nope"])


def test_gap_null_recorded_in_run_metadata(capsys):
    """재현성 블록은 결과에 영향을 주는 옵션을 빠짐없이 남겨야 한다."""
    from pubgap import cli

    assert cli.main(["--from-file", "examples/sleep_pubmed.xml", "--gap-null", "degree"]) == 0
    out = capsys.readouterr().out
    assert "gap_null" in out


# --------------------------------------------------------------------------- #
# 하드닝 R4 — 문서 정직성 리뷰어가 지적한 결함의 회귀 테스트
# --------------------------------------------------------------------------- #
def test_degree_null_can_strengthen_a_gap_not_only_weaken_it():
    """degree 모형은 '헛공백 필터'가 아니다 — 공백을 **더 강하게** 만들 수도 있다.

    앵커 주제의 논문들이 코퍼스 평균보다 두껍게 색인돼 있으면 기대값이 올라가
    독립가정에서 유의하지 않던 쌍이 유의해진다. 문서가 한쪽 방향만 말하면
    사용자는 이 옵션을 '진실 필터'로 오해한다(리뷰어 지적).
    """
    arts = []
    # Alpha·Beta 의 논문은 두껍게 색인(주제어 8개), 나머지는 얇게(2개).
    for i in range(12):
        arts.append(_art(f"a{i}", ["Alpha", "Gamma", "Delta", "Eps", "Zeta",
                                   "Eta", "Theta", "Iota"]))
    for i in range(12):
        arts.append(_art(f"b{i}", ["Beta", "Gamma", "Delta", "Eps", "Zeta",
                                   "Eta", "Theta", "Iota"]))
    for i in range(60):
        arts.append(_art(f"c{i}", ["Gamma", "Delta"]))
    kw = dict(top_k=10, min_expected=0.5, max_lift=float("inf"))
    ind = {(g.term_a, g.term_b): g for g in analyze.gap_pairs(arts, **kw)}
    deg = {(g.term_a, g.term_b): g for g in analyze.gap_pairs(arts, null="degree", **kw)}
    key = ("Alpha", "Beta")
    assert deg[key].expected > ind[key].expected
    assert deg[key].p_value < ind[key].p_value


def test_report_does_not_claim_degree_null_removes_false_gaps():
    """각주는 '제거'가 아니라 '재계산'이라고 말해야 하고, 양방향을 밝혀야 한다."""
    arts = _strong_gap_corpus()
    for null in ("independent", "degree"):
        md = report.render_markdown(
            report.build_report(arts, "syn", gap_top_k=6, gap_null=null)
        )
        note = md.split("_귀무모형:")[1].split("\n")[0]
        # 재계산이라는 점과, 공백이 **강해질 수도** 있다는 점이 둘 다 있어야 한다.
        assert ("제거가 아니라" in note) or ("가려 주지는 않습니다" in note)
        assert "강해질" in note or "강해집니다" in note


def test_report_states_index_density_so_the_null_choice_is_actionable():
    """'얇게 색인됐으면 degree 를 쓰라'는 조언에는 관측치가 따라와야 한다."""
    arts = [_art(str(i), ["Alpha", "Gamma", "Delta"]) for i in range(20)]
    arts += [_art(str(i), ["Beta", "Gamma", "Delta"]) for i in range(20, 40)]
    rep = report.build_report(arts, "syn", gap_top_k=6)
    assert rep["mesh_per_article"] == pytest.approx(3.0)
    assert "논문 1편당 평균 3.0개 주제어" in report.render_markdown(rep)


def test_mesh_density_ignores_articles_without_topics():
    """주제어가 없는 논문은 밀도의 분모에서 빠져야 한다(0 으로 희석되면 거짓말)."""
    arts = [_art("1", ["Alpha", "Beta", "Gamma", "Delta"]), _art("2", [])]
    rep = report.build_report(arts, "syn")
    assert rep["n_with_mesh"] == 1
    assert rep["mesh_per_article"] == pytest.approx(4.0)


def test_deficit_footnote_names_the_active_null():
    """`부족` 은 활성 귀무모형의 기대값과의 차이다 — 각주가 그렇게 말해야 한다."""
    arts = _strong_gap_corpus()
    md_ind = report.render_markdown(report.build_report(arts, "s", gap_top_k=6))
    md_deg = report.render_markdown(
        report.build_report(arts, "s", gap_top_k=6, gap_null="degree")
    )
    assert "`부족`=기대−관측(**독립 가정 대비**" in md_ind
    assert "`부족`=기대−관측(**색인 밀도 보정 모형 대비**" in md_deg


def test_deficit_matches_the_active_null_expected():
    """각주의 주장이 실제 숫자와 맞는지 — 값 자체로 확인."""
    arts = _strong_gap_corpus()
    for null in ("independent", "degree"):
        for g in analyze.gap_pairs(arts, top_k=6, null=null):
            assert g.deficit == pytest.approx(g.expected - g.observed)


def test_fragility_footnote_warns_it_depends_on_family_size():
    """취약도는 q 기준이라 --gap-top-k 에 따라 달라진다 — 그 사실을 밝혀야 한다."""
    md = report.render_markdown(
        report.build_report(_strong_gap_corpus(), "s", gap_top_k=6)
    )
    assert "--gap-top-k" in md.split("_`취약도`")[1].split("_")[0]


def test_fragility_actually_moves_with_family_size():
    """위 경고가 빈말이 아님을 실제로 보인다(검정군이 커지면 q 가 나빠져 더 취약)."""
    arts = _strong_gap_corpus()
    small = [g for g in analyze.gap_pairs(arts, top_k=4)
             if {g.term_a, g.term_b} == {"Alpha", "Beta"}][0]
    big = [g for g in analyze.gap_pairs(arts, top_k=6, min_expected=0.5)
           if {g.term_a, g.term_b} == {"Alpha", "Beta"}][0]
    assert big.q_value >= small.q_value
    assert big.fragility <= small.fragility


def test_documented_commands_reference_files_that_exist():
    """문서의 --gap-null 예시가 저장소에 없는 파일을 가리키면 안 된다."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("README.md", "사용법.md"):
        text = (root / name).read_text(encoding="utf-8")
        for raw in re.findall(r"--from-file (\S+)", text):
            path = raw.strip("`'\"),")
            if path.startswith("examples/"):
                assert (root / path).exists(), f"{name}: {path} 가 없습니다"


# --------------------------------------------------------------------------- #
# 하드닝 R4 — 정확성 리뷰어가 찾은 '밀도 모형 포화' 결함의 회귀 테스트
# --------------------------------------------------------------------------- #
def _saturating_corpus():
    """아주 흔한 주제(Sleep, 60%) × 평균보다 깊게 색인된 논문(Melatonin) 코퍼스.

    이 조합에서 raw p_i 가 1 을 넘는다 — 모형이 "논문 하나에 Sleep 을 한 번 넘게
    넣겠다"고 말하는 셈이라 그 쌍에서는 밀도 모형이 성립하지 않는다.
    """
    arts = []
    for i in range(30):
        mesh = ["Melatonin"] + [f"T{j}" for j in range(19)]
        if i < 12:
            mesh[1] = "Sleep"
        arts.append(_art(f"m{i}", mesh))
    for i in range(370):
        mesh = [f"T{j}" for j in range(8)]
        if i < 228:
            mesh[0] = "Sleep"
        arts.append(_art(f"x{i}", mesh))
    return arts


def test_saturated_pair_never_yields_a_zero_pvalue():
    """포화된 쌍을 잘라낸 채 검정하면 분산이 사라져 p 가 정확히 0 이 된다.

    회귀 배경: 독립가정에서는 공백으로 잡히지도 않던 Sleep × Melatonin 이
    `q=0.000`, `부족 +18편` 으로 리포트 1순위에 올랐다.
    """
    arts = _saturating_corpus()
    for g in analyze.gap_pairs(arts, top_k=12, null="degree",
                               min_expected=0.5, max_lift=float("inf")):
        assert g.p_value > 0.0, f"{g.term_a}×{g.term_b} 의 p 가 정확히 0"
        assert g.q_value > 0.0


def test_saturated_pair_falls_back_to_the_independent_null():
    """포화된 쌍은 독립가정으로 되돌리고, 그 사실을 행에 남겨야 한다."""
    arts = _saturating_corpus()
    gaps = analyze.gap_pairs(arts, top_k=12, null="degree",
                             min_expected=0.5, max_lift=float("inf"))
    pair = [g for g in gaps if {g.term_a, g.term_b} == {"Sleep", "Melatonin"}]
    assert pair, "이 쌍은 후보에는 남아 있어야 한다(조용히 사라지면 안 된다)"
    g = pair[0]
    assert g.null_fallback is True
    assert g.null_model == "independent"
    assert g.expected == pytest.approx(g.expected_independent)
    assert g.p_value == pytest.approx(
        analyze.hypergeom_lower_tail(400, g.count_a, g.count_b, g.observed)
    )


def test_saturated_pair_is_not_promoted_over_the_independent_verdict():
    """되돌린 뒤에는 독립가정과 같은 판정이어야 한다(가짜 1순위 방지)."""
    arts = _saturating_corpus()
    key = {"Sleep", "Melatonin"}
    ind = [g for g in analyze.gap_pairs(arts, top_k=12) if {g.term_a, g.term_b} == key]
    deg = [g for g in analyze.gap_pairs(arts, top_k=12, null="degree")
           if {g.term_a, g.term_b} == key]
    assert len(ind) == len(deg) == 0, "두 모형 모두 이 쌍을 공백으로 내세우지 않아야 한다"


def test_non_saturated_pairs_still_use_the_degree_null():
    """되돌림이 밀도 모형 전체를 무력화해서는 안 된다."""
    arts = _strong_gap_corpus()
    gaps = analyze.gap_pairs(arts, top_k=6, null="degree")
    assert gaps
    assert all(g.null_model == "degree" and not g.null_fallback for g in gaps)


def test_report_marks_fallback_rows_and_explains_them():
    arts = _saturating_corpus()
    rep = report.build_report(arts, "sat", gap_top_k=12,
                              gap_null="degree", gap_min_expected=0.5, gap_max_lift=99.0)
    assert any(g["null_fallback"] for g in rep["gaps"])
    md = report.render_markdown(rep)
    assert "⚠밀도모형 포화→독립가정" in md
    assert "독립가정으로 되돌려" in md
    csv_text = report.render_csv(rep).lstrip("﻿")
    header = csv_text.splitlines()[0].split(",")
    assert "null_fallback" in header


def test_report_footnote_names_the_anchor_rule():
    """앵커 선택이 결론을 좌우할 수 있으므로 규칙을 리포트에 밝혀야 한다."""
    md = report.render_markdown(
        report.build_report(_strong_gap_corpus(), "s", gap_top_k=6, gap_null="degree")
    )
    assert "논문이 적은 쪽" in md
