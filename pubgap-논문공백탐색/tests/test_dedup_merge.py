"""여러 출처 합치기 — PMID→DOI→제목+연도 중복 제거와 필드 보강."""

import json

import pytest

from pathlib import Path as _Path

from pubgap.cli import main
from pubgap.records import (
    Article,
    dedup_articles,
    dedup_articles_detailed,
    normalize_doi,
    title_key,
)

_ROOT = _Path(__file__).resolve().parents[1]
# cwd 와 무관하게 돌도록 절대경로로 잡는다 — 상대경로면 다른 폴더에서
# 돌릴 때 rc 2(파일 없음)로 끝나면서도 통과하는 테스트가 생긴다.
_EX_XML = str(_ROOT / "examples" / "sleep_pubmed.xml")
_EX_CSV = str(_ROOT / "examples" / "sleep_export.csv")

LONG_TITLE = "Slow breathing and heart rate variability in healthy adults"


def art(pmid="?", doi="", title=LONG_TITLE, year=2020, mesh=(), pub_types=(), **kw):
    return Article(
        pmid=pmid, year=year, journal=kw.get("journal", "J"), title=title,
        mesh=list(mesh), pub_types=list(pub_types), doi=doi,
        keywords=list(kw.get("keywords", ())),
        qualifiers=[tuple(q) for q in kw.get("qualifiers", ())],
    )


def test_normalize_doi_handles_real_world_shapes():
    for raw in (
        "10.1016/J.SLEEP.2020.01.001",
        "https://doi.org/10.1016/j.sleep.2020.01.001",
        "doi:10.1016/j.sleep.2020.01.001",
        "DOI: 10.1016/j.sleep.2020.01.001.",
        "  10.1016/j.sleep.2020.01.001  ",
    ):
        assert normalize_doi(raw) == "10.1016/j.sleep.2020.01.001"
    assert normalize_doi("") == ""
    assert normalize_doi("not-a-doi") == ""
    assert normalize_doi(None) == ""


def test_title_key_normalises_punctuation_and_rejects_short_titles():
    assert title_key("Slow Breathing and HRV: A Randomized Trial.") == title_key(
        "slow breathing and hrv — a randomized trial"
    )
    assert title_key("Editorial") == ""      # 너무 짧으면 키로 쓰지 않는다
    assert title_key("(no title)") == ""
    assert title_key(None) == ""


def test_pmid_dedup_keeps_first_and_counts():
    arts = [art(pmid="1"), art(pmid="1"),
            art(pmid="2", title="An entirely different study title here")]
    kept, stats = dedup_articles_detailed(arts)
    assert [a.pmid for a in kept] == ["1", "2"]
    assert (stats.n_input, stats.n_unique, stats.by_pmid) == (3, 2, 1)
    assert stats.n_removed == 1


def test_doi_dedup_merges_across_sources():
    xml_rec = art(pmid="123", doi="10.1016/x.2020.1", mesh=["Sleep"], pub_types=["Randomized Controlled Trial"])
    csv_rec = art(pmid="?", doi="https://doi.org/10.1016/X.2020.1", title="Different title entirely here")
    kept, stats = dedup_articles_detailed([xml_rec, csv_rec])
    assert len(kept) == 1 and stats.by_doi == 1
    assert kept[0].pmid == "123"


def test_doi_prefixed_pmid_still_matches_a_real_doi_record():
    a = art(pmid="doi:10.1016/x.2020.1", title="A perfectly distinct title one")
    b = art(pmid="?", doi="10.1016/X.2020.1", title="A perfectly distinct title two")
    kept, stats = dedup_articles_detailed([a, b])
    assert len(kept) == 1 and stats.by_doi == 1


def test_title_year_dedup_only_when_years_are_compatible():
    a = art(pmid="1", year=2020)
    b = art(pmid="2", year=2020)          # 같은 제목·같은 해 → 중복
    c = art(pmid="3", year=2014)          # 같은 제목·다른 해 → 별개(초록 vs 본논문)
    d = art(pmid="4", year=None)          # 연도 미상 → 양립 가능
    kept, stats = dedup_articles_detailed([a, b, c, d])
    assert [x.pmid for x in kept] == ["1", "3"]
    assert stats.by_title == 2


def test_fuzzy_title_dedup_can_be_disabled():
    arts = [art(pmid="1"), art(pmid="2")]
    kept, stats = dedup_articles_detailed(arts, by_title=False)
    assert len(kept) == 2 and stats.by_title == 0


def test_merge_fills_empty_fields_but_never_overwrites():
    poor = art(pmid="1", title=LONG_TITLE, year=None, mesh=[], pub_types=[], journal="(unknown journal)")
    rich = art(pmid="1", year=2019, mesh=["Sleep"], pub_types=["Review"],
               journal="Sleep Med", doi="10.9999/z", qualifiers=[("Sleep", "physiology")],
               keywords=["insomnia"])
    kept, stats = dedup_articles_detailed([poor, rich])
    merged = kept[0]
    assert merged.mesh == ["Sleep"] and merged.pub_types == ["Review"]
    assert merged.year == 2019 and merged.journal == "Sleep Med"
    assert merged.doi == "10.9999/z" and merged.qualifiers == [("Sleep", "physiology")]
    assert merged.keywords == ["insomnia"]
    assert stats.n_enriched == 1

    # 반대 순서: 이미 값이 있으면 덮어쓰지 않는다.
    kept2, _ = dedup_articles_detailed([rich, poor])
    assert kept2[0].mesh == ["Sleep"] and kept2[0].year == 2019


