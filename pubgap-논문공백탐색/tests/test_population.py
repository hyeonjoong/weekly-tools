"""대상집단 공백(연령·성별 축) 테스트.

이 축은 다른 축과 달리 **체크 태그를 신호로 쓴다** — 기본 파이프라인이 지우는 바로
그 태그다. 그래서 (1) 값이 필터 이전에 읽히는지, (2) 분모가 축마다 따로 잡히는지,
(3) 통계가 손계산과 맞는지를 집중적으로 검증한다.
"""

from math import comb
from pathlib import Path

import json

import pytest

from pubgap import analyze
from pubgap.analyze import (
    POPULATION_GROUPS,
    PopulationGap,
    article_populations,
    population_gaps,
    population_profile,
    sort_population_gaps,
)
from pubgap.cli import main
from pubgap.records import Article
from pubgap.report import build_report, pubmed_population_url, render_csv, render_markdown


def art(pmid, mesh, year=2020, pub_types=("Journal Article",)):
    return Article(
        pmid=str(pmid), year=year, journal="J", title=f"t{pmid}",
        mesh=list(mesh), pub_types=list(pub_types),
    )


# --------------------------------------------------------------------------- #
# 그룹 판정
# --------------------------------------------------------------------------- #
def test_article_populations_maps_check_tags_to_groups():
    a = art(1, ["Sleep", "Humans", "Child", "Female", "Aged, 80 and over"])
    assert article_populations(a) == frozenset({"pediatric", "female", "oldest_old"})


def test_article_populations_is_empty_without_check_tags():
    assert article_populations(art(1, ["Sleep", "Melatonin"])) == frozenset()


def test_age_groups_overlap_by_design():
    """40–70세 코호트는 성인·중년·고령이 동시에 붙는다 — 비중의 합은 1이 아니다."""
    a = art(1, ["Sleep", "Adult", "Middle Aged", "Aged"])
    assert article_populations(a) == frozenset({"adult", "middle_aged", "aged"})


# --------------------------------------------------------------------------- #
# 통계 — 손계산 대조
# --------------------------------------------------------------------------- #
def _fisher_two_sided(a, b, c, d):
    """Fisher 정확검정(양측)의 독립 재구현 — '관측만큼 극단인 표의 확률 합'."""
    n = a + b + c + d
    r1, r2, c1 = a + b, c + d, a + c

    def prob(x):
        return comb(r1, x) * comb(r2, c1 - x) / comb(n, c1)

    p_obs = prob(a)
    lo, hi = max(0, c1 - r2), min(r1, c1)
    return sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= p_obs * (1 + 1e-9))


def test_population_gap_matches_hand_computation():
    # T 주제 10편 중 고령 2편, 나머지 10편 중 고령 8편. 전부 성인 태그 보유.
    arts = [art(i, ["T", "Adult"] + (["Aged"] if i < 2 else [])) for i in range(10)]
    arts += [art(100 + i, ["U", "Adult"] + (["Aged"] if i < 8 else [])) for i in range(10)]
    gaps, n_tested = population_gaps(arts, top_k=5, min_articles=3)
    g = next(g for g in gaps if g.term == "T" and g.group == "aged")

    assert (g.n_articles, g.observed) == (10, 2)
    assert (g.rest_n, g.rest_observed) == (10, 8)
    assert g.rest_share == pytest.approx(0.8)
    assert g.expected == pytest.approx(8.0)          # 10 × 0.8
    assert g.deficit == pytest.approx(6.0)           # 8 − 2
    assert g.lift == pytest.approx(0.25)             # 2 / 8
    assert g.share == pytest.approx(0.2)
    assert g.p_value == pytest.approx(_fisher_two_sided(2, 8, 8, 2), abs=1e-12)
    assert g.p_value < 0.05
    # 검정 수: (주제 2개 × 연령그룹 중 코퍼스에 존재하는 것) — 성별 태그는 없다.
    assert n_tested == len(gaps) > 0
    assert g.share_ci_low < g.share < g.share_ci_high
    assert 0.0 <= g.share_ci_low and g.share_ci_high <= 1.0
    assert g.lift_ci_low is not None and g.lift_ci_low <= g.lift <= g.lift_ci_high


