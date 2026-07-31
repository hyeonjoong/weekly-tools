"""CSV/TSV 서지 내보내기 파서 — 실세계의 지저분한 파일을 견디는지.

임상 연구자가 실제로 손에 쥐는 CSV 는 Scopus/WoS/Covidence/엑셀 편집본이 섞여
헤더 이름·구분자·따옴표·연도 표기가 모두 제각각이다.
"""

from pathlib import Path

import pytest

from pubgap.records import (
    detect_format,
    load_articles,
    parse_csv_records,
    parse_records,
    sniff_delimiter,
)

EXAMPLE_CSV = Path(__file__).resolve().parents[1] / "examples" / "sleep_export.csv"

PUBMED_CSV = (
    'PMID,Title,Journal/Book,Publication Year,MeSH Terms,Publication Type\n'
    '111,A study,Sleep Med,2020,"*Sleep; Heart Rate/physiology",Randomized Controlled Trial\n'
    '112,B study,Chest,2019,Respiration,Review\n'
)

SCOPUS_TSV = (
    "Scopus export\tgenerated for a review\n"
    "\n"
    "Authors\tTitle\tYear\tSource title\tIndex Keywords\tDocument Type\tPubMed ID\n"
    "Kim H.\tA\t2020\tSleep Med\tsleep; respiration\tArticle\t111\n"
    "Lee S.\tB\t2021\tChest\tsleep; heart rate\tReview\t112\n"
)


def test_detect_csv_and_tsv():
    assert detect_format(PUBMED_CSV) == "csv"
    assert detect_format(SCOPUS_TSV, hint="tsv") == "csv"


def test_sniff_delimiter():
    assert sniff_delimiter(PUBMED_CSV) == ","
    assert sniff_delimiter(SCOPUS_TSV) == "\t"
    assert sniff_delimiter("a;b;c\n1;2;3\n") == ";"
    assert sniff_delimiter("a|b|c\n1|2|3\n") == "|"


def test_pubmed_style_csv_fields():
    a, b = parse_csv_records(PUBMED_CSV)
    assert a.pmid == "111"
    assert a.year == 2020
    assert a.journal == "Sleep Med"
    assert a.mesh == ["Sleep", "Heart Rate"]      # qualifier 는 벗기고 descriptor 만
    assert a.mesh_major == ["Sleep"]              # '*' 는 대표주제
    assert a.pub_types == ["Randomized Controlled Trial"]
    assert b.mesh == ["Respiration"]


def test_scopus_tsv_with_preamble_rows():
    arts = parse_records(SCOPUS_TSV, hint="tsv")
    assert [a.pmid for a in arts] == ["111", "112"]
    assert arts[0].journal == "Sleep Med"
    # Index Keywords 는 MeSH 표기가 아니므로 키워드로만 남는다.
    assert arts[0].mesh == [] and arts[0].keywords == ["sleep", "respiration"]
    assert arts[1].pub_types == ["Review"]


def test_messy_year_formats():
    text = (
        "Title,Year\n"
        "A,2019.0\n"          # 엑셀이 숫자로 바꿈
        "B,Mar-2018\n"        # 날짜로 인식됨
        "C,2020년 3월\n"       # 한국어 표기
        "D,\n"                # 비어 있음
        "E,n/a\n"             # 쓰레기 값
    )
    arts = parse_csv_records(text)
    assert [a.year for a in arts] == [2019, 2018, 2020, None, None]


def test_space_after_delimiter_is_unquoted():
    """'"a", "b"' 처럼 구분자 뒤 공백이 있어도 따옴표가 제대로 벗겨져야 한다."""
    text = '"Title", "Year", "Publication Type"\n"A", "2020", "Review"\n'
    a = parse_csv_records(text)[0]
    assert a.title == "A" and a.year == 2020
    assert a.pub_types == ["Review"]


def test_quoted_delimiter_and_newline_inside_cell():
    text = (
        'Title,Journal,Keywords\n'
        '"Sleep, breathing, and HRV","J ""Best"" Sleep","apnea; hypopnea"\n'
    )
    a = parse_csv_records(text)[0]
    assert a.title == "Sleep, breathing, and HRV"
    assert a.journal == 'J "Best" Sleep'
    assert a.keywords == ["apnea", "hypopnea"]


def test_mesh_terms_with_commas_are_not_split():
    """MeSH descriptor 자체가 쉼표를 포함한다 — 세미콜론/파이프로만 나눠야 한다."""
    text = 'Title,MeSH Terms\nA,"Aged, 80 and over; Sleep Initiation and Maintenance Disorders"\n'
    a = parse_csv_records(text)[0]
    assert a.mesh == ["Aged, 80 and over", "Sleep Initiation and Maintenance Disorders"]


def test_ragged_rows_and_blank_lines_survive():
    text = (
        "PMID,Title,Journal,Year,MeSH Terms\n"
        "1,A,J,2020,Sleep\n"
        "\n"
        "2,B\n"                       # 열이 모자란 행
        "3,C,J,2021,Sleep,extra,cols\n"  # 열이 남는 행
    )
    arts = parse_csv_records(text)
    assert [a.pmid for a in arts] == ["1", "2", "3"]
    assert arts[1].year is None and arts[1].journal == "(unknown journal)"
    assert arts[2].mesh == ["Sleep"]


def test_multiple_keyword_columns_are_merged():
    text = (
        "Title,Author Keywords,Index Keywords\n"
        "A,hrv; sleep,respiration\n"
    )
    a = parse_csv_records(text)[0]
    assert a.keywords == ["hrv", "sleep", "respiration"]


def test_unrecognisable_csv_raises_valueerror():
    with pytest.raises(ValueError):
        parse_csv_records("foo,bar,baz\n1,2,3\n")


def test_empty_csv_raises():
    with pytest.raises(ValueError):
        parse_csv_records("")
    with pytest.raises(ValueError):
        parse_csv_records("Title,Year\n")  # 헤더만 있고 데이터 없음


def test_bundled_csv_example_matches_xml_example():
    """번들 예시: 지저분한 CSV 내보내기가 원본 XML 과 같은 코퍼스를 만들어야 한다."""
    xml_arts = load_articles(EXAMPLE_CSV.parent / "sleep_pubmed.xml")
    csv_arts = load_articles(EXAMPLE_CSV)
    assert len(csv_arts) == len(xml_arts) == 28
    assert [a.pmid for a in csv_arts] == [a.pmid for a in xml_arts]
    assert [a.year for a in csv_arts] == [a.year for a in xml_arts]
    assert [a.mesh for a in csv_arts] == [a.mesh for a in xml_arts]
    # PublicationType 은 'Journal Article' 만 빠진 채 실려 있다(근거 tier 는 동일).
    from pubgap.analyze import evidence_tier

    assert [evidence_tier(a.pub_types) for a in csv_arts] == [
        evidence_tier(a.pub_types) for a in xml_arts
    ]
