"""불변식 2 — 이 툴은 '가장 유의한 조합'을 절대 추천하지 않는다.

정렬 기준 하나만 잘못 잡으면 이 툴은 p-해킹 자동화 도구가 된다. 그래서
정렬 규칙을 테스트로 못 박고, 소스에 유의성 기반 정렬이 다시 기어들어오는지
문자열 수준에서도 감시한다.
"""

import csv
import io
import os
import re

import pytest

from conftest import analyse_path, make_rows, write_csv
from robustcheck.report import (
    NO_BEST_NOTE,
    SCENARIO_HEADER,
    render_markdown,
    render_report,
    render_scenarios_csv,
)
from robustcheck.verdict import CRITICAL, WARNING, order_key

PACKAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "robustcheck")


def source_files():
    return [os.path.join(PACKAGE, name) for name in sorted(os.listdir(PACKAGE))
            if name.endswith(".py")]


def read_source(name):
    with open(os.path.join(PACKAGE, name), encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------- 정렬 규칙


def test_order_key_signature_takes_no_p_value():
    import inspect
    from robustcheck import verdict
    params = list(inspect.signature(verdict.order_key).parameters)
    assert params == ["severity", "computed", "axes_order"]


def test_ordering_puts_flips_first_not_low_p(fragile_analysis):
    ordered = fragile_analysis.ordered
    severities = [j.severity if j.result.computed else "건너뜀" for j in ordered]
    rank = {CRITICAL: 0, WARNING: 1, "": 2, "건너뜀": 3}
    assert [rank[s] for s in severities] == sorted(rank[s] for s in severities)


def test_ordering_is_not_by_p_value(fragile_analysis):
    """뒤집히지 않은 시나리오 중 p 가 가장 작은 것이 맨 위에 오지 않는다."""
    ordered = [j for j in fragile_analysis.ordered if j.result.computed]
    ps = [j.result.p for j in ordered]
    assert ps != sorted(ps)


def test_most_significant_scenario_is_not_listed_first(fragile_analysis):
    computed = [j for j in fragile_analysis.judged if j.result.computed]
    smallest = min(computed, key=lambda j: j.result.p)
    first = fragile_analysis.ordered[0]
    assert first.axes.key != smallest.axes.key or bool(first.flips)


def test_scenario_csv_row_order_matches_ordered(fragile_analysis):
    rows = list(csv.reader(io.StringIO(
        render_scenarios_csv(fragile_analysis))))[1:]
    expected = [list(j.axes.key) for j in fragile_analysis.ordered]
    actual = [r[:4] for r in rows]
    assert actual == expected


def test_flip_list_is_ordered_by_severity_then_axes(fragile_analysis):
    keys = [order_key(j.severity, True, j.axes.order)
            for j in fragile_analysis.flipped]
    assert keys == sorted(keys)


# ------------------------------------------------------------ 문구 감시


def test_report_carries_the_no_best_warning(fragile_analysis):
    assert NO_BEST_NOTE in render_report(fragile_analysis)
    assert "p-해킹" in render_report(fragile_analysis)


def test_markdown_repeats_the_warning(fragile_analysis):
    assert render_markdown(fragile_analysis).count("p-해킹") >= 2


@pytest.mark.parametrize("phrase", [
    "가장 유의한", "최적 조합", "권장 조합", "추천 조합", "best_scenario",
    "most_significant", "recommend_best",
])
def test_source_never_offers_a_best_scenario(phrase):
    for path in source_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for line in text.splitlines():
            if phrase in line:
                # 경고 문구 안에서만 등장해야 한다 (부정문).
                negated = any(word in line for word in
                              ("않는다", "않습니다", "아니다", "아닙니다", "p-해킹"))
                assert negated, "%s: %s" % (path, line)


def test_no_sort_by_p_anywhere_in_the_package():
    pattern = re.compile(r"sort\w*\([^)]*\.p\b")
    for path in source_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert not pattern.search(text), path


def test_readme_opens_with_the_p_hacking_warning():
    root = os.path.dirname(PACKAGE)
    with open(os.path.join(root, "README.md"), encoding="utf-8") as fh:
        head = fh.read(1600)
    assert "p-해킹" in head
    assert "가장 유의한 조합" in head


def test_help_text_states_the_rule():
    from robustcheck.cli import build_parser
    assert "추천하지 않습니다" in build_parser().epilog


# ------------------------------------------------------------ 기준선 취급


def test_baseline_appears_only_once_in_the_report(two_group_analysis):
    text = render_report(two_group_analysis)
    assert text.count("[기준선]") == 1


def test_baseline_is_never_counted_as_a_flip(fragile_analysis):
    assert all(not j.axes.is_baseline for j in fragile_analysis.flipped)


def test_report_body_is_mostly_changes_not_absolute_values(fragile_analysis):
    """statwise 대용으로 읽히면 경계 설정이 실패한 것이다."""
    text = render_report(fragile_analysis)
    body = text.split("[시나리오 전수 대조]", 1)[1]
    assert "→" in body
    assert body.count("→") >= 4
