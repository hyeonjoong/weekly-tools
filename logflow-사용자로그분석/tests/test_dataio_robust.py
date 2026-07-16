import os
import tempfile
from datetime import date

import pytest

from logflow.dataio import load_events


def _write(text, encoding="utf-8"):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)
    return path


def _write_bytes(data):
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def test_semicolon_delimiter_autodetected():
    path = _write("user_id;event;timestamp\nu1;a;2026-01-01T09:00:00\nu2;b;2026-01-01T10:00:00\n")
    try:
        evs = load_events(path)
        assert [e.user for e in evs] == ["u1", "u2"]
    finally:
        os.remove(path)


def test_tab_delimiter_autodetected():
    path = _write("user_id\tevent\ttimestamp\nu1\ta\t2026-01-01T09:00:00\n")
    try:
        assert [e.user for e in load_events(path)] == ["u1"]
    finally:
        os.remove(path)


def test_explicit_delimiter_overrides():
    path = _write("user_id|event|timestamp\nu1|a|2026-01-01T09:00:00\n")
    try:
        assert [e.user for e in load_events(path, delimiter="|")] == ["u1"]
    finally:
        os.remove(path)


def test_header_whitespace_and_case_tolerant():
    path = _write(" User_ID , Event , TimeStamp \nu1,a,2026-01-01T09:00:00\n")
    try:
        evs = load_events(path, user_col="user_id", event_col="event", time_col="timestamp")
        assert [e.user for e in evs] == ["u1"]
    finally:
        os.remove(path)


def test_dedup_removes_exact_duplicates():
    path = _write(
        "user_id,event,timestamp\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u1,a,2026-01-01T09:00:00\n"   # exact dup
        "u1,a,2026-01-01T09:00:01\n"   # different ts -> kept
    )
    try:
        counters = {}
        evs = load_events(path, dedup=True, counters=counters)
        assert len(evs) == 2
        assert counters["deduped"] == 1
        # without dedup, all three kept
        assert len(load_events(path)) == 3
    finally:
        os.remove(path)


def test_date_range_filtering():
    path = _write(
        "user_id,event,timestamp\n"
        "u1,a,2026-01-01T09:00:00\n"
        "u1,a,2026-01-05T09:00:00\n"
        "u1,a,2026-01-10T09:00:00\n"
    )
    try:
        counters = {}
        evs = load_events(path, date_from=date(2026, 1, 3), date_to=date(2026, 1, 8),
                          counters=counters)
        assert [e.ts.date() for e in evs] == [date(2026, 1, 5)]
        assert counters["filtered"] == 2
    finally:
        os.remove(path)


def test_date_from_after_to_raises():
    path = _write("user_id,event,timestamp\nu1,a,2026-01-05T09:00:00\n")
    try:
        with pytest.raises(ValueError):
            load_events(path, date_from=date(2026, 1, 9), date_to=date(2026, 1, 1))
    finally:
        os.remove(path)


def test_date_filter_emptying_everything_raises():
    path = _write("user_id,event,timestamp\nu1,a,2026-01-05T09:00:00\n")
    try:
        with pytest.raises(ValueError):
            load_events(path, date_from=date(2026, 2, 1))
    finally:
        os.remove(path)


def test_non_utf8_raises_clean_valueerror():
    # cp949-encoded Korean event name, read as utf-8 -> decode error surfaced as ValueError
    path = _write_bytes("user_id,event,timestamp\nu1,".encode("utf-8")
                        + "호흡".encode("cp949")
                        + ",2026-01-01T09:00:00\n".encode("utf-8"))
    try:
        with pytest.raises(ValueError):
            load_events(path, encoding="utf-8")
        # correct encoding works
        evs = load_events(path, encoding="cp949")
        assert evs[0].name == "호흡"
    finally:
        os.remove(path)


def test_dedup_and_filter_counters_default_present():
    path = _write("user_id,event,timestamp\nu1,a,2026-01-01T09:00:00\n")
    try:
        counters = {}
        load_events(path, counters=counters)
        for key in ("skipped_missing", "skipped_bad", "deduped", "filtered"):
            assert key in counters
    finally:
        os.remove(path)
