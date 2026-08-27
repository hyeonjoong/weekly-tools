"""가명화·날짜 이동과 그 자체검증."""

from __future__ import annotations

import datetime as _dt

import pytest

from deidaudit.columns import profile_table
from deidaudit.dates import parse_date
from deidaudit.pseudo import (
    MAX_SHIFT_DAYS,
    VerificationError,
    build_plan,
    key_rows,
    normalize_subject,
    transform_table,
    verify_plan,
    verify_transform,
)
from deidaudit.tabular import Table


def _diary_table(sheet: str = "") -> Table:
    rows = []
    base = _dt.date(2026, 3, 14)
    for i, sid in enumerate(("S01", "S02")):
        for visit in range(3):
            day = base + _dt.timedelta(days=i * 3 + visit * 7)
            night = _dt.datetime.combine(day - _dt.timedelta(days=1), _dt.time(23, 40))
            rows.append([sid, "김현중", day.isoformat(), night.strftime("%Y-%m-%d %H:%M"), str(400 + visit)])
    return Table(
        file="diary.csv", sheet=sheet,
        columns=["subject_id", "name", "visit_date", "night_start", "TST_min"], rows=rows,
    )


def _transform(table, plan, drop=(), shift=True, replace=True):
    profiles = profile_table(table)
    link = table.column_index("subject_id")
    result = transform_table(
        table=table, profiles=profiles, plan=plan, link_index=link,
        drop_columns=drop, shift_dates=shift, fallback_subject="(없음)", replace_id=replace,
    )
    checks = verify_transform(
        table=table, result=result, plan=plan, link_index=link,
        shift_dates=shift, fallback_subject="(없음)",
        profiles=profiles, replace_id=replace,
    )
    return result, checks


def test_plan_is_deterministic_for_same_salt():
    a = build_plan(["S01", "S02", "S03"], salt="abc")
    b = build_plan(["S03", "S01", "S02"], salt="abc")
    assert a.pseudonyms == b.pseudonyms
    assert a.offsets == b.offsets


def test_plan_differs_for_different_salt():
    a = build_plan(["S01", "S02", "S03"], salt="abc")
    b = build_plan(["S01", "S02", "S03"], salt="xyz")
    assert a.offsets != b.offsets


def test_offsets_are_within_range_and_never_zero():
    plan = build_plan([f"S{i:03d}" for i in range(500)], salt="s")
    for offset in plan.offsets.values():
        assert offset != 0
        assert -MAX_SHIFT_DAYS <= offset <= MAX_SHIFT_DAYS


def test_pseudonyms_are_injective():
    plan = build_plan([f"S{i:03d}" for i in range(500)], salt="s")
    assert len(set(plan.pseudonyms.values())) == len(plan.pseudonyms)
    assert verify_plan(plan)


def test_same_subject_gets_same_pseudonym_across_files():
    plan = build_plan(["S01", "S02"], salt="s")
    t1 = _diary_table()
    t2 = Table(file="ut.csv", sheet="", columns=["subject_id", "score"], rows=[["S01", "3"], ["S02", "4"]])
    r1, _ = _transform(t1, plan)
    profiles = profile_table(t2)
    r2 = transform_table(
        table=t2, profiles=profiles, plan=plan, link_index=0,
        drop_columns=(), shift_dates=True, fallback_subject="(없음)",
    )
    assert r1.rows[0][0] == r2.rows[0][0]


def test_within_subject_intervals_are_preserved():
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    result, checks = _transform(table, plan)
    for sid in ("S01", "S02"):
        before = [parse_date(r[2]).value for r in table.rows if r[0] == sid]
        after = [parse_date(r[result.columns.index("visit_date")]).value
                 for r, src in zip(result.rows, table.rows) if src[0] == sid]
        gaps_before = [(before[i + 1] - before[i]).days for i in range(len(before) - 1)]
        gaps_after = [(after[i + 1] - after[i]).days for i in range(len(after) - 1)]
        assert gaps_before == gaps_after == [7, 7]
    assert any("간격" in c for c in checks)


