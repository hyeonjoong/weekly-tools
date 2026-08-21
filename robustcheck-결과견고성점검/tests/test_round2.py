"""적대적 검토 2라운드에서 나온 결함들의 회귀 테스트.

2라운드는 **1라운드 수정이 만들어 낸 새 결함**을 찾는 것이 목적이었고, 실제로
여섯 개가 나왔다. 특히 비모수 검정력 하한(1라운드 수정 #6)은 그 자체가
세 가지 방식으로 틀려 있었다 — 큰 N 에서 오버플로, 경계에서 off-by-one,
그리고 정확분포를 타지 않는 경우에도 적용.
"""

import csv
import io
import math
import os

import pytest

from conftest import EXAMPLES, analyse_path, make_rows, write_csv
from robustcheck.cli import main
from robustcheck.dataio import read_table
from robustcheck.report import (
    OUT_SCENARIOS,
    OUT_SUBJECTS,
    SCENARIO_HEADER,
    render_markdown,
    render_report,
    render_scenarios_csv,
)
from robustcheck.safety import csv_safe
from robustcheck.scenarios import nonparametric_p_floor
from robustcheck.spec import Spec, build_dataset


def two_group():
    return dict(design="two-group", group="arm", value="isi_week4")


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


# ------------------------------------------- 비모수 검정력 하한 (3중 결함)


def test_p_floor_matches_the_exact_minimum():
    """2 / C(n1+n2, n1) 과 2^(1−n) 이 정말 도달 가능한 최소 양측 p 인가."""
    assert nonparametric_p_floor("two-group", 3, 3) == pytest.approx(0.1)
    assert nonparametric_p_floor("two-group", 3, 4) == pytest.approx(2 / 35)
    assert nonparametric_p_floor("paired", 5, 0) == pytest.approx(0.0625)
    assert nonparametric_p_floor("corr", 3, 3) == 0.0


