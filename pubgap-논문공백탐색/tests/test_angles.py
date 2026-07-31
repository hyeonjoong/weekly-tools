"""연구 각도(MeSH 부주제어) 축 — 파싱·통계·구조적 불가 필터·리포트."""

import json

import pytest

from pathlib import Path as _Path

from pubgap import analyze
from pubgap.cli import main
from pubgap.records import (
    Article,
    normalize_qualifier,
    parse_csv_records,
    parse_efetch_xml,
    parse_medline_nbib,
    parse_ris,
)
from pubgap.report import build_report, render_csv, render_markdown

_ROOT = _Path(__file__).resolve().parents[1]
# cwd 와 무관하게 돌도록 절대경로로 잡는다 — 상대경로면 다른 폴더에서
# 돌릴 때 rc 2(파일 없음)로 끝나면서도 통과하는 테스트가 생긴다.
_EX_XML = str(_ROOT / "examples" / "sleep_pubmed.xml")
_EX_CSV = str(_ROOT / "examples" / "sleep_export.csv")


def art(pmid, year, mesh, quals, pub_types=("Journal Article",)):
    return Article(
        pmid=pmid, year=year, journal="J", title=f"t{pmid}",
        mesh=list(mesh), qualifiers=[tuple(q) for q in quals],
        pub_types=list(pub_types),
    )


# --------------------------------------------------------------------------- #
# 파싱
# --------------------------------------------------------------------------- #
XML_WITH_QUALIFIERS = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle><MedlineCitation>
    <PMID>1</PMID>
    <Article>
      <Journal><JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue>
        <ISOAbbreviation>J</ISOAbbreviation></Journal>
      <ArticleTitle>T</ArticleTitle>
      <ELocationID EIdType="doi">10.1000/ABC.123</ELocationID>
    </Article>
    <MeshHeadingList>
      <MeshHeading>
        <DescriptorName MajorTopicYN="N">Hypertension</DescriptorName>
        <QualifierName MajorTopicYN="Y">drug therapy</QualifierName>
        <QualifierName MajorTopicYN="N">physiopathology</QualifierName>
      </MeshHeading>
      <MeshHeading>
        <DescriptorName MajorTopicYN="N">Melatonin</DescriptorName>
      </MeshHeading>
    </MeshHeadingList>
  </MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def test_xml_qualifiers_and_doi_are_parsed():
    a = parse_efetch_xml(XML_WITH_QUALIFIERS)[0]
    assert a.mesh == ["Hypertension", "Melatonin"]
    assert a.qualifiers == [
        ("Hypertension", "drug therapy"),
        ("Hypertension", "physiopathology"),
    ]
    assert a.mesh_major == ["Hypertension"]  # qualifier 가 major 면 descriptor 도 대표
    assert a.doi == "10.1000/abc.123"        # DOI 는 대소문자 무시 → 소문자 정규화


def test_nbib_qualifiers_including_multiple_on_one_line():
    text = (
        "PMID- 9\n"
        "TI  - x\n"
        "MH  - Brain/*drug effects/metabolism\n"
        "MH  - *Sleep\n"
        "MH  - Insomnia/therapy\n"
        "AID - S1389-9457(19)30000-0 [pii]\n"
        "AID - 10.1016/J.SLEEP.2019.01.001 [doi]\n"
    )
    a = parse_medline_nbib(text)[0]
    assert a.mesh == ["Brain", "Sleep", "Insomnia"]
    assert a.qualifiers == [
        ("Brain", "drug effects"),
        ("Brain", "metabolism"),
        ("Insomnia", "therapy"),
    ]
    assert a.doi == "10.1016/j.sleep.2019.01.001"  # [pii] 를 DOI 로 착각하지 않는다


