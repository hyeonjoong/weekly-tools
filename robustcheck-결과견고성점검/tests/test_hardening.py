"""경계 케이스 — 이 파일이 조용히 통과하면 툴이 조용히 거짓말한 것이다.

기획서가 명시한 경계: N=6, 전원 동일값, 결측 100% 열, 군 하나가 n=1,
음수 포함 로그변환. 여기에 적대적 검토에서 나온 사례를 계속 덧붙인다.
"""

import math
import os

import pytest

from conftest import analyse_path, make_rows, write_csv
from robustcheck.analyze import MIN_COMPUTED_SCENARIOS, analyse
from robustcheck.cli import main
from robustcheck.dataio import InputError, read_table
from robustcheck.report import render_markdown, render_report, render_scenarios_csv
from robustcheck.spec import MIN_VALID_N, Spec, build_dataset


def two_group(path):
    return dict(design="two-group", group="arm", value="isi_week4")


def rows_with(n, **overrides):
    rows = make_rows(n)
    for (row, col), value in overrides.items():
        rows[row][col] = value
    return rows


# --------------------------------------------------------------- 최소 N


def test_exactly_six_valid_subjects_is_decidable(tmp_path):
    rows = [["S1", "active", 19, 10, 30, 82],
            ["S2", "active", 20, 11, 31, 83],
            ["S3", "active", 21, 13, 32, 84],
            ["S4", "sham", 19, 18, 33, 79],
            ["S5", "sham", 20, 19, 34, 80],
            ["S6", "sham", 21, 21, 35, 81]]
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.valid_n == MIN_VALID_N == 6
    assert analysis.undecidable_reason == ""
    assert analysis.exit_code in (0, 1)


def test_five_valid_subjects_is_undecidable(tmp_path):
    rows = [["S1", "active", 19, 10, 30, 82],
            ["S2", "active", 20, 11, 31, 83],
            ["S3", "active", 21, 13, 32, 84],
            ["S4", "sham", 19, 18, 33, 79],
            ["S5", "sham", 20, 19, 34, 80]]
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.undecidable_reason
    assert analysis.exit_code == 3


def test_undecidable_when_too_few_scenarios_compute(tmp_path):
    """계산된 시나리오가 5개 미만이면 '흔들어 봤다'고 말할 수 없다."""
    rows = make_rows(12)
    for row in rows:
        row[3] = -abs(float(row[3]))     # 전부 음수 → 로그 시나리오 전멸
        row[2] = -abs(float(row[2]))
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    # 음수라 로그 축 6개가 전부 죽고, 남은 6개는 계산된다 → 판정 가능.
    assert analysis.computed == 6 >= MIN_COMPUTED_SCENARIOS
    assert analysis.undecidable_reason == ""
    assert analysis.coverage["로그변환 불가"] == 6


# ------------------------------------------------------------ 퇴화 입력


def test_all_identical_values_is_undecidable_not_a_crash(tmp_path):
    rows = [["S%d" % i, "active" if i % 2 else "sham", 20, 12, 30, 80]
            for i in range(1, 13)]
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.exit_code == 3
    assert "[커버리지 자백]" in render_report(analysis)


def test_one_group_with_single_member_is_undecidable(tmp_path):
    """군 하나가 n=1 이면 판정불가(3)다 — 조용히 계산해 내지 않는다."""
    rows = make_rows(12)
    for row in rows[1:]:
        row[1] = "sham"
    path = write_csv(tmp_path / "a.csv", rows)
    assert main([path, "--design", "two-group", "--group", "arm",
                 "--value", "isi_week4", "--no-files"]) == 3


def test_group_of_two_makes_scenarios_skip(tmp_path):
    rows = make_rows(14)
    for row in rows[2:]:
        row[1] = "sham"
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.undecidable_reason
    assert any("군 n<" in reason for reason in analysis.coverage)


def test_fully_missing_value_column_names_the_column(tmp_path):
    """'군이 0개' 같은 엉뚱한 말 대신 진짜 원인을 말해야 한다."""
    rows = make_rows(14)
    for row in rows:
        row[3] = ""
    path = write_csv(tmp_path / "a.csv", rows)
    with pytest.raises(InputError) as exc:
        analyse_path(path, **two_group(path))
    assert "isi_week4" in str(exc.value)


def test_fully_missing_column_is_exit_2_not_a_crash(tmp_path):
    rows = make_rows(14)
    for row in rows:
        row[3] = ""
    path = write_csv(tmp_path / "a.csv", rows)
    assert main([path, "--design", "two-group", "--group", "arm",
                 "--value", "isi_week4", "--no-files"]) == 2


def test_negative_values_skip_only_log_scenarios(tmp_path):
    rows = make_rows(16)
    rows[0][3] = -3.0
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.coverage.get("로그변환 불가") == 6
    assert analysis.computed == 6


def test_zero_value_also_blocks_log(tmp_path):
    rows = make_rows(16)
    rows[0][3] = 0
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.coverage.get("로그변환 불가") == 6


def test_single_row_file_is_exit_2(tmp_path):
    path = write_csv(tmp_path / "a.csv", [["S1", "active", 19, 10, 30, 82]])
    assert main([path, "--design", "two-group", "--group", "arm",
                 "--value", "isi_week4", "--no-files"]) == 2


def test_text_in_numeric_column_becomes_missing(tmp_path):
    rows = make_rows(14)
    rows[0][3] = "측정불가"
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.baseline.n == 13