def test_night_attribution_is_preserved():
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    result, checks = _transform(table, plan)
    night_index = result.columns.index("night_start")
    for src, out in zip(table.rows, result.rows):
        offset = plan.offset(normalize_subject(src[0]))
        src_night = parse_date(src[3]).value
        out_night = parse_date(out[night_index]).value
        assert (out_night - src_night).days == offset
        assert out_night.time() == src_night.time() == _dt.time(23, 40)
    assert any("야간" in c for c in checks)


def test_dates_actually_move():
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    result, _ = _transform(table, plan)
    assert result.rows[0][2] != table.rows[0][2]


def test_shift_dates_false_leaves_dates_untouched():
    plan = build_plan(["S01", "S02"], salt="s", shift_dates=False)
    table = _diary_table()
    result, _ = _transform(table, plan, shift=False)
    assert [r[2] for r in result.rows] == [r[2] for r in table.rows]


def test_drop_columns_removes_only_named_columns():
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    result, _ = _transform(table, plan, drop=["name"])
    assert "name" not in result.columns
    assert result.dropped_columns == ["name"]
    assert len(result.columns) == len(table.columns) - 1


def test_replace_id_false_keeps_original_ids_but_still_shifts_per_subject():
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    result, _ = _transform(table, plan, replace=False)
    assert result.rows[0][0] == "S01"
    offsets = set()
    for src, out in zip(table.rows, result.rows):
        offsets.add((parse_date(out[2]).value - parse_date(src[2]).value).days)
    assert offsets == {plan.offset("S01"), plan.offset("S02")}


def test_verification_catches_a_broken_shift():
    """검증이 실제로 잡는지 — 일부러 한 셀을 어긋나게 만듭니다."""
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    profiles = profile_table(table)
    link = table.column_index("subject_id")
    result = transform_table(
        table=table, profiles=profiles, plan=plan, link_index=link,
        drop_columns=(), shift_dates=True, fallback_subject="(없음)",
    )
    broken = parse_date(result.rows[0][2]).value + _dt.timedelta(days=1)
    result.rows[0][2] = broken.strftime("%Y-%m-%d")
    with pytest.raises(VerificationError):
        verify_transform(table=table, result=result, plan=plan, link_index=link,
                         shift_dates=True, fallback_subject="(없음)")


def test_verification_catches_dropped_rows():
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    profiles = profile_table(table)
    link = table.column_index("subject_id")
    result = transform_table(
        table=table, profiles=profiles, plan=plan, link_index=link,
        drop_columns=(), shift_dates=True, fallback_subject="(없음)",
    )
    result.rows.pop()
    with pytest.raises(VerificationError):
        verify_transform(table=table, result=result, plan=plan, link_index=link,
                         shift_dates=True, fallback_subject="(없음)")


def test_verify_plan_rejects_colliding_pseudonyms():
    plan = build_plan(["S01", "S02"], salt="s")
    keys = list(plan.pseudonyms)
    plan.pseudonyms[keys[1]] = plan.pseudonyms[keys[0]]
    with pytest.raises(VerificationError):
        verify_plan(plan)


def test_empty_subject_ids_are_grouped_and_warned():
    plan = build_plan(["S01", "", "  "], salt="s")
    assert "(빈ID)" in plan.pseudonyms
    assert plan.warnings


def test_key_rows_contain_offset():
    plan = build_plan(["S01", "S02"], salt="s")
    rows = key_rows(plan)
    assert len(rows) == 2
    for original, pseudo, offset in rows:
        assert plan.pseudonyms[original] == pseudo
        assert offset == plan.offsets[original]


# --- 검증이 실제로 막아야 하는 공격들 ---------------------------------------
# "자기가 한 일을 자기가 신고한 목록으로 검사"하면 아래 셋이 전부 통과합니다.


