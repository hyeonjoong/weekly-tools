"""DEBUSSY 매니페스트 읽기 + 교란 후보표 (통계 검정 없음)."""
from __future__ import annotations

import os

import pytest

from stimaudit import manifest

HEADER = ("file,duration_s,roughness_asper,sharpness_acum,spectral_centroid_hz,"
          "modulation_peak_hz,lyrics,notes\n")
ROWS = ("a.wav,3.0,0.05,0.62,201.4,0.0,unknown,ok\n"
        "b.wav,3.0,0.07,0.71,940.2,1.2,unknown,ok\n"
        "c.wav,3.0,0.30,1.40,3200.0,0.0,unknown,ok\n")


def _write(tmp_path, text=HEADER + ROWS, name="m.csv"):
    p = os.path.join(str(tmp_path), name)
    open(p, "w", encoding="utf-8").write(text)
    return p


def test_loads_numeric_columns(tmp_path):
    m = manifest.load(_write(tmp_path))
    assert set(m.metrics) == {"a.wav", "b.wav", "c.wav"}
    assert "roughness_asper" in m.columns
    assert "sharpness_acum" in m.columns
    assert m.metrics["a.wav"]["roughness_asper"] == 0.05


def test_non_numeric_columns_skipped(tmp_path):
    m = manifest.load(_write(tmp_path))
    assert "lyrics" in m.skipped_columns
    assert "notes" in m.skipped_columns
    assert "lyrics" not in m.columns


def test_basename_used_as_key(tmp_path):
    text = HEADER + "/Users/x/sounds/a.wav,3.0,0.05,0.62,201.4,0.0,unknown,ok\n"
    m = manifest.load(_write(tmp_path, text))
    assert "a.wav" in m.metrics


def test_bom_is_handled(tmp_path):
    p = os.path.join(str(tmp_path), "bom.csv")
    open(p, "w", encoding="utf-8-sig").write(HEADER + ROWS)
    assert len(manifest.load(p).metrics) == 3


def test_alternative_file_column_name(tmp_path):
    text = "Output_file,x\n" + "a.wav,1\nb.wav,2\n"
    m = manifest.load(_write(tmp_path, text))
    assert set(m.metrics) == {"a.wav", "b.wav"}


def test_first_column_used_when_unnamed(tmp_path):
    text = "id,x\n" + "a.wav,1\nb.wav,2\n"
    assert set(manifest.load(_write(tmp_path, text)).metrics) == {"a.wav", "b.wav"}


def test_missing_file_error(tmp_path):
    with pytest.raises(manifest.ManifestError) as e:
        manifest.load(os.path.join(str(tmp_path), "nope.csv"))
    assert "찾을 수 없" in str(e.value)


def test_empty_manifest_error(tmp_path):
    with pytest.raises(manifest.ManifestError):
        manifest.load(_write(tmp_path, ""))


def test_header_only_manifest_error(tmp_path):
    with pytest.raises(manifest.ManifestError) as e:
        manifest.load(_write(tmp_path, HEADER))
    assert "파일 행" in str(e.value)


def test_non_utf8_error(tmp_path):
    p = os.path.join(str(tmp_path), "m.csv")
    open(p, "wb").write("file,x\n한글.wav,1\n".encode("euc-kr"))
    with pytest.raises(manifest.ManifestError) as e:
        manifest.load(p)
    assert "UTF-8" in str(e.value)


def test_ragged_rows_tolerated(tmp_path):
    text = "file,x,y\na.wav,1\nb.wav,2,3\n"
    m = manifest.load(_write(tmp_path, text))
    assert m.metrics["b.wav"]["y"] == 3.0
    assert "y" not in m.metrics["a.wav"]


def test_confound_table_orders_contrast_first(tmp_path):
    m = manifest.load(_write(tmp_path))
    rows, missing = manifest.confound_table(
        m, {"x": ["a.wav"], "y": ["c.wav"]}, "modulation_peak_hz")
    assert missing == []
    assert rows[0].column == "modulation_peak_hz"
    assert rows[0].is_contrast is True
    assert all(not r.is_contrast for r in rows[1:])


