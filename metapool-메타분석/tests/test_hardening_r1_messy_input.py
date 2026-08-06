"""R1 적대적 리뷰(엣지케이스)에서 재현된 결함의 회귀 시험.

전부 "조용히 틀린 답을 내놓던" 사례다 — 크래시가 아니라서 더 위험했다.
"""

import math

import pytest

from metapool.analysis import run_analysis
from metapool.cli import main
from metapool.effects import EffectError, is_missing, log_risk_ratio
from metapool.io_csv import TableError, read_table
from metapool.report import render_text


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------
# 결측 표기(NA, -, n/a …)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["NA", "na", "n/a", "N.A.", "null", "None", ".", "-", "--", "?", "", "   "])
def test_missing_tokens_are_recognised(token):
    assert is_missing(token) is True


@pytest.mark.parametrize("token", ["0", "0.0", "-1", "1e-3", "NAN2", "미상"])
def test_real_values_are_not_treated_as_missing(token):
    assert is_missing(token) is False


def test_se_column_with_na_falls_back_to_the_confidence_interval(tmp_path):
    """se 가 'NA' 인데 CI 가 있으면 연구를 버리지 말고 CI 에서 SE 를 역산해야 한다."""
    path = write(
        tmp_path, "mixed.csv",
        "study,effect,se,ci_low,ci_high\n"
        "A,0.5,0.1,0.30,0.70\n"
        "B,0.3,NA,0.10,0.50\n"
        "C,0.4,-,0.20,0.60\n"
        "D,0.45,,0.25,0.65\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    assert len(a.studies) == 4, [w for w in a.warnings]
    assert not any("제외" in w for w in a.warnings)
    # CI 폭이 같은 B·C·D 는 SE 도 같아야 한다
    ses = {round(s.sei, 12) for s in a.studies[1:]}
    assert len(ses) == 1


def test_optional_n_column_with_na_does_not_discard_the_study(tmp_path):
    """n 은 총 표본수 표시용 선택 열이다 — 여기 결측이 있다고 효과크기를 버리면 안 된다."""
    path = write(
        tmp_path, "n_na.csv",
        "study,effect,se,n\nA,0.5,0.1,100\nB,0.3,0.2,NA\nC,0.4,0.15,n/a\nD,0.45,0.12,150\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    assert len(a.studies) == 4
    assert a.total_n is None  # 일부만 알면 합계를 만들지 않는다 (기존 규칙 유지)


def test_unreadable_n_value_is_warned_but_keeps_the_study(tmp_path):
    path = write(tmp_path, "n_bad.csv", "study,effect,se,n\nA,0.5,0.1,100\nB,0.3,0.2,많음\nC,0.4,0.15,120\n")
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    assert len(a.studies) == 3
    assert any("표본수(n) 열을 읽지 못해" in w for w in a.warnings)


def test_missing_tokens_in_subgroup_do_not_become_subgroup_levels(tmp_path):
    """'NA' 와 '-' 가 진짜 하위군이 되면 Q_between 이 쓰레기 수준 위에서 계산된다."""
    path = write(
        tmp_path, "sg.csv",
        "study,effect,se,subgroup\nA,0.5,0.1,NA\nB,0.3,0.2,adult\nC,0.4,0.15,-\nD,0.45,0.12,adult\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    names = {r.name for r in a.subgroups}
    assert "NA" not in names and "-" not in names
    assert names == {"adult", "(미지정)"}


# --------------------------------------------------------------------------
# --log-input 오용
# --------------------------------------------------------------------------


def test_log_input_is_rejected_for_measures_that_are_not_generic(tmp_path, capsys):
    """원자료 지표에 --log-input 을 붙이면 결과 전체가 지수변환돼 부호까지 뒤집혔다."""
    path = write(
        tmp_path, "bin.csv",
        "study,events1,n1,events2,n2\nA,10,100,20,100\nB,12,120,25,120\nC,8,90,18,90\n",
    )
    assert main([path, "-m", "rd", "--log-input"]) == 1
    assert "--measure generic 에서만" in capsys.readouterr().err


def test_log_input_is_ignored_by_the_library_for_non_generic_measures(tmp_path):
    """CLI 를 우회해 직접 호출해도 척도가 조용히 log 로 바뀌면 안 된다."""
    path = write(
        tmp_path, "bin.csv",
        "study,events1,n1,events2,n2\nA,10,100,20,100\nB,12,120,25,120\nC,8,90,18,90\n",
    )
    records, _, _ = read_table(path)
    plain = run_analysis(records, "rd")
    tricked = run_analysis(records, "rd", log_input=True)
    assert tricked.scale == "raw"
    assert tricked.back(tricked.random.estimate) == pytest.approx(
        plain.back(plain.random.estimate), rel=1e-12
    )
    assert tricked.random.estimate < 0  # 위험이 줄어드는 자료 — 부호가 유지돼야 한다


# --------------------------------------------------------------------------
# 신뢰수준 표기
# --------------------------------------------------------------------------


def test_conf_999_never_prints_a_hundred_percent_interval(tmp_path):
    """'100% CI' 는 (-무한, 무한) 을 뜻한다 — 원고에 그대로 붙으면 명백한 오류."""
    path = write(tmp_path, "e.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\nC,0.7,0.15\nD,0.4,0.12\n")
    records, _, _ = read_table(path)
    text = render_text(run_analysis(records, "generic", conf=0.999))
    assert "100% CI" not in text and "100% 예측구간" not in text
    assert "99.9% CI" in text


def test_conf_995_keeps_one_decimal(tmp_path):
    path = write(tmp_path, "e.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\nC,0.7,0.15\n")
    records, _, _ = read_table(path)
    assert "99.5% CI" in render_text(run_analysis(records, "generic", conf=0.995))


def test_ordinary_conf_levels_stay_integral(tmp_path):
    path = write(tmp_path, "e.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\nC,0.7,0.15\n")
    records, _, _ = read_table(path)
    text = render_text(run_analysis(records, "generic", conf=0.95))
    assert "95% CI" in text and "95.0% CI" not in text


# --------------------------------------------------------------------------
# 출판편향 블록의 일관성
# --------------------------------------------------------------------------


def test_bias_section_survives_an_undefined_egger_regression(tmp_path):
    """정밀도가 모두 같으면 Egger 는 계산 불가지만 Begg·trim-and-fill 은 유효하다."""
    path = write(
        tmp_path, "eq.csv",
        "study,effect,se\nA,0.5,0.1\nB,0.9,0.1\nC,0.2,0.1\nD,0.6,0.1\nE,0.1,0.1\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    assert a.egger is None and a.begg is not None
    text = render_text(a)
    assert "출판편향" in text
    assert "Egger 회귀: 계산할 수 없습니다" in text
    assert "Begg 순위상관" in text
    from metapool.report import render_markdown

    assert "Begg 순위상관" in render_markdown(a)


# --------------------------------------------------------------------------
# 파일 형식 · 수치 한계
# --------------------------------------------------------------------------


def test_xlsx_is_named_for_what_it_is(tmp_path):
    path = tmp_path / "book.xlsx"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 200)
    with pytest.raises(TableError) as exc:
        read_table(str(path))
    assert "엑셀 통합문서" in str(exc.value)
    assert "UTF-16" not in str(exc.value)


def test_pdf_is_named_for_what_it_is(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00" * 200)
    with pytest.raises(TableError) as exc:
        read_table(str(path))
    assert "PDF" in str(exc.value)


def test_subnormal_variance_drops_only_that_row(tmp_path):
    """가중치가 무한대가 되는 행 하나 때문에 분석 전체가 죽으면 안 된다."""
    path = write(
        tmp_path, "tiny.csv",
        "study,effect,se\nA,0.5,0.10\nB,0.3,1e-160\nC,0.7,0.15\nD,0.4,0.12\n",
    )
    records, _, _ = read_table(path)
    a = run_analysis(records, "generic")
    assert len(a.studies) == 3
    assert any("가중치가 계산 범위를" in w for w in a.warnings)
    assert math.isfinite(a.random.estimate)


def test_risk_ratio_refuses_zero_information_all_event_data():
    """두 군 모두 100% 발생이면 RR=1 은 보정이 만든 값이다 — OR·RD 와 같게 거부한다."""
    with pytest.raises(EffectError) as exc:
        log_risk_ratio(50, 50, 50, 50)
    assert "정보가 없습니다" in str(exc.value)


def test_risk_ratio_still_accepts_a_single_all_event_arm():
    yi, vi, corrected = log_risk_ratio(50, 50, 25, 50)
    assert corrected is True
    assert yi > 0 and vi > 0


def test_abcd_form_warns_when_it_overrides_explicit_sample_sizes(tmp_path):
    path = write(
        tmp_path, "cells.csv",
        "study,a,b,c,d,n1,n2\nA,10,90,20,80,999,999\nB,12,108,25,95,999,999\nC,8,82,18,72,999,999\n",
    )
    records, _, warns = read_table(path)
    assert any("a,b,c,d 형식" in w for w in warns)
    assert records[0]["n1"] == "100.0"
