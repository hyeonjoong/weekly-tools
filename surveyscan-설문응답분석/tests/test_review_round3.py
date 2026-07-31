"""2026-07-31 리뷰 라운드에서 발견된 결함들의 회귀 테스트.

각 테스트는 '무엇이 어떻게 틀렸었는지'를 주석으로 남긴다 — 나중에 같은 실수를
되돌리지 않기 위해서다.
"""
import json
import math
import os

import pytest

from surveyscan import compare, report, special, stats
from surveyscan.analyze import _group_alphas, analyze
from surveyscan.cli import run
from surveyscan.config import ConfigError, SurveyConfig, _from_dict, band_index, band_label
from surveyscan.dataio import load_csv, normalize_label

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
CSV = os.path.join(ROOT, "examples", "sleep_survey.csv")
CFG = os.path.join(ROOT, "examples", "sleep_config.json")


def _write(tmp_path, text, name="d.csv", encoding="utf-8"):
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return str(p)


# ---------------------------------------------------------------- 통계 정확성

def test_pvalue_does_not_underflow_to_zero():
    """1-CDF 로 계산하면 |t|≳9 에서 p 가 정확히 0.0 이 되어 JSON에 0이 실렸다.

    참조: scipy.stats.ttest_ind(equal_var=False) p = 8.045373668041467e-114
    """
    xs = [10.0 + 0.1 * i for i in range(50)]
    ys = [50.0 + 0.1 * i for i in range(50)]
    r = compare.welch_ttest(xs, ys)
    assert r["p"] > 0.0
    assert r["p"] == pytest.approx(8.045373668041467e-114, rel=1e-9)
    # Holm 보정도 0 이 아니어야 한다.
    assert compare.holm_adjust([r["p"]])[0] > 0.0


def test_tail_helpers_match_scipy_reference():
    # 참조: 2*scipy.stats.t.sf(10, 50) = 1.6077334688335447e-13
    assert special.t_sf_two_sided(10.0, 50.0) == pytest.approx(
        1.6077334688335447e-13, rel=1e-10
    )
    # 참조: scipy.stats.f.sf(200, 2, 20) = 5.995246616608975e-14
    assert special.f_sf(200.0, 2.0, 20.0) == pytest.approx(
        5.995246616608975e-14, rel=1e-10
    )
    # 꼬리 + CDF = 1 (일관성)
    assert special.f_sf(2.0, 3.0, 7.0) + special.f_cdf(2.0, 3.0, 7.0) == pytest.approx(1.0)


def test_anova_p_does_not_underflow():
    a = [0.0, 0.1, 0.2, 0.1, 0.0]
    b = [50.0, 50.1, 50.2, 50.1, 50.0]
    c = [100.0, 100.1, 100.2, 100.1, 100.0]
    r = compare.welch_anova([a, b, c])
    assert r["p"] > 0.0 and r["p"] < 1e-15


def test_hedges_g_se_and_ci_reference():
    """g 의 SE(대표본 근사)와 양측 CI 를 값으로 고정.

    Borenstein: Vd = (n1+n2)/(n1·n2) + d²/(2(n1+n2)), SE_g = J·√Vd,
    CI = g ± z_{0.975}·SE_g. 아래 값은 그 공식으로 손계산한 값이다.
    """
    xs = [12.0, 14, 15, 9, 20, 22, 13, 11, 17, 18, 7, 25]
    ys = [10.0, 11, 13, 8, 9, 14, 12, 15, 7, 6]
    g = compare.hedges_g(xs, ys)
    n1, n2 = len(xs), len(ys)
    v1, v2 = stats.variance(xs), stats.variance(ys)
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    d = (stats.mean(xs) - stats.mean(ys)) / sp
    j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)
    se = j * math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2.0 * (n1 + n2)))
    z = 1.959963984540054
    assert g["se"] == pytest.approx(se, rel=1e-12)
    assert g["ci"][0] == pytest.approx(j * d - z * se, rel=1e-10)
    assert g["ci"][1] == pytest.approx(j * d + z * se, rel=1e-10)
    # 단측 z(1.6449)를 쓰면 폭이 좁아진다 — 양측인지 확인
    assert (g["ci"][1] - g["ci"][0]) == pytest.approx(2 * z * se, rel=1e-10)


