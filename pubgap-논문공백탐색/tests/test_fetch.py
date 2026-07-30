"""fetch.py — 네트워크를 주입(injected http_get)해 오프라인으로 검증."""

from urllib.parse import parse_qs, urlparse

from pubgap.fetch import efetch_xml, esearch_pmids, fetch_articles_xml

ESEARCH_XML = b"""<eSearchResult><IdList>
  <Id>111</Id><Id>222</Id></IdList></eSearchResult>"""

EFETCH_XML = b"""<PubmedArticleSet>
  <PubmedArticle><MedlineCitation><PMID>111</PMID>
    <Article><ArticleTitle>A</ArticleTitle>
      <Journal><JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue>
        <ISOAbbreviation>Sleep</ISOAbbreviation></Journal></Article>
    <MeshHeadingList><MeshHeading><DescriptorName>Sleep</DescriptorName></MeshHeading></MeshHeadingList>
  </MedlineCitation></PubmedArticle>
  <PubmedArticle><MedlineCitation><PMID>222</PMID>
    <Article><ArticleTitle>B</ArticleTitle>
      <Journal><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
        <ISOAbbreviation>Chest</ISOAbbreviation></Journal></Article>
  </MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


class FakeHttp:
    """호출된 URL 을 기록하고, endpoint 에 따라 캔드 응답을 돌려준다."""

    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "esearch" in url:
            return ESEARCH_XML
        return EFETCH_XML


def test_esearch_extracts_pmids_and_builds_query():
    http = FakeHttp()
    pmids = esearch_pmids("slow breathing", years=5, email="x@y.z", http_get=http)
    assert pmids == ["111", "222"]
    qs = parse_qs(urlparse(http.urls[0]).query)
    assert qs["db"] == ["pubmed"]
    assert qs["term"] == ["slow breathing"]
    assert qs["tool"] == ["pubgap"]
    assert qs["email"] == ["x@y.z"]
    # years=5 → reldate=5*365+1
    assert qs["reldate"] == [str(5 * 365 + 1)]


def test_efetch_wraps_articles_into_single_root():
    http = FakeHttp()
    xml = efetch_xml(["111", "222"], http_get=http, sleep=0)
    # 하나의 루트로 감싸져 parse 가능해야 한다
    from pubgap.records import parse_efetch_xml

    arts = parse_efetch_xml(xml)
    assert [a.pmid for a in arts] == ["111", "222"]
    assert arts[0].mesh == ["Sleep"]


def test_efetch_empty_pmids_returns_empty_set():
    xml = efetch_xml([], http_get=FakeHttp())
    from pubgap.records import parse_efetch_xml

    assert parse_efetch_xml(xml) == []


def test_efetch_batches_multiple_requests():
    http = FakeHttp()
    efetch_xml([str(i) for i in range(5)], http_get=http, batch_size=2, sleep=0)
    # 5개를 batch_size=2 로 → 3번의 efetch 호출
    assert len(http.urls) == 3


def test_esearch_raises_on_error_element():
    import pytest

    def http(url):
        return b"<eSearchResult><ERROR>Invalid query</ERROR></eSearchResult>"

    with pytest.raises(RuntimeError, match="PubMed 오류"):
        esearch_pmids("bad", http_get=http)


def test_esearch_raises_on_html_error_page():
    import pytest

    def http(url):
        return b"<html><body>429 Too Many Requests</body></html>"

    # HTML 이지만 파싱은 됨 → root.tag != eSearchResult → RuntimeError
    with pytest.raises(RuntimeError):
        esearch_pmids("x", http_get=http)


def test_esearch_raises_on_broken_xml():
    import pytest

    def http(url):
        return b"<eSearchResult><IdList>"  # 잘린 XML

    with pytest.raises(RuntimeError):
        esearch_pmids("x", http_get=http)


def test_fetch_articles_xml_end_to_end():
    http = FakeHttp()
    xml = fetch_articles_xml("query", years=3, http_get=http)
    from pubgap.records import parse_efetch_xml

    arts = parse_efetch_xml(xml)
    assert len(arts) == 2
    # 구버전 호환 진입점은 최신순(recent) — esearch 1 + efetch 1
    assert sum("esearch" in u for u in http.urls) == 1
    assert sum("efetch" in u for u in http.urls) == 1


def test_fetch_articles_stratified_queries_each_year():
    """층화 표집은 연도마다 esearch 를 돌려 표본이 한 해로 붕괴하지 않게 한다."""
    from pubgap.fetch import fetch_articles

    http = FakeHttp()
    res = fetch_articles(
        "query", years=3, retmax=30, http_get=http, sample="stratified",
        this_year=2026, sleep=0,
    )
    esearches = [u for u in http.urls if "esearch" in u]
    assert len(esearches) >= 3
    for year in (2024, 2025, 2026):
        assert any(f"mindate=%s%%2F01%%2F01" % year in u for u in esearches), year
    assert res.n_fetched >= 1
    assert sum("efetch" in u for u in http.urls) == 1


def test_fetch_articles_recent_makes_one_esearch():
    from pubgap.fetch import fetch_articles

    http = FakeHttp()
    res = fetch_articles("query", years=3, http_get=http, sample="recent", sleep=0)
    assert sum("esearch" in u for u in http.urls) == 1
    assert res.total_available >= res.n_fetched
