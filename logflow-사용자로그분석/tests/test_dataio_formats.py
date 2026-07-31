"""새 입력 형식(JSONL · gzip) 과 군 열 로딩 검증."""

import gzip
import json

import pytest

from logflow.dataio import load_events


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return str(path)


ROWS = [
    {"user_id": "u1", "event": "open", "timestamp": "2026-01-01T09:00:00", "arm": "A"},
    {"user_id": "u2", "event": "open", "timestamp": "2026-01-01T10:00:00", "arm": "B"},
    {"user_id": "u1", "event": "done", "timestamp": "2026-01-02T09:00:00", "arm": "A"},
]


# ---------------------------------------------------------------- JSONL

def test_jsonl_loads_by_extension(tmp_path):
    events = load_events(_jsonl(tmp_path / "log.jsonl", ROWS))
    assert [e.user for e in events] == ["u1", "u2", "u1"]
    assert [e.name for e in events] == ["open", "open", "done"]


def test_ndjson_extension_also_works(tmp_path):
    assert len(load_events(_jsonl(tmp_path / "log.ndjson", ROWS))) == 3


def test_jsonl_group_column(tmp_path):
    events = load_events(_jsonl(tmp_path / "log.jsonl", ROWS), group_col="arm")
    assert {e.user: e.group for e in events} == {"u1": "A", "u2": "B"}


def test_jsonl_numeric_values_are_accepted(tmp_path):
    rows = [
        {"user_id": 101, "event": "open", "timestamp": 1767261600},          # epoch 초
        {"user_id": 101, "event": "open", "timestamp": 1767261600000},       # epoch 밀리초
        {"user_id": 102.0, "event": "open", "timestamp": 1767261600.5},
    ]
    events = load_events(_jsonl(tmp_path / "log.jsonl", rows))
    assert [e.user for e in events] == ["101", "101", "102"]
    assert events[0].ts == events[1].ts       # 초/밀리초가 같은 시각으로


def test_jsonl_keys_from_later_lines_are_discovered(tmp_path):
    """첫 줄에 없는 열이 뒷줄에 나와도 열 해석에 성공해야 한다."""
    rows = [
        {"user_id": "u1", "event": "open", "timestamp": "2026-01-01T09:00:00"},
        {"user_id": "u2", "event": "open", "timestamp": "2026-01-01T09:00:00", "arm": "A"},
    ]
    events = load_events(_jsonl(tmp_path / "log.jsonl", rows), group_col="arm")
    assert [e.group for e in events] == [None, "A"]


def test_jsonl_missing_column_raises_with_helpful_message(tmp_path):
    with pytest.raises(ValueError, match="필수 열이 없습니다"):
        load_events(_jsonl(tmp_path / "log.jsonl", ROWS), user_col="없는열")


