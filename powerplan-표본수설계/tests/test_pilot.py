"""사전연구 CSV 읽기와 효과크기 계산 검증 — 지저분한 실제 파일까지.

기대값은 손계산(또는 정의식 재계산)으로 확인했다. 번들 예제 파일의 통계는
하드코딩해 회귀 테스트로 고정한다.
"""

import math
import os

import pytest

from powerplan.distributions import nct_cdf
from powerplan.effects import hedges_correction
from powerplan.pilot import (
    GroupStats,
    effect_from_paired,
    effect_from_two_group,
    read_paired,
    read_two_group,
)
from powerplan.validate import PowerPlanError

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")
SERENE = os.path.join(EXAMPLES, "serene_pilot.csv")
WOWFIT = os.path.join(EXAMPLES, "wowfit_pilot.csv")


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return str(path)


# --------------------------------------------------------------------------
# Welford 온라인 통계
# --------------------------------------------------------------------------
def test_group_stats_matches_direct_computation():
    values = [3.0, 1.5, -2.0, 7.25, 0.0, 4.5, 4.5]
    stats = GroupStats("g")
    for v in values:
        stats.add(v)
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    assert stats.n == 7
    assert stats.mean == pytest.approx(mean, rel=1e-15)
    assert stats.sd == pytest.approx(math.sqrt(var), rel=1e-14)
    assert stats.values_min == -2.0
    assert stats.values_max == 7.25


def test_group_stats_edge_cases():
    empty = GroupStats("e")
    assert empty.sd == 0.0
    assert empty.as_dict()["min"] is None
    one = GroupStats("o")
    one.add(5.0)
    assert one.sd == 0.0
    assert one.as_dict()["mean"] == 5.0
    same = GroupStats("s")
    for _ in range(10):
        same.add(2.5)
    assert same.sd == 0.0


def test_group_stats_numerically_stable_for_large_offsets():
    """평균이 1e9만큼 떨어져 있어도 SD가 무너지지 않는다 (naive 합공식은 깨진다)."""
    stats = GroupStats("big")
    for v in (1e9 + 4, 1e9 + 7, 1e9 + 13, 1e9 + 16):
        stats.add(v)
    assert stats.sd == pytest.approx(5.477225575051661, rel=1e-9)


# --------------------------------------------------------------------------
# 번들 예제 회귀 테스트
# --------------------------------------------------------------------------
def test_serene_example_two_group_stats():
    data = read_two_group(SERENE, "isi_week8", "arm")
    assert data["delimiter"] == ","
    assert data["group1"]["label"] == "device"
    assert data["group1"]["n"] == 17
    assert data["group1"]["missing"] == 1        # 빈 칸 하나
    assert data["group1"]["mean"] == pytest.approx(11.017647058823531, rel=1e-12)
    assert data["group1"]["sd"] == pytest.approx(5.625637218808822, rel=1e-12)
    assert data["group2"]["label"] == "sham"
    assert data["group2"]["n"] == 15
    assert data["group2"]["missing"] == 1        # 'NA' 하나
    assert data["group2"]["mean"] == pytest.approx(12.633333333333331, rel=1e-12)


def test_serene_example_effect_size_and_exact_ci():
    data = read_two_group(SERENE, "isi_week8", "arm")
    eff = effect_from_two_group(data)
    assert eff["df"] == 30
    assert eff["sd_pooled"] == pytest.approx(5.2350677780256945, rel=1e-12)
    assert eff["d"] == pytest.approx(-0.30862757523248824, rel=1e-12)
    # Hedges g = d × J(df)
    assert eff["hedges_g"] == pytest.approx(eff["d"] * hedges_correction(30), rel=1e-12)
    # 정확 CI: 양 끝에서 비중심 t의 확률이 2.5% / 97.5%
    se_unit = math.sqrt(1 / 17 + 1 / 15)
    assert nct_cdf(eff["t"], 30, eff["ci"]["high"] / se_unit) == pytest.approx(0.025, abs=1e-8)
    assert nct_cdf(eff["t"], 30, eff["ci"]["low"] / se_unit) == pytest.approx(0.975, abs=1e-8)
    # CI가 0을 포함 → 보수적 효과크기는 0 (계획 불가 신호)
    assert eff["ci"]["low"] < 0 < eff["ci"]["high"]
    assert eff["conservative_d"] == 0.0


