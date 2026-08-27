"""재식별 위험(k) — 손으로 센 값과 대조합니다."""

from __future__ import annotations

from deidaudit.kanon import compute_k
from deidaudit.tabular import Table


def _table(columns, rows):
    return Table(file="t.csv", sheet="", columns=columns, rows=rows)


def test_hand_counted_equivalence_classes():
    # (M,30대) 3명 / (F,30대) 1명 / (M,40대) 2명 → 최소 k = 1
    rows = [
        ["S1", "M", "30대"], ["S2", "M", "30대"], ["S3", "M", "30대"],
        ["S4", "F", "30대"],
        ["S5", "M", "40대"], ["S6", "M", "40대"],
    ]
    result = compute_k(_table(["subject_id", "sex", "age_group"], rows), ["sex", "age_group"], id_column="subject_id")
    assert result.unit == "사람"
    assert result.n_units == 6
    assert result.n_classes == 3
    assert result.min_k == 1
    assert result.n_units_k1 == 1          # S4 한 명
    assert result.n_units_lt_target == 6   # 세 동치류(3·1·2)가 모두 5 미만 → 전원 위험
    assert dict(result.size_distribution) == {3: 1, 1: 1, 2: 1}


def test_units_lt_target_counts_distinct_people():
    rows = [["S1", "M"], ["S2", "M"], ["S3", "F"]]
    result = compute_k(_table(["id", "sex"], rows), ["sex"], id_column="id", target=5)
    # 두 동치류 모두 5 미만이므로 전원(3명)이 위험 인원입니다.
    assert result.n_units_lt_target == 3
    assert result.n_units_k1 == 1


def test_repeated_measures_do_not_inflate_k():
    """같은 사람의 여러 행이 '동치류 크기'를 키워 안전해 보이면 안 됩니다."""
    rows = []
    for sid in ("S1", "S2"):
        for week in (0, 4, 8):
            rows.append([sid, "M", "30대", str(week)])
    columns = ["subject_id", "sex", "age_group", "week"]
    by_person = compute_k(_table(columns, rows), ["sex", "age_group"], id_column="subject_id")
    by_row = compute_k(_table(columns, rows), ["sex", "age_group"], id_column=None)
    assert by_person.min_k == 2 and by_person.unit == "사람"
    assert by_row.min_k == 6 and by_row.unit == "행"     # 행으로 세면 6 — 실제보다 안전해 보입니다
    assert any("행 단위" in note for note in by_row.notes)


def test_removal_scenarios_are_computed():
    rows = [
        ["S1", "M", "30대", "1988-04-02"],
        ["S2", "M", "30대", "1988-04-03"],
        ["S3", "M", "30대", "1988-04-04"],
        ["S4", "M", "30대", "1988-04-05"],
        ["S5", "M", "30대", "1988-04-06"],
    ]
    result = compute_k(
        _table(["id", "sex", "age_group", "birth"], rows), ["sex", "age_group", "birth"], id_column="id"
    )
    assert result.min_k == 1
    labels = {s.label: s.min_k for s in result.scenarios}
    assert labels["birth"] == 5          # birth 만 빼면 전원이 한 동치류 → k = 5
    assert labels["sex"] == 1
    assert result.best_removal is not None and result.best_removal.removed == ("birth",)


def test_missing_quasi_columns_are_confessed():
    rows = [["S1", "M"], ["S2", "F"]]
    result = compute_k(_table(["id", "sex"], rows), ["sex", "birth", "site"], id_column="id")
    assert result.quasi_used == ["sex"]
    assert result.quasi_missing == ["birth", "site"]


def test_returns_none_when_no_quasi_column_present():
    rows = [["S1"], ["S2"]]
    assert compute_k(_table(["id"], rows), ["birth", "sex"], id_column="id") is None


def test_empty_values_form_their_own_class():
    rows = [["S1", ""], ["S2", ""], ["S3", "M"]]
    result = compute_k(_table(["id", "sex"], rows), ["sex"], id_column="id")
    assert dict(result.size_distribution) == {2: 1, 1: 1}


def test_unicode_normalization_groups_same_value():
    import unicodedata

    nfc = "서울"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    rows = [["S1", nfc], ["S2", nfd]]
    result = compute_k(_table(["id", "site"], rows), ["site"], id_column="id")
    assert result.min_k == 2  # 같은 값으로 묶여야 합니다
