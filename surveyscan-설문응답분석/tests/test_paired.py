"""사전-사후(반복측정) 분석 테스트.

참조값 출처
- 대응표본 t: scipy.stats.ttest_rel 로 미리 계산해 하드코딩.
- ICC(2,1): Shrout & Fleiss(1979)의 고전 예시자료(6 대상 × 4 평정자)의 공표값 0.290,
  CI 는 psych::ICC 가 같은 자료에서 내는 [0.019, 0.761].
런타임은 표준 라이브러리만 쓰므로 여기서도 scipy를 import하지 않는다.
"""
import pytest

from surveyscan import paired


PRE = [15, 18, 12, 20, 14, 17, 19, 11, 16, 13]
POST = [11, 13, 10, 14, 12, 12, 15, 10, 13, 11]
DIFFS = [b - a for a, b in zip(PRE, POST)]


# ── 대응표본 t · dz ─────────────────────────────────────────────────────────
def test_paired_ttest_matches_scipy():
    r = paired.paired_ttest(DIFFS)
    assert r["t"] == pytest.approx(-6.5298808765776934, rel=1e-12)
    assert r["p"] == pytest.approx(0.00010765027603855119, rel=1e-10)
    assert r["df"] == 9.0
    assert r["mean_diff"] == pytest.approx(-3.4)
    assert r["sd_diff"] == pytest.approx(1.6465452046971292, rel=1e-12)


def test_paired_ttest_ci_brackets_mean():
    r = paired.paired_ttest(DIFFS)
    lo, hi = r["diff_ci"]
    assert lo < r["mean_diff"] < hi
    # t(9, .975) = 2.262157... → 폭은 2·t·SE
    assert hi - lo == pytest.approx(2 * 2.262157162740992 * 1.6465452046971292 / 10 ** 0.5,
                                    rel=1e-9)


def test_paired_ttest_needs_variation():
    assert paired.paired_ttest([2.0, 2.0, 2.0]) is None  # 차이가 모두 같음(분산 0)
    assert paired.paired_ttest([1.0]) is None


def test_cohen_dz_value_and_ci():
    e = paired.cohen_dz(DIFFS)
    dz = -3.4 / 1.6465452046971292
    n = len(DIFFS)
    assert e["dz"] == pytest.approx(dz, rel=1e-12)
    # SE = √(1/n + dz²/(2n)) — dz² 항이 빠지면 CI 가 좁아진다.
    assert e["se"] == pytest.approx((1 / n + dz * dz / (2 * n)) ** 0.5, rel=1e-12)
    lo, hi = e["ci"]
    assert (hi - lo) / 2 == pytest.approx(1.959963984540054 * e["se"], rel=1e-9)
    assert lo < e["dz"] < hi
    assert paired.cohen_dz([1.0, 1.0]) is None


# ── ICC(2,1) ───────────────────────────────────────────────────────────────
SHROUT_FLEISS = [
    [9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8],
    [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7],
]


def test_icc_matches_published_example():
    r = paired.icc_agreement([[float(v) for v in row] for row in SHROUT_FLEISS])
    assert r["icc"] == pytest.approx(0.2898, abs=5e-4)     # 공표값 0.290
    lo, hi = r["ci"]
    assert lo == pytest.approx(0.019, abs=2e-3)            # psych::ICC
    assert hi == pytest.approx(0.761, abs=2e-3)
    assert lo < r["icc"] < hi


def test_icc_perfect_agreement_is_one():
    rows = [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0]]
    r = paired.icc_agreement(rows)
    assert r["icc"] == pytest.approx(1.0)
    assert r["sem"] == pytest.approx(0.0)
    assert r["mdc95"] == pytest.approx(0.0)


def test_icc_agreement_penalises_systematic_shift():
    """절대일치 ICC 는 '모두 +5' 같은 계통 이동을 벌점으로 반영해야 한다.

    (일치도가 아니라 상관만 본다면 두 경우가 같은 값이 나온다 — 그러면 잘못된 구현이다.)
    """
    same = [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0], [5.0, 5.0]]
    shifted = [[a, b + 5.0] for a, b in same]
    assert paired.icc_agreement(shifted)["icc"] < paired.icc_agreement(same)["icc"]