def test_csv_and_ris_qualifiers():
    csv_text = (
        "PMID,Title,Year,MeSH Terms,DOI\n"
        "5,T,2020,Insomnia/*drug therapy; Melatonin/therapeutic use,"
        "https://doi.org/10.5555/Xy.9\n"
    )
    a = parse_csv_records(csv_text)[0]
    assert a.qualifiers == [
        ("Insomnia", "drug therapy"), ("Melatonin", "therapeutic use")
    ]
    assert a.doi == "10.5555/xy.9"

    ris = (
        "TY  - JOUR\nTI  - T\nPY  - 2020\n"
        "KW  - Insomnia/*drug therapy\nKW  - Melatonin/therapeutic use\n"
        "DO  - 10.5555/xy.9\nER  - \n"
    )
    r = parse_ris(ris)[0]
    assert r.qualifiers == [
        ("Insomnia", "drug therapy"), ("Melatonin", "therapeutic use")
    ]
    assert r.doi == "10.5555/xy.9"


def test_qualifier_normalisation_is_case_and_star_insensitive():
    assert normalize_qualifier("*Drug Therapy") == "drug therapy"
    assert normalize_qualifier("  DRUG THERAPY \n") == "drug therapy"
    assert normalize_qualifier("") == ""
    assert normalize_qualifier(None) == ""


def test_same_qualifier_in_different_cases_is_one_angle():
    arts = [
        art("1", 2020, ["Sleep"], [("Sleep", "physiology")]),
        art("2", 2020, ["Sleep"], [("Sleep", "PHYSIOLOGY")]),
    ]
    # 파서를 거치지 않고 직접 만든 레코드는 정규화 전이므로, 커버리지가 이를 드러낸다.
    cov = analyze.qualifier_coverage(
        [art("1", 2020, ["Sleep"], [("Sleep", normalize_qualifier("PHYSIOLOGY"))]),
         art("2", 2020, ["Sleep"], [("Sleep", normalize_qualifier("physiology"))])]
    )
    assert cov["n_distinct"] == 1
    assert len(arts) == 2


# --------------------------------------------------------------------------- #
# 통계
# --------------------------------------------------------------------------- #
def _grid_corpus():
    """A 는 x 각도로만, B 는 x·y 모두, C 는 y 로만 — A×y 가 공백이 되어야 한다."""
    arts = []
    for i in range(10):
        arts.append(art(f"a{i}", 2020, ["TopicA", "TopicB"],
                        [("TopicA", "x"), ("TopicB", "x")]))
    for i in range(10):
        arts.append(art(f"b{i}", 2020, ["TopicB", "TopicC"],
                        [("TopicB", "y"), ("TopicC", "y")]))
    return arts


def test_angle_gap_finds_the_empty_cell_with_correct_arithmetic():
    """분석 단위는 **색인 표목**((논문, 주제어) 칸)이어야 한다 — 논문이 아니라."""
    arts = _grid_corpus()
    gaps = analyze.angle_gaps(arts, min_term_articles=3, min_expected=1.0)
    cells = {(g.term, g.qualifier): g for g in gaps}
    assert ("TopicA", "y") in cells
    g = cells[("TopicA", "y")]
    # 표목: 앞 10편이 (A,x)(B,x) 2칸씩, 뒤 10편이 (B,y)(C,y) 2칸씩 → N=40.
    # TopicA 표목 10, 각도 y 표목 20 → 기대 = 10*20/40 = 5.
    assert (g.n_term, g.n_qualifier, g.observed) == (10, 20, 0)
    assert g.expected == pytest.approx(5.0)
    assert g.deficit == pytest.approx(5.0)
    assert g.lift == 0.0
    assert g.p_value == pytest.approx(
        analyze.hypergeom_lower_tail(40, 10, 20, 0), abs=1e-12
    )
    assert g.lift_ci_low == 0.0 and g.lift_ci_high == pytest.approx(3.68888 / 5.0, abs=1e-5)
    assert g.top_angles == [["x", 10]]