def test_group_alphas_are_computed_per_group_not_pooled():
    """집단별 α 가 라벨을 무시하고 전체를 합쳐 계산되면(=같은 값) 점검이 무의미해진다."""
    rows = []
    for i in range(6):  # A: 문항이 서로 일치(높은 α)
        rows.append(f"A,{i},{i},{i}")
    for i in range(6):  # B: 문항이 제각각(낮은/음수 α)
        rows.append(f"B,{i},{5 - i},{(i * 3) % 6}")
    csv = "G,Q1,Q2,Q3\n" + "\n".join(rows) + "\n"
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "g.csv")
    open(path, "w", encoding="utf-8").write(csv)
    data = load_csv(path, group_column="G")
    cfg = SurveyConfig(subscales={"S": ["Q1", "Q2", "Q3"]})
    alphas = _group_alphas(data, cfg, ["Q1", "Q2", "Q3"], data.group_values)
    assert set(alphas) == {"A", "B"}
    assert alphas["A"] == pytest.approx(1.0, abs=1e-12)
    assert alphas["B"] < 0.5
    assert abs(alphas["A"] - alphas["B"]) > 0.3
    # 손계산 대조: A 집단만으로 Cronbach α
    cols = [[float(i) for i in range(6)] for _ in range(3)]
    assert alphas["A"] == pytest.approx(stats.cronbach_alpha(cols))


def test_group_alpha_none_when_group_has_one_complete_row(tmp_path):
    path = _write(tmp_path, "G,Q1,Q2\nA,1,2\nA,,2\nB,3,4\nB,4,5\n")
    data = load_csv(path, group_column="G")
    cfg = SurveyConfig(subscales={"S": ["Q1", "Q2"]})
    alphas = _group_alphas(data, cfg, ["Q1", "Q2"], data.group_values)
    assert alphas["A"] is None  # 완전응답 1명 → 산출불가(0.0 이 아님)
    assert alphas["B"] is not None


# ------------------------------------------------------------- 심각도 구간

def test_duplicate_band_labels_are_not_double_counted(tmp_path):
    """라벨로 집계하면 같은 라벨의 두 구간이 서로의 인원을 합산해 100%를 넘겼다."""
    path = _write(tmp_path, "A,B\n0,0\n0,1\n2,2\n3,4\n4,4\n")
    cfg = SurveyConfig(
        subscales={"S": ["A", "B"]}, scale_min=0, scale_max=4, score_method="sum",
        severity_bands={"S": [(0.0, 2.0, "동일"), (3.0, 5.0, "중간"), (6.0, 8.0, "동일")]},
    )
    res = analyze(load_csv(path), cfg)
    bands = res["subscales"][0]["bands"]
    assert [b["n"] for b in bands] == [2, 1, 2]
    assert sum(b["pct"] for b in bands) == pytest.approx(100.0)


def test_band_pct_denominator_is_scored_respondents(tmp_path):
    """분모는 '점수가 산출된 인원'이어야 한다(전체 응답자 아님)."""
    # 4번째 응답자는 절반만 응답 → min_valid_ratio 0.75 미만이라 점수 없음
    path = _write(tmp_path, "A,B\n1,1\n2,2\n3,3\n,3\n")
    cfg = SurveyConfig(
        subscales={"S": ["A", "B"]}, scale_min=0, scale_max=4, score_method="sum",
        min_valid_ratio=0.75,
        severity_bands={"S": [(0.0, 8.0, "전부")]},
    )
    res = analyze(load_csv(path), cfg)
    s = res["subscales"][0]
    assert s["n_scored"] == 3
    assert s["bands"][0]["n"] == 3
    assert s["bands"][0]["pct"] == 100.0  # 75.0 이면 분모가 전체 응답자
    # 점수가 없는 응답자는 '미분류'로도 세지 않는다.
    assert s["n_unbanded"] == 0
    assert s["band_scores"][3] is None


def test_band_boundary_semantics_outside_tolerance(tmp_path):
    bands = [(0.0, 7.0, "없음"), (8.0, 14.0, "있음")]
    assert band_label(7.0 - 1e-12, bands) == "없음"
    assert band_label(7.0, bands) == "없음"
    assert band_label(7.0 + 1e-7, bands) is None   # 허용오차(1e-9) 밖 → 미분류
    assert band_label(8.0 - 1e-7, bands) is None
    assert band_index(8.0, bands) == 1