def test_icc_sem_and_mdc_formulas():
    r = paired.icc_agreement([[float(v) for v in row] for row in SHROUT_FLEISS])
    n, k = 6, 4
    assert r["sem"] == pytest.approx(r["ms_e"] ** 0.5)
    assert r["mdc95"] == pytest.approx(1.959963984540054 * 2 ** 0.5 * r["sem"])
    # 절대일치 SEM 은 시점 간 평균 이동(MSC)까지 오차로 포함하므로 더 크다.
    assert r["sem_agreement"] == pytest.approx(
        (r["ms_e"] + (r["ms_c"] - r["ms_e"]) / n) ** 0.5
    )
    assert r["sem_agreement"] > r["sem"]
    assert r["mdc95_agreement"] == pytest.approx(
        1.959963984540054 * 2 ** 0.5 * r["sem_agreement"]
    )
    assert k == r["k"] and r["n"] == n


def test_icc_needs_enough_data():
    assert paired.icc_agreement([[1.0, 2.0]]) is None          # 대상 1명
    assert paired.icc_agreement([[1.0], [2.0]]) is None        # 측정 1회
    assert paired.icc_agreement([[1.0, 2.0], [3.0]]) is None   # 길이 불일치


# ── 시점 순서 · 짝짓기 ──────────────────────────────────────────────────────
def test_order_labels_numeric_beats_string_sort():
    labs, rule = paired.order_labels(["12", "0", "4", "0"])
    assert labs == ["0", "4", "12"] and rule == "numeric"


def test_order_labels_korean_uses_file_order():
    # 문자열 정렬이면 '12주' 가 '기저' 앞에 와서 변화량 부호가 뒤집힌다.
    labs, rule = paired.order_labels(["기저", "12주", "기저", "12주"])
    assert labs == ["기저", "12주"] and rule == "appearance"


def test_resolve_timepoints_rules():
    assert paired.resolve_timepoints(["A", "B"], None, None) == ("A", "B", None)
    _, _, err = paired.resolve_timepoints(["A", "B", "C"], None, None)
    assert "지정하세요" in err
    _, _, err = paired.resolve_timepoints(["A", "B"], "A", None)
    assert "함께 지정" in err
    _, _, err = paired.resolve_timepoints(["A", "B"], "A", "Z")
    assert "없는 값" in err
    _, _, err = paired.resolve_timepoints(["A", "B"], "A", "A")
    assert "같습니다" in err


def test_build_pairs_excludes_duplicates_and_singletons():
    keys = ["P1", "P1", "P2", "P3", "P3", "P3", ""]
    times = ["기저", "12주", "기저", "기저", "12주", "12주", "기저"]
    info = paired.build_pairs(keys, times, "기저", "12주")
    assert [k for k, _, _ in info["pairs"]] == ["P1"]
    assert info["n_unpaired"] == 1      # P2 는 기저만
    assert info["n_dup"] == 1           # P3 는 12주가 두 줄
    assert info["n_no_id"] == 1


def test_build_pairs_ignores_other_timepoints():
    keys = ["P1", "P1", "P1"]
    times = ["기저", "4주", "12주"]
    info = paired.build_pairs(keys, times, "기저", "12주")
    assert len(info["pairs"]) == 1


# ── compare_prepost 통합 ────────────────────────────────────────────────────
def _subscale(name, scores, mdc95=None, method="sum"):
    return {"name": name, "scores": scores, "score_method": method, "mdc95": mdc95}


def _fixture():
    """4명 × 2시점(기저→12주). 점수는 행 순서: P1기저,P1사후,…"""
    keys = ["P1", "P1", "P2", "P2", "P3", "P3", "P4", "P4"]
    times = ["기저", "12주"] * 4
    scores = [20, 12, 18, 14, 16, 15, 22, 10]  # 변화: -8, -4, -1, -12
    return keys, times, scores


def test_compare_prepost_basic_change():
    keys, times, scores = _fixture()
    out = paired.compare_prepost(
        [_subscale("S", scores)], keys, times, "시점", conf=0.95
    )
    assert out["usable"] and out["pre"] == "기저" and out["post"] == "12주"
    row = out["subscales"][0]
    assert row["n_pairs"] == 4
    assert row["pre"]["mean"] == pytest.approx(19.0)
    assert row["post"]["mean"] == pytest.approx(12.75)
    assert row["change"]["mean"] == pytest.approx(-6.25)
    assert row["test"]["df"] == 3.0