def test_angle_denominator_counts_qualified_headings_only():
    """부주제어가 없는 논문/표목은 분모에 들어가면 안 된다(색인 부재 ≠ 연구 부재)."""
    arts = _grid_corpus() + [
        art(f"bare{i}", 2020, ["TopicA", "TopicB", "TopicZ"], []) for i in range(20)
    ]
    g = {(x.term, x.qualifier): x for x in analyze.angle_gaps(arts, min_expected=1.0)}
    cell = g[("TopicA", "y")]
    assert (cell.n_term, cell.n_qualifier) == (10, 20)   # 표목 수는 그대로
    assert cell.expected == pytest.approx(5.0)           # 분모도 그대로(40)
    cov = analyze.qualifier_coverage(arts)
    assert cov["n_with_qualifiers"] == 20 and cov["coverage"] == pytest.approx(0.5)


def test_angle_statistic_is_calibrated_under_a_true_null():
    """주제와 각도가 **독립**인 코퍼스에서 유의한 칸이 쏟아지면 안 된다.

    예전 구현은 주변확률을 논문 수준에서, 관측을 표목 수준에서 세어 논문당 주제어가
    d개면 lift 가 1/d 로 눌렸다 — 진짜 귀무가설에서도 p≤0.05 비율이 1.00 이었다.
    """
    import random

    rng = random.Random(20260731)
    terms = [f"T{i}" for i in range(6)]
    quals = ["qa", "qb", "qc"]
    arts = []
    for i in range(400):
        chosen = rng.sample(terms, 4)          # 논문당 주제어 4개
        pairs = [(t, rng.choice(quals)) for t in chosen]   # 각도는 주제와 무관하게 배정
        arts.append(art(f"n{i}", 2020, chosen, pairs))
    cands, m, _imp = analyze.angle_analysis(
        arts, top_k=10, top_qualifiers=5, min_expected=1.0, max_lift=float("inf")
    )
    ps = [g.p_value for g in cands]
    assert m == len(ps) == 18
    frac_sig = sum(1 for p in ps if p <= 0.05) / len(ps)
    median = sorted(ps)[len(ps) // 2]
    assert frac_sig <= 0.25, f"귀무가설인데 p<=0.05 비율이 {frac_sig:.2f}"
    assert 0.2 <= median <= 0.8, f"p 중앙값이 {median:.3f} — 보정이 어긋났다"
    assert all(g.lift_ci_low <= g.lift <= g.lift_ci_high for g in cands)


def test_angle_gap_excludes_cells_below_min_expected():
    arts = _grid_corpus()
    assert analyze.angle_gaps(arts, min_expected=99.0) == []


def test_angle_gap_respects_min_term_articles():
    arts = _grid_corpus() + [art("z", 2020, ["Rare"], [("Rare", "z")])]
    terms = {g.term for g in analyze.angle_gaps(arts, min_term_articles=3)}
    assert "Rare" not in terms


def test_implausible_cells_are_flagged_ranked_last_and_still_tested():
    """어휘가 완전히 다른 조합(색인 규칙상 불가능)은 **표시만** 뒤로 미룬다."""
    arts = []
    for i in range(10):  # 생리 어휘
        arts.append(art(f"p{i}", 2020, ["Sleep", "Heart Rate"],
                        [("Sleep", "physiology"), ("Heart Rate", "physiology")]))
    for i in range(10):  # 기법 어휘
        arts.append(art(f"m{i}", 2020, ["EEG"], [("EEG", "methods")]))
    kept, m, implausible = analyze.angle_analysis(arts, min_expected=1.0)
    cells = {(g.term, g.qualifier): g for g in kept}
    assert ("EEG", "physiology") in cells
    assert cells[("EEG", "physiology")].plausible is False
    # 검정 집합에서 빼지 않는다 — m 은 (lift 필터로 표에서 빠진 칸까지) 모두 센다.
    assert m >= len(kept) and implausible >= 1
    assert m == len(analyze.angle_analysis(
        arts, min_expected=1.0, max_lift=float("inf"))[0])
    # 불가로 보이는 칸은 항상 뒤로 밀린다.
    flags = [g.plausible for g in kept]
    assert flags == sorted(flags, reverse=True)
    hidden, m2, imp2 = analyze.angle_analysis(
        arts, min_expected=1.0, hide_implausible=True
    )
    assert m2 == m and imp2 == implausible          # 검정 수는 그대로
    assert all(g.plausible for g in hidden)         # 표시만 줄어든다
    assert len(hidden) < len(kept)


def test_plausibility_does_not_depend_on_the_cell_being_tested():
    """판정이 '관측이 0인가'에 좌우되면 공백만 골라서 지우게 된다(회귀 방지).

    같은 어휘 가족을 쓰는 두 주제 중 하나가 그 각도를 아직 안 썼을 뿐인 경우,
    그 칸은 **가능**으로 남아야 한다.
    """
    arts = []
    for i in range(10):
        arts.append(art(f"a{i}", 2020, ["Melatonin"],
                        [("Melatonin", "therapeutic use"), ("Melatonin", "pharmacology")]))
    for i in range(10):
        arts.append(art(f"b{i}", 2020, ["Benzodiazepines"],
                        [("Benzodiazepines", "adverse effects"),
                         ("Benzodiazepines", "pharmacology")]))
    kept = analyze.angle_gaps(arts, min_expected=1.0)
    cells = {(g.term, g.qualifier): g for g in kept}
    # Melatonin 은 /adverse effects 를 한 번도 안 썼지만, 같은 약물 어휘
    # (pharmacology)를 공유하므로 색인 가능한 조합이다 → 진짜 공백 후보로 남아야 한다.
    assert ("Melatonin", "adverse effects") in cells
    assert cells[("Melatonin", "adverse effects")].plausible is True


def test_plausible_cell_survives_when_vocabulary_is_shared():
    """같은 어휘 가족(생리/약물영향)을 공유하면 후보로 남는다."""
    arts = []
    for i in range(8):
        arts.append(art(f"s{i}", 2020, ["Sleep"],
                        [("Sleep", "physiology"), ("Sleep", "drug effects")]))
    for i in range(8):
        arts.append(art(f"h{i}", 2020, ["Heart Rate"], [("Heart Rate", "physiology")]))
    gaps = analyze.angle_gaps(arts, min_expected=1.0)
    assert ("Heart Rate", "drug effects") in {(g.term, g.qualifier) for g in gaps}


def test_angle_gaps_ignore_terms_removed_from_analysis():
    """--exclude-term / 체크태그 제거로 빠진 주제는 각도 표에도 나오면 안 된다."""
    arts = _grid_corpus()
    stripped = analyze.drop_terms(arts, ["TopicA"])
    assert all(g.term != "TopicA" for g in analyze.angle_gaps(stripped))
    assert all(
        t != "TopicA" for a in stripped for t, _q in analyze.article_angles(a)
    )


def test_angle_sorting_is_deterministic_and_validated():
    arts = _grid_corpus()
    for key in analyze.ANGLE_SORTS:
        first = analyze.angle_gaps(arts, sort=key)
        second = analyze.angle_gaps(list(reversed(arts)), sort=key)
        assert [(g.term, g.qualifier) for g in first] == [
            (g.term, g.qualifier) for g in second
        ]
    with pytest.raises(ValueError):
        analyze.angle_gaps(arts, sort="nope")


def test_angle_analysis_on_corpus_without_qualifiers_is_empty_not_error():
    arts = [art("1", 2020, ["A", "B"], [])]
    assert analyze.angle_gaps(arts) == []
    assert analyze.angle_analysis(arts) == ([], 0, 0)
    cov = analyze.qualifier_coverage(arts)
    assert cov["n_with_qualifiers"] == 0 and cov["coverage"] == 0.0


def test_qualifier_coverage_counts_articles_not_pairs():
    arts = [
        art("1", 2020, ["A"], [("A", "x"), ("A", "y")]),
        art("2", 2020, ["A"], []),
    ]
    cov = analyze.qualifier_coverage(arts)
    assert cov["n_with_qualifiers"] == 1
    assert cov["coverage"] == pytest.approx(0.5)
    assert dict(cov["top_qualifiers"]) == {"x": 1, "y": 1}


# --------------------------------------------------------------------------- #
# 리포트 / CLI
# --------------------------------------------------------------------------- #
def test_report_renders_angle_section_and_csv():
    rep = build_report(_grid_corpus(), "q", angle_min_expected=1.0)
    md = render_markdown(rep)
    assert "연구 각도 공백" in md
    assert "TopicA" in md and "각도 제안:" in md
    assert rep["angle_n_tested"] >= 1
    csv_text = render_csv(rep, section="angles")
    head, *rows = csv_text.lstrip("﻿").splitlines()
    assert head.startswith("term,qualifier,n_term")
    assert rows and rows[0].startswith("TopicA,y")
    assert "pubmed.ncbi.nlm.nih.gov" in rows[0]


def test_report_without_qualifiers_says_so_instead_of_claiming_gaps():
    rep = build_report([art("1", 2020, ["A", "B"], [])], "q")
    md = render_markdown(rep)
    assert "부주제어" in md and "각도 분석을 낼 수 없습니다" in md


def test_no_angles_flag_removes_the_section(capsys):
    rc = main(["--from-file", _EX_XML, "--no-angles", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "angle_gaps" not in data and "qualifier_coverage" not in data


def _angle_json(capsys, *args):
    assert main(["--from-file", _EX_XML, "--format", "json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_angle_options_change_the_analysis_not_just_the_meta(capsys):
    data = _angle_json(capsys, "--angle-top-k", "5", "--angle-max-lift", "0.9")
    assert data["meta"]["params"]["angle_top_k"] == 5
    for g in data["angle_gaps"]:
        assert g["lift"] <= 0.9
    # 옵션이 실제 분석을 바꾸는지 — 검정 칸 수가 K 에 따라 단조 증가해야 한다.
    tested = [
        _angle_json(capsys, "--angle-top-k", str(k))["angle_n_tested"]
        for k in (1, 2, 12)
    ]
    assert tested == sorted(tested) and tested[0] < tested[-1]
    quals = [
        _angle_json(capsys, "--angle-top-qualifiers", str(m))["angle_n_tested"]
        for m in (1, 3, 10)
    ]
    assert quals == sorted(quals) and quals[0] < quals[-1]


def test_cli_hide_implausible_only_changes_display(capsys):
    shown = _angle_json(capsys)
    hidden = _angle_json(capsys, "--angle-hide-implausible")
    assert hidden["meta"]["params"]["angle_hide_implausible"] is True
    assert hidden["angle_n_tested"] == shown["angle_n_tested"]      # 검정 수 동일
    assert hidden["angle_n_implausible"] == shown["angle_n_implausible"]
    assert all(g["plausible"] for g in hidden["angle_gaps"])
    assert len(hidden["angle_gaps"]) < len(shown["angle_gaps"])
    # q 값도 바뀌면 안 된다(검정 집합이 같으므로).
    by_cell = {(g["term"], g["qualifier"]): g["q_value"] for g in shown["angle_gaps"]}
    for g in hidden["angle_gaps"]:
        assert by_cell[(g["term"], g["qualifier"])] == pytest.approx(g["q_value"])


def test_bundled_example_angle_gap_is_the_pharmacological_one(capsys):
    rc = main(["--from-file", _EX_XML, "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    rows = data["angle_gaps"]
    cells = {(g["term"], g["qualifier"]): g for g in rows}
    # 약리학적으로 타당한 공백이 1순위여야 한다(그리고 유일한 '가능' 후보다).
    assert (rows[0]["term"], rows[0]["qualifier"]) == ("Heart Rate", "drug effects")
    assert rows[0]["plausible"] is True
    # 구조적으로 불가능한 조합(기법 용어 × 생리 각도)은 표에 남되 뒤로 밀리고 표시된다.
    assert cells[("Electroencephalography", "physiology")]["plausible"] is False
    assert data["angle_n_implausible"] > 0
    md_rc = main(["--from-file", _EX_XML])
    assert md_rc == 0
    md = capsys.readouterr().out
    assert "⚠ 규칙상 불가?" in md
    assert "각도 제안: **Heart Rate** 를 **drug effects**" in md
