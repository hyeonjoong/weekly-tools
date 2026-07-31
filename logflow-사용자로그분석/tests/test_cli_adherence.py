"""CLI 통합 — --adherence-* 와 --anonymize."""

import json
from datetime import date, timedelta

import pytest

from logflow.cli import main


def _arm_csv(tmp_path, n_per_arm=6, weeks=3):
    """2군 로그. 중재군은 주 5일, 대조군은 주 1일 사용하도록 만든다."""
    rows = ["user_id,event,timestamp,arm"]
    start = date(2026, 1, 1)
    for arm, per_week in (("control", 1), ("intervention", 5)):
        for i in range(n_per_arm):
            uid = f"{arm[:1]}{i}"
            for w in range(weeks):
                for d in range(per_week):
                    day = start + timedelta(days=w * 7 + d)
                    rows.append(f"{uid},open,{day.isoformat()}T09:00:00,{arm}")
    p = tmp_path / "arms.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_adherence_section_rendered(tmp_path, capsys):
    assert main([str(_arm_csv(tmp_path)), "--adherence-days", "3"]) == 0
    out = capsys.readouterr().out
    assert "[ 프로토콜 준수도 ]" in out
    assert "한 주에 3일 이상 사용 = 준수" in out
    assert "준수 참여자" in out
    assert "주는 분모에서 제외" in out
    assert "관찰 주의 80% 이상 준수" in out


