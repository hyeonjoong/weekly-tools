"""Round 4 적대적 하드닝 회귀 테스트 (2026-07-31).

4개 독립 패널(정확성·엣지·문서정직성·테스트품질/PII)이 찾은 결함과, 변이 테스트에서
살아남았던 변이(= 테스트 공백)를 각각 고정한다.
"""

import gzip
import json
import os
from datetime import datetime, timedelta

import pytest

from logflow.cli import main
from logflow.dataio import load_events
from logflow.groups import (
    SMALL_CELL_THRESHOLD,
    churn_survival,
    compare_groups,
    filter_to_groups,
)
from logflow.dataio import Event
from logflow.stats import holm_adjust, kaplan_meier, mann_whitney_u

BASE = datetime(2026, 1, 1, 9, 0, 0)


def ev(user, name, day, hour=9, minute=0, group=None):
    """테스트용 이벤트 — 패키지 상대 임포트 없이 쓰도록 여기서 정의(실행 위치 무관)."""
    return Event(user=user, name=name,
                 ts=BASE.replace(hour=hour, minute=minute) + timedelta(days=day),
                 group=group)


def two_arm_events():
    """A군 4명 중 3명이 day-1 재방문, B군 4명 중 1명."""
    events = [ev(u, "open", 0, group="A") for u in ["a1", "a2", "a3", "a4"]]
    events += [ev(u, "open", 1, group="A") for u in ["a1", "a2", "a3"]]
    events += [ev(u, "open", 0, group="B") for u in ["b1", "b2", "b3", "b4"]]
    events.append(ev("b1", "open", 1, group="B"))
    events.append(ev("a1", "open", 9, group="A"))
    return sorted(events, key=lambda e: (e.ts, e.user))


# ================================================================ 통계 공백 (변이 테스트)

def test_mann_whitney_tie_and_continuity_corrections_are_applied():
    """동점 보정과 연속성 보정이 둘 다 살아 있는지 — 값으로 고정.

    보정을 빼면 p 가 눈에 띄게 달라진다(동점 보정 제거 시 0.2413, 연속성 보정
    제거 시 0.2149). 실제 로그는 사용자당 이벤트 수가 대량으로 동점이라 중요하다.
    """
    x = [1, 1, 1, 2, 2, 2, 3, 3, 3, 4]
    y = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    r = mann_whitney_u(x, y)
    assert r.p == pytest.approx(0.22959006559238004, abs=1e-9)
    # z 는 방향을 담는다: x 의 U 가 기대값보다 작으므로 음수
    assert r.z == pytest.approx(-1.2014155024329285, abs=1e-9)
    assert mann_whitney_u(y, x).z == pytest.approx(1.2014155024329285, abs=1e-9)
    # 보정을 뺀 값들과는 확실히 달라야 한다
    assert abs(r.p - 0.2413) > 1e-3     # 동점 보정 제거 시
    assert abs(r.p - 0.2149) > 1e-3     # 연속성 보정 제거 시


def test_km_median_when_survival_lands_exactly_on_half():
    """S(t) 가 정확히 0.5 인 경우의 중앙값 (<= 0.5 경계)."""
    km = kaplan_meier([1.0, 2.0, 3.0, 4.0], [True] * 4)
    assert [p.survival for p in km.points] == pytest.approx([0.75, 0.5, 0.25, 0.0])
    assert km.median_survival == 2.0


def test_group_retention_eligibility_boundary_is_inclusive():
    """day-N 이 관찰 지평과 정확히 같으면 eligible 에 포함돼야 한다 (n > horizon 만 제외)."""
    events = [
        ev("a1", "open", 0, group="A"), ev("a1", "open", 1, group="A"),
        ev("b1", "open", 0, group="B"),
    ]
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)), retention_days=[1])
    arm_a = next(a for a in g.arms if a.group == "A")
    arm_b = next(a for a in g.arms if a.group == "B")
    assert arm_a.retention[1] == (1, 1)     # day-1 재방문함
    assert arm_b.retention[1] == (0, 1)     # 기회는 있었으나 안 옴 (eligible 0 이 아님)


