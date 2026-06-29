import os
import tempfile
from datetime import datetime

import pytest

from logflow.dataio import Event, load_events, parse_timestamp


def test_parse_iso_basic():
    assert parse_timestamp("2026-01-01T08:00:00") == datetime(2026, 1, 1, 8, 0, 0)
    assert parse_timestamp("2026-01-01 08:00:00") == datetime(2026, 1, 1, 8, 0, 0)


def test_parse_iso_with_offset_converts_to_utc():
    # +09:00 08:00 == 23:00 UTC 전날
    assert parse_timestamp("2026-01-01T08:00:00+09:00") == datetime(2025, 12, 31, 23, 0, 0)
    assert parse_timestamp("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, 0, 0, 0)


def test_parse_epoch_seconds_and_millis():
    # 2021-01-01T00:00:00Z = 1609459200
    assert parse_timestamp("1609459200") == datetime(2021, 1, 1, 0, 0, 0)
    assert parse_timestamp("1609459200000") == datetime(2021, 1, 1, 0, 0, 0)


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse_timestamp("  ")


def _write_csv(text):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_load_events_sorted_and_skips_blank_rows():
    path = _write_csv(
        "user_id,event,timestamp\n"
        "u2,b,2026-01-01T10:00:00\n"
        "u1,a,2026-01-01T09:00:00\n"
        ",x,2026-01-01T11:00:00\n"     # 사용자 비어있음 -> skip
        "u3,,2026-01-01T12:00:00\n"    # 이벤트 비어있음 -> skip
    )
    try:
        events = load_events(path)
        assert [e.user for e in events] == ["u1", "u2"]
        assert events[0].ts < events[1].ts
    finally:
        os.remove(path)


def test_load_missing_column_raises():
    path = _write_csv("user_id,event\nu1,a\n")
    try:
        with pytest.raises(ValueError):
            load_events(path)
    finally:
        os.remove(path)


def test_load_no_data_rows_raises():
    path = _write_csv("user_id,event,timestamp\n")
    try:
        with pytest.raises(ValueError):
            load_events(path)
    finally:
        os.remove(path)


def test_load_bad_timestamp_raises():
    path = _write_csv("user_id,event,timestamp\nu1,a,not-a-date\n")
    try:
        with pytest.raises(ValueError):
            load_events(path)
    finally:
        os.remove(path)
