"""CLI + report 통합 테스트 (번들 예시 XML, 완전 오프라인)."""

import json
from pathlib import Path

import pytest

from pubgap.cli import main
from pubgap.records import parse_efetch_xml
from pubgap.report import build_report, render_markdown

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sleep_pubmed.xml"


def test_example_file_exists():
    assert EXAMPLE.exists(), "번들 예시 XML 이 있어야 한다"


def load_example():
    return parse_efetch_xml(EXAMPLE.read_text(encoding="utf-8"))


def test_build_report_on_example():
    arts = load_example()
    assert len(arts) == 18
    rep = build_report(arts, "example")
    assert rep["n_articles"] == 18
    assert rep["year_span"] == [2015, 2024]
    # 상위 공백은 EEG × (Heart Rate / Respiration) — 설계상 lift 0
    gaps = rep["gaps"]
    assert gaps, "공백이 검출되어야 한다"
    top_terms = {gaps[0]["term_a"], gaps[0]["term_b"]}
    assert "Electroencephalography" in top_terms
    assert gaps[0]["observed"] == 0
    assert gaps[0]["lift"] == 0.0


def test_render_markdown_contains_sections():
    md = render_markdown(build_report(load_example(), "example"))
    assert "# 연구 동향·공백 리포트" in md
    assert "## 연도별 발행량" in md
    assert "덜 연구된 각도" in md
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
    assert data["n_articles"] == 18
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

    monkeypatch.setattr(fetch_mod, "fetch_articles_xml", boom)
    rc = main(["some query"])
    assert rc == 3
    assert "가져오지 못했습니다" in capsys.readouterr().err


def test_cli_network_path_is_isolated(monkeypatch, capsys):
    """query 경로에서 fetch 를 가짜로 주입해도 네트워크 없이 동작."""
    import pubgap.fetch as fetch_mod

    def fake_fetch(*a, **k):
        return EXAMPLE.read_text(encoding="utf-8")

    monkeypatch.setattr(fetch_mod, "fetch_articles_xml", fake_fetch)
    rc = main(["sleep breathing"])
    assert rc == 0
    assert "리포트" in capsys.readouterr().out
