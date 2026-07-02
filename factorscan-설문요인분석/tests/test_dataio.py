"""dataio: CSV 로딩·문항선택·역문항·결측처리 검증."""
import numpy as np
import pytest

from factorscan.dataio import (DataError, apply_reverse, listwise, load_csv,
                               reverse_range_violations, select_items)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_and_select_numeric(tmp_path):
    path = write(tmp_path, "d.csv",
                 "ID,Q1,Q2,note\nS1,1,2,hi\nS2,3,4,bye\nS3,5,1,x\n")
    cols = load_csv(path)
    ds = select_items(cols, id_cols=["ID"])
    # note 열은 숫자값이 없어 자동 제외, Q1/Q2만 선택
    assert ds.names == ["Q1", "Q2"]
    assert ds.data.shape == (3, 2)
    assert ds.data[0, 0] == 1.0


def test_explicit_items_missing_column(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\n1,2\n3,4\n")
    cols = load_csv(path)
    with pytest.raises(DataError, match="없는 문항 열"):
        select_items(cols, items=["Q1", "Q9"])


def test_missing_values_become_nan_and_listwise(tmp_path):
    path = write(tmp_path, "d.csv",
                 "Q1,Q2,Q3\n1,2,3\n2,,4\n3,4,5\nNA,1,2\n")
    cols = load_csv(path)
    ds = select_items(cols, items=["Q1", "Q2", "Q3"])
    assert np.isnan(ds.data[1, 1])   # 빈칸
    assert np.isnan(ds.data[3, 0])   # 'NA'
    prep = listwise(ds)
    assert prep.n_total == 4
    assert prep.n_used == 2          # 2개 행에 결측 → 제거
    assert prep.n_dropped == 2


def test_constant_column_dropped_on_autoselect(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\n5,1\n5,2\n5,3\n5,4\n")
    cols = load_csv(path)
    ds = select_items(cols)          # Q1은 상수 → 제외
    assert ds.names == ["Q2"]


def test_explicit_constant_item_errors(tmp_path):
    # 명시적으로 지정한 문항이 상수면 조용히 빠뜨리지 않고 오류를 내야 한다(P3).
    path = write(tmp_path, "d.csv", "Q1,Q2\n5,1\n5,2\n5,3\n5,4\n")
    cols = load_csv(path)
    with pytest.raises(DataError, match="분산 0"):
        select_items(cols, items=["Q1", "Q2"])


def test_reverse_range_violations(tmp_path):
    # 선언 범위를 벗어난 값 개수를 문항별로 센다(P4).
    path = write(tmp_path, "d.csv", "Q1,Q2\n1,2\n7,3\n0,4\n5,5\n")
    cols = load_csv(path)
    ds = select_items(cols, items=["Q1", "Q2"])
    viol = reverse_range_violations(ds, ["Q1"], 1, 5)
    assert viol == {"Q1": 2}      # 7 과 0 이 [1,5] 밖
    assert reverse_range_violations(ds, ["Q2"], 1, 5) == {}


def test_reverse_scoring(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\n1,5\n5,1\n3,3\n")
    cols = load_csv(path)
    ds = select_items(cols, items=["Q1", "Q2"])
    rev = apply_reverse(ds, ["Q1"], 1, 5)
    # 1->5, 5->1, 3->3  (6 - x)
    assert list(rev.data[:, 0]) == [5.0, 1.0, 3.0]
    assert list(rev.data[:, 1]) == [5.0, 1.0, 3.0]   # Q2 그대로


def test_reverse_unknown_item(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\n1,2\n3,4\n")
    cols = load_csv(path)
    ds = select_items(cols, items=["Q1", "Q2"])
    with pytest.raises(DataError, match="역문항 목록"):
        apply_reverse(ds, ["Q9"], 1, 5)


def test_duplicate_columns_rejected(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q1\n1,2\n3,4\n")
    with pytest.raises(DataError, match="중복된 열"):
        load_csv(path)


def test_empty_file(tmp_path):
    path = write(tmp_path, "d.csv", "")
    with pytest.raises(DataError, match="빈 파일"):
        load_csv(path)


def test_header_only(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\n")
    with pytest.raises(DataError, match="데이터 행이 없"):
        load_csv(path)


def test_no_numeric_columns(tmp_path):
    path = write(tmp_path, "d.csv", "A,B\nx,y\nz,w\n")
    cols = load_csv(path)
    with pytest.raises(DataError, match="숫자형 문항 열"):
        select_items(cols)


def test_short_rows_padded(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2,Q3\n1,2\n3,4,5\n6,7,8\n")
    cols = load_csv(path)
    ds = select_items(cols, items=["Q1", "Q2", "Q3"])
    assert np.isnan(ds.data[0, 2])   # 빠진 Q3 → NaN
