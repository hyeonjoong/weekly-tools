"""정규화 — 같은 표기는 같게, **다른 값은 절대 같지 않게**."""

import unicodedata

import pytest

from irbpack import normalize as N


def test_canon_collapses_whitespace():
    assert N.canon("  만   5세  ") == "만 5세"


def test_canon_converts_fullwidth_digits():
    assert N.canon("１２３") == "123"


def test_canon_converts_fullwidth_latin():
    assert N.canon("Ｖｅｒｓｉｏｎ") == "Version"


def test_canon_converts_fullwidth_space():
    assert N.canon("만　5세") == "만 5세"


@pytest.mark.parametrize("tilde", ["~", "∼", "〜", "～", "–", "—", "−"])
def test_canon_unifies_tilde_family(tilde):
    assert N.canon("만 5%s12세" % tilde) == "만 5~12세"


def test_canon_nfd_hangul_becomes_nfc():
    decomposed = unicodedata.normalize("NFD", "한글 문서")
    assert N.canon(decomposed) == "한글 문서"


def test_canon_unifies_curly_quotes():
    assert N.canon("“동의서”") == '"동의서"'


def test_canon_none_is_empty():
    assert N.canon(None) == ""


def test_strip_control_removes_newlines():
    assert "\n" not in N.strip_control("가\n나")


def test_strip_control_removes_zero_width():
    assert N.strip_control("가​나") == "가나"


def test_strip_control_removes_bidi_override():
    assert N.strip_control("가‮나") == "가나"


def test_compact_removes_all_spaces():
    assert N.compact(" 만 5 ~ 12 세 ") == "만5~12세"


@pytest.mark.parametrize("text,expected", [
    ("260518", "2026-05-18"),
    ("20260518", "2026-05-18"),
    ("2026-05-18", "2026-05-18"),
    ("2026.05.18", "2026-05-18"),
    ("2026/5/8", "2026-05-08"),
    ("2026년 5월 18일", "2026-05-18"),
    ("990518", "1999-05-18"),
])
def test_norm_date_variants(text, expected):
    assert N.norm_date(text) == expected


@pytest.mark.parametrize("text", ["123456", "261340", "2026-13-01", "abc", "", None, "2026-02-99"])
def test_norm_date_rejects_non_dates(text):
    assert N.norm_date(text) is None


@pytest.mark.parametrize("text,expected", [
    ("Version No: V1.0", "1.0"),
    ("v1.0", "1.0"),
    ("1", "1.0"),
    ("버전 2.1", "2.1"),
    ("1.0.2", "1.0.2"),
    ("Ver. 3.4", "3.4"),
])
def test_norm_version_variants(text, expected):
    assert N.norm_version(text) == expected


@pytest.mark.parametrize("text", ["v", "", None, "1.0-draft", "초안"])
def test_norm_version_rejects_non_versions(text):
    assert N.norm_version(text) is None


def test_norm_version_distinguishes_minor():
    assert N.norm_version("v1.1") != N.norm_version("v1.2")


def test_norm_amount_strips_commas():
    assert N.norm_amount("10,000") == 10000


def test_norm_amount_man_unit():
    assert N.norm_amount("1", unit_man=True) == 10000


def test_norm_amount_rejects_text():
    assert N.norm_amount("일만") is None


def test_norm_amount_equates_man_and_commas():
    assert N.norm_amount("1", unit_man=True) == N.norm_amount("10,000")


@pytest.mark.parametrize("word,expected", [("한", 1), ("두", 2), ("세", 3), ("네", 4), ("열", 10), ("3", 3)])
def test_norm_count_word(word, expected):
    assert N.norm_count_word(word) == expected


def test_norm_count_word_rejects_other():
    assert N.norm_count_word("여러") is None


def test_norm_minutes_hours_to_minutes():
    assert N.norm_minutes(1, "시간") == 60


def test_norm_minutes_minutes_pass_through():
    assert N.norm_minutes(90, "분") == 90


def test_norm_minutes_rejects_zero():
    assert N.norm_minutes(0, "분") is None


def test_norm_minutes_rejects_absurd():
    assert N.norm_minutes(999999, "시간") is None


def test_range_key_single_and_range():
    assert N.range_key(5, 12, "세") == "5-12세"
    assert N.range_key(3, 3, "회") == "3회"
    assert N.range_key(3, None, "회") == "3회"


def test_rules_are_printable_pairs():
    assert len(N.RULES) >= 8
    names = [name for name, _ in N.RULES]
    assert len(names) == len(set(names))
    for name, description in N.RULES:
        assert name.strip() and len(description) > 10