def test_bands_with_gap_narrower_than_tolerance_rejected():
    """판정은 ±1e-9 허용, 검증은 엄격이면 두 구간에 동시에 걸리는 점수가 생겼다."""
    with pytest.raises(ConfigError):
        _from_dict({
            "subscales": {"S": ["A", "B"]},
            "severity_bands": {"S": [[0, 0.999999999, "L"], [1, 2, "H"]]},
        })


def test_bands_range_unknown_flag_and_warning(tmp_path):
    """scale_min/max 가 없으면 단위 불일치를 점검할 수 없다는 사실 자체를 알린다."""
    path = _write(tmp_path, "A,B,C\n2,2,2\n3,3,3\n1,1,1\n")
    cfg = SurveyConfig(
        subscales={"S": ["A", "B", "C"]}, score_method="mean",
        severity_bands={"S": [(0.0, 7.0, "없음"), (8.0, 14.0, "역치하")]},
    )
    res = analyze(load_csv(path), cfg)
    s = res["subscales"][0]
    assert s["bands_range_unknown"] is True
    assert s["bands_out_of_range"] is False  # 판정 불가일 뿐 '문제없음'이 아님
    txt = report.render(res)
    assert "점검할 수 없었습니다" in txt
    assert "점검할 수 없었습니다" in report.render_markdown(res)


def test_prorated_respondents_are_disclosed_in_band_header(tmp_path):
    path = _write(tmp_path, "A,B,C\n2,2,\n3,3,3\n")
    cfg = SurveyConfig(
        subscales={"S": ["A", "B", "C"]}, scale_min=0, scale_max=4, score_method="sum",
        min_valid_ratio=0.5,
        severity_bands={"S": [(0.0, 5.0, "낮음"), (6.0, 12.0, "높음")]},
    )
    res = analyze(load_csv(path), cfg)
    assert res["subscales"][0]["n_prorated"] == 1
    assert "비례배분" in report.render(res)
    assert "비례배분 1명" in report.render_markdown(res)


def test_markdown_band_row_values(tmp_path):
    path = _write(tmp_path, "A,B\n0,0\n4,4\n")
    cfg = SurveyConfig(
        subscales={"S": ["A", "B"]}, scale_min=0, scale_max=4, score_method="sum",
        severity_bands={"S": [(0.0, 3.0, "낮음"), (4.0, 8.0, "높음")]},
    )
    md = report.render_markdown(analyze(load_csv(path), cfg))
    assert "| 낮음 | 0~3 | 1 | 50 |" in md
    assert "| 높음 | 4~8 | 1 | 50 |" in md


# ---------------------------------------------------------------- 집단 비교

def test_missing_value_tokens_in_group_column_are_not_groups(tmp_path):
    """'NA', '.', 999 같은 결측 표기가 하나의 '군'이 되어 유령 집단이 검정됐다."""
    csv = ("G,Q1,Q2\n치료,1,2\n치료,2,3\n대조,3,4\n대조,4,5\n"
           "NA,2,2\nNA,3,3\n.,1,1\nN/A,2,4\n-,3,2\n999,4,4\n999,2,3\n")
    path = _write(tmp_path, csv)
    data = load_csv(path, group_column="G", na_numbers=[999])
    assert sorted(set(v for v in data.group_values if v)) == ["대조", "치료"]
    res = analyze(data, SurveyConfig(subscales={"S": ["Q1", "Q2"]}))
    gc = res["group_compare"]
    assert gc["labels"] == ["대조", "치료"]
    assert gc["n_no_label"] == 7  # 결측 표기 7명은 '라벨 없음'으로 집계·표기


def test_invisible_characters_do_not_split_a_group(tmp_path):
    csv = "G,Q1,Q2\n치료군,1,2\n치료군​,2,3\n치료군 ,3,4\n대조군,4,5\n대조군,2,2\n"
    path = _write(tmp_path, csv)
    data = load_csv(path, group_column="G")
    assert sorted(set(data.group_values)) == ["대조군", "치료군"]
    assert normalize_label("치료군﻿​ ") == "치료군"


