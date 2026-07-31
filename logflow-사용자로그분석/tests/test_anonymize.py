"""사용자 ID 가명화 (--anonymize)."""

from datetime import datetime

from logflow.analyze import analyze, to_csv_tables, to_dict
from logflow.anonymize import anonymize_users, build_alias_map, choose_prefix
from logflow.dataio import Event


def _ev(user, day, hour=10, name="open", group=None):
    return Event(user=user, name=name, ts=datetime(2026, 1, day, hour), group=group)


def test_numbering_follows_first_seen_then_id():
    evs = [_ev("zed", 1), _ev("alice", 2), _ev("zed", 3)]
    alias = build_alias_map(evs)
    assert alias == {"zed": "U001", "alice": "U002"}


def test_tie_on_first_seen_breaks_by_original_id():
    evs = [_ev("b", 1), _ev("a", 1)]
    assert build_alias_map(evs) == {"a": "U001", "b": "U002"}


def test_deterministic_across_row_order():
    evs = [_ev("x", 3), _ev("y", 1), _ev("z", 2)]
    assert build_alias_map(evs) == build_alias_map(list(reversed(evs)))


def test_width_grows_past_999_users():
    evs = [Event(user=f"u{i}", name="open", ts=datetime(2026, 1, 1, 0, i % 60, i // 60))
           for i in range(1200)]
    alias = build_alias_map(evs)
    assert len(set(alias.values())) == 1200
    assert all(len(v) == 5 for v in alias.values())   # U0001 … U1200


def test_original_events_not_mutated_and_group_preserved():
    evs = [_ev("alice", 1, group="중재군"), _ev("bob", 2, group="대조군")]
    out, n, prefix = anonymize_users(evs)
    assert n == 2 and prefix == "U"
    assert [e.user for e in evs] == ["alice", "bob"]          # 원본 불변
    assert [e.user for e in out] == ["U001", "U002"]
    assert [e.group for e in out] == ["중재군", "대조군"]     # 군 라벨은 그대로
    assert [e.name for e in out] == ["open", "open"]


def test_metrics_identical_after_anonymization():
    evs = [_ev(u, d, group=g)
           for u, g in (("alice", "a"), ("bob", "b"), ("carol", "a"))
           for d in (1, 2, 3, 8, 9)]
    plain = to_dict(analyze(evs, group_col="arm", adherence_min_days=2))
    anon = to_dict(analyze(anonymize_users(evs)[0], group_col="arm",
                           adherence_min_days=2))
    for key in ("overview", "retention", "active_users", "stickiness", "activity"):
        assert plain[key] == anon[key]
    assert plain["adherence"]["n_adherent_users"] == anon["adherence"]["n_adherent_users"]
    assert [a["n_users"] for a in plain["groups"]["arms"]] == \
           [a["n_users"] for a in anon["groups"]["arms"]]


def test_no_original_ids_leak_into_outputs():
    evs = [_ev("patient@example.com", d, group="중재군") for d in (1, 2, 3, 8)]
    a = analyze(anonymize_users(evs)[0], group_col="arm", adherence_min_days=1)
    blob = repr(to_dict(a)) + "".join(to_csv_tables(a).values())
    assert "patient@example.com" not in blob
    assert "U001" in blob


def test_prefix_switches_when_input_ids_already_look_like_aliases():
    """입력에 U001 이 있으면 가명이 원본과 겹쳐 엉뚱한 사람에게 값이 붙는다."""
    evs = [_ev("U001", 1), _ev("zzz", 2)]
    alias = build_alias_map(evs)
    assert alias == {"U001": "PID001", "zzz": "PID002"}
    out, n, prefix = anonymize_users(evs)
    assert prefix == "PID"
    assert sorted(e.user for e in out) == ["PID001", "PID002"]


def test_prefix_falls_back_when_all_candidates_collide():
    evs = [_ev("U001", 1), _ev("PID001", 2), _ev("ANON001", 3)]
    assert choose_prefix([e.user for e in evs]) == "ANON"