def test_wowfit_example_paired_stats_semicolon_and_korean_headers():
    data = read_paired(WOWFIT, "훈련전_단어인지도", "훈련후_단어인지도")
    assert data["delimiter"] == ";"
    assert data["diff"]["n"] == 22
    assert data["incomplete_pairs"] == 1
    assert data["diff"]["mean"] == pytest.approx(7.386363636363635, rel=1e-12)
    assert data["diff"]["sd"] == pytest.approx(7.2352507084311055, rel=1e-12)
    eff = effect_from_paired(data)
    assert eff["dz"] == pytest.approx(1.0208856519313754, rel=1e-12)
    assert eff["t"] == pytest.approx(eff["dz"] * math.sqrt(22), rel=1e-12)
    assert eff["ci"]["low"] == pytest.approx(0.4945498519011587, rel=1e-9)
    assert eff["conservative_d"] == pytest.approx(eff["ci"]["low"], rel=1e-12)


def test_wowfit_example_group_mode_with_korean_labels():
    data = read_two_group(WOWFIT, "훈련후_단어인지도", "군")
    labels = {data["group1"]["label"], data["group2"]["label"]}
    assert labels == {"중재", "대조"}
    assert data["group1"]["n"] == 11 and data["group2"]["n"] == 11


def test_paired_difference_relationship():
    """차이의 평균 = 사후 평균 − 사전 평균 (완전한 쌍만 쓰므로 정확히 성립)."""
    data = read_paired(WOWFIT, "훈련전_단어인지도", "훈련후_단어인지도")
    assert data["diff"]["mean"] == pytest.approx(
        data["post"]["mean"] - data["pre"]["mean"], rel=1e-12)


# --------------------------------------------------------------------------
# 지저분한 입력
# --------------------------------------------------------------------------
def test_reads_tab_and_pipe_and_semicolon(tmp_path):
    for delim in ("\t", "|", ";", ","):
        text = f"v{delim}g\n1{delim}a\n2{delim}a\n5{delim}b\n7{delim}b\n"
        path = write(tmp_path, f"d{ord(delim)}.csv", text)
        data = read_two_group(path, "v", "g")
        assert data["delimiter"] == delim
        assert data["group1"]["n"] == 2


def test_reads_cp949_korean_excel_export(tmp_path):
    text = "값,군\n1,중재\n2,중재\n5,대조\n7,대조\n"
    path = write(tmp_path, "cp949.csv", text, encoding="cp949")
    data = read_two_group(path, "값", "군")
    assert data["encoding"] == "cp949"
    assert {data["group1"]["label"], data["group2"]["label"]} == {"중재", "대조"}