def test_compare_prepost_missing_score_counted_not_dropped_silently():
    keys, times, scores = _fixture()
    scores = list(scores)
    scores[2] = None  # P2 기저 점수 없음
    out = paired.compare_prepost([_subscale("S", scores)], keys, times, "시점")
    row = out["subscales"][0]
    assert row["n_pairs"] == 3 and row["n_missing_score"] == 1


def test_responders_prefers_mcid_over_mdc():
    keys, times, scores = _fixture()
    sub = _subscale("S", scores, mdc95=2.0)
    out = paired.compare_prepost(
        [sub], keys, times, "시점", mcid={"S": 5.0}
    )
    rsp = out["subscales"][0]["responders"]
    assert rsp["source"] == "mcid" and rsp["threshold"] == 5.0
    # 변화 -8, -4, -1, -12 중 |변화| ≥ 5 이면서 감소한 사람은 2명
    assert rsp["decreased"] == 2 and rsp["increased"] == 0 and rsp["unchanged"] == 2
    assert rsp["decreased_pct"] == 50.0


def test_responders_fall_back_to_retest_mdc_not_pooled_alpha_mdc():
    """mcid 가 없으면 **짝 자료에서 나온** MDC₉₅ 를 쓴다.

    α 기반 MDC₉₅ 는 --time-col 자료에서 모든 시점을 합친 표본(같은 사람이 두 번)에서
    나오고 개입 효과로 SD 가 부풀려져 있어, 임계값으로 쓰면 반응자가 과소 계산된다.
    """
    keys, times, scores = _fixture()
    out = paired.compare_prepost([_subscale("S", scores, mdc95=2.0)], keys, times, "시점")
    row = out["subscales"][0]
    rsp = row["responders"]
    assert rsp["source"] == "mdc95_retest"
    assert rsp["threshold"] == pytest.approx(row["icc"]["mdc95"])
    assert rsp["threshold"] != 2.0


def test_responders_use_alpha_mdc_only_when_icc_unavailable():
    # 쌍이 1개면 ICC 를 낼 수 없다 → α 기반 MDC₉₅ 로 내려간다.
    out = paired.compare_prepost(
        [_subscale("S", [20, 8], mdc95=2.0)], ["P1", "P1"], ["기저", "12주"], "시점"
    )
    rsp = out["subscales"][0]["responders"]
    assert rsp["source"] == "mdc95_alpha" and rsp["threshold"] == 2.0
    assert rsp["decreased"] == 1


def test_responders_absent_without_any_threshold():
    out = paired.compare_prepost(
        [_subscale("S", [20, 8])], ["P1", "P1"], ["기저", "12주"], "시점"
    )
    assert out["subscales"][0]["responders"] is None


def test_responder_threshold_boundary_is_inclusive():
    """변화가 임계값과 정확히 같으면 반응자로 센다('MCID 이상' 관례)."""
    keys = ["P1", "P1", "P2", "P2", "P3", "P3"]
    times = ["기저", "12주"] * 3
    scores = [20, 15, 20, 25, 20, 19]  # 변화 -5, +5, -1
    out = paired.compare_prepost(
        [_subscale("S", scores)], keys, times, "시점", mcid={"S": 5.0}
    )
    rsp = out["subscales"][0]["responders"]
    assert (rsp["decreased"], rsp["increased"], rsp["unchanged"]) == (1, 1, 1)
    assert rsp["decreased_pct"] == pytest.approx(100 / 3, abs=0.1)
    assert rsp["unchanged_pct"] == pytest.approx(100 / 3, abs=0.1)
    assert rsp["decreased"] + rsp["increased"] + rsp["unchanged"] == rsp["n"]


def test_nonpositive_mcid_does_not_double_count():
    """임계값 0 이면 같은 사람이 감소·증가에 동시에 잡혀 합계가 N을 넘는다 → 쓰지 않는다."""
    keys, times, scores = _fixture()
    out = paired.compare_prepost(
        [_subscale("S", scores)], keys, times, "시점", mcid={"S": 0.0}
    )
    rsp = out["subscales"][0]["responders"]
    assert rsp["source"] != "mcid"     # 0 이하 임계값은 무시하고 다음 후보로 내려간다
    assert rsp["decreased"] + rsp["increased"] + rsp["unchanged"] == rsp["n"]
    assert rsp["unchanged"] >= 0


