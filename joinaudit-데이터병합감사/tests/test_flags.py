"""옵션 하나하나가 **실제로 결과를 바꾸는가**.

적대적 리뷰가 짚은 대로, 초록불 테스트 스위트를 그대로 둔 채 11개 플래그의
배선을 끊을 수 있었다. 여기 있는 테스트는 전부 "그 플래그를 끄면 숫자가
달라진다"를 확인한다 — 플래그가 argparse 에만 있고 아무 일도 안 하면 실패한다.
"""

from __future__ import annotations

import csv
import os

import pytest

from conftest import write_rows, write_text, write_xlsx
from joinaudit.cli import main


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def merged(out_dir):
    return read_csv(os.path.join(out_dir, "merged.csv"))


def run(tmp_path, files, args, name="o"):
    out = str(tmp_path / name)
    code = main([*files, "--out-dir", out, *args])
    return code, out


# --------------------------------------------------------------------------
# --night-cutoff
# --------------------------------------------------------------------------

def test_night_cutoff_actually_moves_the_boundary(tmp_path):
    """05:00 기록은 컷오프 04:00 이면 당일 밤, 06:00 이면 전날 밤이다."""
    watch = write_rows(str(tmp_path / "w.csv"),
                       [["subject_id", "measured_at", "v"],
                        ["S01", "2026-03-11 05:00", "1"]])
    diary = write_rows(str(tmp_path / "d.csv"),
                       [["subject_id", "날짜", "w"], ["S01", "2026-03-10", "9"]])
    files, args = [watch, diary], ["--align", "night", "--how", "inner"]

    _c, early = run(tmp_path, files, args + ["--night-cutoff", "04:00"], "a")
    _c, late = run(tmp_path, files, args + ["--night-cutoff", "06:00"], "b")
    assert len(merged(early)) == 1                 # 헤더뿐 — 붙지 않는다
    assert len(merged(late)) == 2                  # 전날 밤으로 귀속돼 붙는다


# --------------------------------------------------------------------------
# --alias
# --------------------------------------------------------------------------

def test_alias_table_changes_the_subject_count(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["피험자7", "2026-03-10", "1"], ["S08", "2026-03-10", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"],
                    ["S07", "2026-03-10", "9"], ["S08", "2026-03-10", "8"]])
    alias = write_rows(str(tmp_path / "alias.csv"),
                       [["파일", "원본ID", "표준ID"], ["a.csv", "피험자7", "S07"]])

    _c, without = run(tmp_path, [a, b], [], "no")
    _c, with_ = run(tmp_path, [a, b], ["--alias", alias], "yes")
    assert len(merged(without)) == 4               # 헤더 + 3명
    assert len(merged(with_)) == 3                 # 헤더 + 2명 — 실제로 붙었다


# --------------------------------------------------------------------------
# --unify-id-heads
# --------------------------------------------------------------------------

def test_unify_id_heads_changes_the_subject_count(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["S01", "2026-03-10", "1"], ["S02", "2026-03-10", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"],
                    ["01", "2026-03-10", "9"], ["02", "2026-03-10", "8"]])
    _c, off = run(tmp_path, [a, b], [], "off")
    _c, on = run(tmp_path, [a, b], ["--unify-id-heads"], "on")
    assert len(merged(off)) == 5                   # 4명(붙지 않음)
    assert len(merged(on)) == 3                    # 2명


# --------------------------------------------------------------------------
# --tolerance-days
# --------------------------------------------------------------------------

def test_tolerance_days_changes_the_row_count(tmp_path):
    base = write_rows(str(tmp_path / "base.csv"),
                      [["subject_id", "date", "v"], ["S01", "2026-03-10", "1"]])
    other = write_rows(str(tmp_path / "other.csv"),
                       [["subject_id", "date", "w"], ["S01", "2026-03-11", "9"]])
    _c, zero = run(tmp_path, [base, other], ["--tolerance-days", "0"], "z")
    _c, one = run(tmp_path, [base, other], ["--tolerance-days", "1"], "o")
    assert len(merged(zero)) == 3                  # 두 행이 따로 남는다
    assert len(merged(one)) == 2                   # 한 행으로 맞물린다


# --------------------------------------------------------------------------
# --dup-policy
# --------------------------------------------------------------------------

@pytest.mark.parametrize("policy, expected", [
    ("error", ""), ("first", "10"), ("last", "30"), ("mean", "20"),
])
def test_dup_policy_picks_the_stated_value(tmp_path, policy, expected):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["S01", "2026-03-10", "10"], ["S01", "2026-03-10", "30"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    _c, out = run(tmp_path, [a, b], ["--dup-policy", policy], policy)
    table = merged(out)
    row = dict(zip(table[0], table[1]))
    assert row["a_v"] == expected, row
    # 어떤 정책에서도 행이 늘어나지 않는다.
    assert len(table) == 2


# --------------------------------------------------------------------------
# --how / --base
# --------------------------------------------------------------------------

@pytest.mark.parametrize("how, rows", [("inner", 1), ("outer", 3), ("left", 2)])
def test_how_changes_the_key_set(tmp_path, how, rows):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["S01", "2026-03-10", "1"], ["S02", "2026-03-10", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"],
                    ["S02", "2026-03-10", "9"], ["S03", "2026-03-10", "8"]])
    _c, out = run(tmp_path, [a, b], ["--how", how], how)
    assert len(merged(out)) == rows + 1


