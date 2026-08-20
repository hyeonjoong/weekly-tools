"""판정 — 등급 승격·강등이 의도대로 되는지."""

from decimal import Decimal

from tracecheck.analyze import analyze, parse_sections
from tracecheck.bundle import Cell, collect
from tracecheck.judge import (GRADE_CRITICAL, GRADE_INFO, GRADE_WARN,
                              VERDICT_AMBIGUOUS, VERDICT_CHANCE,
                              VERDICT_MATCHED, VERDICT_MISSING,
                              VERDICT_PERCENT, VERDICT_SIGN, VERDICT_STALE,
                              judge_number)
from tracecheck.manuscript import Block
from tracecheck.match import NumberIndex, needed_decimals
from tracecheck.numbers import extract_numbers


def cell(value, file="stat.csv", row=2, col="mean"):
    text = str(value)
    return Cell(file=file, rel=file, sheet="", row=row, col=col, ordinal=0,
                raw=text, value=Decimal(text),
                decimals=len(text.split(".")[1]) if "." in text else 0,
                is_percent=False)


def number(text, kind="para"):
    block = Block(index=0, line=1, section="results", kind=kind, text=text)
    return [n for n in extract_numbers(block) if not n.skip][0]


def index(cells):
    return NumberIndex(cells, needed_decimals(range(0, 6)))


def judge(text, current, previous=None, **kw):
    return judge_number(number(text), index(current),
                        index(previous) if previous is not None else None, **kw)


def test_exact_match_is_info():
    result = judge("평균은 12.44 이다", [cell("12.44")])
    assert result.grade == GRADE_INFO and result.verdict == VERDICT_MATCHED
    assert result.method == "정확"


def test_rounded_match_is_info():
    result = judge("평균은 12.4 이다", [cell("12.44")])
    assert result.grade == GRADE_INFO and result.method == "반올림(1자리)"


def test_missing_value_is_critical_with_nearest_hint():
    result = judge("순응도는 91.2% 였다", [cell("87.3")])
    assert result.grade == GRADE_CRITICAL and result.verdict == VERDICT_MISSING
    assert "87.3" in result.note
    assert "--previous" in result.note        # 미지정 사실을 자백


def test_sign_only_difference_is_a_warning_not_a_critical():
    """원고 "3.47 감소" ↔ 출력 -3.47 은 같은 결과입니다.

    치명으로 올리면 실제 원고에서 거짓 치명이 쏟아지고(적대적 검토에서 18건 중
    13건이 이 원인이었습니다), 정보로 내리면 방향이 뒤집힌 사고를 놓칩니다.
    """
    result = judge("감소량은 3.47 이었다", [cell("-3.47")])
    assert result.grade == GRADE_WARN and result.verdict == VERDICT_SIGN
    assert "부호만 다른 값" in result.note
    assert result.method == "부호무시"


def test_sign_only_difference_still_promotes_stale_from_previous():
    """부호 표기가 달라도 '구버전 잔존'은 그대로 잡혀야 합니다."""
    result = judge("11.4분 감소하였다", [cell("-6.31", row=4, col="diff")],
                   previous=[cell("-11.42", row=4, col="diff")])
    assert result.grade == GRADE_CRITICAL and result.verdict == VERDICT_STALE


def test_stale_value_from_previous_bundle_is_critical():
    result = judge("서파수면은 11.7분 증가", [cell("9.82", row=4, col="diff")],
                   previous=[cell("11.68", row=4, col="diff")])
    assert result.grade == GRADE_CRITICAL and result.verdict == VERDICT_STALE
    assert result.current_at_coord.value == Decimal("9.82")
    assert "9.82" in result.note and "갱신 누락" in result.note


def test_stale_without_matching_coordinate_still_reports():
    result = judge("서파수면은 11.7분 증가", [cell("9.82", row=9, col="other")],
                   previous=[cell("11.68", row=4, col="diff")])
    assert result.verdict == VERDICT_STALE and result.current_at_coord is None


def test_value_in_both_bundles_is_not_stale():
    result = judge("평균 12.44", [cell("12.44")], previous=[cell("12.44")])
    assert result.grade == GRADE_INFO


def test_percent_conversion_is_downgraded_to_warning():
    result = judge("보고율은 6.25% 였다", [cell("0.0625", col="ae_rate")])
    assert result.grade == GRADE_WARN and result.verdict == VERDICT_PERCENT
    assert result.method == "백분율환산"
    assert "0.0625" in result.note


def test_proportion_to_percent_conversion():
    result = judge("비율은 0.63 이었다", [cell("62.5", col="rate")])
    assert result.grade == GRADE_WARN and result.method == "백분율환산"