def test_too_many_groups_does_not_leak_labels():
    """상한 초과 분기는 '환자 ID를 그룹으로 지정한' 경우 — 라벨을 실으면 식별자가 샌다."""
    n = compare.MAX_GROUPS + 5
    out = compare.compare_subscales(
        [{"name": "S", "scores": [float(i) for i in range(n)], "score_method": "mean"}],
        [f"PT{i:05d}" for i in range(n)],
        "PID",
    )
    assert out["usable"] is False
    assert out["labels"] == []
    assert "PT00000" not in json.dumps(out, ensure_ascii=False)
    assert str(n) in out["reason"]  # 개수는 알려준다


def test_max_groups_boundary_is_inclusive():
    n = compare.MAX_GROUPS
    scores = [float(i) for i in range(2 * n)]
    labels = [f"G{i:02d}" for i in range(n)] * 2
    out = compare.compare_subscales(
        [{"name": "S", "scores": scores, "score_method": "mean"}], labels, "G"
    )
    assert out["usable"] is True and len(out["labels"]) == n


def test_high_cardinality_group_column_is_fast():
    """리스트 선형탐색으로 라벨을 모으면 O(N²) 가 되어 수십만 행에서 멈췄다."""
    import time
    n = 60000
    scores = [float(i % 7) for i in range(n)]
    labels = [f"PT{i}" for i in range(n)]
    t0 = time.time()
    out = compare.compare_subscales(
        [{"name": "S", "scores": scores, "score_method": "mean"}], labels, "PID"
    )
    assert out["usable"] is False
    assert time.time() - t0 < 3.0


def test_n_tests_counts_only_testable_subscales():
    scores_ok = [1.0, 2, 3, 4, 5, 6]
    scores_const = [5.0] * 6
    groups = ["A", "A", "A", "B", "B", "B"]
    out = compare.compare_subscales(
        [{"name": "OK", "scores": scores_ok, "score_method": "mean"},
         {"name": "CONST", "scores": scores_const, "score_method": "mean"}],
        groups, "G",
    )
    assert out["n_tests"] == 1
    assert out["subscales"][1]["p"] is None


def test_group_labels_are_sanitized_in_reports(tmp_path):
    csv = (
        "G,Q1,Q2\n"
        "\x1b[31mRED,1,2\n"
        "\x1b[31mRED,2,3\n"
        "<img src=x onerror=alert(1)>,3,4\n"
        "<img src=x onerror=alert(1)>,4,5\n"
    )
    path = _write(tmp_path, csv)
    res = analyze(load_csv(path, group_column="G"), SurveyConfig(subscales={"S": ["Q1", "Q2"]}))
    txt = report.render(res)
    md = report.render_markdown(res)
    assert "\x1b" not in txt  # 터미널 제어문자 제거
    assert "<img" not in md   # 원시 HTML 미출력
    assert "&lt;img" in md


# ------------------------------------------------------------------- CLI

def test_scores_out_notice_goes_to_stderr_so_json_stays_parseable(tmp_path, capsys):
    out_csv = str(tmp_path / "s.csv")
    rc = run([CSV, "-c", CFG, "--id-col", "ID", "--format", "json", "--scores-out", out_csv])
    cap = capsys.readouterr()
    assert rc == 0
    json.loads(cap.out)  # stdout 은 순수 JSON 이어야 한다
    assert "점수 저장됨" in cap.err


def test_output_path_cannot_overwrite_input_csv(tmp_path, capsys):
    path = _write(tmp_path, "ID,Q1,Q2\nP1,1,2\nP2,3,4\n")
    before = open(path, encoding="utf-8").read()
    for opt in ("--scores-out", "-o"):
        rc = run([path, "--id-col", "ID", opt, path])
        assert rc == 2
        assert "덮어쓸 수 없습니다" in capsys.readouterr().err
    assert open(path, encoding="utf-8").read() == before  # 원자료 그대로


def test_scores_out_and_output_cannot_be_same(tmp_path, capsys):
    path = _write(tmp_path, "ID,Q1,Q2\nP1,1,2\nP2,3,4\n")
    target = str(tmp_path / "both.csv")
    rc = run([path, "--id-col", "ID", "--scores-out", target, "-o", target])
    assert rc == 2
    assert "같습니다" in capsys.readouterr().err