def _bh(pvalues):
    """BH-FDR 의 독립 재구현(단조화 포함)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, m * pvalues[i] / rank)
        q[i] = min(1.0, running)
    return q


def test_over_represented_rows_are_returned_too_and_share_the_fdr_budget():
    """과소대표만 골라 보정하면 분모가 결과에 의존해 q 가 정직하지 않다."""
    arts = [art(i, ["T", "Adult"] + (["Aged"] if i < 2 else [])
                + (["Middle Aged"] if i < 5 else [])) for i in range(10)]
    arts += [art(100 + i, ["U", "Adult"] + (["Aged"] if i < 8 else [])
                 + (["Middle Aged"] if i < 6 else [])) for i in range(10)]
    gaps, n_tested = population_gaps(arts, top_k=5, min_articles=3)
    assert any(g.deficit > 0 for g in gaps)      # T×고령 = 과소
    assert any(g.deficit < 0 for g in gaps)      # U×고령 = 과대
    # 손계산: 주제 2개 × 정보가 있는 연령그룹 2개(고령·중년). '성인'은 20/20 편에
    # 붙어 2×2 표의 한 변이 상수라 검정에서 빠진다.
    assert n_tested == 4 == len(gaps)
    # q 는 **과대대표를 포함한 검정 전부**에 BH 를 적용한 값과 일치해야 한다.
    expected = _bh([g.p_value for g in gaps])
    assert [g.q_value for g in gaps] == pytest.approx(expected)
    assert max(g.q_value for g in gaps) > min(g.q_value for g in gaps)


def test_saturated_group_is_excluded_from_the_multiple_testing_budget():
    """모든 논문에 붙은 집단(혼성 연구의 Male/Female)은 p 가 늘 1 인데 m 만 키운다."""
    arts = [art(i, ["T", "Adult", "Male", "Female"] + (["Aged"] if i < 2 else []))
            for i in range(10)]
    arts += [art(100 + i, ["U", "Adult", "Male", "Female"] + (["Aged"] if i < 8 else []))
             for i in range(10)]
    gaps, n_tested = population_gaps(arts, top_k=5, min_articles=3)
    assert {g.group for g in gaps} == {"aged"}
    assert n_tested == 2
    # 포화 집단을 넣었다면 m=6 이 되어 q 가 3배로 부풀었을 것이다.
    target = next(g for g in gaps if g.term == "T")
    assert target.q_value == pytest.approx(target.p_value)
    assert target.q_value < 0.05


def test_absent_group_is_not_tested():
    """코퍼스에 한 명도 없는 집단(임신 등)은 비교 자체가 성립하지 않는다."""
    arts = [art(i, ["T", "Adult"]) for i in range(6)]
    arts += [art(100 + i, ["U", "Adult"]) for i in range(6)]
    gaps, _ = population_gaps(arts, top_k=5, min_articles=3)
    assert {g.group for g in gaps} == {"adult"} or not gaps
    assert all(g.group != "pregnancy" for g in gaps)


# --------------------------------------------------------------------------- #
# 분모 — 축마다 따로
# --------------------------------------------------------------------------- #
def test_sex_only_articles_do_not_dilute_the_age_denominator():
    """성별만 색인된 논문을 연령 분모에 넣으면 모든 연령대가 실제보다 비어 보인다."""
    aged = [art(i, ["T", "Adult", "Aged"]) for i in range(6)]
    sex_only = [art(100 + i, ["T", "Male"]) for i in range(60)]
    prof = population_profile(aged + sex_only)
    assert prof["axis_base"] == {"age": 6, "sex": 60}
    aged_row = next(r for r in prof["groups"] if r["key"] == "aged")
    assert aged_row["count"] == 6 and aged_row["base"] == 6
    assert aged_row["share"] == pytest.approx(1.0)   # 6/6 이지 6/66 이 아니다


def test_profile_reports_coverage_and_does_not_confuse_unindexed_with_unstudied():
    arts = [art(i, ["T", "Adult"]) for i in range(3)] + [art(9, ["T"])]
    prof = population_profile(arts)
    assert prof["n_articles"] == 4
    assert prof["n_indexed"] == 3
    assert prof["coverage"] == pytest.approx(0.75)


def test_profile_counts_humans_and_animals():
    arts = [art(1, ["T", "Humans", "Adult"]), art(2, ["T", "Animals", "Mice"])]
    prof = population_profile(arts)
    assert prof["n_human"] == 1 and prof["n_animal"] == 1


# --------------------------------------------------------------------------- #
# 필터·옵션
# --------------------------------------------------------------------------- #
def test_min_articles_filters_out_thin_topics():
    arts = [art(i, ["T", "Adult", "Aged"]) for i in range(4)]
    arts += [art(100 + i, ["U", "Adult"]) for i in range(10)]
    assert not [g for g in population_gaps(arts, top_k=5, min_articles=5)[0] if g.term == "T"]
    assert [g for g in population_gaps(arts, top_k=5, min_articles=4)[0] if g.term == "T"]


def test_top_k_zero_and_empty_corpus_are_safe():
    assert population_gaps([], top_k=12) == ([], 0)
    assert population_gaps([art(1, ["T", "Adult"])], top_k=0) == ([], 0)
    assert population_profile([])["coverage"] == 0.0


def test_check_tags_are_never_topic_candidates_even_with_include_check_tags():
    """--include-check-tags 를 켜도 'Aged × 고령' 같은 자기순환 검정은 나오면 안 된다."""
    arts = [art(i, ["Aged", "Adult", "Sleep"]) for i in range(10)]
    arts += [art(100 + i, ["Adult", "Melatonin"]) for i in range(10)]
    gaps, _ = population_gaps(arts, top_k=10, min_articles=3)
    assert all(g.term not in analyze.POPULATION_TAGS for g in gaps)
    assert all(not analyze.is_non_topical(g.term) for g in gaps)


def test_pops_length_mismatch_is_rejected_loudly():
    with pytest.raises(ValueError):
        population_gaps([art(1, ["T", "Adult"])], pops=[], top_k=5)


@pytest.mark.parametrize("sort", analyze.POPULATION_SORTS)
def test_every_sort_is_deterministic(sort):
    arts = [art(i, ["T", "Adult"] + (["Aged"] if i % 3 else [])) for i in range(12)]
    arts += [art(100 + i, ["U", "Adult", "Aged"]) for i in range(12)]
    first, _ = population_gaps(arts, top_k=5, min_articles=3, sort=sort)
    second, _ = population_gaps(arts, top_k=5, min_articles=3, sort=sort)
    assert [(g.term, g.group) for g in first] == [(g.term, g.group) for g in second]


def test_unknown_sort_is_rejected():
    with pytest.raises(ValueError):
        sort_population_gaps([], sort="nope")


def test_sort_handles_infinite_lift_without_crashing():
    """비교군에 그 집단이 0편이면 기대=0 → lift=inf. 정렬이 죽으면 안 된다."""
    g = PopulationGap(
        term="T", group="aged", axis="age", label="고령 (65+)", n_articles=5,
        observed=3, share=0.6, rest_n=5, rest_observed=0, rest_share=0.0,
        expected=0.0, deficit=0.0, lift=float("inf"), p_value=0.1,
    )
    assert sort_population_gaps([g], sort="lift") == [g]


# --------------------------------------------------------------------------- #
# 리포트 통합
# --------------------------------------------------------------------------- #
def _corpus():
    arts = [art(i, ["Insomnia", "Humans", "Adult", "Female", "Male"]) for i in range(12)]
    arts += [
        art(100 + i, ["Melatonin", "Humans", "Adult", "Aged", "Female", "Male"])
        for i in range(12)
    ]
    return arts


def test_build_report_reads_populations_before_check_tags_are_stripped():
    """기본 파이프라인은 체크 태그를 지운다 — 그전에 읽지 않으면 축 전체가 빈다."""
    rep = build_report(_corpus(), "q", drop_check_tags=True)
    assert rep["population"]["n_indexed"] == 24
    assert rep["population_gaps"], "체크 태그를 지운 뒤에 읽으면 여기가 빈다"
    # 주제 축에서는 여전히 체크 태그가 빠져 있어야 한다.
    assert all(t not in analyze.POPULATION_TAGS for t, _n in rep["top_mesh"])


def test_population_can_be_switched_off():
    rep = build_report(_corpus(), "q", population=False)
    assert "population" not in rep and "population_gaps" not in rep
    assert "👥" not in render_markdown(rep)


def test_markdown_section_renders_and_states_its_denominator():
    md = render_markdown(build_report(_corpus(), "q"))
    assert "## 👥 대상집단 공백 (연령·성별 축)" in md
    assert "대상집단이 색인된 논문 **24편**" in md
    assert "구간이 서로 겹치므로" in md          # 겹침을 밝힌다
    assert "절대적 부재가 아니라 상대적 과소대표" in md   # 과장하지 않는다
    assert "둘 다** 붙습니다" in md              # 성별 축 포화를 밝힌다


def test_markdown_says_so_when_the_input_has_no_check_tags():
    arts = [art(i, ["Sleep", "Melatonin"]) for i in range(10)]
    md = render_markdown(build_report(arts, "q"))
    assert "연령·성별 체크 태그" in md and "없어" in md
    assert "과소대표" not in md


def test_markdown_mentions_animals_when_that_explains_missing_tags():
    arts = [art(i, ["Sleep", "Animals", "Mice"]) for i in range(10)]
    md = render_markdown(build_report(arts, "q"))
    assert "동물실험" in md


def test_min_q_note_uses_the_displayed_underrepresented_rows_only():
    """과대대표 줄의 q=0.00x 를 '달성한 최소 q' 로 쓰면 정반대로 읽힌다."""
    rep = build_report(_corpus(), "q")
    rows = rep["population_gaps"]
    under = [r for r in rows if r["deficit"] > 0]
    assert under, "이 코퍼스는 과소대표 줄이 있어야 한다"
    md = render_markdown(rep)
    best_under = min(r["q_value"] for r in under)
    assert f"달성한 최소 q={best_under:.3f}" in md


def test_summary_lists_the_top_underrepresented_population_gap():
    """요약이 *과대대표* 행을 1순위로 올리면 독자가 정반대로 읽는다."""
    rep = build_report(_corpus(), "q")
    under = [r for r in rep["population_gaps"] if r["deficit"] > 0]
    top = max(under, key=lambda r: r["deficit"])
    line = next(l for l in render_markdown(rep).splitlines() if "대상집단 1순위" in l)
    assert top["term"] in line and top["label"] in line
    assert f"{top['observed']}편" in line
    # q>0.05 면 탐색적이라고 말해야 한다(이 축의 1순위는 대개 상대적 신호다).
    assert ("탐색적(q>0.05)" in line) == (top["q_value"] > 0.05)


def test_pubmed_url_ors_all_tags_of_a_group_and_is_encoded():
    url = pubmed_population_url("Sleep Apnea", "pediatric")
    assert url.startswith("https://pubmed.ncbi.nlm.nih.gov/?term=")
    assert " " not in url and '"' not in url
    from urllib.parse import parse_qs, urlparse

    term = parse_qs(urlparse(url).query)["term"][0]
    assert term.startswith('"Sleep Apnea"[MeSH Terms] AND (')
    assert term.count(" OR ") == 4          # 소아 = 5개 태그
    assert '"Infant, Newborn"[MeSH Terms:noexp]' in term
    # 링크가 표와 같은 것을 세려면 **하위어 확장을 꺼야** 한다: MeSH 트리에서
    # Aged/Middle Aged/Young Adult 는 Adult 의 하위어라, 확장을 켜면 '성인(19–44)'
    # 링크가 19세 이상 전부를 돌려준다.
    assert '"Adult"[MeSH Terms:noexp]' in parse_qs(
        urlparse(pubmed_population_url("Sleep", "adult")).query
    )["term"][0]
    # Markdown 링크 [PubMed](url) 를 깨뜨리는 괄호·개행은 반드시 인코딩돼야 한다.
    assert "(" not in url and ")" not in url and "\n" not in url


def test_pubmed_url_survives_quotes_and_control_chars_in_terms():
    """주제어의 따옴표는 검색식을 조기 종료시켜 검증 결과를 정반대로 만든다."""
    from urllib.parse import parse_qs, urlparse

    term = parse_qs(urlparse(pubmed_population_url('Sle"ep\nApnea', "aged")).query)["term"][0]
    assert term == '"Sleep Apnea"[MeSH Terms] AND ("Aged"[MeSH Terms:noexp])'


def test_pubmed_url_falls_back_when_the_group_is_unknown():
    from urllib.parse import parse_qs, urlparse

    url = pubmed_population_url("Sleep", "no-such-group")
    assert parse_qs(urlparse(url).query)["term"][0] == '"Sleep"[MeSH Terms]'


# --------------------------------------------------------------------------- #
# CLI / 출력 형식
# --------------------------------------------------------------------------- #
EXAMPLE = str(Path(__file__).resolve().parents[1] / "examples" / "sleep_pubmed.xml")


def test_cli_json_is_valid_and_carries_the_population_axis(capsys, tmp_path):
    rc = main(["--from-file", EXAMPLE, "--format", "json", "--no-meta"])
    assert rc == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["population"]["n_indexed"] == 28
    assert rep["population_n_tested"] == len(rep["population_gaps"])
    row = rep["population_gaps"][0]
    for k in ("term", "group", "axis", "label", "observed", "expected", "deficit",
              "q_value", "pubmed_url"):
        assert k in row


def test_cli_no_population_removes_the_section(capsys):
    assert main(["--from-file", EXAMPLE, "--no-meta", "--no-population"]) == 0
    assert "대상집단" not in capsys.readouterr().out


def test_cli_warns_when_csv_section_was_switched_off(capsys):
    rc = main(["--from-file", EXAMPLE, "--format", "csv",
               "--csv-section", "population", "--no-population"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "--no-population" in err


def test_csv_sections_render_expected_columns(capsys):
    main(["--from-file", EXAMPLE, "--format", "csv", "--csv-section", "population"])
    lines = capsys.readouterr().out.lstrip("﻿").splitlines()
    assert lines[0].startswith("term,group,axis,label,n_articles,observed,expected")
    assert len(lines) > 1
    main(["--from-file", EXAMPLE, "--format", "csv", "--csv-section", "population-profile"])
    lines = capsys.readouterr().out.lstrip("﻿").splitlines()
    assert lines[0].startswith("group,axis,label,count,base,share")
    assert len(lines) == 1 + len(POPULATION_GROUPS)


def test_cli_population_options_are_honoured(capsys):
    main(["--from-file", EXAMPLE, "--format", "json", "--no-meta",
          "--population-top-k", "2", "--population-min-articles", "3"])
    rep = json.loads(capsys.readouterr().out)
    assert len({r["term"] for r in rep["population_gaps"]}) <= 2


def test_population_options_are_recorded_in_meta(capsys):
    main(["--from-file", EXAMPLE, "--format", "json", "--population-sort", "q"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["meta"]["params"]["population_sort"] == "q"
    assert rep["meta"]["params"]["population_top_k"] == 12


def test_default_population_options_are_not_flagged_as_changed(capsys):
    """기본값을 '기본값과 다른 옵션'으로 적으면 실행 정보가 거짓말이 된다."""
    main(["--from-file", EXAMPLE])
    assert "옵션: 전부 기본값" in capsys.readouterr().out


def test_report_is_byte_reproducible_with_the_new_axis(capsys):
    assert main(["--from-file", EXAMPLE, "--no-meta"]) == 0
    first = capsys.readouterr().out
    assert "대상집단" in first          # 빈 출력끼리 비교해 통과하지 않도록
    assert main(["--from-file", EXAMPLE, "--no-meta"]) == 0
    assert first == capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 하드닝 라운드(2026-07-31) 회귀 테스트
# --------------------------------------------------------------------------- #
def test_author_keywords_can_never_become_check_tags():
    """키워드 'female' 이 MeSH 'Female' 로 승격되면 없는 공백이 q=0.001 로 나온다."""
    from pubgap.records import apply_include_keywords

    arts = [
        Article(pmid=str(i), year=2020, journal="J", title="t", mesh=[],
                keywords=["Hypertension", "female", "Aged"])
        for i in range(10)
    ]
    merged = apply_include_keywords(arts)
    assert merged[0].mesh == ["Hypertension"]
    assert article_populations(merged[0]) == frozenset()


def test_keyword_fallback_does_not_fabricate_a_population_axis(capsys, tmp_path):
    """RIS/CSV 처럼 MeSH 가 없는 입력 — 리포트는 '태그가 없다'고 말해야 한다."""
    ris = "\n\n".join(
        f"TY  - JOUR\nTI  - Study {i}\nPY  - 2020\nJO  - J\n"
        f"KW  - Hypertension\nKW  - female\nKW  - aged\nER  - "
        for i in range(12)
    )
    path = tmp_path / "kw.ris"
    path.write_text(ris, encoding="utf-8")
    assert main(["--from-file", str(path), "--no-meta"]) == 0
    out = capsys.readouterr().out
    assert "연령·성별 체크 태그" in out and "없어" in out
    assert "과소대표" not in out


def test_major_topics_only_keeps_the_population_axis_alive(capsys):
    """체크 태그에는 별표가 붙지 않는다 — 함께 지우면 축이 통째로 사라졌다."""
    assert main(["--from-file", EXAMPLE, "--major-topics-only", "--format", "json",
                 "--no-meta"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["population"]["n_indexed"] == 28
    assert rep["population"]["n_human"] == 28


def test_major_topics_only_still_warns_when_no_major_topic_exists(capsys):
    """체크 태그를 남긴다고 해서 '주제가 비었다' 경고가 사라지면 안 된다."""
    main(["--from-file", EXAMPLE, "--major-topics-only", "--no-meta"])
    assert "대표(별표) MeSH 주제가 하나도 없어" in capsys.readouterr().err


def test_single_topic_corpus_has_no_comparison_group():
    """주제가 하나뿐이면 '나머지'가 없다 — 0으로 나누지 말고 조용히 비어야 한다."""
    arts = [art(i, ["OnlyTopic", "Adult", "Aged"]) for i in range(8)]
    assert population_gaps(arts, top_k=5, min_articles=3) == ([], 0)


@pytest.mark.parametrize("sort,key,reverse", [
    ("deficit", lambda g: g.deficit, True),
    ("share", lambda g: g.share, False),
    ("q", lambda g: g.q_value, False),
    ("lift", lambda g: g.lift, False),
])
def test_each_sort_actually_orders_by_its_key(sort, key, reverse):
    arts = [art(i, ["A", "Adult"] + (["Aged"] if i % 5 else [])
                + (["Middle Aged"] if i < 3 else [])) for i in range(15)]
    arts += [art(100 + i, ["B", "Adult"] + (["Aged"] if i < 4 else [])
                 + (["Middle Aged"] if i % 2 else [])) for i in range(15)]
    arts += [art(200 + i, ["C", "Adult", "Aged", "Middle Aged"]) for i in range(10)]
    gaps, _ = population_gaps(arts, top_k=5, min_articles=3, sort=sort)
    values = [key(g) for g in gaps]
    assert values == sorted(values, reverse=reverse)


def test_sorts_are_not_all_the_same_order():
    """정렬 옵션이 무시되고 있으면(하드코딩) 이 테스트가 잡는다."""
    arts = [art(i, ["A", "Adult"] + (["Aged"] if i % 5 else [])
                + (["Middle Aged"] if i < 3 else [])) for i in range(15)]
    arts += [art(100 + i, ["B", "Adult"] + (["Aged"] if i < 4 else [])
                 + (["Middle Aged"] if i % 2 else [])) for i in range(15)]
    orders = {
        s: tuple((g.term, g.group) for g in population_gaps(
            arts, top_k=5, min_articles=3, sort=s)[0])
        for s in analyze.POPULATION_SORTS
    }
    assert len(set(orders.values())) > 1


def test_cli_population_sort_changes_the_reported_order(capsys):
    def first_row(sort):
        main(["--from-file", EXAMPLE, "--format", "json", "--no-meta",
              "--population-sort", sort])
        rows = json.loads(capsys.readouterr().out)["population_gaps"]
        return (rows[0]["term"], rows[0]["group"])

    assert first_row("deficit") != first_row("q")


def test_over_representation_note_picks_the_most_over_represented_rows():
    """--population-sort 를 바꿨다고 각주에 엉뚱한 행이 실리면 안 된다."""
    arts = [art(i, ["A", "Adult"] + (["Aged"] if i % 5 else [])) for i in range(15)]
    arts += [art(100 + i, ["B", "Adult"] + (["Aged"] if i < 2 else [])) for i in range(15)]
    arts += [art(200 + i, ["C", "Adult", "Aged"]) for i in range(12)]
    for sort in analyze.POPULATION_SORTS:
        rep = build_report(arts, "q", population_sort=sort, population_min_articles=3)
        over = [r for r in rep["population_gaps"] if r["deficit"] < 0]
        if len(over) < 2:
            continue
        worst = sorted(over, key=lambda r: r["deficit"])[0]
        md = render_markdown(rep)
        note = next(line for line in md.splitlines() if "과대대표" in line)
        assert f"{worst['term']}×{worst['label']}" in note


def test_population_profile_rejects_mismatched_pops():
    with pytest.raises(ValueError):
        population_profile([art(1, ["T", "Adult"])], pops=[])


def test_new_csv_sections_defend_against_formula_injection():
    arts = [art(i, ["=cmd|' /C calc'!A0", "Adult"] + (["Aged"] if i < 2 else []))
            for i in range(8)]
    arts += [art(100 + i, ["U", "Adult", "Aged"]) for i in range(8)]
    rep = build_report(arts, "q", population_min_articles=3)
    for section in ("population", "population-profile"):
        for line in render_csv(rep, section).lstrip("﻿").splitlines():
            for cell in line.split(","):
                assert not cell.lstrip('"').startswith("=")


def test_population_table_escapes_markdown_injection():
    arts = [art(i, ["[CLICK](javascript:alert(1))|x", "Adult"] + (["Aged"] if i < 2 else []))
            for i in range(8)]
    arts += [art(100 + i, ["U", "Adult", "Aged"]) for i in range(8)]
    md = render_markdown(build_report(arts, "q", population_min_articles=3))
    assert "\\[CLICK\\]" in md
    assert "[CLICK](javascript:" not in md


def test_population_csv_rows_carry_sane_values(capsys):
    """헤더만 보는 테스트는 열 값이 뒤바뀌어도 통과한다."""
    main(["--from-file", EXAMPLE, "--format", "csv", "--csv-section", "population"])
    rows = capsys.readouterr().out.lstrip("﻿").splitlines()[1:]
    keys = {k for k, _ax, _lab, _t in POPULATION_GROUPS}
    for row in rows:
        cells = next(__import__("csv").reader([row]))
        assert cells[1] in keys              # group
        assert cells[2] in ("age", "sex")    # axis
        assert int(cells[5]) <= int(cells[4])   # observed ≤ n_articles
