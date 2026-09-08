"""정규화 규칙 — 같은 것은 같고, 다른 것은 절대 같지 않다."""
import pytest

from irbpack import textnorm as T


@pytest.mark.parametrize("a,b", [
    ("만 5~12세", "만 5-12세"), ("만 5~12세", "만 5∼12세"), ("만 5~12세", "만 5〜12세"),
    ("만 5 ~ 12세", "만5~12세"), ("만 ５~１２세", "만 5~12세"), ("만 5–12세", "만 5~12세"),
    ("5세 이상 12세 이하", "만 5~12세"), ("만 19세 이상 45세 이하", "만 19~45세"),
])
def test_age_equivalents(a, b):
    assert T.age_ranges(a) == T.age_ranges(b) != []


@pytest.mark.parametrize("a,b", [("만 5~12세", "만 6~12세"), ("만 5~12세", "만 5~13세"), ("만 19~45세", "만 19~65세")])
def test_age_not_equal(a, b):
    assert T.age_ranges(a) != T.age_ranges(b)


@pytest.mark.parametrize("text,won", [
    ("10,000원", 10000), ("1만원", 10000), ("1만 원", 10000), ("만원", 10000), ("50,000 원", 50000),
    ("5만원", 50000), ("1.5만원", 15000), ("3000원", 3000), ("100만원", 1000000),
])
def test_money(text, won):
    assert T.money(text) == won


def test_money_none():
    assert T.money("실비") is None


@pytest.mark.parametrize("text,won", [("5천원", 5000), ("3만5천원", 35000), ("2만 5천원", 25000), ("1억원", 100000000), ("1억 2천만원", 120000000), ("12,000,000원", 12000000)])
def test_money_korean_units(text, won):
    assert T.money(text) == won


def test_money_huge_digits_do_not_crash():
    assert T.money("1" * 5000 + "원") in (None, int("1" * 12))


def test_months_years_and_months():
    assert T.months("1년 6개월") == 18


@pytest.mark.parametrize("text,pairs", [
    ("만 19세 이상 45세 미만", [(19, 44)]), ("만 19세 ~ 만 45세", [(19, 45)]), ("만 19세 이상 만 45세 이하", [(19, 45)]),
    ("만 19세부터 45세까지", [(19, 45)]), ("만 19세에서 45세 사이", [(19, 45)]), ("만 5~12세 미만", [(5, 11)]),
])
def test_age_more_forms(text, pairs):
    assert T.age_ranges(text) == pairs


@pytest.mark.parametrize("text,n", [("2회", 2), ("두 번", 2), ("2번", 2), ("2차례", 2), ("2 회기", 2), ("세 번", 3), ("10회", 10), ("한 번", 1)])
def test_count(text, n):
    assert T.count(text) == n


@pytest.mark.parametrize("text,mins", [
    ("90분", 90), ("1시간 30분", 90), ("1.5시간", 90), ("2시간", 120), ("한 시간", 60), ("두시간 반", 150),
    ("약 60 분", 60), ("1시간반", 90), ("30분", 30),
])
def test_minutes(text, mins):
    assert T.minutes(text) == mins


@pytest.mark.parametrize("text,months", [("3년", 36), ("3 년간", 36), ("삼년", 36), ("36개월", 36), ("5년", 60), ("6개월", 6)])
def test_months(text, months):
    assert T.months(text) == months


@pytest.mark.parametrize("text", ["260518", "2026.05.18", "2026-05-18", "2026년 5월 18일", "20260518", "2026/05/18"])
def test_date(text):
    assert T.date(text) == "2026-05-18"


def test_date_invalid_month():
    assert T.date("261318") is None


@pytest.mark.parametrize("text", ["Version No: 1.0", "v1.0", "V1.0", "ver 1.0", "1.0판", "버전 1.0", "Version 1.0", "Ver.1.0"])
def test_version(text):
    assert T.version(text) == "1"


def test_version_multi_part():
    assert T.version("v1.2.3") == "1.2.3"


@pytest.mark.parametrize("a,b", [("v2.0", "버전 2"), ("Version No: 1.0", "v1"), ("1.0.0판", "v1"), ("Ver 1.2.0", "v1.2")])
def test_version_canonical_equivalents(a, b):
    assert T.version(a) == T.version(b)


def test_version_date_like_is_not_version():
    assert T.version("Version 2026.05.18") is None


def test_version_with_trailing_period():
    assert T.version("Version 1.2.") == "1.2"


def test_squash_ignores_space_punct_case():
    assert T.squash("소아 인공와우 — 음악 (v2)") == T.squash("소아인공와우음악v2") == T.squash("소아 인공와우 – 음악 (V2)")


def test_nfc_join():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "동의서")
    assert T.basic(nfd) == "동의서"


def test_halfwidth():
    assert T.halfwidth("ＡＢＣ１２３（）：") == "ABC123():"


def test_rules_documented():
    for name in ("물결 통일", "금액 통일", "횟수 통일", "시간→분", "날짜 통일", "버전 통일", "기간 통일"):
        assert name in T.RULES and T.RULES[name]
