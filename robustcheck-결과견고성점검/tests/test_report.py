"""리포트 — 커버리지 자백 강제, 서식, 산출 CSV."""

import csv
import io
import math

import pytest

from conftest import analyse_path, make_rows, write_csv
from robustcheck.loo import LOO_RULE_TEXT
from robustcheck.report import (
    CONFESSION_HEADER,
    ISSUE_HEADER,
    NO_BEST_NOTE,
    SCENARIO_HEADER,
    SUBJECT_HEADER,
    ReportIntegrityError,
    _require_confession,
    fmt_delta,
    fmt_effect,
    fmt_p,
    render_issues_csv,
    render_markdown,
    render_report,
    render_scenarios_csv,
    render_subjects_csv,
)
from robustcheck.verdict import MULTIPLICITY_NOTE


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


# --------------------------------------------------- 불변식 1: 커버리지 자백


def test_report_contains_confession_block(two_group_analysis):
    text = render_report(two_group_analysis)
    assert CONFESSION_HEADER in text
    assert LOO_RULE_TEXT in text
    assert MULTIPLICITY_NOTE in text
    assert NO_BEST_NOTE in text


def test_report_states_total_computed_skipped(two_group_analysis):
    text = render_report(two_group_analysis)
    assert "총 12 / 계산 12 / 건너뜀 0" in text


def test_require_confession_rejects_missing_header():
    with pytest.raises(ReportIntegrityError):
        _require_confession("총 12 / 계산 12 / 건너뜀 0")


def test_require_confession_rejects_missing_loo_rule():
    body = "\n".join([CONFESSION_HEADER, "총 1 계산 1 건너뜀 0",
                      MULTIPLICITY_NOTE, NO_BEST_NOTE])
    with pytest.raises(ReportIntegrityError):
        _require_confession(body)


def test_require_confession_rejects_missing_multiplicity_note():
    body = "\n".join([CONFESSION_HEADER, "총 1 계산 1 건너뜀 0",
                      LOO_RULE_TEXT, NO_BEST_NOTE])
    with pytest.raises(ReportIntegrityError):
        _require_confession(body)


def test_require_confession_rejects_missing_no_best_note():
    body = "\n".join([CONFESSION_HEADER, "총 1 계산 1 건너뜀 0",
                      LOO_RULE_TEXT, MULTIPLICITY_NOTE])
    with pytest.raises(ReportIntegrityError):
        _require_confession(body)


def test_require_confession_error_lists_what_is_missing():
    with pytest.raises(ReportIntegrityError) as exc:
        _require_confession("아무것도 없는 리포트")
    assert CONFESSION_HEADER[:6] in str(exc.value)


def test_markdown_also_passes_through_the_gate(two_group_analysis):
    assert CONFESSION_HEADER in render_markdown(two_group_analysis)


def test_skip_reasons_are_broken_down(tmp_path):
    rows = make_rows(16)
    rows[0][3] = -5.0        # 음수 → 로그변환 불가
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, design="two-group", group="arm",
                            value="isi_week4")
    text = render_report(analysis)
    assert "로그변환 불가" in text
    assert analysis.coverage["로그변환 불가"] == 6


# ------------------------------------------------------------------ 서식


@pytest.mark.parametrize("p,expected", [
    (0.0426, ".043"), (0.5, ".500"), (0.0004, "<.001"), (0.0, "<.001"),
    (1.0, "1.000"),
])
def test_fmt_p(p, expected):
    assert fmt_p(p) == expected


def test_fmt_p_of_nan():
    assert fmt_p(float("nan")) == "NA"


@pytest.mark.parametrize("value,expected", [
    (0.026, "+.026"), (-0.026, "-.026"), (0.0, "+.000"), (1.5, "+1.500"),
])
def test_fmt_delta(value, expected):
    assert fmt_delta(value) == expected


def test_fmt_delta_tiny_uses_scientific_notation():
    assert "e-" in fmt_delta(1e-9)
    assert fmt_delta(0.0) == "+.000"


def test_fmt_effect_always_signed():
    assert fmt_effect(0.71) == "+0.710"
    assert fmt_effect(-0.71) == "-0.710"
    assert fmt_effect(float("nan")) == "NA"


# -------------------------------------------------------------- 리포트 본문


def test_baseline_printed_once_as_a_reference_point(two_group_analysis):
    text = render_report(two_group_analysis)
    assert text.count("[기준선]") == 1
    assert "기준점일 뿐이다" in text


def test_report_says_it_does_not_choose_the_test(two_group_analysis):
    assert "검정을 골라 주지 않는다" in render_report(two_group_analysis)


def test_report_prints_grade_formula(two_group_analysis):
    text = render_report(two_group_analysis)
    assert "[등급 산출식]" in text
    assert "취약 = " in text


