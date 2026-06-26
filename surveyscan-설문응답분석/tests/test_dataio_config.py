"""CSV 로딩과 config 검증 테스트."""
import json

import pytest

from surveyscan.config import ConfigError, auto_config, load_config
from surveyscan.dataio import DataError, load_csv


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_csv_basic_and_missing(tmp_path):
    csv = "ID,Q1,Q2\nS1,1,2\nS2,,3\nS3,NA,4\n"
    path = write(tmp_path, "d.csv", csv)
    data = load_csv(path, id_columns=["ID"])
    assert data.columns == ["Q1", "Q2"]
    assert data.n_respondents == 3
    # 빈칸과 NA는 결측
    assert data.rows[1]["Q1"] is None
    assert data.rows[2]["Q1"] is None
    assert data.present_values("Q1") == [1.0]
    assert data.present_values("Q2") == [2.0, 3.0, 4.0]


def test_load_csv_na_number(tmp_path):
    csv = "Q1,Q2\n1,999\n2,3\n"
    path = write(tmp_path, "d.csv", csv)
    data = load_csv(path, na_numbers=[999])
    assert data.rows[0]["Q2"] is None
    assert data.present_values("Q2") == [3.0]


def test_load_csv_skips_blank_lines(tmp_path):
    csv = "Q1,Q2\n1,2\n\n3,4\n"
    path = write(tmp_path, "d.csv", csv)
    data = load_csv(path)
    assert data.n_respondents == 2


def test_load_csv_ragged_row_errors(tmp_path):
    csv = "Q1,Q2\n1,2\n3\n"
    path = write(tmp_path, "d.csv", csv)
    with pytest.raises(DataError):
        load_csv(path)


def test_load_csv_duplicate_header_errors(tmp_path):
    csv = "Q1,Q1\n1,2\n"
    path = write(tmp_path, "d.csv", csv)
    with pytest.raises(DataError):
        load_csv(path)


def test_load_csv_header_only_errors(tmp_path):
    path = write(tmp_path, "d.csv", "Q1,Q2\n")
    with pytest.raises(DataError):
        load_csv(path)


def test_load_csv_bom_handled(tmp_path):
    # 엑셀 저장본의 BOM(﻿) 제거 확인
    path = write(tmp_path, "d.csv", "﻿Q1,Q2\n1,2\n")
    data = load_csv(path)
    assert data.columns == ["Q1", "Q2"]


def test_config_valid(tmp_path):
    cfg = {
        "scale_min": 1,
        "scale_max": 5,
        "subscales": {"A": ["Q1", "Q2"], "B": ["Q3"]},
        "reverse_items": ["Q2"],
    }
    path = write(tmp_path, "c.json", json.dumps(cfg))
    c = load_config(path)
    assert c.subscales["A"] == ["Q1", "Q2"]
    assert c.reverse_set() == {"Q2"}
    assert c.all_items() == ["Q1", "Q2", "Q3"]


def test_config_reverse_requires_scale(tmp_path):
    cfg = {"subscales": {"A": ["Q1"]}, "reverse_items": ["Q1"]}
    path = write(tmp_path, "c.json", json.dumps(cfg))
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_reverse_unknown_item(tmp_path):
    cfg = {
        "scale_min": 1,
        "scale_max": 5,
        "subscales": {"A": ["Q1"]},
        "reverse_items": ["Q9"],
    }
    path = write(tmp_path, "c.json", json.dumps(cfg))
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_bad_min_valid_ratio(tmp_path):
    cfg = {"subscales": {"A": ["Q1"]}, "min_valid_ratio": 1.5}
    path = write(tmp_path, "c.json", json.dumps(cfg))
    with pytest.raises(ConfigError):
        load_config(path)


def test_auto_config():
    c = auto_config(["Q1", "Q2"])
    assert c.subscales == {"전체": ["Q1", "Q2"]}
    with pytest.raises(ConfigError):
        auto_config([])
