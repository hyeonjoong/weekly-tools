# -*- coding: utf-8 -*-
"""한글 자모 분해·정규화."""

import unicodedata

import pytest

from jamoscore import hangul as H


def test_roundtrip_all_11172_syllables():
    """완성형 11,172자 전수: 분해 → 재조합이 원문과 100% 일치해야 한다."""
    bad = []
    for i in range(H.SCOUNT):
        ch = chr(H.SBASE + i)
        parts = H.decompose(ch)
        assert parts is not None
        if H.compose(*parts) != ch:
            bad.append(ch)
    assert bad == []


def test_jamo_tables_match_unicode():
    assert len(H.CHOSEONG) == 19
    assert len(H.JUNGSEONG) == 21
    assert len(H.JONGSEONG) == 28
    assert H.SCOUNT == 11172
    assert H.NCOUNT == 588


@pytest.mark.parametrize("ch,expected", [
    ("가", ("ㄱ", "ㅏ", "")),
    ("힣", ("ㅎ", "ㅣ", "ㅎ")),
    ("앞", ("ㅇ", "ㅏ", "ㅍ")),
    ("닭", ("ㄷ", "ㅏ", "ㄺ")),
    ("뻥", ("ㅃ", "ㅓ", "ㅇ")),
    ("쌀", ("ㅆ", "ㅏ", "ㄹ")),
    ("김", ("ㄱ", "ㅣ", "ㅁ")),
    ("귄", ("ㄱ", "ㅟ", "ㄴ")),
    ("톱", ("ㅌ", "ㅗ", "ㅂ")),
    ("찜", ("ㅉ", "ㅣ", "ㅁ")),
    ("넷", ("ㄴ", "ㅔ", "ㅅ")),
    ("맵", ("ㅁ", "ㅐ", "ㅂ")),
])
def test_decompose_known(ch, expected):
    assert H.decompose(ch) == expected


@pytest.mark.parametrize("bad", ["ㄱ", "A", "1", "漢", "", "가나", " ", "ｱ"])
def test_decompose_rejects_non_syllable(bad):
    assert H.decompose(bad) is None


@pytest.mark.parametrize("cho,jung,jong,expected", [
    ("ㄱ", "ㅏ", "", "가"),
    ("ㅎ", "ㅣ", "ㅎ", "힣"),
    ("ㅂ", "ㅏ", "ㅂ", "밥"),
    ("ㅉ", "ㅣ", "ㅁ", "찜"),
])
def test_compose_known(cho, jung, jong, expected):
    assert H.compose(cho, jung, jong) == expected


@pytest.mark.parametrize("args", [("X", "ㅏ", ""), ("ㄱ", "X", ""),
                                 ("ㄱ", "ㅏ", "X"), ("", "ㅏ", "")])
def test_compose_rejects_bad_jamo(args):
    assert H.compose(*args) is None


def test_normalize_nfd_to_nfc():
    nfd = unicodedata.normalize("NFD", "한글")
    assert nfd != "한글"
    assert H.normalize(nfd) == "한글"


def test_normalize_nfd_survives_decomposition():
    """macOS 에서 붙여넣은 자모 분리형도 목표음소 비교가 되어야 한다."""
    nfd = unicodedata.normalize("NFD", "후드")
    assert H.jamo_at(nfd, 1, "중성") == "ㅜ"


def test_normalize_strips_zero_width_and_control():
    assert H.normalize("아​라") == "아라"
    assert H.normalize("아라") == "아라"


def test_normalize_fullwidth_to_halfwidth():
    assert H.normalize("ＡＢ１") == "AB1"
    assert H.normalize("아　라") == "아 라"


def test_normalize_none_and_whitespace():
    assert H.normalize(None) == ""
    assert H.normalize("  아라  ") == "아라"
    assert H.normalize(12) == "12"


def test_normalize_keeps_compatibility_jamo_distinct():
    """NFKC 였다면 호환 자모가 바뀌어 목표음소 비교가 왜곡된다."""
    assert H.normalize("ㄱ") == "ㄱ"


@pytest.mark.parametrize("text,idx,pos,expected", [
    ("아라", 2, "초성", "ㄹ"),
    ("아라", 1, "초성", "ㅇ"),
    ("후드", 1, "중성", "ㅜ"),
    ("해드", 1, "중성", "ㅐ"),
    ("가", 1, "종성", H.NO_JONG),
    ("김", 1, "종성", "ㅁ"),
    ("닭", 1, "종성", "ㄺ"),
])
def test_jamo_at(text, idx, pos, expected):
    assert H.jamo_at(text, idx, pos) == expected


@pytest.mark.parametrize("text,idx", [("아", 2), ("", 1), ("A", 1), ("아라", 3),
                                      ("아라", 0), ("아라", -1)])
def test_jamo_at_returns_none_when_undecidable(text, idx):
    assert H.jamo_at(text, idx, "초성") is None


def test_jamo_at_rejects_bad_position():
    with pytest.raises(ValueError):
        H.jamo_at("가", 1, "받침")


def test_no_jong_symbol_is_visible():
    """빈 문자열은 CSV 에서 사라진다 — '받침 없음'에도 보이는 기호가 필요하다."""
    assert H.NO_JONG and H.NO_JONG.strip()


def test_hangul_syllables_filters():
    assert H.hangul_syllables("아a라1") == ["아", "라"]
    assert H.hangul_syllables("") == []


def test_syllables_keeps_non_hangul():
    assert H.syllables("아a라") == ["아", "a", "라"]


def test_is_syllable_boundaries():
    assert H.is_syllable(chr(0xAC00))
    assert H.is_syllable(chr(0xD7A3))
    assert not H.is_syllable(chr(0xABFF))
    assert not H.is_syllable(chr(0xD7A4))