def test_group_retention_respects_rolling_mode():
    """군 비교에서도 exact/rolling 이 실제로 다르게 동작해야 한다."""
    # a1 은 day 0 과 day 2 활동 → exact day-1 은 이탈, rolling day-1 은 잔존
    events = [
        ev("a1", "open", 0, group="A"), ev("a1", "open", 2, group="A"),
        ev("b1", "open", 0, group="B"), ev("b1", "open", 2, group="B"),
    ]
    events = sorted(events, key=lambda e: (e.ts, e.user))
    exact = compare_groups(events, retention_days=[1], retention_mode="exact")
    rolling = compare_groups(events, retention_days=[1], retention_mode="rolling")
    assert next(a for a in exact.arms if a.group == "A").retention[1] == (0, 1)
    assert next(a for a in rolling.arms if a.group == "A").retention[1] == (1, 1)


def _holm_of(p, all_ps):
    """Holm 정의로부터 독립 계산: 정렬해 (m-k)를 곱하고 누적 최대."""
    m = len(all_ps)
    running = 0.0
    for k, q in enumerate(sorted(all_ps)):
        running = max(running, (m - k) * q)
        if q == p:
            return min(1.0, running)
    raise AssertionError("p not found")


def test_holm_adjusted_values_are_attached_to_the_right_tests():
    """p(Holm) 이 자기 검정의 raw p 에서 나온 값이어야 한다 (인덱스 어긋남 방지)."""
    events = []
    for i in range(10):
        events.append(ev(f"a{i}", "open", 0, group="A"))
        events.append(ev(f"a{i}", "open", 1, group="A"))
        for k in range(5):
            events.append(ev(f"a{i}", "open", 2, hour=9, minute=k * 5, group="A"))
    for i in range(10):
        events.append(ev(f"b{i}", "open", 0, group="B"))
        if i < 2:
            events.append(ev(f"b{i}", "open", 1, group="B"))
    events.append(ev("a0", "open", 9, group="A"))
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)),
                       retention_days=[1], churn_days=3, reference="B")

    raws = [t.p_value for t in g.proportions]
    raws += [t.result.p for t in g.distributions]
    if g.survival and g.survival.logrank:
        raws.append(g.survival.logrank.p)
    assert len(raws) == g.n_tests
    # 값이 같은 검정은 보정값도 같으므로 매핑 검증에 무해하지만, 종류별로는 달라야
    # 비율/분포/생존 사이의 인덱스 어긋남을 잡을 수 있다.
    assert len(set(raws)) >= 3

    for t in g.proportions:
        assert t.p_adjusted == pytest.approx(_holm_of(t.p_value, raws))
    for t in g.distributions:
        assert t.p_adjusted == pytest.approx(_holm_of(t.result.p, raws))
    if g.survival and g.survival.logrank:
        assert g.survival.p_adjusted == pytest.approx(
            _holm_of(g.survival.logrank.p, raws)
        )


def test_holm_monotonicity_is_enforced():
    """누적 최대(step-down)를 빼면 정렬된 입력에서 역전이 생긴다."""
    adj = holm_adjust([0.02, 0.021, 0.022])
    assert adj == sorted(adj)
    assert adj[0] == pytest.approx(0.06)
    assert adj[1] == pytest.approx(0.06)   # 2*0.021=0.042 < 0.06 → 0.06 으로 끌어올림


# ================================================================ 정직성 (조용한 누락 없음)

