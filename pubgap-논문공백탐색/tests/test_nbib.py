"""MEDLINE/NBIB 파서와 견고한 파일 로딩(gzip/인코딩/포맷 판별/중복 제거)."""

import gzip
from pathlib import Path

import pytest

from pubgap.records import (
    Article,
    decode_bytes,
    dedup_articles,
    detect_format,
    load_articles,
    parse_medline_nbib,
    parse_records,
)

NBIB = """PMID- 40000001
DP  - 2020 Jan
TA  - Sleep Med
TI  - Slow breathing and heart rate
      variability in adults
MH  - *Respiration
MH  - Heart Rate/*physiology
MH  - Autonomic Nervous System
OT  - vagal tone
OT  - paced breathing

PMID- 40000002
DP  - 2021 Mar-Apr
TA  - J Sleep Res
TI  - EEG during sleep
MH  - Electroencephalography
MH  - *Sleep
"""


def test_nbib_basic_fields():
    arts = parse_medline_nbib(NBIB)
    assert len(arts) == 2
    a = arts[0]
    assert a.pmid == "40000001"
    assert a.year == 2020
    assert a.journal == "Sleep Med"
    # 6칸 이어짐 병합
    assert "variability in adults" in a.title


def test_nbib_mesh_and_major():
    a = parse_medline_nbib(NBIB)[0]
    assert a.mesh == ["Respiration", "Heart Rate", "Autonomic Nervous System"]
    # *Respiration(descriptor 별표), Heart Rate/*physiology(qualifier 별표) → 둘 다 대표
    assert a.mesh_major == ["Respiration", "Heart Rate"]
    assert a.keywords == ["vagal tone", "paced breathing"]


def test_nbib_second_record_major_descriptor():
    b = parse_medline_nbib(NBIB)[1]
    assert b.mesh == ["Electroencephalography", "Sleep"]
    assert b.mesh_major == ["Sleep"]  # *Sleep 만 대표
    assert b.year == 2021


def test_nbib_year_fallback_tags():
    txt = "PMID- 1\nEDAT- 2019/05/01\nTI  - x\nMH  - Sleep\n"
    a = parse_medline_nbib(txt)[0]
    assert a.year == 2019


def test_nbib_missing_fields_defaults():
    txt = "PMID- 7\nTI  - only title\n"
    a = parse_medline_nbib(txt)[0]
    assert a.journal == "(unknown journal)"
    assert a.mesh == [] and a.keywords == []
    assert a.year is None


def test_nbib_empty_raises():
    with pytest.raises(ValueError):
        parse_medline_nbib("")
    with pytest.raises(ValueError):
        parse_medline_nbib("   \n  \n")


def test_nbib_jt_used_when_no_ta():
    txt = "PMID- 9\nJT  - Journal of Sleep Research\nTI  - t\nMH  - Sleep\n"
    a = parse_medline_nbib(txt)[0]
    assert a.journal == "Journal of Sleep Research"


# --------------------------------------------------------------------------- #
# 포맷 판별 / 디코드 / 로딩
# --------------------------------------------------------------------------- #
def test_detect_format():
    assert detect_format("<PubmedArticleSet></PubmedArticleSet>") == "xml"
    assert detect_format("﻿<xml>") == "xml"
    assert detect_format("  \n  <root/>") == "xml"
    assert detect_format("PMID- 1\nTI  - x\n") == "nbib"


def test_parse_records_dispatches():
    xml = "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID>" \
          "<Article><ArticleTitle>t</ArticleTitle></Article></MedlineCitation>" \
          "</PubmedArticle></PubmedArticleSet>"
    assert parse_records(xml)[0].pmid == "1"
    assert parse_records("PMID- 2\nTI  - y\n")[0].pmid == "2"


def test_parse_records_empty_raises():
    with pytest.raises(ValueError):
        parse_records("")


def test_decode_gzip_bytes():
    raw = gzip.compress("PMID- 1\nTI  - x\n".encode("utf-8"))
    assert "PMID- 1" in decode_bytes(raw)


def test_decode_latin1_fallback():
    # UTF-8 로 못 읽는 latin-1 바이트(0xE9 = é)도 죽지 않고 디코드.
    raw = "PMID- 1\nTI  - caf\xe9\n".encode("latin-1")
    text = decode_bytes(raw)
    assert "caf" in text  # 예외 없이 통과


