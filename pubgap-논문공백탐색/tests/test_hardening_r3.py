"""하드닝 라운드 3 회귀 테스트 — 병렬 리뷰어 5종이 실제로 찾아낸 결함들.

각 테스트는 '고치기 전에는 실패하던' 구체적 상황을 재현한다.
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pubgap import analyze
from pubgap.cli import main
from pubgap.records import (
    Article,
    NLM_SUBHEADINGS,
    dedup_articles,
    dedup_articles_detailed,
    is_subheading,
    parse_csv_records,
    parse_efetch_xml,
    parse_medline_nbib,
    read_source,
    title_key,
)
from pubgap.report import _md_cell, build_report, render_csv, render_markdown

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sleep_pubmed.xml"


def art(pmid, year, mesh, quals, **kw):
    return Article(
        pmid=pmid, year=year, journal=kw.get("journal", "J"),
        title=kw.get("title", f"Study number {pmid} of the synthetic series"),
        mesh=list(mesh), qualifiers=[tuple(q) for q in quals],
        pub_types=list(kw.get("pub_types", ())), doi=kw.get("doi", ""),
    )


# --------------------------------------------------------------------------- #
# [정확성 HIGH] 각도 검정의 모집단 — 표목 단위여야 한다
# --------------------------------------------------------------------------- #
def test_angle_marginals_and_observed_share_one_population():
    """주변확률과 관측이 서로 다른 단위였던 버그(모든 칸이 공백으로 보임)."""
    arts = [
        art(f"a{i}", 2020, ["T1", "T2", "T3"],
            [("T1", "physiology"), ("T2", "physiology"), ("T3", "methods")])
        for i in range(30)
    ]
    cands, _m, _imp = analyze.angle_analysis(
        arts, min_expected=0.0, max_lift=float("inf")
    )
    cells = {(g.term, g.qualifier): g for g in cands}
    # 표목: 논문마다 3칸 → N=90. physiology 표목 60, T3 표목 30 → 기대 20.
    g = cells[("T3", "physiology")]
    assert (g.n_term, g.n_qualifier) == (30, 60)
    assert g.expected == pytest.approx(30 * 60 / 90)
    # 그리고 관측(0)과 주변확률이 같은 모집단이므로 p 는 0 이 아니라 유한한 값이다.
    assert 0.0 < g.p_value <= 1.0
    for g in cands:
        assert g.observed <= min(g.n_term, g.n_qualifier) <= 90
        assert g.p_value > 0.0, (g.term, g.qualifier)


def test_angle_p_values_are_not_all_zero_on_multi_topic_corpora():
    """논문당 주제어가 많아도 p 가 0 으로 붕괴하면 안 된다(초기하 support 밖 관측)."""
    arts = [
        art(f"a{i}", 2020, [f"T{j}" for j in range(8)],
            [(f"T{j}", "physiology" if j % 2 else "methods") for j in range(8)])
        for i in range(40)
    ]
    cands, _m, _i = analyze.angle_analysis(
        arts, top_k=8, min_expected=0.0, max_lift=float("inf")
    )
    assert cands and all(g.p_value > 0 for g in cands)


# --------------------------------------------------------------------------- #
# [정확성 MEDIUM] 구조 필터가 결과에 의존하면 안 된다
# --------------------------------------------------------------------------- #
def test_implausible_cells_do_not_change_the_fdr_denominator():
    """구조 판정은 표시에만 쓰고 검정 집합(m)은 건드리지 않는다."""
    arts = []
    for i in range(25):
        arts.append(art(f"x{i}", 2020, ["X"], [("X", "physiology")]))
    for i in range(25):
        arts.append(art(f"y{i}", 2020, ["Y"], [("Y", "methods")]))
    shown, m, implausible = analyze.angle_analysis(arts, min_expected=1.0)
    hidden, m2, imp2 = analyze.angle_analysis(
        arts, min_expected=1.0, hide_implausible=True
    )
    assert (m, implausible) == (m2, imp2)
    q_by_cell = {(g.term, g.qualifier): g.q_value for g in shown}
    for g in hidden:
        assert q_by_cell[(g.term, g.qualifier)] == pytest.approx(g.q_value)


def test_plausibility_is_leave_one_out_not_observed_based():
    """'그 주제가 이미 그 각도를 쓰는가'(=관측≥1)로 판정하면 공백만 지운다."""
    arts = []
    for i in range(10):
        arts.append(art(f"m{i}", 2020, ["DrugA"],
                        [("DrugA", "pharmacology"), ("DrugA", "adverse effects")]))
    for i in range(10):
        arts.append(art(f"n{i}", 2020, ["DrugB"], [("DrugB", "pharmacology")]))
    cells = {(g.term, g.qualifier): g
             for g in analyze.angle_gaps(arts, min_expected=0.5, max_lift=float("inf"))}
    # DrugB 는 /adverse effects 를 한 번도 안 썼지만(관측 0) 같은 어휘를 공유한다.
    assert cells[("DrugB", "adverse effects")].plausible is True


# --------------------------------------------------------------------------- #
# [엣지 HIGH] 자원 상한 · 인코딩
# --------------------------------------------------------------------------- #
def test_input_limit_is_checked_before_reading_everything(tmp_path, monkeypatch):
    """상한을 나중에 검사하면 가드 자체가 메모리 폭탄이 된다."""
    from pubgap import records

    monkeypatch.setattr(records, "MAX_INPUT_BYTES", 1024)
    big = tmp_path / "big.csv"
    big.write_bytes(b"Title,Year\n" + b"x" * 5000)
    with pytest.raises(ValueError, match="넘습니다"):
        read_source(big)


def test_unbounded_stream_is_rejected_quickly(monkeypatch):
    """/dev/zero 같은 무한 스트림에서 멈추지 않고 상한으로 끊어야 한다."""
    from pubgap import records

    monkeypatch.setattr(records, "MAX_INPUT_BYTES", 1 << 20)
    with pytest.raises(ValueError, match="넘습니다"):
        read_source(Path("/dev/zero"))


def test_non_utf8_terminal_still_produces_output():
    """cp949/ascii 콘솔에서 rc 0 + 출력 0바이트로 끝나던 버그."""
    proc = subprocess.run(
        [sys.executable, "-m", "pubgap.cli", "--from-file", str(EXAMPLE), "--no-meta"],
        capture_output=True,
        env={"PYTHONIOENCODING": "ascii", "PATH": "/usr/bin:/bin",
             "HOME": str(Path.home())},
        cwd=str(EXAMPLE.parent.parent),
    )
    assert proc.returncode == 0
    assert b"28" in proc.stdout and len(proc.stdout) > 1000


def test_encoding_failure_is_reported_not_swallowed(monkeypatch, capsys):
    """출력 자체가 불가능하면 조용한 성공이 아니라 rc 3 이어야 한다."""
    import pubgap.cli as climod

    class Dead:
        def write(self, *_a, **_k):
            raise UnicodeEncodeError("ascii", "x", 0, 1, "nope")

        def flush(self):
            pass

    monkeypatch.setattr(climod.sys, "stdout", Dead())
    with pytest.raises(climod.EncodingError):
        climod._print_safely("한국어")


# --------------------------------------------------------------------------- #
# [엣지 MEDIUM] 오류 메시지가 어느 파일인지 밝힌다 / XML 형식 오인
# --------------------------------------------------------------------------- #
def test_parse_error_names_the_offending_file_in_multi_file_mode(tmp_path, capsys):
    good = tmp_path / "good.csv"
    good.write_text("PMID,Title,Year,MeSH Terms\n1,T,2020,Sleep\n", encoding="utf-8")
    bad = tmp_path / "broken_export.csv"
    bad.write_text("완전히 형식 밖의 텍스트\n두 번째 줄\n", encoding="utf-8")
    rc = main(["--from-file", str(good), "--from-file", str(bad)])
    assert rc == 2
    assert "broken_export.csv" in capsys.readouterr().err


def test_non_pubmed_xml_is_named_as_a_format_problem(tmp_path, capsys):
    """PMC/JATS 전문 XML 을 '검색 결과 없음'(rc 1)으로 안내하던 버그."""
    jats = tmp_path / "pmc.xml"
    jats.write_text(
        '<?xml version="1.0"?><article><front><title>x</title></front></article>',
        encoding="utf-8",
    )
    rc = main(["--from-file", str(jats)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "PubMed 레코드를 찾지 못했습니다" in err and "article" in err


def test_empty_pubmed_result_set_is_still_a_normal_zero_result():
    """진짜 '결과 0편' XML 은 오류가 아니다."""
    assert parse_efetch_xml("<PubmedArticleSet></PubmedArticleSet>") == []


# --------------------------------------------------------------------------- #
# [엣지 MEDIUM] 제목 중복 제거가 순서·개수에 무관해야 한다
# --------------------------------------------------------------------------- #
def test_title_dedup_has_no_silent_cap():
    """같은 제목이 9종 이상 연도로 들어와도 이후 중복을 놓치면 안 된다."""
    same = "Annual meeting abstract on sleep and autonomic function"
    arts = []
    for year in range(2000, 2020):          # 20개 연도
        for i in range(3):                  # 각 3부씩(같은 제목)
            arts.append(art(f"p{year}-{i}", year, ["Sleep"], [], title=same))
    kept = dedup_articles(arts)
    assert len(kept) == 20                  # 연도마다 한 편


def test_title_dedup_is_order_independent():
    import random

    same = "Annual meeting abstract on sleep and autonomic function"
    base = []
    for year in range(2000, 2012):
        for i in range(3):
            base.append(art(f"p{year}-{i}", year, ["Sleep"], [], title=same))
    counts = set()
    for seed in range(5):
        shuffled = list(base)
        random.Random(seed).shuffle(shuffled)
        counts.add(len(dedup_articles(shuffled)))
    assert counts == {12}


def test_dedup_never_overwrites_a_populated_field():
    """merge 는 **빈 칸만** 채운다 — 값이 있으면 절대 덮어쓰지 않는다."""
    keep = art("1", 2019, ["Sleep"], [("Sleep", "physiology")],
               pub_types=["Review"], doi="10.1016/a.2019.1", journal="Sleep Med")
    other = art("1", 2001, ["Cancer"], [("Cancer", "therapy")],
                pub_types=["Randomized Controlled Trial"], doi="10.1016/b.2001.2",
                journal="Other J", title="Totally different title text here")
    merged, stats = dedup_articles_detailed([keep, other])
    assert len(merged) == 1
    m = merged[0]
    assert m.mesh == ["Sleep"] and m.qualifiers == [("Sleep", "physiology")]
    assert m.pub_types == ["Review"] and m.year == 2019
    assert m.doi == "10.1016/a.2019.1" and m.journal == "Sleep Med"
    assert stats.n_enriched == 0


# --------------------------------------------------------------------------- #
# [엣지 MEDIUM] 저자 키워드를 MeSH 로 오인하지 않는다
# --------------------------------------------------------------------------- #
def test_slashy_author_keywords_are_not_read_as_mesh_qualifiers():
    csv_text = (
        "Title,Year,Author Keywords\n"
        "A study of machine learning in clinics,2020,AI/machine learning; sleep\n"
        "Cost effectiveness of a sleep program,2020,cost/benefit analysis; sleep\n"
        "Input output modelling of breathing,2021,input/output; respiration\n"
        "Risk and benefit of hypnotic drugs,2021,risk/benefit; hypnotics\n"
    )
    arts = parse_csv_records(csv_text)
    assert all(not a.qualifiers for a in arts)
    assert all(not a.mesh for a in arts)                 # 저자 키워드는 주제가 아니다
    assert "AI/machine learning" in arts[0].keywords     # 값은 그대로 보존


def test_real_mesh_subheadings_are_still_parsed():
    csv_text = (
        "Title,Year,Keywords\n"
        "Melatonin trial in older adults today,2020,Melatonin/*therapeutic use; Sleep/drug effects\n"
    )
    a = parse_csv_records(csv_text)[0]
    assert a.mesh == ["Melatonin", "Sleep"]
    assert a.qualifiers == [("Melatonin", "therapeutic use"), ("Sleep", "drug effects")]


def test_keyword_corpus_reports_its_true_topic_source(capsys, tmp_path):
    f = tmp_path / "kw.csv"
    f.write_text(
        "Title,Year,Author Keywords\n"
        + "".join(
            f"Study number {i} on sleep and cognition,2020,"
            f"AI/machine learning; sleep; cognition\n"
            for i in range(12)
        ),
        encoding="utf-8",
    )
    rc = main(["--from-file", str(f), "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["topic_source"] == "keywords"        # 'mesh' 로 둔갑하면 안 된다


def test_subheading_list_is_sane():
    assert is_subheading("*Drug Therapy") and is_subheading("adverse effects")
    assert not is_subheading("machine learning")
    assert "therapeutic use" in NLM_SUBHEADINGS and len(NLM_SUBHEADINGS) >= 70


def test_nbib_prefers_the_doi_tagged_identifier_over_a_doi_shaped_pii():
    text = (
        "PMID- 5\nTI  - t\n"
        "AID - 10.1002/1097-0142(20000815)89:4<1::AID-CNCR1>3.0.CO;2-X [pii]\n"
        "AID - 10.1016/j.sleep.2019.01.001 [doi]\n"
    )
    assert parse_medline_nbib(text)[0].doi == "10.1016/j.sleep.2019.01.001"


# --------------------------------------------------------------------------- #
# [엣지 LOW] 오래된 연도 · 표 안전성
# --------------------------------------------------------------------------- #
def test_pre_1900_years_are_accepted_consistently_across_formats():
    """XML <Year> 는 1809 를 받고 CSV/NBIB 는 버리던 불일치."""
    csv_arts = parse_csv_records("Title,Year\nOld medicine study,1809\n")
    assert csv_arts[0].year == 1809
    nbib = parse_medline_nbib("PMID- 1\nTI  - t\nDP  - 1809 Jan\n")
    assert nbib[0].year == 1809


def test_markdown_cells_neutralise_injection_and_absurd_length():
    cell = _md_cell("[CLICK](javascript:alert(1))<script>x</script>")
    assert "<script>" not in cell and "&lt;script&gt;" in cell
    # 링크 문법이 성립하지 않도록 ']' 가 이스케이프돼야 한다.
    assert cell.count("](") == cell.count("\\](")
    assert len(_md_cell("A" * 5000)) <= 120


def test_markdown_injection_in_a_term_stays_inert():
    payload = "[CLICK ME](javascript:fetch('//evil'))"
    arts = [art(f"a{i}", 2020, [payload, "Sleep"], []) for i in range(6)]
    arts += [art(f"b{i}", 2020, ["Sleep"], []) for i in range(6)]
    arts += [art(f"c{i}", 2020, [payload], []) for i in range(6)]
    md = render_markdown(build_report(arts, "q", gap_min_expected=1.0))
    unescaped = md.replace("\\[", "[").replace("\\]", "]")
    assert payload in unescaped          # 값 자체는 보존(지우지 않는다)
    # 링크 문법이 성립하려면 ']' 바로 뒤에 '(' 가 와야 한다 — 모든 ']' 가 이스케이프돼
    # 있으면(\]) 마크다운 렌더러는 링크로 읽지 않는다.
    assert md.count("](") == md.count("\\](")


def test_bidi_override_is_stripped_from_terms():
    a = parse_csv_records("Title,Year,MeSH Terms\nT,2020,‮Sleep\n")[0]
    assert a.mesh == ["Sleep"]


# --------------------------------------------------------------------------- #
# [사용성] 리포트가 정직하고 실행 가능해야 한다
# --------------------------------------------------------------------------- #
def test_report_leads_with_a_conclusion_block(capsys):
    assert main(["--from-file", str(EXAMPLE), "--no-meta"]) == 0
    md = capsys.readouterr().out
    head = md.split("## 연도별 발행량")[0]
    assert "## 요약 (결론부터)" in head
    assert "주제 조합 1순위" in head and "달성한 최소 q" in head


def test_representative_papers_carry_titles_and_design_mix(capsys):
    assert main(["--from-file", str(EXAMPLE), "--no-meta"]) == 0
    md = capsys.readouterr().out
    assert "대표 논문(확인용" in md
    assert "Wearable HRV monitoring across the night" in md
    assert "설계 구성:" in md


def test_hierarchy_suspicion_is_flagged():
    arts = [art(f"a{i}", 2020, ["Sleep"], []) for i in range(12)]
    arts += [art(f"b{i}", 2020, ["Sleep, REM"], []) for i in range(12)]
    arts += [art(f"c{i}", 2020, ["Sleep", "Sleep, REM"], []) for i in range(1)]
    gaps = analyze.gap_pairs(arts, min_expected=1.0, max_lift=1.0)
    pair = [g for g in gaps if {g.term_a, g.term_b} == {"Sleep", "Sleep, REM"}]
    assert pair and pair[0].hierarchy_suspect is True
    assert analyze.looks_hierarchical("Heart Rate", "Heart Rate, Fetal")
    assert not analyze.looks_hierarchical("Sleep", "Heart Rate")


def test_gap_trend_column_shows_the_actual_rates(capsys):
    assert main(["--from-file", str(EXAMPLE), "--no-meta"]) == 0
    md = capsys.readouterr().out
    assert "→" in md and "/10→" in md


def test_evidence_section_reports_its_achieved_q(capsys):
    assert main(["--from-file", str(EXAMPLE), "--no-meta"]) == 0
    md = capsys.readouterr().out
    section = md.split("## 🧪")[1].split("## 🎯")[0]
    assert "달성한 최소 q" in section
    assert "개입연구가 상대적으로 적은 주제" in section


def test_angle_section_blames_the_right_thing_when_filters_empty_it(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--exclude-term", "Sleep",
               "--exclude-term", "Heart Rate", "--exclude-term", "Respiration",
               "--exclude-term", "Electroencephalography",
               "--exclude-term", "Autonomic Nervous System",
               "--exclude-term", "Sleep Initiation and Maintenance Disorders",
               "--exclude-term", "Melatonin", "--exclude-term", "Hypnotics and Sedatives",
               "--exclude-term", "Biofeedback, Psychology",
               "--exclude-term", "Monitoring, Physiologic",
               "--exclude-term", "Parasympathetic Nervous System",
               "--exclude-term", "Acoustic Stimulation", "--exclude-term", "Arousal",
               "--exclude-term", "Memory", "--exclude-term", "Relaxation",
               "--exclude-term", "Respiratory Rate",
               "--exclude-term", "Adrenergic beta-Antagonists", "--no-meta"])
    assert rc == 0
    out = capsys.readouterr()
    assert "지금 설정" in out.out or "부주제어" in out.out
    assert "분석할 주제가 하나도 남지 않았습니다" in out.err


def test_csv_section_for_a_disabled_analysis_warns(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--no-angles",
               "--format", "csv", "--csv-section", "angles"])
    assert rc == 0
    assert "헤더만 출력됩니다" in capsys.readouterr().err


def test_angle_csv_row_values_are_correct(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--format", "csv", "--csv-section", "angles"])
    assert rc == 0
    import csv as _csv

    rows = list(_csv.DictReader(io.StringIO(capsys.readouterr().out.lstrip("﻿"))))
    first = rows[0]
    assert first["term"] == "Heart Rate" and first["qualifier"] == "drug effects"
    assert first["plausible"] == "yes"
    assert int(first["observed"]) == 0
    assert float(first["lift_ci_low"]) <= float(first["lift"]) <= float(first["lift_ci_high"])
    assert float(first["expected"]) == pytest.approx(
        int(first["n_term"]) * int(first["n_qualifier"]) / 78, abs=0.02
    )
    for row in rows:
        assert float(row["lift_ci_low"]) <= float(row["lift"]) <= float(row["lift_ci_high"])


def test_gaps_csv_carries_the_lift_interval(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--format", "csv"])
    assert rc == 0
    import csv as _csv

    rows = list(_csv.DictReader(io.StringIO(capsys.readouterr().out.lstrip("﻿"))))
    assert rows
    for row in rows:
        assert float(row["lift_ci_low"]) <= float(row["lift"]) <= float(row["lift_ci_high"])
        assert float(row["lift_ci_low"]) >= 0.0


def test_angle_top_qualifiers_is_capped(capsys):
    with pytest.raises(SystemExit):
        main(["--from-file", str(EXAMPLE), "--angle-top-qualifiers", "1000000"])


def test_low_qualifier_coverage_is_warned():
    arts = [art(f"q{i}", 2020, ["Sleep"], [("Sleep", "physiology")]) for i in range(3)]
    arts += [art(f"n{i}", 2020, ["Sleep"], []) for i in range(17)]
    md = render_markdown(build_report(arts, "q"))
    assert "부주제어 커버리지가" in md and "낮습니다" in md


def test_title_key_boundary_is_pinned():
    from pubgap.records import MIN_TITLE_KEY_LEN

    assert MIN_TITLE_KEY_LEN == 20
    assert title_key("a" * (MIN_TITLE_KEY_LEN - 1)) == ""
    assert title_key("a" * MIN_TITLE_KEY_LEN) != ""


def test_angle_url_strips_quotes_like_the_pair_url():
    from pubgap.report import pubmed_angle_url

    url = pubmed_angle_url('Sleep" OR "Cancer', "therapy")
    # 따옴표를 남기면 검색식의 인용이 조기 종료돼 최상위 OR 가 만들어진다.
    assert url.count("%22") == 2                      # 우리가 붙인 바깥 따옴표뿐
    assert "Sleep+OR+Cancer%2Ftherapy" in url