def test_skipped_retention_comparison_is_reported(tmp_path, capsys):
    """한 군의 eligible 이 0명이면 행을 조용히 빼지 말고 이유를 남긴다."""
    # B군은 마지막 날에만 등장 → day-1 관찰 기회 없음
    events = [
        ev("a1", "open", 0, group="A"), ev("a1", "open", 1, group="A"),
        ev("a2", "open", 0, group="A"),
        ev("b1", "open", 1, group="B"), ev("b2", "open", 1, group="B"),
    ]
    g = compare_groups(sorted(events, key=lambda e: (e.ts, e.user)), retention_days=[1])
    assert not any(t.label == "day-1 리텐션" for t in g.proportions)
    assert any("day-1 리텐션 비교 불가" in n for n in g.notes)


def test_small_arm_triggers_reidentification_warning():
    events = two_arm_events()
    g = compare_groups(events, retention_days=[1])
    assert all(a.n_users >= SMALL_CELL_THRESHOLD for a in g.arms) or any(
        "재식별" in n for n in g.notes
    )
    tiny = compare_groups(
        sorted([ev("a1", "open", 0, group="A"), ev("a1", "open", 1, group="A"),
                ev("b1", "open", 0, group="B"), ev("b1", "open", 1, group="B")],
               key=lambda e: (e.ts, e.user)),
        retention_days=[1],
    )
    assert any("재식별" in n for n in tiny.notes)


def test_dedup_keeps_rows_that_differ_only_by_group(tmp_path):
    """군 라벨만 다른 행은 중복이 아니라 모순 — 지우면 충돌 경고가 사라진다."""
    p = tmp_path / "log.csv"
    p.write_text(
        "user_id,event,timestamp,arm\n"
        "u1,open,2026-01-01T09:00:00,A\n"
        "u1,open,2026-01-01T09:00:00,B\n"
        "u1,open,2026-01-02T09:00:00,A\n",
        encoding="utf-8",
    )
    counters = {}
    events = load_events(str(p), group_col="arm", dedup=True, counters=counters)
    assert counters["deduped"] == 0
    g = compare_groups(events, retention_days=[1])
    assert g.conflicting_users == 1


def test_confidence_level_in_group_table_header_matches_flag(tmp_path, capsys):
    p = tmp_path / "log.csv"
    p.write_text(
        "user_id,event,timestamp,arm\n"
        "a1,o,2026-01-01T09:00:00,A\na1,o,2026-01-02T09:00:00,A\n"
        "a2,o,2026-01-01T09:00:00,A\n"
        "b1,o,2026-01-01T09:00:00,B\nb1,o,2026-01-02T09:00:00,B\n"
        "b2,o,2026-01-01T09:00:00,B\n",
        encoding="utf-8",
    )
    assert main([str(p), "--group-col", "arm", "--confidence", "0.99"]) == 0
    out = capsys.readouterr().out
    assert "99%CI" in out
    assert "95%CI" not in out.split("[ 군 비교 ]")[1]


def test_three_arms_show_caveat_and_no_reference_label(tmp_path, capsys):
    p = tmp_path / "log.csv"
    rows = ["user_id,event,timestamp,arm"]
    for i, arm in enumerate(["A", "A", "B", "B", "C", "C"]):
        rows.append(f"u{i},o,2026-01-01T09:00:00,{arm}")
        rows.append(f"u{i},o,2026-01-02T09:00:00,{arm}")
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main([str(p), "--group-col", "arm", "--ref-group", "A"]) == 0
    section = capsys.readouterr().out.split("[ 군 비교 ]")[1]
    assert "기준군" not in section.splitlines()[0]
    assert "사후(post-hoc)" in section          # 검정이 없어도 캐비앗은 남는다


def test_holm_family_size_caveat_present(tmp_path, capsys):
    assert main([str(_two_arm_csv(tmp_path)), "--group-col", "arm"]) == 0
    assert "--retention/--funnel 에 따라 달라지므로" in capsys.readouterr().out