def test_merge_never_mutates_the_input_objects():
    poor = art(pmid="1", year=None, mesh=[])
    rich = art(pmid="1", year=2019, mesh=["Sleep"])
    before = (poor.year, list(poor.mesh))
    dedup_articles_detailed([poor, rich])
    assert (poor.year, poor.mesh) == before


def test_unknown_ids_are_never_collapsed_together():
    a = art(pmid="?", title="Short one")     # 제목 키가 안 만들어짐
    b = art(pmid="?", title="Short two")
    assert len(dedup_articles([a, b])) == 2
    # 제목이 '(no title)' 인 레코드들도 서로 합쳐지면 안 된다.
    c = art(pmid="?", title="(no title)")
    d = art(pmid="?", title="(no title)")
    assert len(dedup_articles([c, d])) == 2


def test_dedup_is_linear_on_pathological_same_title_input():
    """제목이 모두 같고 연도가 전부 다른 입력에서도 이차 폭발이 없어야 한다."""
    import time

    arts = [art(pmid=f"p{i}", year=1900 + (i % 300), title=LONG_TITLE) for i in range(4000)]
    start = time.perf_counter()
    kept = dedup_articles(arts)
    assert time.perf_counter() - start < 5.0
    assert 0 < len(kept) <= 4000


def test_cli_merges_multiple_files_and_reports_dedup(capsys, tmp_path):
    rc = main([
        "--from-file", _EX_XML,
        "--from-file", _EX_CSV,
        "--format", "json",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_articles"] == 28                 # 같은 논문 24편이 두 번 들어왔다
    dd = data["dedup"]
    assert dd["n_input"] == 56 and dd["n_unique"] == 28 and dd["by_pmid"] == 28
    assert data["meta"]["input"]["sources"][1]["format"] == "csv"
    assert len(data["meta"]["input"]["sources"]) == 2


def test_cli_merge_markdown_mentions_the_merge(capsys):
    rc = main([
        "--from-file", _EX_XML,
        "--from-file", _EX_CSV,
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "중복 28건을 제거" in out and "PMID 28편" in out


def test_cli_merge_recovers_mesh_from_the_richer_source(tmp_path, capsys):
    """PMID 없는 CSV(주제어 없음) + DOI 가 같은 XML → 한 편으로 합쳐지고 주제를 얻는다."""
    xml = tmp_path / "a.xml"
    xml.write_text(
        """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>77</PMID>
        <Article><Journal><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
        <ISOAbbreviation>J</ISOAbbreviation></Journal>
        <ArticleTitle>Melatonin for chronic insomnia in older adults</ArticleTitle>
        <ELocationID EIdType="doi">10.1016/aa.2021.7</ELocationID></Article>
        <MeshHeadingList><MeshHeading>
          <DescriptorName>Melatonin</DescriptorName>
          <QualifierName>therapeutic use</QualifierName>
        </MeshHeading></MeshHeadingList>
        </MedlineCitation></PubmedArticle></PubmedArticleSet>""",
        encoding="utf-8",
    )
    csv_path = tmp_path / "b.csv"
    csv_path.write_text(
        "Title,Year,DOI,Document Type\n"
        "Melatonin for chronic insomnia in older adults,2021,10.1016/AA.2021.7,Review\n",
        encoding="utf-8",
    )
    rc = main(["--from-file", str(xml), "--from-file", str(csv_path), "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_articles"] == 1
    assert data["dedup"]["by_doi"] == 1
    assert data["top_mesh"] == [["Melatonin", 1]]


def test_cli_no_fuzzy_dedup_keeps_title_duplicates(tmp_path, capsys):
    body = (
        "TY  - JOUR\nTI  - {t}\nPY  - 2020\nKW  - Sleep\nER  - \n"
    )
    f1 = tmp_path / "a.ris"
    f1.write_text(body.format(t=LONG_TITLE), encoding="utf-8")
    f2 = tmp_path / "b.ris"
    f2.write_text(body.format(t=LONG_TITLE.upper()), encoding="utf-8")

    rc = main(["--from-file", str(f1), "--from-file", str(f2), "--format", "json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["n_articles"] == 1

    rc = main(["--from-file", str(f1), "--from-file", str(f2),
               "--no-fuzzy-dedup", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_articles"] == 2
    assert data["meta"]["params"]["fuzzy_dedup"] is False


def test_cli_missing_file_names_the_offending_path(tmp_path, capsys):
    rc = main(["--from-file", _EX_XML,
               "--from-file", str(tmp_path / "nope.xml")])
    assert rc == 2
    assert "nope.xml" in capsys.readouterr().err


def test_cli_directory_input_names_the_offending_path(tmp_path, capsys):
    rc = main(["--from-file", str(tmp_path)])
    assert rc == 2
    assert str(tmp_path) in capsys.readouterr().err