def test_group_change_compare_and_conflict_excluded():
    keys = ["P%d" % i for i in range(1, 9) for _ in (0, 1)]
    times = ["기저", "12주"] * 8
    scores = [20, 10, 21, 12, 19, 9, 22, 11,   # 치료군 4명: 큰 감소
              18, 17, 19, 19, 20, 18, 17, 17]  # 대조군 4명: 거의 변화 없음
    groups = ["치료군"] * 8 + ["대조군"] * 8
    groups[3] = "대조군"  # P2 의 사후 행만 군이 다르게 적힘 → 그 사람은 군 비교에서 제외
    out = paired.compare_prepost(
        [_subscale("S", scores)], keys, times, "시점",
        group_values=groups, group_column="군",
    )
    row = out["subscales"][0]
    assert row["n_group_conflict"] == 1
    gc = row["group_change"]
    labels = {g["label"]: g["n"] for g in gc["groups"]}
    assert labels == {"치료군": 3, "대조군": 4}
    assert gc["test"]["test"] == "welch_t"
    assert gc["test"]["p"] < 0.05


def test_unusable_reasons_are_explicit():
    # 시점이 하나뿐
    out = paired.compare_prepost(
        [_subscale("S", [1, 2])], ["P1", "P2"], ["기저", "기저"], "시점"
    )
    assert not out["usable"] and "사전-사후" in out["reason"]
    # ID 가 없음
    out = paired.compare_prepost(
        [_subscale("S", [1, 2])], ["", ""], ["기저", "12주"], "시점"
    )
    assert not out["usable"] and "--id-col" in out["reason"]
    # 짝이 하나도 없음
    out = paired.compare_prepost(
        [_subscale("S", [1, 2])], ["P1", "P2"], ["기저", "12주"], "시점"
    )
    assert not out["usable"] and "모두 나온 ID" in out["reason"]


def test_r_prepost_is_the_pre_post_correlation():
    keys, times, scores = _fixture()
    out = paired.compare_prepost([_subscale("S", scores)], keys, times, "시점")
    row = out["subscales"][0]
    pre = [20, 18, 16, 22]
    post = [12, 14, 15, 10]
    from surveyscan import stats as _st
    assert row["r_prepost"] == pytest.approx(_st.pearson(pre, post))
    assert row["r_prepost"] != pytest.approx(1.0)   # pre vs pre 였다면 1.0


def test_holm_values_are_the_holm_formula():
    """단조성만 보면 '보정 안 함'도 통과한다 — 값 자체를 고정한다."""
    keys = ["P%d" % i for i in range(1, 7) for _ in (0, 1)]
    times = ["기저", "12주"] * 6
    strong = [20, 9, 21, 12, 19, 8, 22, 12, 20, 11, 21, 10]    # 매우 유의(변화량에 변동 있음)
    weak = [10, 10, 11, 12, 12, 11, 13, 14, 11, 11, 12, 13]    # 약함
    out = paired.compare_prepost(
        [_subscale("A", strong), _subscale("B", weak)], keys, times, "시점"
    )
    rows = {r["name"]: r for r in out["subscales"]}
    pa, pb = rows["A"]["p"], rows["B"]["p"]
    smaller, larger = (("A", "B") if pa < pb else ("B", "A"))
    assert rows[smaller]["p_holm"] == pytest.approx(min(1.0, 2 * rows[smaller]["p"]))
    assert rows[larger]["p_holm"] == pytest.approx(
        max(rows[larger]["p"], rows[smaller]["p_holm"])
    )
    assert rows[smaller]["p_holm"] > rows[smaller]["p"]


def test_max_timepoints_guard_is_twenty():
    # 상한 자체를 고정한다(테스트가 상수를 참조하면 상한을 늘려도 통과한다).
    assert paired.MAX_TIMEPOINTS == 20
    keys = [f"P{i % 10}" for i in range(40)]
    times = [f"2026-01-{(i % 20) + 1:02d}" for i in range(40)]
    out = paired.compare_prepost([_subscale("S", [1.0] * 40)], keys, times, "방문일")
    # 정확히 20개면 라벨을 보여주고(사전/사후 지정 안내), 21개부터 차단한다.
    assert out["labels"] and len(out["labels"]) == 20
    keys21 = [f"P{i % 10}" for i in range(42)]
    times21 = [f"2026-01-{(i % 21) + 1:02d}" for i in range(42)]
    out21 = paired.compare_prepost([_subscale("S", [1.0] * 42)], keys21, times21, "방문일")
    assert out21["labels"] == [] and "너무 많습니다" in out21["reason"]