def test_confound_table_sorted_by_relative_difference(tmp_path):
    """단위가 큰 지표가 항상 앞에 오면 순위가 '단위 크기' 순이 됩니다."""
    m = manifest.load(_write(tmp_path))
    rows, _ = manifest.confound_table(m, {"x": ["a.wav"], "y": ["c.wav"]}, None)
    rel = [r.relative_diff for r in rows if r.relative_diff is not None]
    assert rel == sorted(rel, reverse=True)


def test_scale_free_ranking_beats_raw_magnitude(tmp_path):
    """러프니스 10배 차이(0.05→0.5)가 중심주파수 3배 차이보다 위에 와야 합니다."""
    text = ("file,roughness_asper,spectral_centroid_hz\n"
            "a.wav,0.05,1000\n"
            "c.wav,0.50,3000\n")
    m = manifest.load(_write(tmp_path, text))
    rows, _ = manifest.confound_table(m, {"x": ["a.wav"], "y": ["c.wav"]}, None)
    assert rows[0].column == "roughness_asper"
    # 원시 차이로 정렬했다면 2000 > 0.45 라 중심주파수가 앞에 왔을 것입니다.
    assert rows[0].max_diff == pytest.approx(0.45)
    assert rows[1].column == "spectral_centroid_hz"


def test_relative_diff_is_none_without_data(tmp_path):
    m = manifest.load(_write(tmp_path))
    rows, _ = manifest.confound_table(m, {"x": ["a.wav"], "y": ["ghost.wav"]}, None)
    assert all(r.relative_diff is None for r in rows)


def test_nan_and_inf_cells_are_not_numeric(tmp_path):
    """`최대차이 nan` 같은 무의미한 줄이 교란 후보표에 생기면 안 됩니다."""
    text = "file,good,bad\na.wav,1.0,nan\nb.wav,2.0,inf\n"
    m = manifest.load(_write(tmp_path, text))
    assert "good" in m.columns
    assert "bad" not in m.columns
    assert "bad" in m.skipped_columns


def test_oversized_cell_is_a_clean_error(tmp_path):
    """csv 모듈의 필드 상한(131072자)에서 트레이스백이 나면 안 됩니다."""
    text = "file,val\na.wav," + "9" * 200000 + "\n"
    p = _write(tmp_path, text)
    import csv as _csv
    old = _csv.field_size_limit(1000)
    try:
        with pytest.raises(manifest.ManifestError) as e:
            manifest.load(p)
        assert "해석할 수 없" in str(e.value)
    finally:
        _csv.field_size_limit(old)


def test_confound_table_computes_condition_means(tmp_path):
    m = manifest.load(_write(tmp_path))
    rows, _ = manifest.confound_table(m, {"x": ["a.wav", "b.wav"], "y": ["c.wav"]}, None)
    rough = next(r for r in rows if r.column == "roughness_asper")
    assert rough.per_condition["x"] == pytest.approx(0.06)
    assert rough.per_condition["y"] == pytest.approx(0.30)
    assert rough.max_diff == pytest.approx(0.24)
    assert set(rough.max_pair) == {"x", "y"}


def test_confound_table_reports_missing_files(tmp_path):
    m = manifest.load(_write(tmp_path))
    rows, missing = manifest.confound_table(m, {"x": ["a.wav"], "y": ["ghost.wav"]}, None)
    assert missing == ["ghost.wav"]


def test_confound_table_handles_condition_with_no_data(tmp_path):
    m = manifest.load(_write(tmp_path))
    rows, _ = manifest.confound_table(m, {"x": ["a.wav"], "y": ["ghost.wav"]}, None)
    rough = next(r for r in rows if r.column == "roughness_asper")
    assert rough.per_condition["y"] is None
    assert rough.max_diff is None


def test_covered(tmp_path):
    m = manifest.load(_write(tmp_path))
    assert m.covered(["a.wav", "ghost.wav"]) == ["a.wav"]


def test_manifest_values_are_not_recomputed(tmp_path):
    """경계 — 매니페스트의 asper 값은 그대로 통과해야 합니다(자체 계산 금지)."""
    m = manifest.load(_write(tmp_path))
    assert m.metrics["c.wav"]["roughness_asper"] == 0.30
    assert m.metrics["c.wav"]["sharpness_acum"] == 1.40