def _make(table, plan, shift=True, replace=True, drop=()):
    profiles = profile_table(table)
    link = table.column_index("subject_id")
    result = transform_table(
        table=table, profiles=profiles, plan=plan, link_index=link,
        drop_columns=drop, shift_dates=shift, fallback_subject="(없음)", replace_id=replace,
    )
    return profiles, link, result


def _verify(table, result, plan, link, profiles, shift=True, replace=True):
    return verify_transform(
        table=table, result=result, plan=plan, link_index=link,
        shift_dates=shift, fallback_subject="(없음)",
        profiles=profiles, replace_id=replace,
    )


def test_verification_catches_ids_put_back(): 
    """공격 1: 날짜는 제대로 옮기고 ID 만 원본으로 되돌린다."""
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    profiles, link, result = _make(table, plan)
    for out_row, src_row in zip(result.rows, table.rows):
        out_row[0] = src_row[0]
    with pytest.raises(VerificationError, match="가명"):
        _verify(table, result, plan, link, profiles)


def test_verification_catches_an_unrelated_column_being_overwritten():
    """공격 2: 날짜·ID 는 손대지 않고 다른 열을 통째로 999 로 덮는다."""
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    profiles, link, result = _make(table, plan)
    tst = result.columns.index("TST_min")
    for out_row in result.rows:
        out_row[tst] = "999"
    with pytest.raises(VerificationError, match="변경"):
        _verify(table, result, plan, link, profiles)


def test_verification_catches_shifting_nothing_and_claiming_nothing():
    """공격 3: 아무 날짜도 옮기지 않고 '옮길 열이 없었다'고 신고한다."""
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    profiles, link, result = _make(table, plan)
    for out_row, src_row in zip(result.rows, table.rows):
        out_row[2] = src_row[2]
        out_row[3] = src_row[3]
    result.shifted_columns = []
    with pytest.raises(VerificationError, match="이동되지 않았습니다"):
        _verify(table, result, plan, link, profiles)


def test_verification_names_the_year_when_a_shift_leaves_the_supported_range():
    """1900-01-01 은 레지스트리의 '미상' 센티널로 흔합니다 — 진단 가능한 메시지여야 합니다."""
    plan = build_plan(["S01"], salt="s")
    table = Table(
        file="y1900.csv", sheet="", columns=["subject_id", "visit_date"],
        rows=[["S01", "1900-01-05"]],
    )
    profiles = profile_table(table)
    link = 0
    result = transform_table(
        table=table, profiles=profiles, plan=plan, link_index=link,
        drop_columns=(), shift_dates=True, fallback_subject="(없음)",
    )
    if plan.offset("S01") < 0:
        with pytest.raises(VerificationError, match="1900"):
            verify_transform(table=table, result=result, plan=plan, link_index=link,
                             shift_dates=True, fallback_subject="(없음)",
                             profiles=profiles, replace_id=True)


def test_week_aligned_offsets_preserve_day_of_week():
    plan = build_plan(["S01", "S02"], salt="s", week_aligned=True)
    table = _diary_table()
    profiles, link, result = _make(table, plan)
    _verify(table, result, plan, link, profiles)
    for src, out in zip(table.rows, result.rows):
        assert parse_date(src[2]).value.weekday() == parse_date(out[2]).value.weekday()
    assert plan.week_aligned


def test_verification_catches_a_column_dropped_without_being_asked():
    """공격 4: 지정하지 않은 열을 몰래 빼고 '뺐다'고 신고한다."""
    plan = build_plan(["S01", "S02"], salt="s")
    table = _diary_table()
    profiles, link, result = _make(table, plan)
    victim = result.columns.index("TST_min")
    name = result.columns[victim]
    result.columns = [c for c in result.columns if c != name]
    result.rows = [[c for i, c in enumerate(row) if i != victim] for row in result.rows]
    result.dropped_columns = [name]
    with pytest.raises(VerificationError, match="요청하지 않았는데 사라진 열"):
        verify_transform(
            table=table, result=result, plan=plan, link_index=link,
            shift_dates=True, fallback_subject="(없음)",
            profiles=profiles, replace_id=True, drop_columns=[],
        )