def test_jsonl_blank_lines_are_skipped(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text("\n" + json.dumps(ROWS[0]) + "\n\n" + json.dumps(ROWS[1]) + "\n",
                 encoding="utf-8")
    assert len(load_events(str(p))) == 2


def test_jsonl_broken_line_raises_with_line_number(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(json.dumps(ROWS[0]) + "\n{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2행 JSON 파싱 실패"):
        load_events(str(p))


def test_jsonl_broken_line_skipped_with_flag(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(json.dumps(ROWS[0]) + "\n{not json}\n" + json.dumps(ROWS[1]) + "\n",
                 encoding="utf-8")
    counters = {}
    events = load_events(str(p), skip_bad_rows=True, counters=counters)
    assert len(events) == 2
    assert counters["skipped_bad"] == 1


def test_jsonl_non_object_line_rejected(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(json.dumps(ROWS[0]) + "\n[1,2,3]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 객체가 아닙니다"):
        load_events(str(p))


def test_jsonl_null_and_nan_values_are_treated_as_missing(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        json.dumps({"user_id": None, "event": "open", "timestamp": "2026-01-01T09:00:00"})
        + "\n"
        + '{"user_id": "u2", "event": "open", "timestamp": NaN}\n'
        + json.dumps(ROWS[0]) + "\n",
        encoding="utf-8",
    )
    counters = {}
    events = load_events(str(p), counters=counters)
    assert len(events) == 1
    assert counters["skipped_missing"] == 2


def test_empty_jsonl_raises(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 객체가 없습니다"):
        load_events(str(p))


# ---------------------------------------------------------------- 형식 강제

def test_format_override_reads_jsonl_from_txt_extension(tmp_path):
    p = tmp_path / "log.txt"
    p.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n", encoding="utf-8")
    assert len(load_events(str(p), input_format="jsonl")) == 3


def test_format_override_reads_csv_from_jsonl_extension(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text("user_id,event,timestamp\nu1,open,2026-01-01T09:00:00\n", encoding="utf-8")
    assert len(load_events(str(p), input_format="csv")) == 1


def test_invalid_format_rejected(tmp_path):
    p = tmp_path / "log.csv"
    p.write_text("user_id,event,timestamp\nu1,open,2026-01-01T09:00:00\n", encoding="utf-8")
    with pytest.raises(ValueError, match="input_format"):
        load_events(str(p), input_format="parquet")


# ---------------------------------------------------------------- gzip

def test_gzipped_csv_is_read_transparently(tmp_path):
    p = tmp_path / "log.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write("user_id,event,timestamp,arm\nu1,open,2026-01-01T09:00:00,A\n")
    events = load_events(str(p), group_col="arm")
    assert len(events) == 1 and events[0].group == "A"


def test_gzipped_jsonl_is_read_transparently(tmp_path):
    p = tmp_path / "log.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in ROWS:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    assert len(load_events(str(p), group_col="arm")) == 3


def test_gzip_detected_by_content_not_extension(tmp_path):
    """확장자가 .csv 여도 내용이 gzip 이면 풀어서 읽는다."""
    p = tmp_path / "log.csv"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write("user_id,event,timestamp\nu1,open,2026-01-01T09:00:00\n")
    assert len(load_events(str(p))) == 1


def test_gzipped_csv_delimiter_autodetect_still_works(tmp_path):
    p = tmp_path / "log.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write("user_id;event;timestamp\nu1;open;2026-01-01T09:00:00\n"
                 "u2;open;2026-01-01T10:00:00\n")
    assert len(load_events(str(p))) == 2


# ---------------------------------------------------------------- 군 열 (CSV)

def test_csv_group_column_loaded_and_missing_value_is_none(tmp_path):
    p = tmp_path / "log.csv"
    p.write_text(
        "user_id,event,timestamp,arm\n"
        "u1,open,2026-01-01T09:00:00,중재\n"
        "u2,open,2026-01-01T09:00:00,\n"
        "u3,open,2026-01-01T09:00:00,NA\n",
        encoding="utf-8",
    )
    events = load_events(str(p), group_col="arm")
    assert {e.user: e.group for e in events} == {"u1": "중재", "u2": None, "u3": None}


def test_missing_group_column_raises(tmp_path):
    p = tmp_path / "log.csv"
    p.write_text("user_id,event,timestamp\nu1,open,2026-01-01T09:00:00\n", encoding="utf-8")
    with pytest.raises(ValueError, match="필수 열이 없습니다"):
        load_events(str(p), group_col="arm")


def test_group_column_matching_is_case_and_space_tolerant(tmp_path):
    p = tmp_path / "log.csv"
    p.write_text("user_id,event,timestamp, ARM \nu1,open,2026-01-01T09:00:00,A\n",
                 encoding="utf-8")
    events = load_events(str(p), group_col="arm")
    assert events[0].group == "A"


def test_dedup_keeps_group(tmp_path):
    p = tmp_path / "log.csv"
    p.write_text(
        "user_id,event,timestamp,arm\n"
        "u1,open,2026-01-01T09:00:00,A\n"
        "u1,open,2026-01-01T09:00:00,A\n",
        encoding="utf-8",
    )
    counters = {}
    events = load_events(str(p), group_col="arm", dedup=True, counters=counters)
    assert len(events) == 1 and events[0].group == "A"
    assert counters["deduped"] == 1