def test_report_prints_pipeline_order(two_group_analysis):
    assert "결측 처리(C) → 로그변환(E)" in render_report(two_group_analysis)


def test_report_prints_exit_code(two_group_analysis):
    assert "종료코드 0" in render_report(two_group_analysis)


def test_fragile_report_lists_critical_flips(fragile_analysis):
    text = render_report(fragile_analysis)
    assert "치명" in text
    assert "유의 → 비유의" in text


def test_report_warns_subjects_are_not_removable(fragile_analysis):
    assert "빼야 할 사람'이 아니다" in render_report(fragile_analysis)


def test_undecidable_report_still_renders(undecidable_csv):
    analysis = analyse_path(undecidable_csv, design="two-group", group="arm",
                            value="isi_week4")
    text = render_report(analysis)
    assert "판정불가" in text
    assert CONFESSION_HEADER in text


def test_markdown_includes_korean_and_english_drafts(two_group_analysis):
    text = render_markdown(two_group_analysis)
    assert "민감도 분석 (한국어 초안)" in text
    assert "Sensitivity analysis (English draft)" in text
    assert "no multiplicity correction" in text


def test_english_draft_reports_actual_counts(two_group_analysis):
    assert "all 12 analytic specifications" in render_markdown(two_group_analysis)


def test_undecidable_draft_refuses_to_give_a_sentence(undecidable_csv):
    analysis = analyse_path(undecidable_csv, design="two-group", group="arm",
                            value="isi_week4")
    text = render_markdown(analysis)
    assert "문장을 만들지 않는다" in text
    assert "No draft sentence is produced" in text


# -------------------------------------------------------------- 산출 CSV


def test_scenario_csv_has_one_row_per_scenario(two_group_analysis):
    rows = rows_of(render_scenarios_csv(two_group_analysis))
    assert rows[0] == SCENARIO_HEADER
    assert len(rows) == 13


def test_scenario_csv_marks_the_baseline(two_group_analysis):
    rows = rows_of(render_scenarios_csv(two_group_analysis))
    flags = [r[SCENARIO_HEADER.index("기준선여부")] for r in rows[1:]]
    assert flags.count("Y") == 1


def test_scenario_csv_includes_skipped_rows_with_reasons(tmp_path):
    rows = make_rows(16)
    rows[0][3] = -5.0
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, design="two-group", group="arm",
                            value="isi_week4")
    out = rows_of(render_scenarios_csv(analysis))
    skipped = [r for r in out[1:] if r[SCENARIO_HEADER.index("계산됨")] == "N"]
    assert skipped
    assert all(r[SCENARIO_HEADER.index("건너뜀사유")] for r in skipped)


def test_subject_csv_covers_every_subject(two_group_analysis):
    rows = rows_of(render_subjects_csv(two_group_analysis))
    assert rows[0] == SUBJECT_HEADER
    assert len(rows) - 1 == len(two_group_analysis.dataset.subjects)


def test_subject_csv_marks_solo_flippers(fragile_analysis):
    rows = rows_of(render_subjects_csv(fragile_analysis))
    idx = SUBJECT_HEADER.index("단독뒤집기")
    assert any(r[idx] == "Y" for r in rows[1:])


def test_issues_csv_header(two_group_analysis):
    rows = rows_of(render_issues_csv(two_group_analysis))
    assert rows[0] == ISSUE_HEADER


def test_issues_csv_is_empty_for_robust_data(two_group_analysis):
    assert len(rows_of(render_issues_csv(two_group_analysis))) == 1


def test_issues_csv_lists_flips_and_subjects(fragile_analysis):
    rows = rows_of(render_issues_csv(fragile_analysis))[1:]
    kinds = {r[0] for r in rows}
    assert "시나리오" in kinds
    assert "피험자" in kinds


def test_issues_csv_never_tells_you_to_pick_a_scenario(fragile_analysis):
    rows = rows_of(render_issues_csv(fragile_analysis))[1:]
    for row in rows:
        assert "골라 쓰라는 뜻이 아닙니다" in row[5] or "빼라는 뜻이 아닙니다" in row[5] \
            or "함께 보고하세요" in row[5]


def test_issues_csv_for_undecidable(undecidable_csv):
    analysis = analyse_path(undecidable_csv, design="two-group", group="arm",
                            value="isi_week4")
    rows = rows_of(render_issues_csv(analysis))[1:]
    assert rows and rows[0][0] == "판정불가"


def test_scenario_csv_effect_columns_are_present(two_group_analysis):
    rows = rows_of(render_scenarios_csv(two_group_analysis))
    idx = SCENARIO_HEADER.index("비교효과크기")
    computed = [r for r in rows[1:] if r[SCENARIO_HEADER.index("계산됨")] == "Y"]
    assert all(r[idx] for r in computed)