def test_p_floor_does_not_overflow_for_large_samples():
    """C(1030, 515) 는 배정도를 넘는다 — 나누려 들면 OverflowError 였다."""
    for n in (1028, 1030, 2000, 100_000):
        value = nonparametric_p_floor("two-group", n // 2, n - n // 2)
        assert 0.0 <= value <= 1.0
        assert math.isfinite(value)
    assert nonparametric_p_floor("paired", 5000, 0) == 0.0


def test_large_two_group_still_runs_the_nonparametric_axis(tmp_path):
    rows = make_rows(1100, effect=2.0, seed=5)
    path = write_csv(tmp_path / "big.csv", rows)
    analysis = analyse_path(path, **two_group())
    computed = [j for j in analysis.judged if j.result.computed]
    assert any(j.axes.test == "비모수" for j in computed)
    assert analysis.undecidable_reason == ""


def test_p_floor_boundary_uses_greater_or_equal(tmp_path):
    """유의 판정이 `p < alpha` 이므로 floor == alpha 도 도달 불가능이다."""
    rows = [["A1", "active", 9, 1.0, 1, 1], ["A2", "active", 9, 1.5, 1, 1],
            ["A3", "active", 9, 2.0, 1, 1], ["B1", "sham", 9, 90.0, 1, 1],
            ["B2", "sham", 9, 91.5, 1, 1], ["B3", "sham", 9, 92.0, 1, 1]]
    path = write_csv(tmp_path / "sep.csv", rows)
    analysis = analyse_path(path, alpha=0.10, **two_group())
    assert nonparametric_p_floor("two-group", 3, 3) == pytest.approx(0.10)
    assert analysis.verdict.grade != "취약", "완전 분리 자료가 취약일 수 없다"
    skipped = [j for j in analysis.judged
               if not j.result.computed and j.axes.test == "비모수"]
    assert skipped and all("검정력 부족" in j.result.skip_reason for j in skipped)


def test_p_floor_is_not_applied_when_the_exact_branch_is_not_taken(tmp_path):
    """동점·0차이가 있으면 정규근사를 타므로 하한이 성립하지 않는다."""
    rows = []
    for i in range(1, 7):
        rows.append(["S%d" % i, "active", 10, 10 + (i % 3), 30, 80])
    path = write_csv(tmp_path / "ties.csv", rows)
    analysis = analyse_path(path, design="paired", pre="isi_baseline",
                            post="isi_week4")
    skipped = [j for j in analysis.judged
               if not j.result.computed and "검정력 부족" in j.result.skip_reason]
    assert not skipped, "동점 자료에 정확분포 하한을 적용하면 안 된다"


def test_p_floor_is_not_applied_to_covariate_scenarios(tmp_path):
    """공변량이 있으면 비모수 축은 Quade 라 Mann–Whitney 하한과 무관하다."""
    noise = [0.4, -1.1, 2.3, -0.7, 1.6, -2.2, 0.9, -1.5]
    rows = [["S%d" % i, "active" if i % 2 else "sham",
             20 + i, round(10 + i + noise[i - 1], 2), 30, 80]
            for i in range(1, 9)]
    path = write_csv(tmp_path / "cov.csv", rows)
    analysis = analyse_path(path, design="two-group", group="arm",
                            value="isi_week4", covariate="isi_baseline")
    nonparametric = [j for j in analysis.judged if j.axes.test == "비모수"]
    assert any(j.result.computed for j in nonparametric)
    assert all("검정력 부족" not in j.result.skip_reason for j in nonparametric)


# ---------------------------------------------------- NFC / NFD 정규화


def test_nfd_and_nfc_group_labels_are_the_same_group(tmp_path):
    """macOS 한글 CSV 는 NFD 다 — 정규화하지 않으면 군이 자기 자신과 비교된다."""
    import unicodedata
    nfc = "치료군"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    rows = []
    for i in range(1, 15):
        rows.append(["S%02d" % i, nfc if i % 2 else nfd, 20, 10 + i, 30, 80])
    path = write_csv(tmp_path / "nfd.csv", rows)
    from robustcheck.dataio import InputError
    with pytest.raises(InputError) as exc:
        analyse_path(path, **two_group())
    assert "2개여야" in str(exc.value)


def test_nfd_and_nfc_subject_ids_collide_as_duplicates(tmp_path):
    import unicodedata
    from robustcheck.dataio import InputError
    rows = make_rows(8)
    rows[0][0] = "김철수"
    rows[1][0] = unicodedata.normalize("NFD", "김철수")
    path = write_csv(tmp_path / "nfd.csv", rows)
    with pytest.raises(InputError) as exc:
        analyse_path(path, **two_group())
    assert "중복" in str(exc.value)


def test_zero_width_characters_do_not_split_an_id(tmp_path):
    from robustcheck.dataio import InputError
    rows = make_rows(8)
    rows[0][0] = "S001"
    rows[1][0] = "S​001"
    path = write_csv(tmp_path / "zw.csv", rows)
    with pytest.raises(InputError):
        analyse_path(path, **two_group())


# ------------------------------------------ 숫자로 못 읽은 값의 자백


def test_unreadable_values_are_confessed_in_the_report(tmp_path):
    """유럽식 소수점이 섞인 열이 조용히 반토막 나던 사고."""
    rows = make_rows(24)
    for index, row in enumerate(rows):
        if index % 3:
            row[3] = "13,4"          # 유럽식 소수점 → 숫자로 읽지 않는다
    path = write_csv(tmp_path / "euro.csv", rows)
    analysis = analyse_path(path, **two_group())
    assert analysis.dataset.unreadable_cells.get("isi_week4") == 16
    text = render_report(analysis)
    assert "숫자로 읽지 못한 칸 16개" in text


def test_ordinary_missing_values_are_not_reported_as_unreadable(tmp_path):
    rows = make_rows(12)
    rows[0][3] = ""
    rows[1][3] = "NA"
    path = write_csv(tmp_path / "na.csv", rows)
    analysis = analyse_path(path, **two_group())
    assert analysis.dataset.unreadable_cells == {}


# ------------------------------- 뒤집힘 0건일 때 방향을 단정하지 않는다


def _sign_shift_fixture(tmp_path):
    """양쪽 다 비유의인데 효과크기 부호가 뒤집히는 자료를 만든다."""
    import random
    rng = random.Random(20260821)
    for attempt in range(4000):
        rows = []
        for i in range(1, 21):
            active = i % 2 == 1
            rows.append(["S%02d" % i, "active" if active else "sham",
                         round(rng.gauss(19, 3), 2), round(rng.gauss(12, 4), 2),
                         round(rng.gauss(30, 6), 2), round(rng.gauss(82, 4), 2)])
        rows[0][3] = round(float(rows[0][3]) + 22, 2)
        path = write_csv(tmp_path / ("s%d.csv" % attempt), rows)
        analysis = analyse_path(path, **two_group())
        if analysis.silent_effect_shifts and not analysis.flipped:
            return analysis
    return None


def test_no_flip_report_never_claims_the_direction_held(tmp_path):
    analysis = _sign_shift_fixture(tmp_path)
    if analysis is None:
        pytest.skip("부호 이동 픽스처를 찾지 못했다")
    text = render_report(analysis)
    assert "유의성 판정" in text
    assert "효과크기가 크게 달라진 명세" in text
    draft = render_markdown(analysis)
    assert "검정 방향과 유의성이" not in draft
    assert "the direction and statistical significance" not in draft


def test_no_flip_wording_is_about_significance_only(two_group_analysis):
    """부호 이동조차 없는 깨끗한 경우에도 '방향'을 단정하지 않는다."""
    assert two_group_analysis.silent_effect_shifts == []
    text = render_report(two_group_analysis)
    assert "**유의성 판정**이 기준선과 같았다" in text
    assert "검정 방향과" not in render_markdown(two_group_analysis)


# ------------------------------------ ② 접기가 다른 경로로 새지 않는다


def _nonsignificant_with_two(tmp_path):
    import random
    rng = random.Random(7)
    for attempt in range(4000):
        rows = []
        for i in range(1, 25):
            active = i % 2 == 1
            rows.append(["S%02d" % i, "active" if active else "sham",
                         round(rng.gauss(19, 3), 2), round(rng.gauss(12, 5), 2),
                         round(rng.gauss(30, 6), 2), round(rng.gauss(82, 4), 2)])
        path = write_csv(tmp_path / ("c%d.csv" % attempt), rows)
        analysis = analyse_path(path, **two_group())
        if (analysis.baseline.p >= 0.05
                and any(f.code == "②" for j in analysis.flipped for f in j.flips)):
            return analysis
    return None


def test_collapsed_two_never_leaks_p_values(tmp_path):
    analysis = _nonsignificant_with_two(tmp_path)
    if analysis is None:
        pytest.skip("② 픽스처를 찾지 못했다")
    text = render_report(analysis)
    assert "개수만 알린다" in text
    for judged in analysis.flipped:
        if any(f.code == "②" for f in judged.flips):
            assert "p %s → %s" % (
                ("%.3f" % analysis.baseline.p).lstrip("0"),
                ("%.3f" % judged.result.p).lstrip("0")) not in text


def test_collapsed_two_still_lands_in_the_csv(tmp_path):
    analysis = _nonsignificant_with_two(tmp_path)
    if analysis is None:
        pytest.skip("② 픽스처를 찾지 못했다")
    rows = rows_of(render_scenarios_csv(analysis))[1:]
    codes = [r[SCENARIO_HEADER.index("뒤집힘코드")] for r in rows]
    assert any("②" in c for c in codes), "값은 CSV 에는 반드시 남아야 한다"


# ------------------------------- 척도가 다르면 Δ효과크기를 찍지 않는다


def test_delta_effect_is_blank_across_the_log_axis(two_group_analysis):
    rows = rows_of(render_scenarios_csv(two_group_analysis))[1:]
    log_index = SCENARIO_HEADER.index("로그변환")
    delta_index = SCENARIO_HEADER.index("Δ효과크기")
    logged = [r for r in rows if r[log_index] == "적용" and r[
        SCENARIO_HEADER.index("계산됨")] == "Y"]
    assert logged
    assert all(r[delta_index] == "척도다름" for r in logged)


def test_delta_effect_is_present_within_the_same_scale(two_group_analysis):
    rows = rows_of(render_scenarios_csv(two_group_analysis))[1:]
    log_index = SCENARIO_HEADER.index("로그변환")
    delta_index = SCENARIO_HEADER.index("Δ효과크기")
    plain = [r for r in rows if r[log_index] == "미적용"
             and r[SCENARIO_HEADER.index("계산됨")] == "Y"
             and not r[SCENARIO_HEADER.index("기준선여부")]]
    assert plain
    assert all(r[delta_index] not in ("", "척도다름") for r in plain)


# --------------------------------------------- 산출물은 전부 아니면 전무


def test_a_failing_write_leaves_no_partial_output(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET", encoding="utf-8")
    os.symlink(victim, out / OUT_SUBJECTS)
    code = main([os.path.join(EXAMPLES, "취약_예제.csv"), "--design", "two-group",
                 "--group", "arm", "--value", "isi_week4", "--out-dir", str(out)])
    assert code == 2
    assert victim.read_text(encoding="utf-8") == "SECRET"
    assert sorted(os.listdir(out)) == [OUT_SUBJECTS]


def test_stdout_and_exit_code_agree_on_failure(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET", encoding="utf-8")
    os.symlink(victim, out / OUT_SCENARIOS)
    code = main([os.path.join(EXAMPLES, "취약_예제.csv"), "--design", "two-group",
                 "--group", "arm", "--value", "isi_week4", "--out-dir", str(out)])
    captured = capsys.readouterr()
    assert code == 2
    assert "종료코드" not in captured.out


def test_no_temp_files_survive_a_successful_run(tmp_path):
    out = tmp_path / "out"
    main([os.path.join(EXAMPLES, "견고_예제.csv"), "--design", "two-group",
          "--group", "arm", "--value", "isi_week4", "--out-dir", str(out)])
    assert not [n for n in os.listdir(out) if n.endswith(".rc-tmp")]


# ------------------------------------------- 보이지 않는 문자 방어


@pytest.mark.parametrize("prefix", [
    "‎", "‏", "⁦", "⁩", "᠎", "\x1b", "​",
    "‮", " ", "\t",
])
def test_invisible_prefixes_do_not_smuggle_a_formula(prefix):
    assert csv_safe(prefix + "=1+1").startswith("'")


def test_ordinary_values_are_still_untouched():
    assert csv_safe("-0.71") == "-0.71"
    assert csv_safe("이상치=±3SD") == "이상치=±3SD"
    assert csv_safe("S001") == "S001"


# ------------------------------------------------- --no-files 정직성


def test_report_does_not_point_at_files_that_were_not_written(capsys):
    main([os.path.join(EXAMPLES, "취약_예제.csv"), "--design", "two-group",
          "--group", "arm", "--value", "isi_week4", "--no-files"])
    out = capsys.readouterr().out
    if "%s 참조" % OUT_SCENARIOS in out:
        raise AssertionError("--no-files 인데 CSV 를 참조하라고 안내한다")


def test_report_points_at_files_when_they_are_written(tmp_path, capsys):
    main([os.path.join(EXAMPLES, "취약_예제.csv"), "--design", "two-group",
          "--group", "arm", "--value", "isi_week4",
          "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert OUT_SCENARIOS in out


# --------------------------------------------- 판정불가 사유가 실행 가능


def test_undecidable_reason_keeps_the_actionable_detail(tmp_path):
    rows = make_rows(14)
    for index, row in enumerate(rows):
        row[3] = 1e-250 * (1 + index)
    path = write_csv(tmp_path / "tiny.csv", rows)
    analysis = analyse_path(path, **two_group())
    assert "단위를 바꿔" in analysis.undecidable_reason