def test_reason_shows_only_a_few_timepoint_labels():
    """시점이 사실 방문일자면 라벨이 곧 진료일 — 사유 문구에 전부 싣지 않는다."""
    n = 12
    keys = [f"P{i}" for i in range(n)]
    times = [f"2026-03-{i + 1:02d}" for i in range(n)]
    out = paired.compare_prepost([_subscale("S", [1.0] * n)], keys, times, "방문일")
    assert not out["usable"]
    assert out["reason"].count("2026-03-") <= paired.REASON_LABEL_PREVIEW
    assert "외 7개" in out["reason"]


def test_group_change_needs_enough_pairs():
    # 쌍이 3개뿐이면 집단별 변화량 비교를 하지 않는다(집단당 1~2명짜리 비교 방지).
    keys = ["P1", "P1", "P2", "P2", "P3", "P3"]
    times = ["기저", "12주"] * 3
    scores = [20, 10, 21, 12, 19, 18]
    groups = ["치료군"] * 4 + ["대조군"] * 2
    out = paired.compare_prepost(
        [_subscale("S", scores)], keys, times, "시점",
        group_values=groups, group_column="군",
    )
    assert out["subscales"][0]["group_change"] is None


def test_group_with_single_member_is_excluded_not_tested():
    keys = ["P%d" % i for i in range(1, 8) for _ in (0, 1)]
    times = ["기저", "12주"] * 7
    scores = [20, 10, 21, 12, 19, 9, 22, 11, 18, 17, 17, 15, 20, 19]
    groups = ["치료군"] * 8 + ["대조군"] * 4 + ["단독군"] * 2
    out = paired.compare_prepost(
        [_subscale("S", scores)], keys, times, "시점",
        group_values=groups, group_column="군",
    )
    gc = out["subscales"][0]["group_change"]
    assert [g["label"] for g in gc["groups"]] == ["대조군", "치료군"]
    assert gc["excluded_groups"] == ["단독군"]


def test_nan_timepoint_label_does_not_use_numeric_rule():
    labs, rule = paired.order_labels(["기저", "nan"])
    assert rule == "appearance" and labs == ["기저", "nan"]


def test_blank_id_at_other_timepoint_counted_as_other_time():
    info = paired.build_pairs(["A", "A", "", ""], ["t0", "t1", "t9", "t9"], "t0", "t1")
    assert info["n_other_time"] == 2 and info["n_no_id"] == 0
    info2 = paired.build_pairs(["A", "A", "", ""], ["t0", "t1", "t0", "t1"], "t0", "t1")
    assert info2["n_no_id"] == 2 and info2["n_other_time"] == 0


def test_pairs_are_sorted_deterministically():
    keys = ["Z", "Z", "A", "A", "M", "M"]
    times = ["기저", "12주"] * 3
    info = paired.build_pairs(keys, times, "기저", "12주")
    assert [k for k, _, _ in info["pairs"]] == ["A", "M", "Z"]


def test_too_many_timepoints_hides_labels():
    n = paired.MAX_TIMEPOINTS + 1
    keys = [f"P{i}" for i in range(n)]
    times = [f"2026-01-{i:02d}" for i in range(1, n + 1)]
    out = paired.compare_prepost(
        [_subscale("S", [1.0] * n)], keys, times, "방문일"
    )
    assert not out["usable"]
    assert out["labels"] == []           # 날짜(식별정보)를 리포트에 싣지 않는다
    assert "너무 많습니다" in out["reason"]


def test_holm_applied_across_subscales():
    keys, times, scores = _fixture()
    out = paired.compare_prepost(
        [_subscale("A", scores), _subscale("B", scores)], keys, times, "시점"
    )
    ps = [r["p"] for r in out["subscales"]]
    holm = [r["p_holm"] for r in out["subscales"]]
    assert all(h >= p for h, p in zip(holm, ps))
    assert out["n_tests"] == 2
