"""피험자 ID 정규화 — 붙어야 할 것은 붙고, **붙으면 안 되는 것은 절대 안 붙는다.**

두 번째가 훨씬 중요하다. 임상 데이터에서 `S01` 과 `S02` 를 편집거리로 이어
붙이면 두 사람의 자료가 한 사람 것이 되고, 그 사실은 아무 데도 남지 않는다.
"""

from __future__ import annotations

import pytest

from conftest import write_rows
from joinaudit.dataio import LoadError
from joinaudit.keys import (AliasTable, KeyNormalizer, canonical_key,
                            common_head, load_alias_table, strip_common_head)


# --------------------------------------------------------------------------
# 결정론적 정규화 규칙
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("S01", "S1"),          # 제로패딩
    ("S001", "S1"),
    ("s01", "S1"),          # 대소문자
    (" S01 ", "S1"),        # 앞뒤 공백
    ("S 01", "S1"),         # 내부 공백
    ("S 01", "S1"),    # NBSP
    ("Ｓ０１", "S1"),        # 전각
    ("S1", "S1"),
])
def test_canonical_key_collapses_only_表記_differences(raw, expected):
    assert canonical_key(raw) == expected


def test_canonical_key_keeps_distinct_subjects_distinct():
    """이 툴에서 가장 중요한 성질: 한 글자 차이는 **다른 사람**이다."""
    keys = [canonical_key(v) for v in ("S01", "S02", "S10", "S11", "S21")]
    assert len(set(keys)) == 5
    assert canonical_key("S01") != canonical_key("S02")
    assert canonical_key("S1") != canonical_key("S11")


def test_all_zero_id_keeps_one_zero():
    assert canonical_key("S000") == "S0"


def test_prefix_removal_requires_the_prefix_to_be_present():
    assert canonical_key("BELL-001-07", prefixes=["BELL-001-"]) == "7"
    # 접두어가 없는 값은 그대로 둔다(억지로 떼지 않는다).
    assert canonical_key("S07", prefixes=["BELL-001-"]) == "S7"
    # ID 전체가 접두어면 빈 키가 되면 안 되므로 떼지 않는다.
    assert canonical_key("BELL-001-", prefixes=["BELL-001-"]) == "BELL-001-"


def test_zero_pad_can_be_switched_off():
    assert canonical_key("S01", zero_pad=False) == "S01"


# --------------------------------------------------------------------------
# 자동 접두어
# --------------------------------------------------------------------------

def test_auto_prefix_only_cuts_at_a_separator():
    """`S01..S26` 의 공통 접두어 `S` 를 떼면 숫자만 남아 남의 ID 와 붙는다."""
    assert KeyNormalizer.detect_common_prefix(["S01", "S02", "S03"]) == ""
    assert KeyNormalizer.detect_common_prefix(
        ["BELL-001-01", "BELL-001-02"]) == "BELL-001-"


def test_auto_prefix_refuses_when_stripping_would_collide():
    # 접두어를 떼고 남은 구분자까지 걷어내면 `A--1` 과 `A-1` 이 둘 다 `1` 이 된다.
    # 서로 다른 두 사람이 한 사람이 되므로 접두어 제거 자체를 포기해야 한다.
    assert KeyNormalizer.detect_common_prefix(["A--1", "A-1"]) == ""
    # 떼도 서로 구분되면 정상 동작한다.
    assert KeyNormalizer.detect_common_prefix(["A-1", "A-2"]) == "A-"


def test_auto_prefix_needs_at_least_two_values():
    assert KeyNormalizer.detect_common_prefix(["BELL-001-01"]) == ""


def test_normalizer_reports_what_it_actually_did():
    norm = KeyNormalizer()
    keys, stats = norm.normalize_column(
        "watch.csv", ["S01", " S02 ", "Ｓ０３", "s04", ""])
    assert keys == ["S1", "S2", "S3", "S4", ""]
    assert stats.zero_pad == 4
    assert stats.whitespace == 1
    assert stats.fullwidth == 1
    assert stats.missing == 1


