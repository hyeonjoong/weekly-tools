"""CLI + report 통합 테스트 (번들 예시 XML, 완전 오프라인)."""

import json
from pathlib import Path

import pytest

from pubgap.cli import main
from pubgap.records import parse_efetch_xml
from pubgap.report import build_report, render_csv, render_markdown

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sleep_pubmed.xml"


def test_example_file_exists():
    assert EXAMPLE.exists(), "번들 예시 XML 이 있어야 한다"


def load_example():
    return parse_efetch_xml(EXAMPLE.read_text(encoding="utf-8"))


def test_build_report_on_example():
    arts = load_example()
    assert len(arts) == 28
    rep = build_report(arts, "example")
    assert rep["n_articles"] == 28
    assert rep["year_span"] == [2015, 2024]
    # 상위 공백은 EEG × (Heart Rate / Respiration) — 설계상 lift 0
    gaps = rep["gaps"]
    assert gaps, "공백이 검출되어야 한다"
    # 기본 정렬은 deficit(기대−관측 편수) 내림차순.
    assert [g["deficit"] for g in gaps] == sorted(
        (g["deficit"] for g in gaps), reverse=True
    )
    # lift 로 정렬하면 설계상 lift=0 인 EEG × (Heart Rate / Respiration) 이 맨 위.
    by_lift = build_report(arts, "example", gap_sort="lift")["gaps"]
    top_terms = {by_lift[0]["term_a"], by_lift[0]["term_b"]}
    assert "Electroencephalography" in top_terms
    assert by_lift[0]["observed"] == 0
    assert by_lift[0]["lift"] == 0.0


def test_render_markdown_contains_sections():
    md = render_markdown(build_report(load_example(), "example"))
    assert "# 연구 동향·공백 리포트" in md
    assert "## 연도별 발행량" in md
    assert "덜 연구된 주제 조합" in md
    assert "Electroencephalography" in md


