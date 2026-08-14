"""정규화 — 이 파일이 무너지면 인용 대조 전체가 오탐이 된다."""

from __future__ import annotations

import pytest

from revcheck.normalize import canonical, norm_compare, norm_display, numbers_in

BASE = 'The "primary" outcome fell by 5.2-6.1 points at week 8.'


@pytest.mark.parametrize(
    "variant",
    [
        BASE,
        # 굽은 따옴표(워드 자동 변환)
        "The “primary” outcome fell by 5.2-6.1 points at week 8.",
        # en-dash / em-dash / 마이너스 기호
        'The "primary" outcome fell by 5.2–6.1 points at week 8.',
        'The "primary" outcome fell by 5.2—6.1 points at week 8.',
        'The "primary" outcome fell by 5.2−6.1 points at week 8.',
        # 연속 공백 · 줄바꿈 · 탭 · non-breaking space
        'The  "primary"   outcome fell by 5.2-6.1\npoints at week 8.',
        'The\t"primary" outcome fell by 5.2-6.1 points at week 8.',
        # 마크다운 강조
        'The **"primary"** outcome fell by 5.2-6.1 points at week 8.',
        # LaTeX 서식
        'The \\textit{"primary"} outcome fell by 5.2-6.1 points at week 8.',
        # 대소문자
        'THE "PRIMARY" OUTCOME FELL BY 5.2-6.1 POINTS AT WEEK 8.',
        # 전각 문자(한글 워드에서 흔하다)
        'The "primary" outcome fell by ５.２-６.１ points at week ８.',
    ],
)
def test_all_variants_normalise_to_the_same_string(variant):
    assert norm_compare(variant) == norm_compare(BASE)


def test_different_numbers_do_not_normalise_together():
    assert norm_compare("42 participants") != norm_compare("45 participants")


def test_canonical_keeps_case_and_shape_for_display():
    assert canonical("The “ISI”  fell") == 'The "ISI" fell'


def test_norm_display_truncates_in_the_middle():
    text = "A" * 300
    shown = norm_display(text, 80)
    assert len(shown) <= 82 and "…" in shown


def test_control_characters_are_stripped():
    assert "\x1b" not in canonical("safe\x1b[2Jtext")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("mean ISI decreased by 5.2 (SD 3.1)", ["5.2", "3.1"]),
        ("1,240 participants (45.2%)", ["1240", "45.2"]),
        ("p = .05", ["0.05"]),
        ("p = 0.050", ["0.05"]),
        ("no numbers here", []),
        ("5.0 and 5", ["5", "5"]),
    ],
)
def test_numbers_in(text, expected):
    assert numbers_in(text) == expected


def test_numbers_survive_quote_normalisation():
    quoted = "“42 participants per arm”"
    assert numbers_in(quoted) == ["42"]
