"""하드닝 라운드 1에서 발견된 결함의 회귀 테스트.

각 테스트는 리뷰어가 **실제로 재현한** 실패 하나에 대응한다. 주석의 '이전 동작'은
관측된 사실이며, 이 테스트들은 그 동작이 되돌아오면 즉시 실패한다.
"""

import gzip
import json
from pathlib import Path

import pytest

from pubgap.analyze import (
    METHOD_TAGS,
    _classify_gap_trend,
    article_tier,
    evidence_profile,
    evidence_tier,
    fisher_exact_two_sided,
    gap_pairs,
    growth_summary,
    hypergeom_lower_tail,
    is_interventional,
    is_non_topical,
    theil_sen,
    topic_evidence,
)
from pubgap.cli import main
from pubgap.records import (
    Article,
    MAX_DECOMPRESSED_BYTES,
    decode_bytes,
    load_articles,
    parse_csv_records,
    parse_efetch_xml,
    parse_records,
    parse_ris,
)
from pubgap.report import (
    build_report,
    json_safe,
    pubmed_pair_url,
    render_csv,
    render_markdown,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sleep_pubmed.xml"


def _mk(pmid, year=2020, mesh=(), pts=(), kw=()):
    return Article(
        pmid=pmid, year=year, journal="J", title="t",
        mesh=list(mesh), pub_types=list(pts), keywords=list(kw),
    )


# --------------------------------------------------------------------------- #
# [HIGH] 공백 분모에 '주제어 없는 논문'이 섞여 진짜 공백을 가렸다
# --------------------------------------------------------------------------- #
def test_gap_denominator_excludes_articles_without_topics():
    """MeSH 가 없는 논문은 어떤 주제쌍도 가질 수 없으므로 분모에서 빠져야 한다.

    이전: n = len(articles) 라 색인 안 된 논문이 기대값을 낮추고 lift 를 부풀려
    통계적으로 유의한 공백이 유의하지 않게 보였다(실측 p 0.012 → 0.069).
    """
    topical = [_mk(f"a{i}", mesh=["A"]) for i in range(20)]
    topical += [_mk(f"b{i}", mesh=["B"]) for i in range(20)]
    unindexed = [_mk(f"u{i}", mesh=[]) for i in range(60)]

    only = gap_pairs(topical, top_k=5, min_expected=1.0, max_lift=5.0)[0]
    withpad = gap_pairs(topical + unindexed, top_k=5, min_expected=1.0, max_lift=5.0)[0]

    assert only.expected == pytest.approx(withpad.expected)
    assert only.p_value == pytest.approx(withpad.p_value)
    # 손계산: N=40, cA=cB=20 → 기대 = 20*20/40 = 10
    assert only.expected == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# [HIGH] 'Journal Article' 때문에 설계 커버리지가 항상 100% 로 나왔다
# --------------------------------------------------------------------------- #
def test_evidence_coverage_is_not_inflated_by_journal_article_tag():
    """PubMed 레코드는 사실상 전부 'Journal Article' 을 단다 — 그것은 설계 정보가 아니다.

    이전: `[a for a in articles if a.pub_types]` 를 분모로 써서, 설계 태그가 2편에만
    있어도 커버리지 100% 로 보고하고 '기타/미분류' 를 분모에 남겼다.
    """
    arts = [_mk("1", pts=["Journal Article", "Randomized Controlled Trial"]),
            _mk("2", pts=["Journal Article", "Observational Study"])]
    arts += [_mk(str(i), pts=["Journal Article"]) for i in range(3, 21)]

    prof = evidence_profile(arts)
    assert prof["n_articles"] == 20
    assert prof["n_typed"] == 2           # 설계가 실제로 확인된 논문만
    assert prof["n_unknown"] == 18
    assert prof["coverage"] == pytest.approx(0.1)
    assert prof["interventional_share"] == pytest.approx(0.5)   # 1/2, 1/20 아님
    # 'other'(미분류) 는 더 이상 tier 표에 없다 — 분모에서 뺐다고 말했으면 없어야 한다.
    assert all(t["tier"] != "other" for t in prof["tiers"])
    assert sum(t["count"] for t in prof["tiers"]) == prof["n_typed"]


def test_low_coverage_warning_can_actually_fire():
    """커버리지 경고(<50%)가 죽은 코드가 아니어야 한다."""
    arts = [_mk("1", pts=["Journal Article", "Review"])]
    arts += [_mk(str(i), pts=["Journal Article"]) for i in range(2, 11)]
    md = render_markdown(build_report(arts, "q"))
    assert "커버리지가" in md


def test_ris_without_design_info_reports_zero_coverage_not_zero_trials():
    """'TY - JOUR' 만 있는 RIS 를 '개입연구 0%' 로 단정하면 안 된다."""
    arts = parse_ris(
        "TY  - JOUR\nTI  - A\nPY  - 2020\nKW  - *Sleep\nAN  - 1\nER  -\n"
        "TY  - JOUR\nTI  - B\nPY  - 2021\nKW  - *Sleep\nAN  - 2\nER  -\n"
    )
    prof = evidence_profile(arts)
    assert prof["n_typed"] == 0 and prof["coverage"] == 0.0
    md = render_markdown(build_report(arts, "q"))
    assert "연구 설계 정보(PublicationType)가 없어" in md


# --------------------------------------------------------------------------- #
# [HIGH] 개입연구 판정이 '대표 tier' 라 메타분석+RCT 논문을 놓쳤다
# --------------------------------------------------------------------------- #
def test_meta_analysis_plus_rct_still_counts_as_interventional():
    a = _mk("1", pts=["Meta-Analysis", "Randomized Controlled Trial"])
    assert evidence_tier(a.pub_types) == "systematic_review"   # 대표 tier 는 그대로
    assert is_interventional(a) is True                        # 그래도 개입연구다
    prof = evidence_profile([a])
    assert prof["n_interventional"] == 1


def test_non_rct_trial_counts_as_interventional():
    """'trial' tier(비무작위 임상시험)의 기여가 실제로 세어지는지."""
    arts = [_mk(str(i), mesh=["A"], pts=["Clinical Trial, Phase II"]) for i in range(4)]
    arts += [_mk(f"o{i}", mesh=["A"], pts=["Observational Study"]) for i in range(4)]
    prof = evidence_profile(arts)
    assert prof["n_interventional"] == 4
    assert prof["interventional_share"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# [HIGH] 관찰연구 설계가 MeSH 로만 색인돼 '설계 미상'으로 빠졌다
# --------------------------------------------------------------------------- #
def test_observational_design_read_from_mesh_when_pubtype_lacks_it():
    """NLM 은 코호트·후향 연구를 PublicationType 이 아니라 MeSH 로 단다."""
    a = _mk("1", mesh=["Sleep", "Cohort Studies"], pts=["Journal Article"])
    assert evidence_tier(a.pub_types) == "other"        # 타입만 보면 미상
    assert article_tier(a) == "observational"           # MeSH 보조 신호로 관찰연구
    # PublicationType 에 설계가 있으면 그쪽이 우선이다.
    b = _mk("2", mesh=["Cohort Studies"], pts=["Randomized Controlled Trial"])
    assert article_tier(b) == "rct"


def test_report_reads_design_mesh_before_check_tags_are_stripped():
    """설계 MeSH 는 체크 태그 제거 대상이라, 제거 *전* 에 읽어야 한다."""
    arts = [_mk(f"a{i}", mesh=["Sleep", "Cohort Studies"], pts=["Journal Article"])
            for i in range(10)]
    rep = build_report(arts, "q")
    assert rep["evidence"]["n_typed"] == 10
    assert rep["evidence"]["coverage"] == pytest.approx(1.0)
    # 그래도 'Cohort Studies' 는 주제 목록에는 들어가지 않는다(방법론 표제어).
    assert all(t != "Cohort Studies" for t, _ in rep["top_mesh"])


# --------------------------------------------------------------------------- #
# [HIGH] 방법론 MeSH 가 상위 주제·공백표를 점령했다
# --------------------------------------------------------------------------- #
def test_methodology_descriptors_are_dropped_by_default():
    for term in ("Treatment Outcome", "Surveys and Questionnaires",
                 "Randomized Controlled Trials as Topic", "Risk Factors"):
        assert is_non_topical(term), term
    # '... as Topic' 은 접미사 규칙으로도 잡힌다(목록에 없는 것도).
    assert is_non_topical("Antidepressive Agents as Topic")
    assert not is_non_topical("Sleep")
    assert not is_non_topical("Heart Rate")


def test_method_tags_removed_from_topics_but_optional():
    arts = [_mk(f"a{i}", mesh=["Sleep", "Treatment Outcome"]) for i in range(10)]
    assert all(t != "Treatment Outcome" for t, _ in build_report(arts, "q")["top_mesh"])
    kept = build_report(arts, "q", drop_check_tags=False)["top_mesh"]
    assert any(t == "Treatment Outcome" for t, _ in kept)


# --------------------------------------------------------------------------- #
# [HIGH] 근거공백 표 제목이 '비어 있는 주제'인데 과밀 주제가 실렸다
# --------------------------------------------------------------------------- #
def test_evidence_gap_table_only_lists_actual_gaps():
    arts = [_mk(f"e{i}", mesh=["EEG"], pts=["Observational Study"]) for i in range(8)]
    arts += [_mk(f"h{i}", mesh=["HRV"], pts=["Randomized Controlled Trial"]) for i in range(8)]
    md = render_markdown(build_report(arts, "q"))
    _head, _, rest = md.partition("### 개입연구가 상대적으로 적은 주제")
    gap_table = rest.split("_참고:")[0]
    assert "EEG" in gap_table          # 개입연구 0% → 진짜 공백
    assert "| HRV |" not in gap_table  # 개입연구 100% → 표에 없어야 한다
    # 과밀 주제는 표가 아니라 한 줄 요약으로만 언급된다(제목과 내용이 어긋나지 않게).
    assert "오히려 개입연구가 **많은** 주제" in rest
    assert "HRV" in rest


def test_rest_share_column_is_not_labelled_corpus_average():
    """'코퍼스 평균' 이라는 라벨은 거짓이었다 — 값은 leave-one-out 비교군 비율이다."""
    md = render_markdown(build_report(load_articles(EXAMPLE), "example"))
    assert "코퍼스 평균" not in md
    assert "그 외 논문" in md


# --------------------------------------------------------------------------- #
# [HIGH] 표본이 잘렸는데 추세를 그대로 보고했다
# --------------------------------------------------------------------------- #
def test_truncated_sample_suppresses_trend_output():
    arts = [_mk(f"a{i}", year=2025, mesh=["A", "B"]) for i in range(5)]
    arts += [_mk(f"b{i}", year=2026, mesh=["A"]) for i in range(95)]
    rep = build_report(arts, "q", total_available=5000)
    assert rep["truncated"] is True
    assert rep["trend_reliable"] is False
    assert rep["emerging"] == [] and rep["declining"] == []
    md = render_markdown(rep)
    assert "표본입니다 → 추세 관련 출력을 생략합니다" in md
    assert "5,000편" in md and "추세 아님" in md
    # 성장 배수/기울기 같은 추세 수치는 아예 나오지 않아야 한다.
    assert "Theil–Sen" not in md
    assert "Mann–Kendall" not in md


def test_untruncated_sample_keeps_trend_output():
    arts = [_mk(f"a{i}", year=2015 + i % 10, mesh=["A", "B"]) for i in range(40)]
    rep = build_report(arts, "q", total_available=40)
    assert rep["truncated"] is False
    assert "추세 관련 출력을 생략" not in render_markdown(rep)


# --------------------------------------------------------------------------- #
# [HIGH] 키워드 폴백을 쓰고도 리포트는 "MeSH 기반"이라고 말했다
# --------------------------------------------------------------------------- #
def test_keyword_fallback_is_disclosed_in_the_report(tmp_path, capsys):
    ris = tmp_path / "kw.ris"
    ris.write_text(
        "".join(
            f"TY  - JOUR\nTI  - P{i}\nPY  - 2020\nKW  - sleep\nKW  - hrv\n"
            f"AN  - {700000 + i}\nER  -\n\n" for i in range(6)
        ),
        encoding="utf-8",
    )
    assert main(["--from-file", str(ris)]) == 0
    md = capsys.readouterr().out
    assert "저자 키워드" in md
    assert "MeSH 주제어 보유" not in md
    assert "MeSH 주제어 공동출현 기반" not in md


def test_major_only_does_not_silently_trigger_keyword_fallback(tmp_path, capsys):
    """사용자가 '대표주제만' 이라고 했는데 키워드로 몰래 채우면 안 된다."""
    ris = tmp_path / "m.ris"
    ris.write_text(
        "".join(
            f"TY  - JOUR\nTI  - P{i}\nPY  - 2020\nKW  - sleep\nAN  - {600000 + i}\nER  -\n\n"
            for i in range(4)
        ),
        encoding="utf-8",
    )
    main(["--from-file", str(ris), "--major-topics-only"])
    err = capsys.readouterr().err
    assert "저자 키워드를 주제로 사용" not in err


# --------------------------------------------------------------------------- #
# [MEDIUM] 성장 배수가 창 길이 차이를 성장으로 착각했다
# --------------------------------------------------------------------------- #
def test_growth_ratio_is_normalised_per_year():
    """2016–2026, split 2021 → 초기 5년 / 최근 6년. 완전히 평평해도 총량비는 1.2배."""
    counts = {y: 10 for y in range(2016, 2027)}
    g = growth_summary(counts)
    assert g["early_years"] == 5 and g["recent_years"] == 6
    assert g["ratio"] == pytest.approx(60 / 50)            # 총량비는 1.2배(오해 소지)
    assert g["ratio_per_year"] == pytest.approx(1.0)       # 연평균비는 정확히 1.0배
    assert "연 10.0편" in render_markdown(
        build_report([_mk(f"a{i}", year=2016 + i % 11, mesh=["A"]) for i in range(110)], "q")
    )


def test_theil_sen_is_robust_to_a_partial_final_year():
    """CAGR 은 마지막(진행 중) 해 하나에 -17% 를 만들었다. Theil–Sen 은 버틴다."""
    flat = [20] * 10 + [3]          # 10년 평평 + 올해 아직 3편
    assert theil_sen(flat) == pytest.approx(0.0)
    assert theil_sen([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert theil_sen([5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    assert theil_sen([1, 2]) is None


# --------------------------------------------------------------------------- #
# [MEDIUM] 공백 '추이' 가 1편으로 뒤집히고 0/0 을 '유지'로 위장했다
# --------------------------------------------------------------------------- #
def test_gap_trend_marks_never_cooccurring_pairs_as_empty_gap():
    assert _classify_gap_trend(0, 0, 10, 10) == "empty"
    assert "완전공백" in render_markdown(build_report(
        [_mk(f"a{i}", year=2015 + i % 6, mesh=["A"]) for i in range(20)]
        + [_mk(f"b{i}", year=2015 + i % 6, mesh=["B"]) for i in range(20)],
        "q", gap_min_expected=1.0,
    ))


def test_gap_trend_needs_minimum_evidence():
    # 1~2편으로는 방향을 말하지 않는다.
    assert _classify_gap_trend(0, 1, 10, 10) == "unknown"
    assert _classify_gap_trend(1, 1, 10, 10) == "unknown"
    # 3편부터 판정.
    assert _classify_gap_trend(0, 3, 10, 10) == "closing"
    assert _classify_gap_trend(3, 0, 10, 10) == "widening"


def test_gap_trend_unknown_when_undated_articles_dominate():
    """연도 미상 논문이 동시등장에 섞이면 구간 비교는 표본 일부만 본 것이다."""
    assert _classify_gap_trend(2, 2, 10, 10, observed=25) == "unknown"
    assert _classify_gap_trend(2, 2, 10, 10, observed=4) == "stable"


def test_observed_early_recent_never_exceed_observed():
    arts = [_mk(f"d{i}", year=2015, mesh=["A", "B"]) for i in range(5)]
    arts += [_mk(f"n{i}", year=None, mesh=["A", "B"]) for i in range(20)]
    arts += [_mk(f"a{i}", year=2015 + i % 8, mesh=["A"]) for i in range(60)]
    arts += [_mk(f"b{i}", year=2015 + i % 8, mesh=["B"]) for i in range(60)]
    for g in gap_pairs(arts, top_k=5, min_expected=1.0, max_lift=99.0):
        assert g.observed_early + g.observed_recent <= g.observed


# --------------------------------------------------------------------------- #
# [MEDIUM] 가교가 항상 '가장 흔한 주제'(=검색어)였다
# --------------------------------------------------------------------------- #
def test_bridge_prefers_specific_term_over_ubiquitous_one():
    """이전: min(A&C, C&B) 원시 편수 정렬 → 코퍼스 최빈 주제가 늘 1위."""
    arts = []
    for i in range(20):   # Query 는 모든 논문에 붙는다(정보량 0)
        arts.append(_mk(f"a{i}", mesh=["A", "Query", "Specific"] if i < 6 else ["A", "Query"]))
    for i in range(20):
        arts.append(_mk(f"b{i}", mesh=["B", "Query", "Specific"] if i < 6 else ["B", "Query"]))
    g = next(
        x for x in gap_pairs(arts, top_k=6, min_expected=1.0, max_lift=99.0)
        if {x.term_a, x.term_b} == {"A", "B"}
    )
    names = [b[0] for b in g.bridges]
    assert names and names[0] == "Specific"
    assert "Query" not in names   # 유병률 100% → 가교 후보에서 제외


# --------------------------------------------------------------------------- #
# [MEDIUM] 부상/쇠퇴 p 에 다중검정 보정이 없었다
# --------------------------------------------------------------------------- #
def test_emerging_declining_rows_carry_qvalues():
    rep = build_report(load_articles(EXAMPLE), "example")
    rows = rep["emerging"] + rep["declining"]
    assert rows
    for t in rows:
        assert t["q_value"] is not None
        assert 0.0 <= t["q_value"] <= 1.0
        assert t["q_value"] >= t["p_value"] - 1e-12
    md = render_markdown(rep)
    assert "선택편향" in md   # 순위로 고른 뒤 검정한다는 사실을 밝힌다


# --------------------------------------------------------------------------- #
# [MEDIUM] 리포트가 q≤0.05 를 권하면서 q=1.000 후보를 굵게 추천했다
# --------------------------------------------------------------------------- #
def test_suggestion_warns_when_top_candidate_fails_fdr():
    arts = [_mk(f"a{i}", year=2015 + i % 6, mesh=["A", "C"]) for i in range(8)]
    arts += [_mk(f"b{i}", year=2015 + i % 6, mesh=["B", "C"]) for i in range(8)]
    rep = build_report(arts, "q", gap_min_expected=1.0, gap_max_lift=1.0)
    md = render_markdown(rep)
    if rep["gaps"] and rep["gaps"][0]["q_value"] > 0.05:
        assert "다중검정 보정 기준(0.05)을 넘습니다" in md


def test_report_states_the_multiple_testing_budget():
    rep = build_report(load_articles(EXAMPLE), "example")
    assert rep["gap_n_tested"] > 0
    md = render_markdown(rep)
    assert f"m={rep['gap_n_tested']}개" in md


def test_empty_gap_advice_does_not_recommend_making_q_worse():
    """`--gap-min-expected` 를 낮추면 검정 수가 늘어 q 는 나빠진다 — 권하면 안 된다."""
    arts = [_mk(f"a{i}", year=2015 + i % 5, mesh=["A", "B"]) for i in range(20)]
    md = render_markdown(build_report(arts, "q", gap_max_lift=0.0))
    assert "저조 조합을 찾지 못했습니다" in md
    assert "`--gap-min-expected` _를 낮추거나" not in md


# --------------------------------------------------------------------------- #
# [MEDIUM] 공백 탐색 대상 주제가 무엇인지 밝히지 않았다
# --------------------------------------------------------------------------- #
def test_report_marks_topics_excluded_from_gap_search():
    rep = build_report(load_articles(EXAMPLE), "example", top_mesh_n=15, gap_top_k=3)
    assert len(rep["gap_terms"]) == 3
    md = render_markdown(rep)
    assert "공백 탐색 제외" in md


# --------------------------------------------------------------------------- #
# 검증 링크 (새 기능)
# --------------------------------------------------------------------------- #
def test_pubmed_pair_url_is_correctly_encoded():
    url = pubmed_pair_url("Heart Rate", "Sleep, REM")
    assert url.startswith("https://pubmed.ncbi.nlm.nih.gov/?term=")
    from urllib.parse import parse_qs, urlparse

    term = parse_qs(urlparse(url).query)["term"][0]
    assert term == '"Heart Rate"[MeSH Terms] AND "Sleep, REM"[MeSH Terms]'


def test_gap_rows_carry_both_verification_urls():
    rep = build_report(load_articles(EXAMPLE), "example")
    for g in rep["gaps"]:
        assert "MeSH+Terms" in g["pubmed_url_mesh"]
        assert "Title%2FAbstract" in g["pubmed_url_text"]
    assert "검증:" in render_markdown(rep)
    assert "pubmed.ncbi.nlm.nih.gov" in render_csv(rep)


# --------------------------------------------------------------------------- #
# 입력 견고성 (엣지케이스 리뷰어)
# --------------------------------------------------------------------------- #
def test_utf16_input_is_decoded_not_mojibake(tmp_path):
    """이전: latin-1 폴백이 UTF-16 을 NUL 섞인 쓰레기로 '성공' 파싱했다."""
    p = tmp_path / "u16.csv"
    p.write_bytes(
        "PMID,Title,Journal,Year,MeSH Terms\n1,T,J,2020,Sleep;X\n2,U,J,2021,Sleep;Y\n"
        .encode("utf-16")
    )
    arts = load_articles(p)
    assert [a.pmid for a in arts] == ["1", "2"]
    assert [a.year for a in arts] == [2020, 2021]
    assert arts[0].mesh == ["Sleep", "X"]
    assert all("\x00" not in a.title for a in arts)


def test_gzip_bomb_is_rejected():
    """279바이트가 50MB 로 부푸는 중첩 gzip 을 실제로 확인했다."""
    payload = gzip.compress(b"A" * (MAX_DECOMPRESSED_BYTES + 1024))
    with pytest.raises(ValueError, match="압축"):
        decode_bytes(payload)


def test_nested_gzip_still_supported():
    inner = gzip.compress("PMID- 1\nTI  - t\nMH  - Sleep\n".encode("utf-8"))
    assert "MH  - Sleep" in decode_bytes(gzip.compress(inner))


def test_unrecognised_input_raises_instead_of_reporting_no_results(tmp_path, capsys):
    """이전: 형식 판별 실패 → 0편 → '검색어를 바꿔 보세요'(진짜 원인 은폐)."""
    p = tmp_path / "junk.txt"
    p.write_text("garbage not a bibliography", encoding="utf-8")
    assert main(["--from-file", str(p)]) == 2
    assert "입력 형식을 알아보지 못했습니다" in capsys.readouterr().err


def test_er_only_file_is_not_a_phantom_article(tmp_path):
    p = tmp_path / "er.ris"
    p.write_text("ER  - \nER  - \n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_articles(p)


def test_year_is_not_extracted_from_longer_digit_runs():
    """unix epoch·accession 번호에서 연도를 뽑아 만들어 내던 문제."""
    arts = parse_csv_records(
        "Title,Year\nA,1719878400\nB,S32019456\nC,2020\nD,Mar-2018\nE,-2019\n"
    )
    assert [a.year for a in arts] == [None, None, 2020, 2018, 2019]


def test_repeated_header_row_is_not_an_article():
    text = (
        "PMID,Title,Journal,Year,MeSH Terms\n"
        "1,A,J,2020,Sleep\n"
        "PMID,Title,Journal,Year,MeSH Terms\n"
        "2,B,J,2021,Sleep\n"
    )
    arts = parse_csv_records(text)
    assert [a.pmid for a in arts] == ["1", "2"]
    assert all("MeSH Terms" not in a.mesh for a in arts)


def test_namespaced_xml_is_parsed():
    xml = (
        '<PubmedArticleSet xmlns="http://example.org/ns"><PubmedArticle>'
        "<MedlineCitation><PMID>7</PMID>"
        "<Article><ArticleTitle>T</ArticleTitle></Article>"
        "<MeshHeadingList><MeshHeading><DescriptorName>Sleep</DescriptorName>"
        "</MeshHeading></MeshHeadingList></MedlineCitation></PubmedArticle></PubmedArticleSet>"
    )
    arts = parse_efetch_xml(xml)
    assert len(arts) == 1 and arts[0].pmid == "7" and arts[0].mesh == ["Sleep"]


def test_extension_does_not_override_content_for_tsv(tmp_path):
    """이전: '.tsv' 라는 이유로 구분자를 탭으로 못박아 쉼표 CSV 가 실패했다."""
    p = tmp_path / "reallycsv.tsv"
    p.write_text(
        "PMID,Title,Journal,Year,MeSH Terms\n1,A,J,2020,Sleep\n2,B,J,2021,Sleep\n",
        encoding="utf-8",
    )
    assert [a.pmid for a in load_articles(p)] == ["1", "2"]


def test_pipe_in_term_does_not_break_markdown_table():
    """RIS 의 `KW - Sleep | Wake` 는 파이프를 그대로 담아 표를 밀어냈다."""
    arts = [_mk(f"a{i}", year=2015 + i % 5, mesh=["Alpha|Bad"]) for i in range(20)]
    arts += [_mk(f"b{i}", year=2015 + i % 5, mesh=["Beta"]) for i in range(20)]
    arts += [_mk(f"c{i}", year=2015 + i % 5, mesh=["Alpha|Bad", "Beta"]) for i in range(2)]
    md = render_markdown(build_report(arts, "q", gap_min_expected=1.0, gap_max_lift=2.0))
    for line in md.splitlines():
        if line.startswith("|") and "Alpha" in line:
            assert "\\|" in line
    _assert_markdown_tables_well_formed(md)


def _assert_markdown_tables_well_formed(md: str) -> None:
    """모든 표에서 각 데이터 행의 칸 수가 헤더와 같아야 한다."""
    header_cols = None
    for line in md.splitlines():
        if not line.startswith("|"):
            header_cols = None
            continue
        cols = len(re_split_cells(line))
        if header_cols is None:
            header_cols = cols
        elif set(line.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
            continue  # 구분선
        else:
            assert cols == header_cols, f"칸 수 불일치: {line}"


def re_split_cells(line: str):
    """이스케이프되지 않은 '|' 로만 칸을 나눈다."""
    cells, cur, i = [], "", 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if ch == "|":
            cells.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur)
    return cells


def test_all_report_tables_are_well_formed():
    _assert_markdown_tables_well_formed(
        render_markdown(build_report(load_articles(EXAMPLE), "example"))
    )


def test_csv_formula_injection_is_neutralised():
    arts = [_mk(f"a{i}", year=2015 + i % 5, mesh=["=cmd|' /C calc'!A0"]) for i in range(20)]
    arts += [_mk(f"b{i}", year=2015 + i % 5, mesh=["Beta"]) for i in range(20)]
    text = render_csv(build_report(arts, "q", gap_min_expected=1.0, gap_max_lift=2.0))
    assert "=cmd" in text                    # 값 자체는 보존
    assert "\n=cmd" not in text and ",=cmd" not in text  # 셀 선두의 '=' 는 없음


# --------------------------------------------------------------------------- #
# CLI 견고성
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("argv_extra, expect", [
    (["--out", "{dir}"], "디렉터리"),
    (["--out", "/nonexistent-dir-xyz/a/b.md"], "상위 폴더가 없습니다"),
])
def test_out_write_failures_are_clean_errors(tmp_path, capsys, argv_extra, expect):
    """이전: --out 이 try 밖이라 원시 트레이스백 + rc 1('결과 없음')."""
    argv = ["--from-file", str(EXAMPLE)] + [
        a.replace("{dir}", str(tmp_path)) for a in argv_extra
    ]
    assert main(argv) == 2
    assert expect in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--max-records", "0"],
    ["--max-records", "-3"],
    ["--years", "-5"],
    ["--gap-top-k", "100000"],
    ["--gap-max-q", "1.5"],
    ["--gap-max-q", "nan"],
    ["--gap-min-expected", "-1"],
    ["--gap-max-lift", "inf"],
])
def test_invalid_numeric_options_are_rejected(argv):
    with pytest.raises(SystemExit):
        main(["--from-file", str(EXAMPLE)] + argv)


def test_file_is_read_only_once(tmp_path, monkeypatch, capsys):
    """이전: load 에서 한 번, meta(sha256) 에서 또 한 번 읽어 FIFO 는 영구 정지했다."""
    src = EXAMPLE.read_bytes()
    p = tmp_path / "in.xml"
    p.write_bytes(src)

    reads = []
    real = Path.open

    def counting(self, *a, **k):
        # read_source 는 상한을 먼저 걸기 위해 open() 으로 청크 읽기를 한다 —
        # 세어야 할 것은 "파일을 몇 번 여는가"다.
        if self == p:
            reads.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", counting)
    assert main(["--from-file", str(p), "--format", "json"]) == 0
    assert len(reads) == 1


def test_save_xml_writes_the_fetched_payload(tmp_path, monkeypatch, capsys):
    """--save-xml 은 지금까지 테스트가 하나도 없던 디스크 쓰기 기능이었다."""
    import pubgap.fetch as fetch_mod

    payload = EXAMPLE.read_text(encoding="utf-8")

    def fake(*a, **k):
        return fetch_mod.FetchResult(xml_text=payload, total_available=28, n_fetched=28)

    monkeypatch.setattr(fetch_mod, "fetch_articles", fake)
    out = tmp_path / "raw.xml"
    assert main(["some query", "--save-xml", str(out), "--format", "json"]) == 0
    assert out.read_text(encoding="utf-8") == payload


def test_save_xml_to_directory_is_a_clean_error(tmp_path, monkeypatch, capsys):
    import pubgap.fetch as fetch_mod

    def fake(*a, **k):
        return fetch_mod.FetchResult(xml_text="<x/>", total_available=0, n_fetched=0)

    monkeypatch.setattr(fetch_mod, "fetch_articles", fake)
    assert main(["some query", "--save-xml", str(tmp_path)]) == 2
    assert "--save-xml" in capsys.readouterr().err


def test_save_xml_and_years_are_flagged_as_ignored_for_file_input(capsys):
    main(["--from-file", str(EXAMPLE), "--save-xml", "/tmp/x.xml", "--years", "3",
          "--format", "json"])
    err = capsys.readouterr().err
    assert "--save-xml 은 네트워크 조회에만" in err
    assert "--years 는 네트워크 조회에만" in err


def test_api_key_is_scrubbed_from_error_messages(monkeypatch, capsys):
    import pubgap.fetch as fetch_mod

    def boom(*a, **k):
        raise RuntimeError(
            "failed to open https://eutils.ncbi.nlm.nih.gov/x?db=pubmed&api_key=SECRET123&term=x"
        )

    monkeypatch.setattr(fetch_mod, "fetch_articles", boom)
    assert main(["some query"]) == 3
    err = capsys.readouterr().err
    assert "SECRET123" not in err
    assert "api_key=<redacted>" in err


def test_meta_never_contains_credentials(monkeypatch, capsys):
    import pubgap.fetch as fetch_mod

    def fake(*a, **k):
        return fetch_mod.FetchResult(
            xml_text=EXAMPLE.read_text(encoding="utf-8"), total_available=28, n_fetched=28
        )

    monkeypatch.setattr(fetch_mod, "fetch_articles", fake)
    assert main(["q", "--email", "me@lab.org", "--api-key", "SECRET123",
                 "--format", "json"]) == 0
    blob = capsys.readouterr().out
    assert "SECRET123" not in blob and "me@lab.org" not in blob


def test_meta_records_every_analysis_option(capsys):
    """Methods 에 옮겨 적어 재현하려면 결과에 영향 주는 옵션이 다 있어야 한다."""
    main(["--from-file", str(EXAMPLE), "--format", "json",
          "--top-mesh", "3", "--top-evidence", "4", "--no-bridges"])
    params = json.loads(capsys.readouterr().out)["meta"]["params"]
    assert params["top_mesh"] == 3
    assert params["top_evidence"] == 4
    assert params["bridges"] is False
    for key in ("gap_top_k", "gap_sort", "gap_max_q", "evidence", "top_journals",
                "include_check_tags", "min_year", "max_year"):
        assert key in params


# --------------------------------------------------------------------------- #
# 통계 가드 (정확성 리뷰어)
# --------------------------------------------------------------------------- #
def test_negative_cells_raise_instead_of_returning_max_significance():
    """음수 칸은 호출부 버그다. 0.0(=최대 유의)을 조용히 돌려주면 버그가 '발견'이 된다."""
    with pytest.raises(ValueError):
        fisher_exact_two_sided(-1, 5, 3, 3)
    with pytest.raises(ValueError):
        hypergeom_lower_tail(10, -1, 3, 1)


def test_growth_summary_empty_matches_normal_schema():
    assert set(growth_summary({})) == set(growth_summary({2020: 1, 2021: 3}))


# --------------------------------------------------------------------------- #
# 하드닝 라운드 2 — 라운드 1에서 새로 들어간 코드의 결함
# --------------------------------------------------------------------------- #
def test_single_year_corpus_does_not_claim_infinite_growth():
    """이전: split==lo 라 early_years=0 → '연 0.0편 대비 ∞배' 라는 헛된 성장 주장.

    `truncated` 가드가 막으려던 것과 같은 종류의 오류가 다른 경로로 새어 나왔다.
    """
    arts = [_mk(str(i), year=2020, mesh=["A", "B"]) for i in range(40)]
    rep = build_report(arts, "q")
    assert rep["growth"]["early_years"] == 0
    assert rep["growth"]["ratio_per_year"] == float("inf")
    md = render_markdown(rep)
    assert not [ln for ln in md.splitlines() if ln.startswith("- 발행량")]
    assert "∞" not in md
    # JSON 은 여전히 표준 JSON 이어야 한다(inf → null).
    assert json.loads(json.dumps(json_safe(rep), allow_nan=False))["growth"][
        "ratio_per_year"
    ] is None


def test_complete_gap_is_reported_even_in_a_single_year_corpus():
    """이전: `n_early<=0` 가드가 `empty` 판정보다 먼저 걸려, 가장 강한 신호를 감췄다."""
    arts = [_mk(f"a{i}", year=2020, mesh=["Apnea"]) for i in range(20)]
    arts += [_mk(f"b{i}", year=2020, mesh=["Obesity"]) for i in range(20)]
    rep = build_report(arts, "q", gap_min_expected=1.0)
    g = rep["gaps"][0]
    assert g["observed"] == 0
    assert g["gap_trend"] == "empty"
    assert "⬜ 완전공백" in render_markdown(rep)


@pytest.mark.parametrize("value, expected", [
    ("20190315", 2019),      # MEDLINE DEP / 일부 RIS DA 는 YYYYMMDD
    ("2019", 2019),
    ("2019 Jan-Feb", 2019),
    ("1999-2000", 1999),
    ("2019/03/01/", 2019),
    ("1719878400", None),    # unix epoch — 연도가 아니다
    ("S32019456", None),     # accession
    ("201903", None),        # YYYYMM 은 accession 과 구분 불가 → 거부
    ("20191345", None),      # 월/일이 불가능한 값
])
def test_year_extraction_accepts_yyyymmdd_but_still_rejects_digit_runs(value, expected):
    from pubgap.records import _year_from

    assert _year_from(value) == expected


def test_nbib_dep_only_record_keeps_its_year():
    """회귀: 숫자열 경계 규칙을 넣으면서 DEP(YYYYMMDD) 분기가 죽은 코드가 됐었다."""
    from pubgap.records import _nbib_year

    assert _nbib_year({"DEP": "20190315"}) == 2019
    assert parse_ris("TY  - JOUR\nTI  - A\nDA  - 20170504\nER  -\n")[0].year == 2017


# --------------------------------------------------------------------------- #
# 테스트 품질 리뷰어가 지적한 미검증 경로
# --------------------------------------------------------------------------- #
def test_large_n_hypergeometric_matches_exact_without_scipy():
    """log 경로(N>60)를 scipy 없이 고정한다 — 이전엔 이 경로가 선택적 의존성으로만 검증됐다."""
    from fractions import Fraction
    from math import comb

    def exact(N, K, n, k):
        lo = max(0, n - (N - K))
        return Fraction(
            sum(comb(K, i) * comb(N - K, n - i) for i in range(lo, k + 1)), comb(N, n)
        )

    for N, K, n, k in [(100, 50, 40, 15), (200, 30, 80, 5), (500, 120, 90, 10),
                       (61, 30, 20, 4), (1000, 400, 300, 100)]:
        assert hypergeom_lower_tail(N, K, n, k) == pytest.approx(
            float(exact(N, K, n, k)), rel=1e-9, abs=1e-12
        )


def test_min_expected_boundary_is_inclusive():
    """기대값이 임계와 정확히 같은 쌍은 **포함**된다(경계 의미를 고정)."""
    arts = [_mk(f"a{i}", mesh=["A"]) for i in range(10)]
    arts += [_mk(f"b{i}", mesh=["B"]) for i in range(10)]
    # N=20, cA=cB=10 → 기대 = 5.0
    assert gap_pairs(arts, top_k=5, min_expected=5.0, max_lift=99.0)
    assert not gap_pairs(arts, top_k=5, min_expected=5.0001, max_lift=99.0)


def test_split_point_rounds_up_on_odd_spans():
    from pubgap.analyze import split_point, term_trends

    arts = [_mk("1", year=2015, mesh=["A"]), _mk("2", year=2022, mesh=["A"])]
    assert split_point(arts) == 2019   # (2015+2022+1)//2, 내림이면 2018

    # split 연도에 정확히 걸친 논문은 '최근' 구간에 들어간다(>= 경계).
    arts = [_mk("1", year=2015, mesh=["A"]), _mk("2", year=2019, mesh=["A"]),
            _mk("3", year=2022, mesh=["A"])]
    trend = {t.term: t for t in term_trends(arts, min_total=1)}["A"]
    assert (trend.early_count, trend.recent_count) == (1, 2)


def test_topics_from_keywords_leaves_mixed_corpora_alone():
    """일부만 MeSH 가 있는 코퍼스에 키워드를 섞으면 주제 밀도가 어긋난다."""
    from pubgap.records import topics_from_keywords

    arts = [_mk("1", mesh=["Sleep"], kw=["hrv"]), _mk("2", mesh=[], kw=["hrv", "apnea"])]
    out, used = topics_from_keywords(arts)
    assert used is False
    assert [a.mesh for a in out] == [["Sleep"], []]


def test_sniff_delimiter_prefers_consistency_over_raw_count():
    """헤더 칸 안에 세미콜론이 많아도 실제 구분자(쉼표)를 골라야 한다."""
    from pubgap.records import sniff_delimiter

    text = (
        'PMID,Title,MeSH Terms\n'
        '1,"A;B;C;D;E","Sleep;Heart Rate"\n'
        '2,"F;G;H;I;J","Sleep;Respiration"\n'
    )
    assert sniff_delimiter(text) == ","


def test_nbib_publication_types_are_parsed():
    """PT 태그가 있는 NBIB 픽스처가 하나도 없었다 — 근거 축이 이 형식에서 미검증."""
    text = (
        "PMID- 1\nDP  - 2020\nTA  - J\nTI  - t\nMH  - *Sleep\n"
        "PT  - Journal Article\nPT  - Randomized Controlled Trial\n\n"
        "PMID- 2\nDP  - 2021\nTA  - J\nTI  - t\nMH  - *Sleep\n"
        "PT  - Journal Article\nPT  - Clinical Trial, Phase II\n\n"
        "PMID- 3\nDP  - 2021\nTA  - J\nTI  - t\nMH  - *Sleep\nPT  - Journal Article\n"
    )
    arts = parse_records(text)
    assert arts[0].pub_types == ["Journal Article", "Randomized Controlled Trial"]
    assert evidence_tier(arts[1].pub_types) == "trial"
    prof = evidence_profile(arts)
    assert prof["n_typed"] == 2 and prof["n_interventional"] == 2
    assert prof["n_unknown"] == 1


def test_csv_keyword_column_carrying_mesh_is_promoted():
    """CSV 의 kw_mesh_mode 분기 — 문서화돼 있었지만 한 번도 실행되지 않았다."""
    text = (
        "PMID,Title,Year,Keywords\n"
        "1,A,2020,*Sleep; Heart Rate/physiology\n"
        "2,B,2021,*Sleep; Respiration/*physiology\n"
    )
    arts = parse_csv_records(text)
    assert arts[0].mesh == ["Sleep", "Heart Rate"]
    assert arts[0].mesh_major == ["Sleep"]
    assert arts[1].mesh_major == ["Sleep", "Respiration"]


def test_efetch_error_element_raises_not_empty(tmp_path):
    xml = "<eFetchResult><ERROR>Empty id list</ERROR></eFetchResult>"
    with pytest.raises(ValueError, match="오류 응답"):
        parse_efetch_xml(xml)


def test_pubmed_book_article_is_parsed():
    xml = (
        "<PubmedArticleSet><PubmedBookArticle><BookDocument>"
        "<PMID>99</PMID><Book><BookTitle>Sleep Handbook</BookTitle></Book>"
        "<PublicationType>Review</PublicationType>"
        "</BookDocument></PubmedBookArticle></PubmedArticleSet>"
    )
    arts = parse_efetch_xml(xml)
    assert len(arts) == 1 and arts[0].pmid == "99"
    assert arts[0].title == "Sleep Handbook"