def test_scores_out_rows_are_per_respondent(tmp_path):
    """행마다 그 응답자의 집단·점수·심각도가 붙어야 한다(0번 응답자 값 복사 금지)."""
    csv = "ID,ARM,A,B\nP1,치료,0,0\nP2,대조,4,4\nP3,치료,2,2\n"
    path = _write(tmp_path, csv)
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({
        "subscales": {"S": ["A", "B"]}, "scale_min": 0, "scale_max": 4,
        "score_method": "sum",
        "severity_bands": {"S": [[0, 3, "낮음"], [4, 8, "높음"]]},
    }, ensure_ascii=False), encoding="utf-8")
    out_csv = str(tmp_path / "s.csv")
    rc = run([path, "-c", str(cfg_path), "--id-col", "ID", "--group-col", "ARM",
              "--scores-out", out_csv])
    assert rc == 0
    lines = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    header = lines[0].split(",")
    assert lines[1].split(",") == ["2", "P1", "치료", "0", "낮음"]
    assert lines[2].split(",") == ["3", "P2", "대조", "8", "높음"]
    assert lines[3].split(",") == ["4", "P3", "치료", "4", "높음"]
    for l in lines[1:]:
        assert len(l.split(",")) == len(header)  # 열 밀림 방지


def test_json_output_has_no_respondent_level_values(tmp_path, capsys):
    rc = run([CSV, "-c", CFG, "--id-col", "ID", "--group-col", "군", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    for s in data["subscales"]:
        assert "scores" not in s
        assert "band_scores" not in s  # 응답자별 심각도 라벨도 새면 안 된다


def test_severity_label_is_escaped_in_scores_csv(tmp_path):
    path = _write(tmp_path, "A,B\n0,0\n4,4\n")
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({
        "subscales": {"S": ["A", "B"]}, "scale_min": 0, "scale_max": 4,
        "score_method": "sum",
        "severity_bands": {"S": [[0, 3, "=cmd()"], [4, 8, "정상"]]},
    }, ensure_ascii=False), encoding="utf-8")
    out_csv = str(tmp_path / "s.csv")
    assert run([path, "-c", str(cfg_path), "--scores-out", out_csv]) == 0
    lines = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    assert lines[1].endswith(",'=cmd()")


def test_duplicate_column_names_in_scores_csv_are_disambiguated(tmp_path):
    path = _write(tmp_path, "전체,Q1,Q2\nA,1,2\nB,3,4\n")
    out_csv = str(tmp_path / "s.csv")
    assert run([path, "--group-col", "전체", "--scores-out", out_csv]) == 0
    header = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert len(set(header)) == len(header)


def test_nonfinite_values_never_reach_text_md_or_scores_csv(tmp_path):
    path = _write(tmp_path, "ID,Q1,Q2\nP1,1e308,1e308\nP2,1e308,1e308\nP3,1e308,1e308\n")
    out_csv = str(tmp_path / "s.csv")
    rc = run([path, "--id-col", "ID", "--scores-out", out_csv, "-o", str(tmp_path / "r.txt")])
    assert rc == 0
    txt = (tmp_path / "r.txt").read_text(encoding="utf-8")
    assert "nan" not in txt.lower() and "inf" not in txt.lower()
    body = (tmp_path / "s.csv").read_text(encoding="utf-8-sig").lower()
    assert "inf" not in body and "nan" not in body


def test_config_item_taken_by_id_or_group_col_gets_clear_message(tmp_path, capsys):
    path = _write(tmp_path, "A,B,G\n1,2,T\n3,4,C\n")
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text('{"subscales":{"S":["A","G"]}}', encoding="utf-8")
    rc = run([path, "-c", str(cfg_path), "--group-col", "G"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "제외되었습니다" in err and "G" in err


def test_oversized_cell_gives_data_error_not_traceback(tmp_path, capsys):
    path = _write(tmp_path, "A,B\n1,2\n3," + "x" * 200000 + "\n")
    rc = run([path])
    assert rc == 2
    assert "CSV를 읽을 수 없습니다" in capsys.readouterr().err