def test_cli_from_file_ok(capsys):
    rc = main(["--from-file", str(EXAMPLE)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "연구 동향·공백 리포트" in out


def test_cli_json_output_valid(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_articles"] == 28
    assert "gaps" in data and "emerging" in data


def test_cli_out_file(tmp_path, capsys):
    out = tmp_path / "report.md"
    rc = main(["--from-file", str(EXAMPLE), "--out", str(out)])
    assert rc == 0
    assert out.exists() and "리포트" in out.read_text(encoding="utf-8")


def test_cli_requires_query_or_file(capsys):
    rc = main([])  # 검색어도 --from-file 도 없음
    assert rc == 2
    assert "검색어" in capsys.readouterr().err


def test_cli_missing_file(capsys):
    rc = main(["--from-file", "/nonexistent/nope.xml"])
    assert rc == 2
    assert "찾을 수 없" in capsys.readouterr().err


def test_cli_empty_result(tmp_path, capsys):
    empty = tmp_path / "empty.xml"
    empty.write_text("<PubmedArticleSet></PubmedArticleSet>", encoding="utf-8")
    rc = main(["--from-file", str(empty)])
    assert rc == 1
    assert "검색 결과가 없습니다" in capsys.readouterr().err


def test_single_year_corpus_suppresses_trends():
    from pubgap.records import Article
    from pubgap.report import build_report

    arts = [
        Article("1", 2022, "A", "t", ["Sleep", "Respiration"]),
        Article("2", 2022, "A", "t", ["Sleep", "Heart Rate"]),
    ]
    rep = build_report(arts, "x")
    # 전부 같은 연도 → 초기/최근 한쪽이 비어 추세는 신뢰 불가 → 생략
    assert rep["trend_reliable"] is False
    assert rep["emerging"] == [] and rep["declining"] == []


def test_cli_fetch_failure_exit_code_3(monkeypatch, capsys):
    """PubMed 조회 실패는 빈 결과(rc1)와 구분되는 rc3 이어야 한다."""
    import pubgap.fetch as fetch_mod

    def boom(*a, **k):
        raise RuntimeError("PubMed 오류: throttled")

    monkeypatch.setattr(fetch_mod, "fetch_articles", boom)
    rc = main(["some query"])
    assert rc == 3
    assert "가져오지 못했습니다" in capsys.readouterr().err


def test_cli_network_path_is_isolated(monkeypatch, capsys):
    """query 경로에서 fetch 를 가짜로 주입해도 네트워크 없이 동작."""
    import pubgap.fetch as fetch_mod

    def fake_fetch(*a, **k):
        return fetch_mod.FetchResult(
            xml_text=EXAMPLE.read_text(encoding="utf-8"),
            total_available=28,
            n_fetched=28,
        )

    monkeypatch.setattr(fetch_mod, "fetch_articles", fake_fetch)
    rc = main(["sleep breathing"])
    assert rc == 0
    assert "리포트" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 새 기능: Mann–Kendall / q-value / CSV / 새 CLI 옵션
# --------------------------------------------------------------------------- #
def test_report_includes_mann_kendall_and_qvalues():
    rep = build_report(load_example(), "example")
    assert "mann_kendall" in rep
    mk = rep["mann_kendall"]
    assert set(mk) == {"n", "tau", "s", "z", "p_value", "direction"}
    # 모든 gap 에 q_value 가 있고 0..1 범위
    for g in rep["gaps"]:
        assert 0.0 <= g["q_value"] <= 1.0
        assert g["q_value"] >= g["p_value"] - 1e-12


def test_markdown_shows_qvalue_column_and_cagr_line():
    md = render_markdown(build_report(load_example(), "example"))
    assert "q(FDR)" in md
    assert "Mann–Kendall" in md


def test_render_csv_structure():
    csv_text = render_csv(build_report(load_example(), "example"))
    lines = csv_text.lstrip("﻿").splitlines()
    assert lines[0] == (
        "term_a,term_b,observed,expected,deficit,lift,"
        "lift_ci_low,lift_ci_high,jaccard,cosine,npmi,"
        "count_a,count_b,p_value,q_value,"
        "observed_early,observed_recent,gap_trend,"
        "pmids_a,pmids_b,pmids_both,bridges,"
        "pubmed_url_mesh,pubmed_url_text,"
        "verdict,pubmed_observed,pubmed_lift"
    )
    # 예시에는 공백이 최소 1개 이상
    assert len(lines) >= 2
    # BOM 으로 시작(엑셀 한글)
    assert csv_text.startswith("﻿")


def test_cli_format_csv(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--format", "csv"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "term_a,term_b,observed" in out


def test_cli_format_json_equivalent_to_flag(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_articles"] == 28


def test_cli_gap_max_q_filters(capsys):
    # 기준선: 필터 없이는 공백이 존재한다.
    assert main(["--from-file", str(EXAMPLE), "--format", "csv"]) == 0
    before = len(capsys.readouterr().out.lstrip("﻿").splitlines())
    assert before >= 2

    # 아주 엄격한 q 임계 → 실제로 행이 사라져야 한다(헤더만 남음).
    assert main(["--from-file", str(EXAMPLE), "--gap-max-q", "0.0", "--format", "csv"]) == 0
    lines = capsys.readouterr().out.lstrip("﻿").splitlines()
    assert lines[0].startswith("term_a")  # 헤더는 항상 존재
    assert len(lines) == 1, "q<=0.0 을 만족하는 공백은 없어야 한다"

    # 느슨한 임계는 원래 개수를 유지한다.
    assert main(["--from-file", str(EXAMPLE), "--gap-max-q", "1.0", "--format", "csv"]) == 0
    assert len(capsys.readouterr().out.lstrip("﻿").splitlines()) == before


def test_cli_major_topics_only_on_corpus_without_major_topics(capsys):
    """예시 XML 에는 별표(major)가 없다 → 주제가 비고, 그 사실을 사용자에게 알려야 한다.

    회귀 배경: 예전 테스트는 `rc in (0, 1)` 로 두 결과를 모두 허용해, 어느 쪽이
    일어나든 통과하는 사실상의 스모크 테스트였다.
    """
    rc = main(["--from-file", str(EXAMPLE), "--major-topics-only", "--format", "json"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "대표(별표) MeSH 주제가 하나도 없어" in cap.err
    data = json.loads(cap.out)
    assert data["n_articles"] == 28      # 논문은 그대로
    assert data["top_mesh"] == []        # 주제만 비었다
    assert data["gaps"] == []


def test_cli_major_topics_only_restricts_topics(tmp_path, capsys):
    # major 가 실제로 있는 입력에서, --major-topics-only 는 대표주제만 남긴다.
    nbib = tmp_path / "m.nbib"
    nbib.write_text(
        "PMID- 1\nDP  - 2020\nTA  - J\nTI  - t\nMH  - *Sleep\nMH  - Respiration\n\n"
        "PMID- 2\nDP  - 2021\nTA  - J\nTI  - t\nMH  - *Sleep\nMH  - Heart Rate\n",
        encoding="utf-8",
    )
    rc = main(["--from-file", str(nbib), "--major-topics-only", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    topics = {t for t, _ in data["top_mesh"]}
    assert topics == {"Sleep"}  # Respiration/Heart Rate 는 대표가 아니라 제외


def test_cli_year_filter(capsys):
    rc = main(["--from-file", str(EXAMPLE), "--min-year", "2020", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["year_span"][0] >= 2020


def test_cli_reads_nbib_file(tmp_path, capsys):
    nbib = tmp_path / "x.nbib"
    nbib.write_text(
        "PMID- 1\nDP  - 2020\nTA  - J\nTI  - t\nMH  - Sleep\nMH  - Respiration\n\n"
        "PMID- 2\nDP  - 2021\nTA  - J\nTI  - t\nMH  - Sleep\nMH  - Heart Rate\n",
        encoding="utf-8",
    )
    rc = main(["--from-file", str(nbib), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_articles"] == 2


def test_cli_reads_gzip_file(tmp_path, capsys):
    import gzip

    gz = tmp_path / "s.xml.gz"
    gz.write_bytes(gzip.compress(EXAMPLE.read_bytes()))
    rc = main(["--from-file", str(gz), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["n_articles"] == 28


# --------------------------------------------------------------------------- #
# 라운드 2 수정/기능: 유효 JSON / 체크태그 / 가교 / 음수 플래그 / CAGR 게이팅
# --------------------------------------------------------------------------- #
def test_json_output_is_strict_valid_when_ratio_infinite(tmp_path, capsys):
    # 단일 연도 코퍼스 → early_total=0 → ratio=inf. 표준 JSON 이어야 한다.
    f = tmp_path / "single.nbib"
    f.write_text("PMID- 1\nDP  - 2020\nTA  - J\nTI  - t\nMH  - Sleep\nMH  - Respiration\n",
                 encoding="utf-8")
    rc = main(["--from-file", str(f), "--json"])
    assert rc == 0
    raw = capsys.readouterr().out
    # 엄격 파서: Infinity/NaN 를 상수로 허용하지 않음
    data = json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert data["growth"]["ratio"] is None  # inf → null


def test_cli_check_tags_filtered_by_default(tmp_path, capsys):
    f = tmp_path / "ct.nbib"
    f.write_text(
        "PMID- 1\nDP  - 2020\nTA  - J\nTI  - t\nMH  - Humans\nMH  - Male\nMH  - Sleep\n\n"
        "PMID- 2\nDP  - 2021\nTA  - J\nTI  - t\nMH  - Humans\nMH  - Female\nMH  - Sleep\n",
        encoding="utf-8",
    )
    rc = main(["--from-file", str(f), "--json"])
    assert rc == 0
    topics = {t for t, _ in json.loads(capsys.readouterr().out)["top_mesh"]}
    assert topics == {"Sleep"}  # Humans/Male/Female 제외


def test_cli_include_check_tags_opt_out(tmp_path, capsys):
    f = tmp_path / "ct.nbib"
    f.write_text(
        "PMID- 1\nDP  - 2020\nTA  - J\nTI  - t\nMH  - Humans\nMH  - Sleep\n",
        encoding="utf-8",
    )
    rc = main(["--from-file", str(f), "--include-check-tags", "--json"])
    assert rc == 0
    topics = {t for t, _ in json.loads(capsys.readouterr().out)["top_mesh"]}
    assert "Humans" in topics


def test_cli_negative_count_flag_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["--from-file", str(EXAMPLE), "--top-mesh", "-2"])


def test_report_gap_has_bridges_and_pmids_in_json():
    from pubgap.records import Article

    arts = []
    for i in range(8):
        arts.append(Article(f"a{i}", 2020, "J", "t", ["Sleep", "Autonomic"]))
    for i in range(8):
        arts.append(Article(f"b{i}", 2020, "J", "t", ["Inflammation", "Autonomic"]))
    for i in range(3):
        arts.append(Article(f"c{i}", 2020, "J", "t", ["Sleep"]))
    for i in range(3):
        arts.append(Article(f"d{i}", 2020, "J", "t", ["Inflammation"]))
    rep = build_report(arts, "x", gap_min_expected=1.0, gap_max_lift=1.0)
    g = next(x for x in rep["gaps"] if {x["term_a"], x["term_b"]} == {"Sleep", "Inflammation"})
    assert g["bridges"][0][0] == "Autonomic"
    assert g["pmids_a"] and g["pmids_b"]


def test_markdown_shows_bridge_and_pmids():
    from pubgap.records import Article

    arts = []
    for i in range(8):
        arts.append(Article(f"a{i}", 2020, "J", "t", ["Sleep", "Autonomic"]))
    for i in range(8):
        arts.append(Article(f"b{i}", 2020, "J", "t", ["Inflammation", "Autonomic"]))
    # 가교 후보는 '거의 모든 논문에 붙은' 주제여선 안 되므로(정보량 0), Autonomic 이
    # 없는 논문을 섞어 유병률을 임계 아래로 내린다.
    for i in range(6):
        arts.append(Article(f"c{i}", 2020, "J", "t", ["Sleep"]))
    for i in range(6):
        arts.append(Article(f"d{i}", 2020, "J", "t", ["Inflammation"]))
    md = render_markdown(build_report(arts, "x", gap_min_expected=1.0, gap_max_lift=1.0))
    assert "가교(Swanson ABC)" in md
    assert "대표 논문(확인용" in md
    # 번호만이 아니라 **제목**이 함께 나와야 확인이 실제로 가능하다.
    assert "`a0`" in md and "Sleep — " in md


def test_two_year_corpus_suppresses_cagr_line(tmp_path, capsys):
    f = tmp_path / "twoyr.nbib"
    f.write_text(
        "PMID- 1\nDP  - 2019\nTA  - J\nTI  - t\nMH  - Sleep\n\n"
        "PMID- 2\nDP  - 2020\nTA  - J\nTI  - t\nMH  - Sleep\n",
        encoding="utf-8",
    )
    rc = main(["--from-file", str(f)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Theil–Sen" not in out  # 2개 연도(연도<3) → 추세 기울기 표시 안 함