def test_reads_utf8_bom_and_crlf(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("v,g\r\n1,a\r\n2,a\r\n5,b\r\n9,b\r\n".encode("utf-8-sig"))
    data = read_two_group(str(path), "v", "g")
    assert data["encoding"] == "utf-8-sig"
    assert data["group2"]["mean"] == pytest.approx(7.0)


def test_column_name_matching_is_forgiving_about_case_and_space(tmp_path):
    path = write(tmp_path, "sp.csv", " Value , Arm \n1,a\n2,a\n5,b\n7,b\n")
    data = read_two_group(path, "value", "arm")
    assert data["group1"]["n"] == 2


def test_missing_tokens_are_treated_as_missing(tmp_path):
    text = ("v,g\n1,a\n,a\nNA,a\nN/A,a\nnan,a\n.,a\n-,a\n결측,a\n2,a\n"
            "5,b\n7,b\n")
    path = write(tmp_path, "miss.csv", text)
    data = read_two_group(path, "v", "g")
    assert data["group1"]["n"] == 2
    assert data["group1"]["missing"] == 7


def test_non_numeric_value_raises_with_row_number(tmp_path):
    path = write(tmp_path, "bad.csv", "v,g\n1,a\n삼,a\n5,b\n7,b\n")
    with pytest.raises(PowerPlanError, match="3행"):
        read_two_group(path, "v", "g")
    # --skip-invalid 로 무시 가능
    with pytest.raises(PowerPlanError, match="n ≥ 2"):
        read_two_group(path, "v", "g", skip_invalid=True)


def test_european_decimal_comma_is_rejected_not_silently_misread(tmp_path):
    path = write(tmp_path, "eu.csv", "v;g\n1,5;a\n2,5;a\n5,5;b\n7,5;b\n")
    with pytest.raises(PowerPlanError, match="1,5"):
        read_two_group(path, "v", "g")


def test_thousands_separator_is_accepted(tmp_path):
    path = write(tmp_path, "th.csv", 'v,g\n"1,234",a\n"2,345",a\n"5,678",b\n"7,890",b\n')
    data = read_two_group(path, "v", "g")
    assert data["group1"]["mean"] == pytest.approx((1234 + 2345) / 2)


def test_ragged_rows_are_reported(tmp_path):
    path = write(tmp_path, "ragged.csv", "v,g\n1,a\n2\n5,b\n7,b\n")
    with pytest.raises(PowerPlanError, match="열 수가 부족"):
        read_two_group(path, "v", "g")


def test_blank_lines_are_skipped(tmp_path):
    path = write(tmp_path, "blank.csv", "v,g\n1,a\n\n2,a\n   \n5,b\n7,b\n")
    data = read_two_group(path, "v", "g")
    assert data["group1"]["n"] == 2 and data["group2"]["n"] == 2


def test_three_groups_requires_explicit_selection(tmp_path):
    path = write(tmp_path, "three.csv", "v,g\n1,a\n2,a\n5,b\n7,b\n9,c\n11,c\n")
    with pytest.raises(PowerPlanError, match="--groups"):
        read_two_group(path, "v", "g")
    data = read_two_group(path, "v", "g", groups=("a", "c"))
    assert data["group1"]["label"] == "a"
    assert data["group2"]["label"] == "c"
    assert data["other_groups"] == [{"label": "b", "rows": 2}]
    with pytest.raises(PowerPlanError, match="찾을 수 없"):
        read_two_group(path, "v", "g", groups=("a", "zzz"))


def test_group_with_one_observation_is_not_usable(tmp_path):
    path = write(tmp_path, "one.csv", "v,g\n1,a\n2,a\n5,b\n")
    with pytest.raises(PowerPlanError, match="n ≥ 2"):
        read_two_group(path, "v", "g")


def test_identical_values_give_clear_error(tmp_path):
    path = write(tmp_path, "const.csv", "v,g\n3,a\n3,a\n3,b\n3,b\n")
    data = read_two_group(path, "v", "g")
    with pytest.raises(PowerPlanError, match="표준편차"):
        effect_from_two_group(data)


def test_paired_with_zero_variance_difference(tmp_path):
    path = write(tmp_path, "zero.csv", "pre,post\n1,2\n3,4\n5,6\n")
    data = read_paired(path, "pre", "post")
    assert data["diff"]["sd"] == 0.0
    with pytest.raises(PowerPlanError, match="변화량이 동일"):
        effect_from_paired(data)


def test_missing_column_lists_available_columns(tmp_path):
    path = write(tmp_path, "cols.csv", "v,g\n1,a\n2,b\n")
    with pytest.raises(PowerPlanError) as exc:
        read_two_group(path, "없는열", "g")
    assert "파일의 열" in str(exc.value) and "v" in str(exc.value)


def test_file_problems_are_reported_clearly(tmp_path):
    with pytest.raises(PowerPlanError, match="찾을 수 없"):
        read_two_group(str(tmp_path / "nope.csv"), "v", "g")
    with pytest.raises(PowerPlanError, match="폴더"):
        read_two_group(str(tmp_path), "v", "g")
    empty = write(tmp_path, "empty.csv", "")
    with pytest.raises(PowerPlanError, match="빈 파일"):
        read_two_group(empty, "v", "g")
    binary = tmp_path / "bin.csv"
    binary.write_bytes(b"PK\x03\x04\x00\x00\x00\x00excel-like")
    with pytest.raises(PowerPlanError, match="이진 파일"):
        read_two_group(str(binary), "v", "g")
    header_only = write(tmp_path, "header.csv", "v,g\n")
    with pytest.raises(PowerPlanError, match="n ≥ 2"):
        read_two_group(header_only, "v", "g")


def test_control_characters_in_labels_are_sanitised(tmp_path):
    """CSV 값이 터미널 이스케이프를 주입하지 못한다."""
    path = write(tmp_path, "esc.csv", "v,g\n1,\x1b[31mred\n2,\x1b[31mred\n5,b\n7,b\n")
    data = read_two_group(path, "v", "g")
    labels = {data["group1"]["label"], data["group2"]["label"]}
    assert "\x1b" not in "".join(labels)
    assert "[31mred" in labels


def test_very_long_label_is_truncated(tmp_path):
    long_label = "군" * 200
    path = write(tmp_path, "long.csv",
                 f"v,g\n1,{long_label}\n2,{long_label}\n5,b\n7,b\n")
    data = read_two_group(path, "v", "g")
    assert max(len(data["group1"]["label"]), len(data["group2"]["label"])) <= 60


def test_too_many_distinct_groups_raises(tmp_path):
    rows = "\n".join(f"{i},g{i}" for i in range(600))
    path = write(tmp_path, "many.csv", "v,g\n" + rows + "\n")
    with pytest.raises(PowerPlanError, match="군 열"):
        read_two_group(path, "v", "g")


def test_paired_requires_different_columns(tmp_path):
    path = write(tmp_path, "same.csv", "pre,post\n1,2\n3,4\n")
    with pytest.raises(PowerPlanError, match="같은 열"):
        read_paired(path, "pre", "pre")


def test_paired_needs_at_least_two_complete_pairs(tmp_path):
    path = write(tmp_path, "few.csv", "pre,post\n1,2\n3,\n,4\n")
    with pytest.raises(PowerPlanError, match="최소 2쌍"):
        read_paired(path, "pre", "post")


def test_large_file_is_streamed(tmp_path):
    """20만 행도 메모리 폭발 없이 처리되고 통계가 정확하다."""
    lines = ["v,g"]
    for i in range(100_000):
        lines.append(f"{i % 100},a")
        lines.append(f"{(i % 100) + 5},b")
    path = write(tmp_path, "big.csv", "\n".join(lines) + "\n")
    data = read_two_group(path, "v", "g")
    assert data["group1"]["n"] == 100_000
    assert data["group1"]["mean"] == pytest.approx(49.5, rel=1e-12)
    assert data["group2"]["mean"] == pytest.approx(54.5, rel=1e-12)
    eff = effect_from_two_group(data)
    assert eff["d"] == pytest.approx(-5.0 / data["group1"]["sd"], rel=1e-6)


def test_effect_ci_widens_with_confidence_level():
    data = read_paired(WOWFIT, "훈련전_단어인지도", "훈련후_단어인지도")
    ci95 = effect_from_paired(data, 0.95)["ci"]
    ci99 = effect_from_paired(data, 0.99)["ci"]
    assert ci99["low"] < ci95["low"] < ci95["high"] < ci99["high"]
