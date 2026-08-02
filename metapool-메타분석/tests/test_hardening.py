"""적대적 검토(HARDENING.md 1라운드)에서 발견된 결함들의 회귀 시험.

각 시험은 "이 입력이 예전에 트레이스백/조용한 오답을 냈다"는 사실을 고정한다.
"""

import json
import math
import os

import pytest

from metapool.analysis import run_analysis
from metapool.cli import main
from metapool.distributions import t_ppf
from metapool.effects import Study
from metapool.meta import heterogeneity, random_effects
from metapool.report import render_text, sentences

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def write(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


# --------------------------------------------------------------------------
# 수치 안정성
# --------------------------------------------------------------------------


def test_cochran_q_survives_large_offsets():
    """y가 크고 흩어짐이 작으면 Q = Σwy² − (Σwy)²/Σw 는 자리수 소실로 0이 된다."""
    base = [(0.1, 0.01), (0.3, 0.01), (0.2, 0.01)]
    ref = heterogeneity([Study("s%d" % i, y, v) for i, (y, v) in enumerate(base)])
    for offset in (1e3, 1e6, 1e8):
        shifted = heterogeneity(
            [Study("s%d" % i, y + offset, v) for i, (y, v) in enumerate(base)]
        )
        # 상수를 더해도 이질성은 변하지 않아야 한다
        assert shifted.q == pytest.approx(ref.q, rel=1e-6), "offset %g" % offset
        assert shifted.i2 == pytest.approx(ref.i2, abs=1e-4)


def test_t_ppf_does_not_silently_clamp_at_extremes():
    """고정 구간 [-1e4, 1e4] 이분법은 극단 분위수를 조용히 잘랐다."""
    assert t_ppf(0.999995, 1) == pytest.approx(63661.97723110508, rel=1e-9)
    assert t_ppf(1e-6, 1) == pytest.approx(-318309.8861827435, rel=1e-9)


# --------------------------------------------------------------------------
# 퇴화(degenerate) 자료: 모든 효과크기가 동일
# --------------------------------------------------------------------------


IDENTICAL = [Study("A", 0.5, 0.01), Study("B", 0.5, 0.04), Study("C", 0.5, 0.09)]


def test_identical_effects_do_not_produce_zero_width_ci():
    p = random_effects(IDENTICAL, knapp_hartung=True)
    assert p.hk_degenerate is True
    assert p.ci_high > p.ci_low          # 폭 0 구간을 만들면 안 된다
    assert math.isfinite(p.stat)         # inf 통계량을 만들면 안 된다
    assert p.p > 0.0                     # p = 0 을 단언하면 안 된다
    assert p.se == pytest.approx(p.se_model, rel=1e-15)


def test_identical_effects_json_does_not_crash(tmp_path, capsys):
    path = write(tmp_path, "same.csv", "study,effect,se\nA,0.5,0.1\nB,0.5,0.2\nC,0.5,0.3\n")
    assert main([path, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)      # inf 가 섞이면 여기서 터진다
    assert data["random_effects"]["ci_method"] == "z"


def test_identical_effects_warn_the_user(tmp_path, capsys):
    path = write(tmp_path, "same.csv", "study,effect,se\nA,0.5,0.1\nB,0.5,0.2\nC,0.5,0.3\n")
    assert main([path]) == 0
    assert "Hartung–Knapp 보정 분산이 0" in capsys.readouterr().out


def test_hk_narrower_than_model_ci_is_flagged(capsys):
    """이질성이 없을 때 HK 구간이 고정효과 구간보다 좁아지는 현상을 알려야 한다."""
    assert main([os.path.join(EXAMPLES, "adherence_or.csv")]) == 0
    out = capsys.readouterr().out
    assert "Hartung–Knapp 신뢰구간이 모형기반 구간보다 좁아졌습니다" in out


# --------------------------------------------------------------------------
# 신뢰수준 배관
# --------------------------------------------------------------------------


def test_prediction_interval_label_matches_requested_conf(capsys):
    assert main([os.path.join(EXAMPLES, "published_effects.csv"), "--conf", "0.99"]) == 0
    out = capsys.readouterr().out
    assert "99% 예측구간" in out
    assert "95% 예측구간" not in out


def test_conf_above_limit_is_rejected():
    with pytest.raises(SystemExit) as exc:
        main([os.path.join(EXAMPLES, "published_effects.csv"), "--conf", "0.9999999"])
    assert exc.value.code == 2


def test_input_conf_flag_changes_weights(tmp_path, capsys):
    path = write(
        tmp_path, "ci.csv",
        "study,effect,ci_low,ci_high\nA,0.50,0.30,0.70\nB,0.60,0.36,0.84\nC,0.40,0.10,0.70\n",
    )
    assert main([path, "--json"]) == 0
    se95 = json.loads(capsys.readouterr().out)["studies"][0]["se"]
    assert main([path, "--conf", "0.99", "--json"]) == 0
    still95 = json.loads(capsys.readouterr().out)["studies"][0]["se"]
    assert still95 == pytest.approx(se95, rel=1e-15)   # --conf 는 입력 해석을 건드리면 안 된다
    assert main([path, "--input-conf", "0.90", "--json"]) == 0
    se90 = json.loads(capsys.readouterr().out)["studies"][0]["se"]
    assert se90 > se95


# --------------------------------------------------------------------------
# 파일 안전
# --------------------------------------------------------------------------


def test_out_refuses_to_overwrite_the_input_file(tmp_path, capsys):
    path = write(tmp_path, "data.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\nC,0.4,0.15\n")
    before = open(path, encoding="utf-8").read()
    assert main([path, "-o", path]) == 1
    assert "원자료를 덮어쓸 뻔했습니다" in capsys.readouterr().err
    assert open(path, encoding="utf-8").read() == before   # 원본이 그대로여야 한다


def test_out_warns_before_overwriting_an_existing_file(tmp_path, capsys):
    path = write(tmp_path, "data.csv", "study,effect,se\nA,0.5,0.1\nB,0.3,0.2\nC,0.4,0.15\n")
    target = str(tmp_path / "old.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("기존 내용")
    assert main([path, "-o", target]) == 0
    assert "기존 파일을 덮어씁니다" in capsys.readouterr().err


def test_device_and_pipe_paths_are_refused(capsys):
    assert main(["/dev/zero"]) == 1
    assert "일반 파일이 아닙니다" in capsys.readouterr().err


# --------------------------------------------------------------------------
# 극단값 · 잘못된 자료
# --------------------------------------------------------------------------


def test_huge_effects_do_not_crash_any_output_format(tmp_path, capsys):
    path = write(
        tmp_path, "huge.csv",
        "study,effect,se\nA,1e300,0.0000001\nB,1,0.5\nC,2,0.5\n",
    )
    for flags in ([], ["--md"], ["--json"], ["--tau2", "PM"]):
        code = main([path] + flags)
        capsys.readouterr()
        assert code in (0, 1), "flags=%r" % (flags,)


def test_overflowing_log_measure_does_not_crash(tmp_path, capsys):
    path = write(
        tmp_path, "zero.csv",
        "study,events1,n1,events2,n2\nA,0,50,10,50\nB,5,40,9,42\nC,3,30,7,33\n",
    )
    assert main([path, "--measure", "or", "--cc", "1e-6"]) == 0
    capsys.readouterr()


def test_cc_zero_with_zero_cell_gives_korean_message_not_traceback(tmp_path, capsys):
    path = write(
        tmp_path, "zero.csv",
        "study,events1,n1,events2,n2\nA,0,50,10,50\nB,5,40,9,42\nC,3,30,7,33\n",
    )
    assert main([path, "--measure", "or", "--cc", "0"]) == 0
    out = capsys.readouterr().out
    assert "연속성 보정이 꺼져 있습니다" in out
    assert "연구 수 (k) : 2" in out                  # 나머지 두 편은 정상 분석


def test_european_decimal_comma_is_not_silently_multiplied(tmp_path, capsys):
    path = write(tmp_path, "eu.csv", "study;effect;se\nA;0,5;0,1\nB;0,6;0,12\nC;0,4;0,15\n")
    assert main([path]) == 1                        # 조용히 10배 틀리느니 실패해야 한다
    assert "유효한 연구가 한 편도 없습니다" in capsys.readouterr().err


def test_control_characters_cannot_corrupt_the_report(tmp_path, capsys):
    path = write(
        tmp_path, "esc.csv",
        "study,effect,se\n\x1b[31mRED\x1b[0m,0.5,0.1\nA\rOVERWRITE,0.3,0.2\nB,0.4,0.15\n",
    )
    assert main([path]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out


def test_newline_in_label_does_not_split_the_forest_row(tmp_path, capsys):
    path = write(
        tmp_path, "nl.csv",
        'study,effect,se\n"Kim\n2021",0.5,0.1\n"Lee, J. 2022",0.6,0.12\n"Park 2023",0.4,0.15\n',
    )
    assert main([path]) == 0
    out = capsys.readouterr().out
    assert "Kim 2021" in out                        # 한 줄로 합쳐져야 한다


def test_utf16_file_gets_a_useful_message_or_parses(tmp_path, capsys):
    path = write(tmp_path, "u16.csv", "study,effect,se\n김2021,0.5,0.1\n이2022,0.3,0.2\n김2023,0.4,0.1\n",
                 encoding="utf-16")
    assert main([path, "--no-forest"]) == 0
    assert "김2021" in capsys.readouterr().out


def test_latin1_fallback_warns_about_possible_mojibake(tmp_path, capsys):
    path = tmp_path / "bad.csv"
    good = "study,effect,se\n김2021,0.5,0.1\n이2022,0.3,0.2\n박2023,0.4,0.1\n".encode("utf-8")
    path.write_bytes(good[:20] + b"\xc3" + good[20:])
    assert main([str(path)]) == 0
    assert "latin-1로 읽었습니다" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 성능 상한
# --------------------------------------------------------------------------


def test_leave_one_out_is_skipped_for_very_large_k(tmp_path, capsys):
    rows = "\n".join("S%d,%.4f,0.1" % (i, 0.5 + (i % 13) * 0.01) for i in range(400))
    path = write(tmp_path, "big.csv", "study,effect,se\n" + rows + "\n")
    assert main([path, "--no-forest"]) == 0
    out = capsys.readouterr().out
    assert "민감도) 분석을 건너뛰었습니다" in out
    assert "── 민감도" not in out


# --------------------------------------------------------------------------
# 논문 문장의 정직성
# --------------------------------------------------------------------------


def _make(records, **kw):
    return run_analysis(
        [dict(r, __row__=str(i + 2)) for i, r in enumerate(records)], "generic", **kw
    )


def test_no_pooled_paragraph_below_three_studies():
    ko, en = sentences(_make([{"study": "A", "effect": "0.5", "se": "0.1"}]))
    assert "1편뿐이라" in ko
    assert "no pooled results paragraph" in en
    assert "statistically significant" not in en   # 거짓 주장 금지


def test_sentence_omits_underpowered_egger_numbers():
    a = _make([
        {"study": "A", "effect": "0.50", "se": "0.10"},
        {"study": "B", "effect": "0.30", "se": "0.20"},
        {"study": "C", "effect": "0.70", "se": "0.15"},
    ])
    ko, en = sentences(a)
    assert a.egger is not None and a.egger.k < 10
    assert "형식적으로 평가하지 않았다" in ko
    assert "was not formally assessed" in en
    assert "절편은" not in ko                       # 못 믿을 수치를 원고에 넣지 않는다


def test_sentence_reports_egger_numbers_when_k_is_at_least_ten():
    records = [
        {"study": "S%d" % i, "effect": "%.2f" % (0.3 + 0.05 * (i % 4)), "se": "%.2f" % (0.1 + 0.02 * i)}
        for i in range(12)
    ]
    a = _make(records)
    ko, en = sentences(a)
    assert a.egger.k == 12
    assert "Egger 회귀 비대칭 검정에서 절편은" in ko
    assert "Egger's regression intercept was" in en


def test_sentence_includes_subgroup_and_sensitivity():
    a = _make([
        {"study": "A", "effect": "0.50", "se": "0.10", "subgroup": "성인"},
        {"study": "B", "effect": "0.55", "se": "0.12", "subgroup": "성인"},
        {"study": "C", "effect": "0.10", "se": "0.11", "subgroup": "노인"},
        {"study": "D", "effect": "0.05", "se": "0.13", "subgroup": "노인"},
    ])
    ko, en = sentences(a)
    assert "하위군 분석에서" in ko and "Q_between" in ko
    assert "민감도 분석에서" in ko
    assert "subgroup analysis" in en and "Leave-one-out" in en


def test_outcome_name_is_woven_into_the_sentence():
    records = [
        {"study": "A", "effect": "0.50", "se": "0.10"},
        {"study": "B", "effect": "0.30", "se": "0.20"},
        {"study": "C", "effect": "0.70", "se": "0.15"},
    ]
    ko, en = sentences(_make(records, outcome="ISI 총점"))
    assert "ISI 총점" in ko and "ISI 총점" in en
    ko2, en2 = sentences(_make(records))
    assert "[결과변수명" in ko2 and "[insert the outcome" in en2


def test_log_scale_caveat_is_carried_into_the_sentence():
    a = run_analysis(
        [
            {"study": "A", "events1": "20", "n1": "50", "events2": "10", "n2": "50", "__row__": "2"},
            {"study": "B", "events1": "25", "n1": "60", "events2": "15", "n2": "60", "__row__": "3"},
            {"study": "C", "events1": "30", "n1": "70", "events2": "18", "n2": "70", "__row__": "4"},
        ],
        "or",
    )
    ko, en = sentences(a)
    assert "로그 척도" in ko and "log scale" in en


# --------------------------------------------------------------------------
# 옵션이 실제로 숫자를 바꾸는지 (문자열만 확인하던 시험 보강)
# --------------------------------------------------------------------------


def test_paule_mandel_actually_changes_tau2(capsys):
    path = os.path.join(EXAMPLES, "breathing_isi_smd.csv")
    assert main([path, "--json"]) == 0
    dl = json.loads(capsys.readouterr().out)["random_effects"]["tau2"]
    assert main([path, "--tau2", "PM", "--json"]) == 0
    pm = json.loads(capsys.readouterr().out)["random_effects"]["tau2"]
    assert pm != dl
    # scipy.optimize.brentq 로 추정방정식을 독립적으로 풀어 얻은 값
    assert pm == pytest.approx(0.02928589434531481, rel=1e-8)
    assert dl == pytest.approx(0.026489115548156122, rel=1e-8)


def test_forced_rr_and_rd_produce_the_hand_computed_first_study(capsys):
    path = os.path.join(EXAMPLES, "adherence_or.csv")   # Trial A: 42/80 vs 30/80
    assert main([path, "--measure", "rr", "--json"]) == 0
    rr = json.loads(capsys.readouterr().out)["studies"][0]
    assert rr["effect"] == pytest.approx(math.log((42 / 80) / (30 / 80)), rel=1e-12)
    assert main([path, "--measure", "rd", "--json"]) == 0
    rd = json.loads(capsys.readouterr().out)["studies"][0]
    assert rd["effect"] == pytest.approx(42 / 80 - 30 / 80, rel=1e-12)


def test_measure_md_end_to_end(capsys):
    path = os.path.join(EXAMPLES, "breathing_isi_smd.csv")   # Kim: -6.20 vs -3.10
    assert main([path, "--measure", "md", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)["studies"][0]
    assert first["effect"] == pytest.approx(-6.20 - (-3.10), rel=1e-12)
    assert first["variance"] == pytest.approx(4.10 ** 2 / 32 + 4.40 ** 2 / 31, rel=1e-12)


def test_sort_label_and_weight(capsys):
    path = os.path.join(EXAMPLES, "published_effects.csv")
    assert main([path, "--sort", "label", "--json"]) == 0
    labels = [s["label"] for s in json.loads(capsys.readouterr().out)["studies"]]
    assert labels == sorted(labels)
    assert main([path, "--sort", "weight", "--json"]) == 0
    ses = [s["se"] for s in json.loads(capsys.readouterr().out)["studies"]]
    assert ses == sorted(ses)          # 가중치 큰(=SE 작은) 순


def test_label_and_subgroup_flags_end_to_end(tmp_path, capsys):
    path = write(
        tmp_path, "cols.csv",
        "code,title,effect,se,site\n1,A연구,0.5,0.1,서울\n2,B연구,0.3,0.2,부산\n3,C연구,0.4,0.15,서울\n",
    )
    assert main([path, "--label", "title", "--subgroup", "site", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [s["label"] for s in data["studies"]] == ["A연구", "B연구", "C연구"]
    assert {s["subgroup"] for s in data["studies"]} == {"서울", "부산"}


def test_markdown_includes_warnings_section(tmp_path, capsys):
    path = write(
        tmp_path, "warn.csv",
        "study,effect,se\nA,0.5,0.1\nB,,0.2\nC,0.4,0.15\nD,0.3,0.2\n",
    )
    assert main([path, "--md"]) == 0
    out = capsys.readouterr().out
    assert "## 경고" in out and "제외" in out


def test_render_text_matches_json_for_the_headline_number(capsys):
    path = os.path.join(EXAMPLES, "published_effects.csv")
    assert main([path, "--json"]) == 0
    est = json.loads(capsys.readouterr().out)["random_effects"]["estimate"]
    a = run_analysis(
        [
            {"study": "Anderson 2018", "effect": "0.42", "se": "0.15", "subgroup": "adult", "__row__": "2"},
            {"study": "Brown 2019", "effect": "0.31", "se": "0.22", "subgroup": "adult", "__row__": "3"},
            {"study": "Chen 2020", "effect": "0.68", "se": "0.19", "subgroup": "older", "__row__": "4"},
            {"study": "Davis 2021", "effect": "0.12", "se": "0.28", "subgroup": "adult", "__row__": "5"},
            {"study": "Evans 2022", "effect": "0.55", "se": "0.13", "subgroup": "older", "__row__": "6"},
            {"study": "Foster 2023", "effect": "0.39", "se": "0.17", "subgroup": "older", "__row__": "7"},
        ],
        "generic",
    )
    assert a.random.estimate == pytest.approx(est, rel=1e-12)
    assert ("%.3f" % est) in render_text(a)
