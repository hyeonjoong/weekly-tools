"""병합 엔진 — 이 툴의 두 불변식을 지키는 테스트.

1. **카테시안 조인은 어떤 경우에도 일어나지 않는다.**
2. **어떤 입력 행도 사유 없이 사라지지 않는다** (입력 = 사용 + Σ드롭).

나머지 기능을 다 빼도 이 둘이 남으면 툴은 값어치를 한다. 그래서 여기 있는
테스트가 이 저장소에서 가장 중요하다.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import random

import pytest

from joinaudit.dataio import Frame
from joinaudit.detect import Detection
from joinaudit.issues import CRITICAL, IssueLog
from joinaudit.keys import KeyNormalizer
from joinaudit.merge import (DROP_DATE_PARSE, DROP_DUPLICATE, DROP_NO_KEY,
                             DROP_SUBJECT_UNMATCHED, DUP_MERGED, USED,
                             FilePlan, Ledger, assign_keys_and_times,
                             make_prefix, merge_files, resolve_duplicates,
                             snap_to_base)
from joinaudit.timeline import DatePlan, VisitNormalizer, plan_date_column

NOON = _dt.time(12, 0)


# --------------------------------------------------------------------------
# 최소한의 조립 도구 (CLI 를 거치지 않고 엔진만 시험한다)
# --------------------------------------------------------------------------

def make_frame(label, header, rows):
    return Frame(path=f"/tmp/{label}", label=label, header=list(header),
                 rows=[list(r) for r in rows])


def make_plan(index, frame, key="id", time_col=None, kind="date"):
    plan = FilePlan(index=index, frame=frame, prefix=make_prefix(frame.label),
                    key_det=Detection(role="key", column=key, confidence="명시"))
    plan.time_kind = kind if time_col else "none"
    plan.time_col = time_col
    if time_col and kind == "date":
        plan.date_plan = plan_date_column(frame.column(time_col))
    plan.value_columns = [c for c in frame.header if c != key]
    return plan


def run_merge(frames_and_cols, how="outer", align="date", dup_policy="error",
              tolerance=0, base_index=0, unify_heads=False):
    """(frame, key, time_col, kind) 목록을 받아 전체 병합을 돌린다."""
    plans = [make_plan(i, f, key, tc, kind)
             for i, (f, key, tc, kind) in enumerate(frames_and_cols)]
    issues = IssueLog()
    ledger = Ledger([p.frame.nrows for p in plans])
    normalizer = KeyNormalizer(unify_heads=unify_heads)
    visits = VisitNormalizer()
    for plan in plans:
        assign_keys_and_times(plan, normalizer, ledger, issues, align, NOON,
                              visits)
    for plan in plans:
        resolve_duplicates(plan, ledger, issues, dup_policy)
    snap_to_base(plans, base_index, tolerance, ledger, issues)
    result = merge_files(plans, ledger, issues, normalizer, how, align,
                         base_index)
    return result, issues


# --------------------------------------------------------------------------
# 불변식 1 — 카테시안 조인 금지 (이 툴의 존재 이유)
# --------------------------------------------------------------------------

def test_duplicate_keys_never_multiply_rows():
    """pandas `merge` 가 경고 없이 행을 곱하는 바로 그 자리.

    양쪽 파일에 같은 (피험자, 날짜) 가 2번씩 있으면 순진한 조인은 2×2 = 4행을
    만든다. 여기서는 정책이 없으면 그 키를 통째로 빼므로 0행이 된다 — 어떤
    경우에도 **늘어나지 않는다.**
    """
    a = make_frame("a.csv", ["id", "date", "v"],
                   [["S01", "2026-03-01", "1"], ["S01", "2026-03-01", "2"]])
    b = make_frame("b.csv", ["id", "date", "w"],
                   [["S01", "2026-03-01", "10"], ["S01", "2026-03-01", "20"]])
    result, issues = run_merge([(a, "id", "date", "date"),
                                (b, "id", "date", "date")])
    assert len(result.rows) == 0                     # 4행이 되지 않는다
    assert issues.has(CRITICAL)
    assert any(i.kind == "중복키" for i in issues)


@pytest.mark.parametrize("policy", ["error", "first", "last", "mean"])
def test_output_rows_never_exceed_distinct_keys(policy):
    """어떤 정책에서도 출력 행 수 ≤ 서로 다른 (피험자, 시점) 수."""
    rows_a = [["S01", "2026-03-01", "1"]] * 3 + [["S02", "2026-03-01", "2"]] * 2
    rows_b = [["S01", "2026-03-01", "9"]] * 4 + [["S02", "2026-03-01", "8"]]
    a = make_frame("a.csv", ["id", "date", "v"], rows_a)
    b = make_frame("b.csv", ["id", "date", "w"], rows_b)
    result, _ = run_merge([(a, "id", "date", "date"), (b, "id", "date", "date")],
                          dup_policy=policy)
    # 서로 다른 (피험자, 날짜) 는 정확히 2개다 — 정책과 무관하게 그 이상 나올 수 없다.
    assert len(result.rows) <= 2
    assert result.ledger_error is None


def test_random_duplicate_heavy_inputs_never_grow(  ):
    """무작위 중복 폭탄 100회 — 출력이 입력보다 커지는 일은 구조적으로 없다."""
    rng = random.Random(7)
    for trial in range(100):
        def rows(prefix):
            return [[f"S{rng.randint(1, 4):02d}",
                     f"2026-03-{rng.randint(1, 3):02d}",
                     f"{prefix}{i}"] for i in range(rng.randint(1, 12))]
        a = make_frame("a.csv", ["id", "date", "v"], rows("a"))
        b = make_frame("b.csv", ["id", "date", "w"], rows("b"))
        policy = rng.choice(["error", "first", "last", "mean"])
        how = rng.choice(["outer", "inner", "left"])
        result, _ = run_merge([(a, "id", "date", "date"),
                               (b, "id", "date", "date")],
                              dup_policy=policy, how=how)
        # 출력 행 수는 서로 다른 (피험자, 시점) 조합 수를 넘을 수 없다.
        distinct = {(k, t) for f in (a, b)
                    for k, t in zip((r[0] for r in f.rows),
                                    (r[1] for r in f.rows))}
        assert len(result.rows) <= len(distinct), (trial, policy, how)
        assert result.ledger_error is None, (trial, policy, how)


def test_subject_level_file_broadcasts_without_multiplying():
    """시점 없는 파일(ISI 등)은 그 피험자의 모든 시점에 붙지만 행을 늘리지 않는다."""
    nights = make_frame("n.csv", ["id", "date", "v"],
                        [["S01", "2026-03-01", "1"], ["S01", "2026-03-02", "2"]])
    isi = make_frame("i.csv", ["id", "isi"], [["S01", "17"]])
    result, _ = run_merge([(nights, "id", "date", "date"),
                           (isi, "id", None, "none")])
    assert len(result.rows) == 2
    assert [r[-1] for r in result.rows] == ["17", "17"]


# --------------------------------------------------------------------------
# 불변식 2 — 입력 = 사용 + Σ드롭
# --------------------------------------------------------------------------

def _assert_ledger_balances(result):
    """원장이 **산출물과** 맞는지까지 본다.

    `sum(counts) == total` 만 세면 아무것도 검증하지 못한다(모든 칸을 세니 언제나
    참이다). `ledger_error` 는 이제 기여 행과 실제 근거 행을 대조한 결과다.
    """
    counts = result.ledger.counts()
    assert "미배정" not in counts, counts
    assert result.ledger_error is None, result.ledger_error
    # 기여로 표시된 행은 전부 어떤 resolved 항목의 근거여야 한다.
    contributed = sum(counts.get(d, 0) for d in (USED, DUP_MERGED))
    backed = {(p.index, i) for p in result.plans
              for gkey, rows in p.backing.items() for i in rows
              if gkey in set(result.final_keys)
              or (p.subject_level and gkey[0] in {k for k, _ in result.final_keys})}
    assert contributed == len(backed), (contributed, len(backed))


def test_every_row_gets_exactly_one_disposition():
    a = make_frame("a.csv", ["id", "date", "v"],
                   [["S01", "2026-03-01", "1"],
                    ["", "2026-03-01", "2"],              # 키 없음
                    ["S02", "말도안되는날짜", "3"],          # 날짜 실패
                    ["S03", "2026-03-01", "4"],
                    ["S03", "2026-03-01", "5"]])          # 중복
    b = make_frame("b.csv", ["id", "date", "w"],
                   [["S01", "2026-03-01", "9"],
                    ["S09", "2026-03-01", "8"]])          # a 에 없는 피험자
    result, _ = run_merge([(a, "id", "date", "date"),
                           (b, "id", "date", "date")], how="inner")
    _assert_ledger_balances(result)
    counts = result.ledger.counts()
    assert counts[DROP_NO_KEY] == 1
    assert counts[DROP_DATE_PARSE] == 1
    assert counts[DROP_DUPLICATE] == 2
    assert counts[DROP_SUBJECT_UNMATCHED] == 1
    assert counts[USED] == 2


@pytest.mark.parametrize("how, policy", list(itertools.product(
    ["outer", "inner", "left"], ["error", "first", "last", "mean"])))
def test_ledger_balances_across_every_mode(how, policy):
    rng = random.Random(hash((how, policy)) & 0xFFFF)
    a = make_frame("a.csv", ["id", "date", "v"],
                   [[f"S{rng.randint(1, 3):02d}", f"2026-03-0{rng.randint(1, 3)}",
                     str(i)] for i in range(10)] + [["", "2026-03-01", "x"]])
    b = make_frame("b.csv", ["id", "date", "w"],
                   [[f"S{rng.randint(2, 5):02d}", f"2026-03-0{rng.randint(1, 4)}",
                     str(i)] for i in range(10)])
    c = make_frame("c.csv", ["id", "isi"], [["S02", "12"], ["S07", "20"]])
    result, _ = run_merge([(a, "id", "date", "date"), (b, "id", "date", "date"),
                           (c, "id", None, "none")], how=how, dup_policy=policy)
    _assert_ledger_balances(result)


def test_night_alignment_pulls_post_midnight_rows_onto_the_diary_date():
    """워치는 03:20 에, 일기는 전날 날짜로 적혔지만 **같은 밤**이어야 한다."""
    watch = make_frame("w.csv", ["id", "measured_at", "rmssd"],
                       [["S01", "2026-03-11 03:20", "42"]])
    diary = make_frame("d.csv", ["id", "날짜", "tst"],
                       [["S01", "2026-03-10", "410"]])
    result, _ = run_merge([(watch, "id", "measured_at", "date"),
                           (diary, "id", "날짜", "date")], align="night",
                          how="inner")
    assert len(result.rows) == 1
    assert result.rows[0][:2] == ["S01", "2026-03-10"]
    assert "42" in result.rows[0] and "410" in result.rows[0]


def test_without_night_alignment_the_same_data_does_not_meet():
    """대조군: `--align date` 였다면 하루 어긋나 아무것도 붙지 않는다."""
    watch = make_frame("w.csv", ["id", "measured_at", "rmssd"],
                       [["S01", "2026-03-11 03:20", "42"]])
    diary = make_frame("d.csv", ["id", "날짜", "tst"],
                       [["S01", "2026-03-10", "410"]])
    result, _ = run_merge([(watch, "id", "measured_at", "date"),
                           (diary, "id", "날짜", "date")], align="date",
                          how="inner")
    assert len(result.rows) == 0


# --------------------------------------------------------------------------
# 중복 정책
# --------------------------------------------------------------------------

def test_dup_policy_first_and_last_pick_the_stated_row():
    def build():
        return make_frame("a.csv", ["id", "date", "v"],
                          [["S01", "2026-03-01", "10"],
                           ["S01", "2026-03-01", "20"],
                           ["S01", "2026-03-01", "30"]])
    other = make_frame("b.csv", ["id", "date", "w"],
                       [["S01", "2026-03-01", "1"]])
    first, _ = run_merge([(build(), "id", "date", "date"),
                          (other, "id", "date", "date")], dup_policy="first")
    last, _ = run_merge([(build(), "id", "date", "date"),
                         (other, "id", "date", "date")], dup_policy="last")
    assert first.rows[0][3] == "10"
    assert last.rows[0][3] == "30"


def test_dup_policy_mean_averages_numbers_exactly():
    """손으로 계산한 값과 대조: (10 + 21) / 2 = 15.5."""
    a = make_frame("a.csv", ["id", "date", "v"],
                   [["S01", "2026-03-01", "10"], ["S01", "2026-03-01", "21"]])
    b = make_frame("b.csv", ["id", "date", "w"],
                   [["S01", "2026-03-01", "1"]])
    result, _ = run_merge([(a, "id", "date", "date"),
                           (b, "id", "date", "date")], dup_policy="mean")
    assert result.rows[0][3] == "15.5"
    counts = result.ledger.counts(0)
    assert counts[DUP_MERGED] == 1 and counts[USED] == 1


def test_dup_policy_mean_blanks_disagreeing_text():
    a = make_frame("a.csv", ["id", "date", "메모"],
                   [["S01", "2026-03-01", "정상"], ["S01", "2026-03-01", "재측정"]])
    b = make_frame("b.csv", ["id", "date", "w"], [["S01", "2026-03-01", "1"]])
    result, _ = run_merge([(a, "id", "date", "date"),
                           (b, "id", "date", "date")], dup_policy="mean")
    assert result.rows[0][3] == ""          # 값이 다르면 지어내지 않고 비운다


def test_dup_policy_mean_keeps_identical_text():
    a = make_frame("a.csv", ["id", "date", "메모"],
                   [["S01", "2026-03-01", "정상"], ["S01", "2026-03-01", "정상"]])
    b = make_frame("b.csv", ["id", "date", "w"], [["S01", "2026-03-01", "1"]])
    result, _ = run_merge([(a, "id", "date", "date"),
                           (b, "id", "date", "date")], dup_policy="mean")
    assert result.rows[0][3] == "정상"


# --------------------------------------------------------------------------
# 시점 허용오차
# --------------------------------------------------------------------------

def test_tolerance_snaps_to_the_nearest_base_date():
    base = make_frame("base.csv", ["id", "date", "v"],
                      [["S01", "2026-03-10", "1"]])
    other = make_frame("o.csv", ["id", "date", "w"],
                       [["S01", "2026-03-11", "9"]])
    result, _ = run_merge([(base, "id", "date", "date"),
                           (other, "id", "date", "date")],
                          tolerance=1, how="inner")
    assert len(result.rows) == 1
    assert result.rows[0][1] == "2026-03-10"


def test_tolerance_tie_is_reported_and_not_merged():
    """앞뒤로 같은 거리면 둘 중 아무거나 고르지 않는다 — 그 행은 빠진다."""
    base = make_frame("base.csv", ["id", "date", "v"],
                      [["S01", "2026-03-09", "1"], ["S01", "2026-03-11", "2"]])
    other = make_frame("o.csv", ["id", "date", "w"],
                       [["S01", "2026-03-10", "9"]])
    result, issues = run_merge([(base, "id", "date", "date"),
                                (other, "id", "date", "date")], tolerance=1)
    assert any(i.kind == "시점충돌" for i in issues)
    assert all("9" not in row for row in result.rows)
    _assert_ledger_balances(result)


def test_two_timepoints_snapping_onto_one_base_date_drop_both():
    """두 시점이 같은 기준 날짜로 끌려오면 한쪽이 조용히 사라지면 안 된다."""
    base = make_frame("base.csv", ["id", "date", "v"],
                      [["S01", "2026-03-10", "1"]])
    other = make_frame("o.csv", ["id", "date", "w"],
                       [["S01", "2026-03-10", "9"], ["S01", "2026-03-11", "8"]])
    result, issues = run_merge([(base, "id", "date", "date"),
                                (other, "id", "date", "date")], tolerance=1)
    assert any(i.kind == "시점충돌" for i in issues)
    _assert_ledger_balances(result)
    # 어느 쪽도 몰래 채택되지 않았다.
    assert all("9" not in row and "8" not in row for row in result.rows)


def test_tolerance_zero_does_nothing():
    base = make_frame("base.csv", ["id", "date", "v"],
                      [["S01", "2026-03-10", "1"]])
    other = make_frame("o.csv", ["id", "date", "w"],
                       [["S01", "2026-03-11", "9"]])
    result, _ = run_merge([(base, "id", "date", "date"),
                           (other, "id", "date", "date")], tolerance=0)
    assert len(result.rows) == 2          # 붙지 않고 각각 남는다


# --------------------------------------------------------------------------
# 조인 방식
# --------------------------------------------------------------------------

def test_inner_outer_left_key_sets():
    a = make_frame("a.csv", ["id", "date", "v"],
                   [["S01", "2026-03-01", "1"], ["S02", "2026-03-01", "2"]])
    b = make_frame("b.csv", ["id", "date", "w"],
                   [["S02", "2026-03-01", "9"], ["S03", "2026-03-01", "8"]])
    pairs = [(a, "id", "date", "date"), (b, "id", "date", "date")]
    assert len(run_merge(pairs, how="inner")[0].rows) == 1
    assert len(run_merge(pairs, how="outer")[0].rows) == 3
    assert len(run_merge(pairs, how="left")[0].rows) == 2


def test_empty_file_still_balances():
    a = make_frame("a.csv", ["id", "date", "v"], [])
    b = make_frame("b.csv", ["id", "date", "w"], [["S01", "2026-03-01", "9"]])
    result, _ = run_merge([(a, "id", "date", "date"), (b, "id", "date", "date")])
    _assert_ledger_balances(result)
    assert len(result.rows) == 1


def test_single_row_files():
    a = make_frame("a.csv", ["id", "date", "v"], [["S01", "2026-03-01", "1"]])
    b = make_frame("b.csv", ["id", "date", "w"], [["S01", "2026-03-01", "9"]])
    result, _ = run_merge([(a, "id", "date", "date"), (b, "id", "date", "date")],
                          how="inner")
    assert len(result.rows) == 1
    _assert_ledger_balances(result)


def test_column_prefix_keeps_same_named_columns_apart():
    a = make_frame("a.csv", ["id", "date", "tst"], [["S01", "2026-03-01", "1"]])
    b = make_frame("b.csv", ["id", "date", "tst"], [["S01", "2026-03-01", "9"]])
    result, _ = run_merge([(a, "id", "date", "date"), (b, "id", "date", "date")])
    assert "a_tst" in result.header and "b_tst" in result.header
    assert len(set(result.header)) == len(result.header)