def test_ambiguous_rounding_is_a_warning():
    """12.44 와 12.35 가 둘 다 12.4 로 반올림되면 어느 쪽인지 확정 불가."""
    result = judge("평균은 12.4 이다", [cell("12.44"), cell("12.35", row=3)])
    assert result.grade == GRADE_WARN and result.verdict == VERDICT_AMBIGUOUS
    assert "12.35" in result.note and "12.44" in result.note


def test_exact_match_beats_ambiguity():
    """정확히 같은 값이 있으면 다른 후보가 있어도 경고로 떨어뜨리지 않습니다."""
    result = judge("평균은 42 이다", [cell("42"), cell("41.72", row=3)])
    assert result.grade == GRADE_INFO


def test_identical_values_in_several_files_are_not_ambiguous():
    result = judge("평균은 12.4 이다",
                   [cell("12.44", file="a.csv"), cell("12.44", file="b.csv")])
    assert result.grade == GRADE_INFO and result.match_count == 2


def test_chance_match_warning_for_low_precision_small_values():
    cells = [cell("1.5", row=i) for i in range(2, 20)]
    result = judge("값은 1.5 였다", cells, chance_matches=12)
    assert result.grade == GRADE_WARN and result.verdict == VERDICT_CHANCE
    assert "18곳" in result.note


def test_chance_rule_does_not_fire_for_precise_values():
    cells = [cell("12.345", row=i) for i in range(2, 30)]
    result = judge("값은 12.345 였다", cells, chance_matches=12)
    assert result.grade == GRADE_INFO


def test_inequality_matches_smaller_output_value():
    result = judge("유의하였다 (p < 0.001)", [cell("0.00003", col="p")])
    assert result.grade == GRADE_INFO and result.method.startswith("부등호")


def test_inequality_without_smaller_value_is_critical():
    result = judge("유의하였다 (p < 0.001)", [cell("0.03", col="p")])
    assert result.grade == GRADE_CRITICAL


def test_source_location_is_reported():
    result = judge("평균 12.44", [cell("12.44", file="statwise.csv", row=4,
                                      col="mean")])
    assert "statwise.csv" in result.source_locs and "4행" in result.source_locs
    assert "mean열" in result.source_locs


# --------------------------------------------------------------------------- #
# analyze() 수준 통합
# --------------------------------------------------------------------------- #

def test_analyze_counts_and_sections(simple_case):
    from tracecheck.manuscript import read_manuscript
    manuscript_path, bundle_dir = simple_case
    analysis = analyze(read_manuscript(manuscript_path),
                       collect([bundle_dir], "현재"), None,
                       sections=parse_sections(""))
    assert analysis.coverage.compared == 6
    assert analysis.coverage.unmatched == 0
    assert analysis.exit_code == 0


def test_analyze_undecidable_when_too_few_comparable(tmp_path):
    from tracecheck.manuscript import read_manuscript
    path = tmp_path / "m.md"
    path.write_text("## Results\n평균 12.44 였다.\n", encoding="utf-8")
    bundle_dir = tmp_path / "out"
    bundle_dir.mkdir()
    (bundle_dir / "a.csv").write_text("m\n12.44\n", encoding="utf-8")
    analysis = analyze(read_manuscript(str(path)),
                       collect([str(bundle_dir)], "현재"), None,
                       sections=parse_sections(""))
    assert analysis.exit_code == 3 and "5개" in analysis.undecidable


def test_analyze_undecidable_when_unmatched_rate_too_high(tmp_path):
    from tracecheck.manuscript import read_manuscript
    path = tmp_path / "m.md"
    path.write_text("## Results\n"
                    "값은 11.1, 22.2, 33.3, 44.4, 55.5, 66.6 이었다.\n",
                    encoding="utf-8")
    bundle_dir = tmp_path / "out"
    bundle_dir.mkdir()
    (bundle_dir / "a.csv").write_text("m\n11.1\n", encoding="utf-8")
    analysis = analyze(read_manuscript(str(path)),
                       collect([str(bundle_dir)], "현재"), None,
                       sections=parse_sections(""))
    assert analysis.exit_code == 3
    assert "미매칭율" in analysis.undecidable
    assert analysis.coverage.unmatched == 5


def test_sections_parsing():
    assert parse_sections("") == ["abstract", "results", "tables", "captions"]
    assert parse_sections("초록,결과") == ["abstract", "results"]
    assert "methods" in parse_sections("abstract,results,tables,methods")
    assert len(parse_sections("all")) == 9


def test_unknown_section_is_rejected():
    import pytest

    from tracecheck.analyze import SectionError
    with pytest.raises(SectionError):
        parse_sections("서문")