def test_decode_utf8_bom_stripped():
    raw = "﻿PMID- 1\nTI  - x\n".encode("utf-8")
    assert decode_bytes(raw).startswith("PMID- 1")


def test_dedup_articles_by_pmid():
    arts = [
        Article("1", 2020, "A", "t", ["Sleep"]),
        Article("1", 2020, "A", "dup", ["Sleep"]),
        Article("2", 2020, "A", "t", ["EEG"]),
        Article("?", 2020, "A", "unknown1", []),
        Article("?", 2020, "A", "unknown2", []),
    ]
    out = dedup_articles(arts)
    # PMID 1 중복 제거, ?(미상)은 각각 유지
    assert [a.pmid for a in out] == ["1", "2", "?", "?"]
    assert out[0].title == "t"  # 첫 등장 유지


def test_load_articles_gzip_xml(tmp_path):
    xml = Path(__file__).resolve().parent.parent / "examples" / "sleep_pubmed.xml"
    gz = tmp_path / "s.xml.gz"
    gz.write_bytes(gzip.compress(xml.read_bytes()))
    arts = load_articles(str(gz))
    assert len(arts) == 18


def test_load_articles_nbib(tmp_path):
    f = tmp_path / "x.nbib"
    f.write_text(NBIB, encoding="utf-8")
    arts = load_articles(str(f))
    assert [a.pmid for a in arts] == ["40000001", "40000002"]


def test_load_articles_dedups(tmp_path):
    dup = NBIB + "\nPMID- 40000001\nTI  - dup\nMH  - Sleep\n"
    f = tmp_path / "d.nbib"
    f.write_text(dup, encoding="utf-8")
    arts = load_articles(str(f))
    assert [a.pmid for a in arts] == ["40000001", "40000002"]


def test_nbib_repeated_descriptor_major_promotion():
    # 같은 descriptor 가 두 번 나오고 나중 것이 major 면, 중복 추가 없이 대표로 승격.
    txt = (
        "PMID- 1\nDP  - 2020\nTI  - t\n"
        "MH  - Heart Rate/physiology\n"
        "MH  - Heart Rate/*genetics\n"
    )
    a = parse_medline_nbib(txt)[0]
    assert a.mesh == ["Heart Rate"]         # 중복 추가 안 됨
    assert a.mesh_major == ["Heart Rate"]   # 두 번째 등장(*genetics)으로 대표 승격


def test_nbib_tab_separated_tags():
    # 표준은 'TAG - value' 지만 탭 구분 변종도 데이터 손실 없이 읽는다.
    txt = "PMID-\t9\nTI  -\ttab title\nMH  -\t*Sleep\n"
    a = parse_medline_nbib(txt)[0]
    assert a.pmid == "9"
    assert a.title == "tab title"
    assert a.mesh == ["Sleep"] and a.mesh_major == ["Sleep"]


def test_nbib_crlf_line_endings():
    txt = "PMID- 1\r\nDP  - 2020\r\nTI  - t\r\nMH  - Sleep\r\n"
    a = parse_medline_nbib(txt)[0]
    assert a.pmid == "1" and a.mesh == ["Sleep"]


def test_xml_entity_declaration_blocked():
    # billion-laughs 류 내부 엔티티 선언은 파싱 전에 거부.
    bad = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY lol "aa">'
        '<!ENTITY lol2 "&lol;&lol;">]><PubmedArticleSet>&lol2;</PubmedArticleSet>'
    )
    with pytest.raises(ValueError, match="엔티티"):
        parse_records(bad)


def test_xml_normal_doctype_still_parses():
    # 정상 PubMed efetch 는 외부 DTD DOCTYPE 을 갖는다 — 막으면 안 된다.
    ok = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD" "http://x/x.dtd">'
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID>"
        "<Article><ArticleTitle>t</ArticleTitle></Article></MedlineCitation>"
        "</PubmedArticle></PubmedArticleSet>"
    )
    arts = parse_records(ok)
    assert len(arts) == 1 and arts[0].pmid == "1"


def test_decode_bytes_replacement_fallback():
    # utf-8/latin-1 로도 못 읽는 바이트는 replace 로 죽지 않고 통과.
    # (latin-1 은 모든 바이트를 매핑하므로, 실제로는 latin-1 경로가 먼저 성공한다 —
    #  여기서는 함수가 예외 없이 문자열을 돌려주는 것만 보증.)
    raw = bytes([0xFF, 0xFE, 0x00, 0x41])
    text = decode_bytes(raw)
    assert isinstance(text, str)
