"""리포트 서식 · 숲그림 · 논문 문장."""

import pytest

from metapool.analysis import run_analysis
from metapool.report import _width, fmt, fmt_p, forest_plot, render_markdown, render_text, sentences


def make(records, measure="generic", **kw):
    return run_analysis([dict(r, __row__=str(i + 2)) for i, r in enumerate(records)], measure, **kw)


BASE = [
    {"study": "Kim 2021", "effect": "0.50", "se": "0.10", "subgroup": "성인"},
    {"study": "Lee 2022", "effect": "0.30", "se": "0.20", "subgroup": "성인"},
    {"study": "Park 2023", "effect": "0.70", "se": "0.15", "subgroup": "노인"},
    {"study": "Choi 2024", "effect": "0.20", "se": "0.12", "subgroup": "노인"},
]


def test_fmt_handles_none_and_nan():
    assert fmt(None) == "—"
    assert fmt(float("nan")) == "—"
    assert fmt(float("inf")) == "—"
    assert fmt(1.23456) == "1.235"


def test_fmt_uses_scientific_notation_for_tiny_values():
    assert "e-" in fmt(1.2e-9)


def test_fmt_p_follows_apa_style():
    assert fmt_p(0.0001) == "< .001"
    assert fmt_p(0.0432) == "= .043"
    assert fmt_p(0.5) == "= .500"
    assert fmt_p(None) == "—"


def test_forest_plot_rows_have_equal_display_width():
    a = make(BASE)
    rows = forest_plot(a)
    widths = {_width(r) for r in rows if r.strip() and not set(r.strip()) <= {"-"}}
    # 축 라벨 줄은 짧을 수 있으므로 본문 행만 검사
    body = [r for r in rows[2:] if "■" in r or "◆" in r or "◇" in r]
    assert len({_width(r) for r in body}) == 1
    assert widths  # 행이 실제로 만들어졌는지


def test_forest_plot_marks_null_line_crossing():
    a = make(BASE)
    rows = "\n".join(forest_plot(a))
    # Lee 2022 (0.30 ± 1.96*0.20) 은 0을 포함 → 교차 기호가 있어야 한다
    lee = [r for r in forest_plot(a) if r.startswith("Lee")][0]
    assert "┼" in lee
    # Kim 2021 (0.50 ± 0.196) 은 0을 포함하지 않는다
    kim = [r for r in forest_plot(a) if r.startswith("Kim")][0]
    assert "┼" not in kim
    assert "│" in rows


def test_forest_plot_clips_extreme_interval_with_arrow():
    data = BASE + [{"study": "Huge", "effect": "0.4", "se": "5.0"}]
    rows = forest_plot(make(data))
    huge = [r for r in rows if r.startswith("Huge")][0]
    assert "►" in huge or "◄" in huge


def test_sentences_contain_key_numbers():
    a = make(BASE)
    ko, en = sentences(a)
    assert "변량효과 모형으로 4편" in ko
    assert "95% CI" in ko and "I²" in ko
    assert en.startswith("A random-effects meta-analysis of 4 studies")
    assert "Hartung–Knapp" in ko and "Hartung–Knapp" in en


def test_sentences_report_nonsignificance_honestly():
    null_data = [
        {"study": "A", "effect": "0.01", "se": "0.30"},
        {"study": "B", "effect": "-0.02", "se": "0.30"},
        {"study": "C", "effect": "0.03", "se": "0.30"},
    ]
    ko, en = sentences(make(null_data))
    assert "유의하지 않았다" in ko
    assert "not statistically significant" in en


def test_sentences_use_odds_ratio_wording_for_binary():
    records = [
        {"study": "A", "events1": "20", "n1": "50", "events2": "10", "n2": "50"},
        {"study": "B", "events1": "25", "n1": "60", "events2": "15", "n2": "60"},
        {"study": "C", "events1": "30", "n1": "70", "events2": "18", "n2": "70"},
    ]
    ko, en = sentences(make(records, measure="or"))
    assert "오즈비" in ko and "odds ratio" in en


def test_text_report_sections_present():
    out = render_text(make(BASE))
    for section in ("통합 효과", "이질성", "하위군 분석", "민감도", "출판편향", "논문에 붙일 문장"):
        assert section in out


def test_text_report_shows_both_models():
    out = render_text(make(BASE))
    assert "고정효과 (fixed)" in out and "변량효과 (random)" in out


def test_markdown_escapes_pipe_in_labels():
    data = [
        {"study": "A|B 2020", "effect": "0.5", "se": "0.1"},
        {"study": "C 2021", "effect": "0.3", "se": "0.2"},
        {"study": "D 2022", "effect": "0.4", "se": "0.15"},
    ]
    out = render_markdown(make(data))
    assert r"A\|B 2020" in out


def test_long_labels_are_truncated_not_wrapped():
    data = [
        {"study": "아주아주아주아주아주아주 긴 연구 이름 2021", "effect": "0.5", "se": "0.1"},
        {"study": "B", "effect": "0.3", "se": "0.2"},
        {"study": "C", "effect": "0.4", "se": "0.15"},
    ]
    rows = forest_plot(make(data))
    body = [r for r in rows[2:] if "■" in r]   # rows[0]은 범례(■ 포함) 머리글
    assert len({_width(r) for r in body}) == 1
    assert "…" in rows[2]


def test_i2_verdict_wording_changes_with_heterogeneity():
    low = render_text(make([
        {"study": "A", "effect": "0.50", "se": "0.10"},
        {"study": "B", "effect": "0.52", "se": "0.10"},
        {"study": "C", "effect": "0.48", "se": "0.10"},
    ]))
    high = render_text(make([
        {"study": "A", "effect": "0.10", "se": "0.05"},
        {"study": "B", "effect": "1.50", "se": "0.05"},
        {"study": "C", "effect": "0.60", "se": "0.05"},
    ]))
    assert "낮음" in low
    assert "매우 큼" in high


def test_leave_one_out_flags_conclusion_flip():
    # 한 연구만 빼면 유의성이 사라지는 자료
    data = [
        {"study": "A", "effect": "0.50", "se": "0.10"},
        {"study": "B", "effect": "0.55", "se": "0.10"},
        {"study": "C", "effect": "0.45", "se": "0.10"},
        {"study": "D", "effect": "-0.90", "se": "0.10"},
    ]
    a = make(data)
    assert a.random.p >= 0.05                       # 네 편 전체로는 유의하지 않다
    assert any((r.p < 0.05) for r in a.loo)         # 한 편을 빼면 유의해진다
    out = render_text(a)
    assert "결론이 바뀝니다" in out or "결론이 바뀜" in out


def test_width_counts_hangul_as_two_columns():
    assert _width("연구") == 4
    assert _width("Kim 2021") == 8
    assert _width("") == 0