def test_usage_time_definition_footnote_present(tmp_path, capsys):
    assert main([str(_two_arm_csv(tmp_path)), "--group-col", "arm"]) == 0
    out = capsys.readouterr().out
    assert "사용시간 = 각 세션의" in out
    assert "이벤트가 하나뿐인 세션은" in out


def _two_arm_csv(tmp_path):
    p = tmp_path / "two.csv"
    p.write_text(
        "user_id,event,timestamp,arm\n"
        "a1,o,2026-01-01T09:00:00,A\na1,o,2026-01-02T09:00:00,A\n"
        "a2,o,2026-01-01T09:00:00,A\n"
        "b1,o,2026-01-01T09:00:00,B\nb1,o,2026-01-02T09:00:00,B\n"
        "b2,o,2026-01-01T09:00:00,B\n",
        encoding="utf-8",
    )
    return p


# ================================================================ --only-groups

def test_filter_to_groups_keeps_only_requested_arms():
    events = [
        ev("a1", "open", 0, group="A"), ev("b1", "open", 0, group="B"),
        ev("c1", "open", 0, group="C"), ev("a1", "open", 1, group="A"),
    ]
    kept, dropped = filter_to_groups(events, ["A", "B"])
    assert {e.user for e in kept} == {"a1", "b1"}
    assert dropped == 1


def test_filter_to_groups_rejects_unknown_label():
    with pytest.raises(ValueError, match="데이터에 없는 군"):
        filter_to_groups([ev("a1", "open", 0, group="A")], ["Z"])