def test_collisions_within_one_file_are_reported():
    norm = KeyNormalizer()
    _keys, stats = norm.normalize_column("a.csv", ["S01", "S1", "S02"])
    assert stats.collisions == {"S1": ["S01", "S1"]}


def test_display_id_prefers_the_fully_padded_short_form():
    norm = KeyNormalizer()
    norm.normalize_column("a.csv", ["S1"])
    norm.normalize_column("b.csv", ["S01"])
    assert norm.display_id("S1") == "S01"


# --------------------------------------------------------------------------
# 별칭표
# --------------------------------------------------------------------------

def test_alias_table_round_trip(tmp_path):
    path = write_rows(str(tmp_path / "alias.csv"),
                      [["파일", "원본ID", "표준ID"],
                       ["watch.csv", "피험자7", "S07"],
                       ["", "레거시03", "S03"]],
                      encoding="utf-8-sig")
    table = load_alias_table(path)
    assert table.lookup("watch.csv", "피험자7") == "S07"
    assert table.lookup("다른파일.csv", "피험자7") is None      # 파일 범위를 지킨다
    assert table.lookup("아무파일.csv", "레거시03") == "S03"    # '*' 범위
    assert table.lookup("watch.csv", "없는ID") is None


def test_alias_beats_the_rules():
    table = AliasTable(entries={("*", "피험자7"): "S07"})
    norm = KeyNormalizer(alias=table)
    keys, stats = norm.normalize_column("watch.csv", ["피험자7", "S08"])
    assert keys == ["S7", "S8"]
    assert stats.alias == 1


def test_alias_table_missing_columns_is_an_error(tmp_path):
    path = write_rows(str(tmp_path / "bad.csv"), [["a", "b"], ["1", "2"]])
    with pytest.raises(LoadError):
        load_alias_table(path)


# --------------------------------------------------------------------------
# 머리말 통일 (--unify-id-heads)
# --------------------------------------------------------------------------

def test_common_head_only_when_it_is_constant():
    assert common_head(["S1", "S2", "S16"]) == "S"
    assert common_head(["1", "2", "16"]) == ""
    # 머리말이 둘이면 사람을 구분하는 정보일 수 있다 → 손대지 않는다.
    assert common_head(["C1", "P1"]) is None
    # 숫자로 끝나지 않는 키가 하나라도 있으면 규칙이 성립하지 않는다.
    assert common_head(["S1", "S1A"]) is None


def test_strip_common_head_is_identity_when_undecidable():
    assert list(strip_common_head(["C1", "P1"])) == ["C1", "P1"]
    assert list(strip_common_head(["S1", "S2"])) == ["1", "2"]


def test_unify_heads_is_off_by_default():
    norm = KeyNormalizer()
    keys, _ = norm.normalize_column("a.csv", ["S01", "S02"])
    assert keys == ["S1", "S2"]


def test_unify_heads_makes_three_notations_meet():
    """같은 사람을 `S07` / `BELL-001-07` / `07` 로 적은 세 파일."""
    watch = KeyNormalizer(unify_heads=True)
    diary = KeyNormalizer(unify_heads=True)
    isi = KeyNormalizer(unify_heads=True)
    a, stats_a = watch.normalize_column("watch.csv", ["S06", "S07"])
    b, stats_b = diary.normalize_column("diary.csv",
                                        ["BELL-001-06", "BELL-001-07"])
    c, stats_c = isi.normalize_column("isi.csv", ["06", "07"])
    assert a == b == c == ["6", "7"]
    # 각 파일이 **다른 규칙으로** 같은 키에 도달했음을 못박는다:
    # watch 는 머리말 'S' 통일, diary 는 접두어 'BELL-001-' 제거, isi 는 그대로.
    assert stats_a.head_value == "S" and stats_a.head == 2
    assert stats_b.prefix_value == "BELL-001-" and stats_b.prefix == 2
    assert stats_c.head_value == "" and stats_c.prefix == 0


def test_unify_heads_still_keeps_different_subjects_apart():
    norm = KeyNormalizer(unify_heads=True)
    keys, _ = norm.normalize_column("a.csv", ["S01", "S02", "S10"])
    assert keys == ["1", "2", "10"]
    assert len(set(keys)) == 3
