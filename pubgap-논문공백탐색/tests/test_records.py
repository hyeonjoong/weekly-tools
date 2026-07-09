"""records.py XML 파싱 — 관대한 필드 추출과 에러 처리."""

import pytest

from pubgap.records import Article, parse_efetch_xml

MINIMAL = """
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">111</PMID>
      <Article>
        <Journal>
          <JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue>
          <ISOAbbreviation>Sleep Med</ISOAbbreviation>
        </Journal>
        <ArticleTitle>Slow breathing and <i>HRV</i></ArticleTitle>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName UI="D1">Respiration</DescriptorName></MeshHeading>
        <MeshHeading><DescriptorName UI="D2">Heart Rate</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""

MEDLINE_DATE = """
<PubmedArticleSet>
  <PubmedArticle><MedlineCitation>
    <PMID>222</PMID>
    <Article><Journal><Title>Journal of Sleep Research</Title></Journal>
      <ArticleTitle>X</ArticleTitle>
      <Journal><JournalIssue><PubDate><MedlineDate>2018 Jan-Feb</MedlineDate></PubDate></JournalIssue></Journal>
    </Article>
  </MedlineCitation></PubmedArticle>
</PubmedArticleSet>
"""


def test_parse_minimal():
    arts = parse_efetch_xml(MINIMAL)
    assert len(arts) == 1
    a = arts[0]
    assert a.pmid == "111"
    assert a.year == 2020
    assert a.journal == "Sleep Med"
    # 인라인 <i> 태그 텍스트도 합쳐진다
    assert "HRV" in a.title
    assert a.mesh == ["Respiration", "Heart Rate"]
    assert a.has("Respiration") and not a.has("Sleep")


def test_parse_medline_date_and_missing_mesh():
    arts = parse_efetch_xml(MEDLINE_DATE)
    assert len(arts) == 1
    a = arts[0]
    assert a.year == 2018  # MedlineDate 에서 연도 추출
    assert a.mesh == []
    assert a.journal  # 제목/약어 중 하나로 채워짐


def test_empty_input_raises():
    with pytest.raises(ValueError):
        parse_efetch_xml("")
    with pytest.raises(ValueError):
        parse_efetch_xml("   ")


def test_broken_xml_raises_valueerror():
    with pytest.raises(ValueError):
        parse_efetch_xml("<PubmedArticleSet><PubmedArticle>")


def test_no_articles_returns_empty_list():
    assert parse_efetch_xml("<PubmedArticleSet></PubmedArticleSet>") == []


def test_year_missing_is_none():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <PMID>9</PMID><Article><ArticleTitle>y</ArticleTitle></Article>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    a = parse_efetch_xml(xml)[0]
    assert a.year is None
    assert a.journal == "(unknown journal)"