def test_only_groups_enables_tests_on_three_arm_data(tmp_path, capsys):
    p = tmp_path / "log.csv"
    rows = ["user_id,event,timestamp,arm"]
    for i, arm in enumerate(["A", "A", "B", "B", "C", "C"]):
        rows.append(f"u{i},o,2026-01-01T09:00:00,{arm}")
        rows.append(f"u{i},o,2026-01-02T09:00:00,{arm}")
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main([str(p), "--group-col", "arm"]) == 0
    assert "검정은 생략" in capsys.readouterr().out
    assert main([str(p), "--group-col", "arm", "--only-groups", "A,B", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["groups"]["groups"] == ["A", "B"]
    assert payload["groups"]["n_tests"] > 0
    assert "사용자 2명을 제외했습니다" in captured.err


def test_only_groups_requires_group_col(tmp_path, capsys):
    assert main([str(_two_arm_csv(tmp_path)), "--only-groups", "A"]) == 1
    assert "--group-col 과 함께" in capsys.readouterr().err


def test_only_groups_unknown_label_errors(tmp_path, capsys):
    assert main([str(_two_arm_csv(tmp_path)), "--group-col", "arm",
                 "--only-groups", "Z"]) == 1
    assert "데이터에 없는 군" in capsys.readouterr().err


# ================================================================ 파일 안전 / 자원

def test_csv_dir_refuses_to_overwrite_the_input_file(tmp_path, capsys):
    """--csv-dir 표 이름(users.csv 등)이 입력과 겹치면 원본이 사라지면 안 된다."""
    d = tmp_path / "d"
    d.mkdir()
    src = d / "users.csv"
    src.write_text(
        "user_id,event,timestamp\nu1,o,2026-01-01T09:00:00\nu2,o,2026-01-02T09:00:00\n",
        encoding="utf-8",
    )
    before = src.read_bytes()
    assert main([str(src), "--csv-dir", str(d)]) == 1
    assert "입력 파일과 같습니다" in capsys.readouterr().err
    assert src.read_bytes() == before          # 원본 그대로
    assert not (d / "retention.csv").exists()  # 부분 저장도 없어야 한다


def test_csv_dir_reports_overwritten_files(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "retention.csv").write_text("기존 내용", encoding="utf-8")
    assert main([str(_two_arm_csv(tmp_path)), "--csv-dir", str(out)]) == 0
    assert "덮어썼습니다" in capsys.readouterr().err


def test_max_rows_stops_before_exhausting_memory(tmp_path, capsys):
    p = tmp_path / "log.csv"
    rows = ["user_id,event,timestamp"]
    rows += [f"u{i},o,2026-01-01T09:00:00" for i in range(50)]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main([str(p), "--max-rows", "10"]) == 1
    assert "--max-rows(10)" in capsys.readouterr().err
    assert main([str(p), "--max-rows", "0"]) == 0     # 0 = 제한 없음


def test_negative_max_rows_rejected(tmp_path, capsys):
    assert main([str(_two_arm_csv(tmp_path)), "--max-rows", "-1"]) == 1
    assert "--max-rows" in capsys.readouterr().err


# ================================================================ 손상 입력 (트레이스백 금지)

def test_truncated_gzip_gives_clean_error(tmp_path, capsys):
    src = _two_arm_csv(tmp_path)
    p = tmp_path / "t.csv.gz"
    p.write_bytes(gzip.compress(src.read_bytes())[:15])
    assert main([str(p)]) == 1
    err = capsys.readouterr().err
    assert "압축 파일이 손상" in err
    assert "Traceback" not in err


def test_non_gzip_with_gzip_magic_gives_clean_error(tmp_path, capsys):
    p = tmp_path / "f.csv.gz"
    p.write_bytes(b"\x1f\x8b" + b"not really gzip content")
    assert main([str(p)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert err.startswith("오류:")


def test_directory_input_gives_clean_error(tmp_path, capsys):
    d = tmp_path / "adir"
    d.mkdir()
    assert main([str(d)]) == 2
    err = capsys.readouterr().err
    assert "폴더는 읽을 수 없습니다" in err
    assert "Traceback" not in err


def test_oversized_csv_field_gives_clean_error(tmp_path, capsys):
    p = tmp_path / "big.csv"
    p.write_text('user_id,event,timestamp\nu1,"' + ("x" * 200000)
                 + '",2026-01-01T09:00:00\n', encoding="utf-8")
    assert main([str(p)]) == 1
    err = capsys.readouterr().err
    assert "CSV 를 읽을 수 없습니다" in err
    assert "Traceback" not in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 오류를 만들 수 없음")
def test_unreadable_file_gives_clean_error(tmp_path, capsys):
    p = _two_arm_csv(tmp_path)
    p.chmod(0o000)
    try:
        assert main([str(p)]) == 2
        err = capsys.readouterr().err
        assert "권한이 없습니다" in err
        assert "Traceback" not in err
    finally:
        p.chmod(0o644)


def test_extreme_numeric_flags_are_rejected_consistently(tmp_path, capsys):
    p = _two_arm_csv(tmp_path)
    assert main([str(p), "--gap-min", "1e308"]) == 1
    assert "--gap-min" in capsys.readouterr().err
    # --json 여부와 무관하게 같은 결과여야 한다 (예전엔 한쪽만 실패했다)
    assert main([str(p), "--gap-min", "1e308", "--json"]) == 1
    assert "--gap-min" in capsys.readouterr().err
    assert main([str(p), "--tz-offset", "1e300"]) == 1
    assert "--tz-offset" in capsys.readouterr().err


# ================================================================ 표시 안전성

def test_group_label_with_newline_does_not_break_the_table(tmp_path, capsys):
    p = tmp_path / "log.csv"
    p.write_text(
        'user_id,event,timestamp,arm\n'
        '"a1",o,2026-01-01T09:00:00,"중재\n군"\n'
        '"a1",o,2026-01-02T09:00:00,"중재\n군"\n'
        'b1,o,2026-01-01T09:00:00,대조\n'
        'b1,o,2026-01-02T09:00:00,대조\n',
        encoding="utf-8",
    )
    assert main([str(p), "--group-col", "arm"]) == 0
    out = capsys.readouterr().out
    section = out.split("[ 군 비교 ]")[1].split("[ 상위 사용자 ]")[0]
    assert "중재 군" in section          # 개행이 공백으로 평탄화
    from logflow.report import _dw
    assert max(_dw(l) for l in section.splitlines()) <= 110


def test_very_long_group_label_is_truncated_for_display(tmp_path, capsys):
    long_label = "가" * 300
    p = tmp_path / "log.csv"
    p.write_text(
        f"user_id,event,timestamp,arm\n"
        f"a1,o,2026-01-01T09:00:00,{long_label}\n"
        f"a1,o,2026-01-02T09:00:00,{long_label}\n"
        f"b1,o,2026-01-01T09:00:00,대조\nb1,o,2026-01-02T09:00:00,대조\n",
        encoding="utf-8",
    )
    assert main([str(p), "--group-col", "arm"]) == 0
    out = capsys.readouterr().out
    from logflow.report import _dw
    section = out.split("[ 군 비교 ]")[1].split("[ 상위 사용자 ]")[0]
    assert max(_dw(l) for l in section.splitlines()) <= 110
    assert "…" in section


def test_json_output_keeps_the_raw_group_label(tmp_path, capsys):
    """표시용 잘라내기가 JSON/CSV 원본까지 훼손하면 안 된다."""
    long_label = "가" * 300
    p = tmp_path / "log.csv"
    p.write_text(
        f"user_id,event,timestamp,arm\n"
        f"a1,o,2026-01-01T09:00:00,{long_label}\n"
        f"b1,o,2026-01-01T09:00:00,대조\n",
        encoding="utf-8",
    )
    assert main([str(p), "--group-col", "arm", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert long_label in payload["groups"]["groups"]


# ================================================================ 입력 형식 감지

def test_gzipped_jsonl_named_csv_gz_is_detected_by_content(tmp_path):
    p = tmp_path / "log.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write('{"user_id":"u1","event":"o","timestamp":"2026-01-01T09:00:00"}\n')
        fh.write('{"user_id":"u2","event":"o","timestamp":"2026-01-02T09:00:00"}\n')
    events = load_events(str(p))
    assert [e.user for e in events] == ["u1", "u2"]


def test_nested_json_value_is_treated_as_missing_not_repr(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        json.dumps({"user_id": "u1", "event": {"nested": 1},
                    "timestamp": "2026-01-01T09:00:00"}) + "\n"
        + json.dumps({"user_id": "u2", "event": "o",
                      "timestamp": "2026-01-02T09:00:00"}) + "\n",
        encoding="utf-8",
    )
    counters = {}
    events = load_events(str(p), counters=counters)
    assert [e.name for e in events] == ["o"]
    assert counters["skipped_missing"] == 1


def test_jsonl_missing_column_error_mentions_the_peek_limit(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(json.dumps({"user_id": "u1", "event": "o",
                             "timestamp": "2026-01-01T09:00:00"}) + "\n",
                 encoding="utf-8")
    with pytest.raises(ValueError, match="줄에서만 열 이름을 찾습니다"):
        load_events(str(p), group_col="arm")


def test_duplicate_retention_days_do_not_inflate_the_holm_family(tmp_path, capsys):
    """--retention 1,1 은 같은 가설을 두 번 세는 것 — family 가 커져 p(Holm) 이 부풀면 안 된다."""
    p = _two_arm_csv(tmp_path)
    assert main([str(p), "--group-col", "arm", "--retention", "1", "--json"]) == 0
    once = json.loads(capsys.readouterr().out)["groups"]
    assert main([str(p), "--group-col", "arm", "--retention", "1,1,1", "--json"]) == 0
    thrice = json.loads(capsys.readouterr().out)["groups"]
    assert once["n_tests"] == thrice["n_tests"]
    assert [t["p_holm"] for t in once["proportion_tests"]] == \
           [t["p_holm"] for t in thrice["proportion_tests"]]
    assert len(thrice["proportion_tests"]) == len(once["proportion_tests"])