def test_base_selects_which_file_left_join_keeps(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["S01", "2026-03-10", "1"], ["S02", "2026-03-10", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S03", "2026-03-10", "9"]])
    _c, first = run(tmp_path, [a, b], ["--how", "left"], "f")
    _c, second = run(tmp_path, [a, b], ["--how", "left", "--base", "b.csv"], "s")
    assert {r[0] for r in merged(first)[1:]} == {"S01", "S02"}
    assert {r[0] for r in merged(second)[1:]} == {"S03"}


# --------------------------------------------------------------------------
# --no-key-normalize / --no-auto-prefix
# --------------------------------------------------------------------------

def test_no_key_normalize_keeps_zero_padding_distinct(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"], ["S1", "2026-03-10", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    _c, on = run(tmp_path, [a, b], [], "on")
    _c, off = run(tmp_path, [a, b], ["--no-key-normalize"], "off")
    assert len(merged(on)) == 2                    # S1 과 S01 이 한 사람
    assert len(merged(off)) == 3                   # 서로 다른 사람으로 남는다


def test_no_auto_prefix_keeps_the_prefix(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"],
                    ["BELL-001-01", "2026-03-10", "1"],
                    ["BELL-001-02", "2026-03-10", "2"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"],
                    ["01", "2026-03-10", "9"], ["02", "2026-03-10", "8"]])
    _c, on = run(tmp_path, [a, b], ["--how", "inner"], "on")
    _c, off = run(tmp_path, [a, b], ["--how", "inner", "--no-auto-prefix"], "off")
    assert len(merged(on)) == 3                    # 접두어를 떼어 2명이 붙는다
    assert len(merged(off)) == 1                   # 헤더뿐


# --------------------------------------------------------------------------
# --prefix / --sheet / --header-row
# --------------------------------------------------------------------------

def test_prefix_renames_the_output_columns(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"], ["S01", "2026-03-10", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    _c, out = run(tmp_path, [a, b], ["--prefix", "워치,일기"], "p")
    header = merged(out)[0]
    assert "워치_v" in header and "일기_w" in header
    assert not any(h.startswith("a_") for h in header)


def test_sheet_selects_the_named_worksheet(tmp_path):
    path = str(tmp_path / "book.xlsx")
    write_xlsx(path, [["subject_id", "date", "v"], ["S01", "2026-03-10", "1"]],
               sheet_name="3월")
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    _c, out = run(tmp_path, [path, b], ["--sheet", "book.xlsx=3월"], "s")
    assert len(merged(out)) == 2
    # 없는 시트를 고르면 실패해야 한다(조용히 첫 시트를 쓰면 안 된다).
    assert main([path, b, "--sheet", "book.xlsx=없는시트",
                 "--out-dir", str(tmp_path / "x")]) == 1


def test_header_row_overrides_the_detected_header(tmp_path):
    path = write_rows(str(tmp_path / "a.csv"),
                      [["안내문", "두번째", "세번째"],
                       ["subject_id", "date", "v"],
                       ["S01", "2026-03-10", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    # 안내문이 데이터만큼 넓으면 자동 탐지가 그것을 헤더로 본다 → 명시로 교정.
    _c, out = run(tmp_path, [path, b], ["--header-row", "a.csv=2"], "h")
    assert len(merged(out)) == 2
    assert any(h.endswith("_v") for h in merged(out)[0])


# --------------------------------------------------------------------------
# --date-format / --long / --spec
# --------------------------------------------------------------------------

def test_date_format_changes_how_the_date_is_read(tmp_path):
    rows = [["subject_id", "date", "v"]] + [[f"S{i:02d}", f"0{i}/02/2026", i]
                                            for i in range(1, 8)]
    a = write_rows(str(tmp_path / "a.csv"), rows)
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-02-01", "9"]])
    _c, dmy = run(tmp_path, [a, b], ["--date-format", "dmy"], "d")
    _c, mdy = run(tmp_path, [a, b], ["--date-format", "mdy"], "m")
    s01_dmy = [r for r in merged(dmy)[1:] if r[0] == "S01"]
    s01_mdy = [r for r in merged(mdy)[1:] if r[0] == "S01"]
    assert [r[1] for r in s01_dmy] == ["2026-02-01"]      # 1일 2월
    assert sorted(r[1] for r in s01_mdy) == ["2026-01-02", "2026-02-01"]


def test_spec_ranges_actually_fire(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "isi_total"],
                    ["S01", "2026-03-10", "45"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    spec = write_text(str(tmp_path / "spec.json"),
                      '{"ranges": {"isi_total": [0, 28]}}')
    code_without, _ = run(tmp_path, [a, b], [], "no")
    code_with, out = run(tmp_path, [a, b], ["--spec", spec], "yes")
    assert code_without == 0
    assert code_with == 2
    kinds = {r[4] for r in
             read_csv(os.path.join(out, "문제목록.csv"))[1:]}
    assert "범위이탈" in kinds


def test_long_format_changes_the_shape(tmp_path):
    a = write_rows(str(tmp_path / "a.csv"),
                   [["subject_id", "date", "v"], ["S01", "2026-03-10", "1"]])
    b = write_rows(str(tmp_path / "b.csv"),
                   [["subject_id", "date", "w"], ["S01", "2026-03-10", "9"]])
    _c, wide = run(tmp_path, [a, b], [], "w")
    _c, long_ = run(tmp_path, [a, b], ["--long"], "l")
    assert merged(long_)[0] == ["subject_id", "timepoint", "variable", "value"]
    assert len(merged(long_)) > len(merged(wide))