def test_adherence_json_matches_report(tmp_path, capsys):
    assert main([str(_arm_csv(tmp_path)), "--adherence-days", "5", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)["adherence"]
    # 중재군 6명만 주 5일을 채운다 → 준수 참여자 6/12
    assert d["n_users"] == 12
    assert d["n_adherent_users"] == 6
    assert d["window_weeks"] == 2
    assert all(w["eligible"] == 12 for w in d["weeks"])


def test_group_comparison_gains_two_adherence_tests(tmp_path, capsys):
    csv_path = str(_arm_csv(tmp_path))
    assert main([csv_path, "--group-col", "arm", "--json"]) == 0
    base = json.loads(capsys.readouterr().out)["groups"]
    assert main([csv_path, "--group-col", "arm", "--adherence-days", "5", "--json"]) == 0
    with_adh = json.loads(capsys.readouterr().out)["groups"]

    assert with_adh["n_tests"] == base["n_tests"] + 2
    labels = [t["label"] for t in with_adh["proportion_tests"]]
    assert "프로토콜 준수(주5일↑·80%↑)" in labels
    assert "사용자당 준수 주 비율" in [t["label"] for t in with_adh["distribution_tests"]]

    prop = next(t for t in with_adh["proportion_tests"] if t["label"].startswith("프로토콜"))
    assert prop["a"] == {"successes": 6, "n": 6, "rate": 1.0}
    assert prop["b"]["successes"] == 0
    assert prop["diff"] == pytest.approx(1.0)

    dist = next(t for t in with_adh["distribution_tests"]
                if t["label"] == "사용자당 준수 주 비율")
    assert dist["unit"] == "%"          # 0~1 이 아니라 0~100 척도여야 한다
    assert dist["median_a"] == pytest.approx(100.0)
    assert dist["median_b"] == pytest.approx(0.0)

    arms = {a["group"]: a["adherence"] for a in with_adh["arms"]}
    assert arms["intervention"]["adherent_users"] == 6
    assert arms["control"]["adherent_users"] == 0
    assert arms["control"]["median_user_rate"] == 0.0


def test_group_text_shows_per_arm_adherence(tmp_path, capsys):
    assert main([str(_arm_csv(tmp_path)), "--group-col", "arm",
                 "--adherence-days", "5"]) == 0
    out = capsys.readouterr().out
    assert "* 프로토콜 준수 참여자:" in out
    assert "intervention 6/6 (100.0%)" in out


def test_csv_dir_writes_adherence_tables(tmp_path, capsys):
    outdir = tmp_path / "표"
    assert main([str(_arm_csv(tmp_path)), "--adherence-days", "3",
                 "--csv-dir", str(outdir)]) == 0
    weekly = (outdir / "adherence_weekly.csv").read_text(encoding="utf-8-sig")
    users = (outdir / "adherence_users.csv").read_text(encoding="utf-8-sig")
    assert weekly.splitlines()[0] == (
        "week,eligible,adherent,rate,ci_low,ci_high,median_active_days,"
        "min_days,period_days"
    )
    assert users.splitlines()[0] == (
        "user,eligible_weeks,adherent_weeks,adherence_rate,"
        "longest_streak_weeks,active_days_in_window"
    )
    assert len(weekly.splitlines()) == 3          # 헤더 + 2주


def test_adherence_not_written_when_not_requested(tmp_path, capsys):
    outdir = tmp_path / "표"
    assert main([str(_arm_csv(tmp_path)), "--csv-dir", str(outdir)]) == 0
    assert not (outdir / "adherence_weekly.csv").exists()


def test_stale_tables_from_a_previous_run_are_flagged(tmp_path, capsys):
    """준수도 없이 다시 돌리면 이전 실행의 adherence_*.csv 가 남아 숫자가 섞인다."""
    outdir = tmp_path / "표"
    csv_path = str(_arm_csv(tmp_path))
    assert main([csv_path, "--adherence-days", "3", "--csv-dir", str(outdir)]) == 0
    capsys.readouterr()
    assert main([csv_path, "--csv-dir", str(outdir)]) == 0
    err = capsys.readouterr().err
    assert (outdir / "adherence_weekly.csv").exists()      # 지우지는 않는다
    assert "이번 실행이 만들지 않은 CSV" in err
    assert "adherence_weekly.csv" in err and "adherence_users.csv" in err


def test_observation_end_is_shown(tmp_path, capsys):
    """관찰 종료일이 보이지 않으면 오타 타임스탬프 하나가 준수율을 뒤집어도 알 수 없다."""
    assert main([str(_arm_csv(tmp_path)), "--adherence-days", "3"]) == 0
    out = capsys.readouterr().out
    assert "관찰 종료일" in out and "2026-01-19" in out


def test_adherence_weeks_beyond_cap_is_rejected(tmp_path, capsys):
    assert main([str(_arm_csv(tmp_path)), "--adherence-days", "3",
                 "--adherence-weeks", "1000000"]) == 1
    assert "104" in capsys.readouterr().err


def test_fractional_target_is_not_rounded_in_labels(tmp_path, capsys):
    assert main([str(_arm_csv(tmp_path)), "--group-col", "arm",
                 "--adherence-days", "5", "--adherence-target", "0.999"]) == 0
    out = capsys.readouterr().out
    assert "99.9% 이상 준수" in out
    assert "·99.9%↑)" in out


@pytest.mark.parametrize(
    "extra",
    [
        ["--adherence-days", "0"],
        ["--adherence-days", "8"],                       # > period(7)
        ["--adherence-days", "3", "--adherence-period", "0"],
        ["--adherence-days", "3", "--adherence-target", "0"],
        ["--adherence-days", "3", "--adherence-target", "1.5"],
        ["--adherence-days", "3", "--adherence-target", "nan"],
        ["--adherence-days", "3", "--adherence-weeks", "0"],
        ["--adherence-weeks", "8"],                      # --adherence-days 없이
        ["--adherence-period", "14"],
        ["--adherence-target", "0.5"],
    ],
)
def test_invalid_adherence_options_rejected(tmp_path, capsys, extra):
    assert main([str(_arm_csv(tmp_path))] + extra) == 1
    assert "오류:" in capsys.readouterr().err


def test_adherence_period_14_label(tmp_path, capsys):
    assert main([str(_arm_csv(tmp_path)), "--group-col", "arm",
                 "--adherence-days", "5", "--adherence-period", "14"]) == 0
    out = capsys.readouterr().out
    assert "한 14일 기간에 5일 이상 사용 = 준수" in out
    assert "프로토콜 준수(14일당5일↑·80%↑)" in out


def test_adherence_denominator_uses_global_end_not_per_arm(tmp_path, capsys):
    """조기 이탈군이 자기 마지막 활동일을 종료일로 쓰면 준수율이 부풀려진다."""
    rows = ["user_id,event,timestamp,arm"]
    s = date(2026, 1, 1)
    for arm, weeks in (("intervention", 6), ("control", 3)):
        for i in range(6):
            for w in range(weeks):
                for d in range(5):
                    day = s + timedelta(days=w * 7 + d)
                    rows.append(f"{arm[0]}{i},open,{day.isoformat()}T09:00:00,{arm}")
    p = tmp_path / "dropout.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main([str(p), "--group-col", "arm", "--adherence-days", "5", "--json"]) == 0
    arms = {a["group"]: a["adherence"]
            for a in json.loads(capsys.readouterr().out)["groups"]["arms"]}
    # 전체 종료일(중재군의 6주차) 기준이면 대조군 분모도 6주 → 3/6 = 50% 준수
    assert arms["control"]["adherent_users"] == 0
    # 대조군 첫 활동 1/1, 전체 종료일 2/9 → 완전 관찰 5주 중 3주 준수 = 0.6
    assert arms["control"]["median_user_rate"] == pytest.approx(0.6)
    assert arms["intervention"]["adherent_users"] == 6
    assert arms["intervention"]["median_user_rate"] == pytest.approx(1.0)


def test_target_and_weeks_flags_reach_the_computation(tmp_path, capsys):
    """유효한(기본값이 아닌) 값이 실제로 결과를 바꾸는지 — 배선이 끊겨도 티가 나게."""
    p = tmp_path / "two.csv"
    rows = ["user_id,event,timestamp"]
    for d in (1, 2, 3, 8, 9, 10, 14):        # u1: 1주차 3일 · 2주차 4일 → 준수율 1.0
        rows.append(f"u1,open,2026-01-{d:02d}T09:00:00")
    for d in (1, 2, 3):                       # u2: 1주차 3일 · 2주차 0일 → 준수율 0.5
        rows.append(f"u2,open,2026-01-{d:02d}T09:00:00")
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    csv_path = str(p)

    assert main([csv_path, "--adherence-days", "3", "--json"]) == 0
    base = json.loads(capsys.readouterr().out)["adherence"]
    assert base["target"] == 0.8 and base["n_users"] == 2
    assert base["n_adherent_users"] == 1      # u2(0.5)는 미달

    assert main([csv_path, "--adherence-days", "3",
                 "--adherence-target", "0.5", "--json"]) == 0
    loose = json.loads(capsys.readouterr().out)["adherence"]
    assert loose["target"] == 0.5 and loose["n_adherent_users"] == 2

    assert main([csv_path, "--adherence-days", "3",
                 "--adherence-weeks", "1", "--json"]) == 0
    short = json.loads(capsys.readouterr().out)["adherence"]
    assert short["window_weeks"] == 1 and short["required_weeks"] == 1
    assert len(short["weeks"]) == 1 and short["n_adherent_users"] == 2


def test_adherence_cis_use_the_requested_confidence_level(tmp_path, capsys):
    csv_path = str(_arm_csv(tmp_path))
    assert main([csv_path, "--adherence-days", "5", "--json"]) == 0
    d95 = json.loads(capsys.readouterr().out)["adherence"]
    assert d95["n_adherent_users"] == 6 and d95["n_users"] == 12
    assert d95["adherent_ci"] == pytest.approx([0.2536, 0.7464], abs=1e-3)  # Wilson 6/12
    assert main([csv_path, "--adherence-days", "5", "--confidence", "0.80", "--json"]) == 0
    d80 = json.loads(capsys.readouterr().out)["adherence"]
    assert d80["adherent_ci"][0] > d95["adherent_ci"][0]
    assert d80["adherent_ci"][1] < d95["adherent_ci"][1]
    assert d80["weeks"][0]["ci"][0] > d95["weeks"][0]["ci"][0]


def test_adherence_text_table_column_order(tmp_path, capsys):
    assert main([str(_arm_csv(tmp_path)), "--adherence-days", "5"]) == 0
    line = next(l for l in capsys.readouterr().out.splitlines()
                if l.strip().startswith("1 ") and "%" in l)
    assert line.split()[:4] == ["1", "12", "6", "50.0%"]   # 주차·대상·준수·준수율


def test_group_summary_and_tests_csv_use_the_same_scale(tmp_path, capsys):
    """group_summary 와 group_tests 가 같은 값을 1.0 / 100.0 으로 달리 적지 않게."""
    outdir = tmp_path / "표"
    assert main([str(_arm_csv(tmp_path)), "--group-col", "arm",
                 "--adherence-days", "5", "--csv-dir", str(outdir)]) == 0
    summary = (outdir / "group_summary.csv").read_text(encoding="utf-8-sig")
    tests = (outdir / "group_tests.csv").read_text(encoding="utf-8-sig")
    assert "median_adherence_pct" in summary.splitlines()[0]
    inter = next(l for l in summary.splitlines() if l.startswith("intervention"))
    assert inter.split(",")[-1] == "100.0"
    dist = next(l for l in tests.splitlines() if "준수 주 비율" in l)
    assert "100.0" in dist.split(",")


# ---------------------------------------------------------------- --anonymize

def test_anonymize_replaces_ids_everywhere(tmp_path, capsys):
    p = tmp_path / "pii.csv"
    p.write_text(
        "user_id,event,timestamp,arm\n"
        "patient@example.com,open,2026-01-01T09:00:00,중재군\n"
        "patient@example.com,open,2026-01-02T09:00:00,중재군\n"
        "010-1234-5678,open,2026-01-01T10:00:00,대조군\n",
        encoding="utf-8",
    )
    outdir = tmp_path / "표"
    assert main([str(p), "--group-col", "arm", "--anonymize",
                 "--adherence-days", "1", "--csv-dir", str(outdir)]) == 0
    cap = capsys.readouterr()
    blob = cap.out + "".join(
        f.read_text(encoding="utf-8-sig") for f in outdir.glob("*.csv")
    )
    assert "patient@example.com" not in blob
    assert "010-1234-5678" not in blob
    assert "U001" in blob and "U002" in blob
    assert "중재군" in blob                     # 군 라벨은 가리지 않는다
    assert "사용자 2명의 ID 를 U001··· 가명으로" in cap.err
    assert "가명화되는 것은 사용자 ID 뿐입니다" in cap.err


def test_anonymize_leaks_nothing_on_any_output_channel(tmp_path, capsys):
    """stdout·stderr·--out·모든 CSV 를 한꺼번에 훑어 원본 ID 가 없는지 확인."""
    p = tmp_path / "pii.csv"
    p.write_text(
        "user_id,event,timestamp,arm\n"
        "patient@example.com,open,2026-01-01T09:00:00,중재군\n"
        "patient@example.com,open,2026-01-09T09:00:00,대조군\n"   # 군 라벨 충돌 경고 유발
        "010-1234-5678,open,2026-01-01T10:00:00,대조군\n"
        "010-1234-5678,open,NOT-A-DATE,대조군\n",                  # 파싱 실패 경고 유발
        encoding="utf-8",
    )
    outdir = tmp_path / "표"
    report = tmp_path / "r.txt"
    assert main([str(p), "--group-col", "arm", "--anonymize", "--skip-bad-rows",
                 "--adherence-days", "1", "--csv-dir", str(outdir),
                 "--out", str(report)]) == 0
    cap = capsys.readouterr()
    blob = (cap.out + cap.err + report.read_text(encoding="utf-8")
            + "".join(f.read_text(encoding="utf-8-sig") for f in outdir.glob("*.csv")))
    for original in ("patient@example.com", "010-1234-5678"):
        assert original not in blob


def test_anonymize_refuses_when_group_col_is_the_user_col(tmp_path, capsys):
    """--group-col 이 사용자 열이면 가리지 않는 군 라벨로 ID 가 그대로 새어 나간다."""
    p = tmp_path / "p.csv"
    p.write_text("user_id,event,timestamp\n"
                 "patient@x.kr,open,2026-01-01T09:00:00\n"
                 "bob,open,2026-01-01T09:00:00\n", encoding="utf-8")
    assert main([str(p), "--group-col", "USER_ID", "--anonymize", "--json"]) == 1
    cap = capsys.readouterr()
    assert "patient@x.kr" not in cap.out
    assert "오류:" in cap.err


def test_anonymize_keeps_top_user_rows_stable_on_ties(tmp_path, capsys):
    """동점자 순서가 ID 로 갈리면 가명화 후 다른 사람이 상위 표에 오른다."""
    p = tmp_path / "tie.csv"
    rows = ["user_id,event,timestamp"]
    for d in (1, 1, 1, 1):
        rows.append(f"zzz,open,2026-01-0{d}T09:00:00")
    for d in (3, 4, 5, 6):
        rows.append(f"aaa,open,2026-01-0{d}T09:00:00")
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main([str(p), "--top", "1", "--json"]) == 0
    plain = json.loads(capsys.readouterr().out)["users"]
    assert main([str(p), "--top", "1", "--anonymize", "--json"]) == 0
    anon = json.loads(capsys.readouterr().out)["users"]
    assert [(u["event_count"], u["active_days"]) for u in plain] == \
           [(u["event_count"], u["active_days"]) for u in anon]


def test_anonymize_does_not_change_numbers(tmp_path, capsys):
    csv_path = str(_arm_csv(tmp_path))
    assert main([csv_path, "--group-col", "arm", "--adherence-days", "5",
                 "--json"]) == 0
    plain = json.loads(capsys.readouterr().out)
    assert main([csv_path, "--group-col", "arm", "--adherence-days", "5",
                 "--anonymize", "--json"]) == 0
    anon = json.loads(capsys.readouterr().out)
    plain.pop("users"), anon.pop("users")
    plain["adherence"].pop("users"), anon["adherence"].pop("users")
    assert plain == anon


def test_anonymize_numbers_only_kept_users(tmp_path, capsys):
    """--only-groups 로 걸러낸 뒤 번호를 매긴다 (번호가 비지 않도록)."""
    p = tmp_path / "three.csv"
    rows = ["user_id,event,timestamp,arm"]
    for i, arm in enumerate(["A", "B", "C"]):
        rows.append(f"user{i},open,2026-01-0{i + 1}T09:00:00,{arm}")
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert main([str(p), "--group-col", "arm", "--only-groups", "A,C",
                 "--anonymize", "--json"]) == 0
    users = [u["user"] for u in json.loads(capsys.readouterr().out)["users"]]
    assert sorted(users) == ["U001", "U002"]