def test_huge_values_never_crash_the_tool(tmp_path):
    """1e250 근처 값은 평균의 1 ulp 오차가 제곱되며 예전엔 OverflowError 였다."""
    rows = make_rows(14)
    for i, row in enumerate(rows):
        row[3] = 1e250 + i
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.exit_code == 3
    assert all(j.result.skip_reason for j in analysis.judged
               if not j.result.computed)
    assert "[커버리지 자백]" in render_report(analysis)


def test_moderately_large_values_still_compute(tmp_path):
    rows = make_rows(14)
    for i, row in enumerate(rows):
        row[3] = 1e12 + i * 1e9
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.baseline.computed
    assert math.isfinite(analysis.baseline.p)


def test_tiny_values_are_refused_with_a_reason_not_a_lie(tmp_path):
    """분산이 배정도 하한 아래면 '분산 0' 이라고 말하지 말고 사유를 밝힌다."""
    rows = make_rows(14)
    for i, row in enumerate(rows):
        row[3] = 1e-250 * (1 + i)
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert not analysis.baseline.computed
    assert "단위를 바꿔" in analysis.baseline.skip_detail


def test_normal_magnitude_values_are_unaffected(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(20))
    analysis = analyse_path(path, **two_group(path))
    assert analysis.baseline.computed


def test_unicode_subject_ids_survive(tmp_path):
    rows = make_rows(14)
    for i, row in enumerate(rows):
        row[0] = "피험자-%02d" % i
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.baseline.computed
    assert any("피험자" in sid for sid in analysis.baseline.ids)


def test_group_labels_with_commas_and_quotes(tmp_path):
    rows = make_rows(14)
    for i, row in enumerate(rows):
        row[1] = 'A,"B"' if i % 2 else "C"
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.baseline.computed


def test_whitespace_only_group_is_treated_as_missing(tmp_path):
    rows = make_rows(14)
    rows[0][1] = "   "
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert analysis.baseline.computed


# ---------------------------------------------------------- 결정론 / 재현성


def test_same_input_gives_identical_report(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(24))
    first = render_report(analyse_path(path, **two_group(path)))
    second = render_report(analyse_path(path, **two_group(path)))
    assert first == second


def test_row_order_does_not_change_the_verdict(tmp_path):
    rows = make_rows(24)
    forward = write_csv(tmp_path / "a.csv", rows)
    backward = write_csv(tmp_path / "b.csv", list(reversed(rows)))
    a = analyse_path(forward, **two_group(forward))
    b = analyse_path(backward, **two_group(backward))
    assert a.verdict.grade == b.verdict.grade
    assert a.baseline.p == pytest.approx(b.baseline.p)
    assert sorted(e.sid for e in a.solo_flippers) == \
        sorted(e.sid for e in b.solo_flippers)


def test_no_randomness_in_the_package():
    package = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "robustcheck")
    for name in os.listdir(package):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            text = fh.read()
        assert "import random" not in text, name
        assert "random." not in text, name


def test_no_network_imports_in_the_package():
    package = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "robustcheck")
    forbidden = ("socket", "urllib", "http.client", "requests", "ftplib",
                 "smtplib", "subprocess")
    for name in os.listdir(package):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(package, name), encoding="utf-8") as fh:
            text = fh.read()
        for module in forbidden:
            assert "import %s" % module not in text, (name, module)


def test_package_has_no_third_party_dependencies():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
        text = fh.read()
    assert "dependencies = []" in text


# ------------------------------------------------------------ 성능 한계


def test_moderate_dataset_completes_quickly(tmp_path):
    import time
    path = write_csv(tmp_path / "big.csv", make_rows(300))
    start = time.time()
    analysis = analyse_path(path, **two_group(path))
    elapsed = time.time() - start
    assert analysis.baseline.computed
    # N=300 이면 leave-one-out 이 300회다. 5초를 넘으면 뭔가 O(n³) 이 된 것이다.
    assert elapsed < 5.0


def test_loo_budget_prevents_explosion(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(40))
    dataset = build_dataset(read_table(path),
                            Spec("two-group", value="isi_week4", group="arm"))
    analysis = analyse(dataset, loo_budget=80)
    total = (len(analysis.loo_baseline.entries) if analysis.loo_baseline else 0)
    total += sum(len(run.entries) for run in analysis.loo_extra)
    assert total <= 80


# ------------------------------------------------------------- 자백 강제


def test_every_skipped_scenario_has_a_reason(tmp_path):
    rows = make_rows(16)
    rows[0][3] = -1.0
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    for judged in analysis.judged:
        if not judged.result.computed:
            assert judged.result.skip_reason


def test_coverage_counts_add_up(tmp_path):
    rows = make_rows(16)
    rows[0][3] = -1.0
    path = write_csv(tmp_path / "a.csv", rows)
    analysis = analyse_path(path, **two_group(path))
    assert sum(analysis.coverage.values()) == analysis.skipped
    assert analysis.computed + analysis.skipped == analysis.total


def test_scenario_csv_row_count_equals_grid_size(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(20))
    analysis = analyse_path(path, design="paired", pre="isi_baseline",
                            post="isi_week4")
    lines = render_scenarios_csv(analysis).strip().splitlines()
    assert len(lines) - 1 == analysis.total == 36


def test_markdown_report_explains_how_to_read_it(tmp_path):
    path = write_csv(tmp_path / "a.csv", make_rows(20))
    text = render_markdown(analyse_path(path, **two_group(path)))
    assert "이 리포트를 읽는 법" in text
    assert "기준선은 결과가 아니다" in text
