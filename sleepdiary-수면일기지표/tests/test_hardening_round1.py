"""심사 라운드 1(2026-08-06)에서 나온 결함들의 회귀 테스트.

각 테스트는 실제로 재현되었던 크래시 또는 조용히 틀린 숫자 하나에 대응한다.
"""

import json

import pytest

from sleepdiary.aggregate import compare_periods, summarize_by_subject, summarize_group
from sleepdiary.cli import main
from sleepdiary.dataio import DataError, read_csv, sanitize_cell
from sleepdiary.nightly import build_night
from sleepdiary.stats import wilcoxon_signed_rank
from sleepdiary.timeparse import TimeParseError, fmt_clock, fmt_hm, parse_clock

HEAD = "subject,period,lights_off,sleep_latency_min,waso_min,final_awake,out_of_bed"
ROWS = ["S1,base,22:10,45,15,06:30,06:50",
        "S1,base,22:20,40,20,06:40,07:00",
        "S2,base,23:00,20,10,07:00,07:10",
        "S2,base,23:10,25,15,07:05,07:20"]

COLS = {"subject": "subject", "date": "date", "period": "period",
        "bedtime": "bedtime", "lights_off": "lights_off", "sol": "sol",
        "waso": "waso", "awakenings": "awakenings",
        "final_awake": "final_awake", "out_of_bed": "out_of_bed"}


def night(**over):
    row = {"subject": "S1", "date": "2026-03-02", "period": "base",
           "bedtime": "22:50", "lights_off": "23:00", "sol": "20", "waso": "30",
           "awakenings": "2", "final_awake": "07:00", "out_of_bed": "07:15"}
    row.update(over)
    return build_night(row, COLS, 2)


def write(tmp_path, text, name="d.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


def csv_file(tmp_path, rows=ROWS, head=HEAD, **kw):
    return write(tmp_path, head + "\n" + "\n".join(rows) + "\n", **kw)


# ================================================================ 크래시

def test_cr_only_line_endings_are_read_not_crashed(tmp_path):
    """옛 Mac 엑셀은 줄 끝을 CR 하나로 쓴다 — 예전엔 _csv.Error 로 죽었다."""
    path = write(tmp_path, "\r".join([HEAD] + ROWS) + "\r")
    rows, fields, _ = read_csv(path)
    assert len(rows) == len(ROWS)
    assert "final_awake" in fields


def test_unbalanced_quote_gives_a_data_error_not_a_traceback(tmp_path):
    """따옴표 하나가 안 닫히면 csv 모듈이 파일 끝까지 삼키고 필드 한도에서 죽었다."""
    big = [f'S{i % 3},base,22:10,45,15,06:30,06:50,{"x" * 200}' for i in range(1500)]
    big[7] = 'S1,base,22:10,45,15,06:30,06:50,"측정불가'
    path = write(tmp_path, HEAD + ",note\n" + "\n".join(big) + "\n")
    try:
        rows, _, _ = read_csv(path)
    except DataError as exc:
        assert "따옴표" in str(exc)
    else:
        assert rows                       # 죽지만 않으면 어느 쪽이든 허용


def test_nan_in_a_duration_is_rejected_instead_of_poisoning_every_mean(tmp_path):
    """float('nan')은 음수 검사도 TST<=0 검사도 통과해 밤이 '유효'로 남았다."""
    path = csv_file(tmp_path, ROWS + ["S3,base,22:00,nan,15,06:30,06:50"])
    assert main([path, "--quiet", "--json", str(tmp_path / "o.json")]) == 0
    payload = json.load(open(tmp_path / "o.json", encoding="utf-8"))
    assert payload["counts"]["nights_excluded"] == 1
    for group in payload["groups"]:
        for entry in group["metrics"].values():
            assert entry["mean"] is None or entry["mean"] == entry["mean"]   # NaN 아님


@pytest.mark.parametrize("text", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_non_finite_durations_are_refused(text):
    from sleepdiary.timeparse import parse_duration_minutes
    with pytest.raises(TimeParseError):
        parse_duration_minutes(text)


def test_formatters_do_not_crash_on_non_finite_values():
    assert fmt_hm(float("nan")) == "—"
    assert fmt_clock(float("inf")) == "—"


def test_unwritable_output_path_is_a_clean_error(tmp_path):
    path = csv_file(tmp_path)
    for flag in ("--json", "--per-night-csv", "--per-subject-csv"):
        assert main([path, "--quiet", flag, "/nonexistent-dir-xyz/out.csv"]) == 2


# ============================================== 조용히 틀린 숫자

def test_duplicate_column_names_are_refused(tmp_path):
    """DictReader는 뒤엣것만 남긴다 — SOL 45가 빈칸으로 바뀌어 0이 되었다."""
    rows = [r + "," for r in ROWS]
    path = csv_file(tmp_path, rows, HEAD + ",sleep_latency_min")
    with pytest.raises(DataError, match="중복"):
        read_csv(path)


def test_a_unit_suffixed_duplicate_still_triggers_the_ambiguity_error():
    """'latency' 와 'sleep_latency_min' 이 함께 있으면 하나를 조용히 버리면 안 된다."""
    from sleepdiary.dataio import resolve_columns
    with pytest.raises(DataError, match="여러 개"):
        resolve_columns(["subject", "sleep_latency_min", "latency",
                         "lights_off", "final_awake", "out_of_bed"], {})
    with pytest.raises(DataError, match="여러 개"):
        resolve_columns(["subject", "waso_min", "minutes_awake",
                         "lights_off", "final_awake", "out_of_bed"], {})


def test_utf16_files_are_decoded_not_mangled_as_latin1(tmp_path):
    path = write(tmp_path, HEAD + "\n" + "\n".join(ROWS) + "\n", encoding="utf-16")
    rows, fields, enc = read_csv(path)
    assert enc == "utf-16"
    assert "subject" in fields and len(rows) == len(ROWS)


def test_zero_width_characters_do_not_split_one_subject_into_two(tmp_path):
    """엑셀 파일을 이어 붙이면 'S1'과 'S1\\u200b'이 섞여 n이 조용히 늘어났다."""
    rows = ["S1​,base,22:10,45,15,06:30,06:50",
            "﻿S1,base,22:20,40,20,06:40,07:00",
            "S1 ,base,22:30,35,25,06:50,07:10"]
    path = csv_file(tmp_path, rows)
    assert main([path, "--quiet", "--json", str(tmp_path / "o.json")]) == 0
    payload = json.load(open(tmp_path / "o.json", encoding="utf-8"))
    assert payload["counts"]["subjects_analyzed"] == 1


def test_midnight_in_colloquial_korean_is_not_noon():
    """'밤 12시'는 자정이다 — 12시간제 규칙을 그대로 적용하면 12시간 어긋난다."""
    assert parse_clock("밤 12시 30분") == pytest.approx(30.0)
    assert parse_clock("새벽 12시 10분") == pytest.approx(10.0)
    assert parse_clock("오전 12:30") == pytest.approx(30.0)
    assert parse_clock("오후 12:30") == pytest.approx(750.0)   # 정오는 그대로
    assert parse_clock("밤 11:30") == pytest.approx(23 * 60 + 30)


def test_single_letter_meridiem_is_understood():
    assert parse_clock("11:15p") == pytest.approx(23 * 60 + 15)
    assert parse_clock("11:15a") == pytest.approx(11 * 60 + 15)


def test_wilcoxon_drops_non_finite_differences_instead_of_inflating_n():
    clean = wilcoxon_signed_rank([3.0, -1.0, 4.0, 2.0, -5.0])
    dirty = wilcoxon_signed_rank([3.0, -1.0, 4.0, 2.0, -5.0, float("nan")])
    assert dirty.n_used == clean.n_used
    assert dirty.p == pytest.approx(clean.p)


def test_json_output_is_strict_and_carries_only_the_file_name(tmp_path):
    """NaN 은 표준 JSON이 아니고, 임상 파일명에는 환자 이름이 들어 있곤 한다."""
    path = csv_file(tmp_path, name="김OO_수면일기.csv")
    out = str(tmp_path / "o.json")
    assert main([path, "--quiet", "--json", out]) == 0
    raw = open(out, encoding="utf-8").read()
    assert "NaN" not in raw and "Infinity" not in raw
    payload = json.loads(raw)          # 엄격한 파서로도 읽혀야 한다
    assert payload["input"]["file"] == "김OO_수면일기.csv"
    assert "/" not in payload["input"]["file"]


def test_formula_injection_guard_sees_through_leading_whitespace():
    assert sanitize_cell(" =1+1").startswith("'")
    assert sanitize_cell(" =1+1").startswith("'")
    assert sanitize_cell("\t=1+1").startswith("'")


# ================================================ 0으로 채운 값의 표시

def test_blank_sol_is_recorded_as_imputed_and_kept_out_of_the_sol_summary():
    """측정한 적 없는 값에 '평균 0분, CI [0,0]' 이 붙던 문제."""
    n = night(sol="")
    assert "sol" in n.imputed
    assert n.sol == 0.0                       # TST 계산에는 0으로 들어간다
    s = summarize_by_subject([n, night(sol="")])[0]
    assert s.metrics["sol_min"]["n"] == 0     # 그러나 SOL 요약에는 없다
    assert s.value("sol_min") is None
    assert s.n_imputed["sol"] == 2
    assert s.value("tst_min") is not None      # TST 는 여전히 계산된다


def test_a_missing_sol_column_is_marked_differently_from_a_blank_cell():
    cols = dict(COLS, sol=None)
    n = build_night({"subject": "S1", "date": "", "period": "", "bedtime": "23:00",
                     "lights_off": "23:00", "waso": "0", "awakenings": "1",
                     "final_awake": "07:00", "out_of_bed": "07:10"}, cols, 2)
    assert "sol(열없음)" in n.imputed


def test_measured_zero_is_not_treated_as_missing():
    n = night(sol="0", waso="0")
    assert n.imputed == []
    s = summarize_by_subject([n, night(sol="0", waso="0")])[0]
    assert s.value("sol_min") == 0.0 and s.metrics["sol_min"]["n"] == 2


def test_report_states_how_many_nights_were_zero_filled(tmp_path, capsys):
    rows = [r.replace(",15,", ",,") for r in ROWS]      # WASO 를 비운다
    path = csv_file(tmp_path, rows)
    main([path])
    out = capsys.readouterr().out
    assert "0으로 계산했습니다" in out
    assert "높게" in out


def test_a_blank_out_of_bed_cell_warns_because_it_shortens_tib():
    n = night(out_of_bed="")
    assert n.valid
    assert "out_of_bed←final_awake" in n.imputed
    assert any("TIB" in w for w in n.warnings)


# ================================================ 밤 유효성 포함관계

@pytest.mark.parametrize("bedtime,lights_off,final_awake,out_of_bed", [
    ("22:00", "23:00", "07:00", "06:00"),   # 기상 후 침대에서 나온 시각이 더 이르다
    ("02:00", "01:00", "09:00", "10:00"),   # 소등이 잠자리에 든 시각보다 이르다
    ("23:30", "23:00", "06:00", "07:30"),   # 같음 — 합만 우연히 맞던 경우
])
def test_out_of_order_clock_times_are_rejected(bedtime, lights_off,
                                               final_awake, out_of_bed):
    n = night(bedtime=bedtime, lights_off=lights_off,
              final_awake=final_awake, out_of_bed=out_of_bed, sol="10", waso="10")
    assert not n.valid, n.as_dict()
    assert any("순서" in e for e in n.errors)


def test_impossible_nights_never_reach_the_group_summary():
    nights = [night(),
              night(bedtime="22:00", lights_off="23:00",
                    final_awake="07:00", out_of_bed="06:00")]
    group = summarize_group(summarize_by_subject(nights), "base")
    assert group.n_excluded == 1
    assert group.metrics["twak_min"]["mean"] < 60      # 예전엔 486분이 나왔다


# ================================================ 시각 차이의 감김

def test_a_twelve_hour_phase_shift_is_not_reported_as_no_change():
    """±720분에서 감기는 차이를 선형 t검정에 넣으면 상쇄돼 p=1.0이 나왔다."""
    nights = []
    for i, (a, b) in enumerate([("00:00", "11:40"), ("00:00", "11:40"),
                                ("00:00", "12:20"), ("00:00", "12:20")]):
        nights.append(night(subject=f"S{i}", period="base",
                            bedtime=a, lights_off=a, sol="0", waso="0",
                            final_awake="06:00", out_of_bed="06:00"))
        nights.append(night(subject=f"S{i}", period="post",
                            bedtime=b, lights_off=b, sol="0", waso="0",
                            final_awake="18:00", out_of_bed="18:00"))
    comps = compare_periods(summarize_by_subject(nights), "base", "post",
                            ["lights_off_min"])
    comp = comps[0]
    assert comp.wrap_unstable is True
    assert comp.ttest is None and comp.wilcoxon is None


def test_ordinary_small_shifts_are_still_tested_normally():
    nights = []
    for i in range(4):
        nights.append(night(subject=f"S{i}", period="base", bedtime="23:00",
                            lights_off="23:00", sol="0", waso="0"))
        nights.append(night(subject=f"S{i}", period="post", bedtime="23:40",
                            lights_off="23:40", sol="0", waso="0"))
    comp = compare_periods(summarize_by_subject(nights), "base", "post",
                           ["lights_off_min"])[0]
    assert comp.wrap_unstable is False
    assert comp.ttest is not None
    assert comp.ttest.mean_diff == pytest.approx(40.0, abs=1e-6)


# ================================================ CLI 옵션이 실제로 먹히는지

def test_min_nights_actually_shrinks_the_analysed_sample(tmp_path):
    rows = ["S1,base,22:10,45,15,06:30,06:50",
            "S1,base,22:20,40,20,06:40,07:00",
            "S1,base,22:30,35,25,06:50,07:10",
            "S2,base,23:00,20,10,07:00,07:10"]      # S2 는 1박뿐
    path = csv_file(tmp_path, rows)

    def analysed(min_nights):
        out = str(tmp_path / f"o{min_nights}.json")
        assert main([path, "--quiet", "--min-nights", str(min_nights),
                     "--json", out]) == 0
        return json.load(open(out, encoding="utf-8"))["counts"]["subjects_analyzed"]

    assert analysed(1) == 2
    assert analysed(3) == 1


def test_confidence_level_changes_the_interval_width(tmp_path):
    path = csv_file(tmp_path)

    def width(conf):
        out = str(tmp_path / f"c{conf}.json")
        assert main([path, "--quiet", "--conf", str(conf), "--json", out]) == 0
        entry = json.load(open(out, encoding="utf-8"))["groups"][0]["metrics"]["tst_min"]
        return entry["ci_high"] - entry["ci_low"]

    assert width(0.99) > width(0.90)


def test_date_means_option_actually_shifts_the_dates(tmp_path):
    rows = ["S1,base,2026-03-05,22:10,45,15,06:30,06:50",
            "S1,base,2026-03-06,22:20,40,20,06:40,07:00"]
    path = csv_file(tmp_path, rows,
                    "subject,period,diary_date,lights_off,sleep_latency_min,"
                    "waso_min,final_awake,out_of_bed")

    def first_date(mode):
        out = str(tmp_path / f"{mode}.csv")
        assert main([path, "--quiet", "--date-means", mode,
                     "--per-subject-csv", out]) == 0
        import csv as _csv
        return list(_csv.DictReader(open(out, encoding="utf-8-sig")))[0]["date_first"]

    assert first_date("morning") == "2026-03-04"
    assert first_date("evening") == "2026-03-05"


def test_json_carries_every_night_and_subject(tmp_path):
    path = csv_file(tmp_path)
    out = str(tmp_path / "o.json")
    assert main([path, "--quiet", "--json", out]) == 0
    payload = json.load(open(out, encoding="utf-8"))
    assert len(payload["nights"]) == len(ROWS)
    assert len(payload["subjects"]) == 2
    # 첫 밤: 22:10 소등 → 06:30 기상 = 500분, TST = 500 − 45 − 15 = 440
    first = payload["nights"][0]
    assert first["spt_min"] == pytest.approx(500.0)
    assert first["tst_min"] == pytest.approx(440.0)


# ================================================ 경고 임계값

@pytest.mark.parametrize("over,under,needle", [
    (dict(bedtime="06:00", lights_off="06:10", final_awake="20:00",
          out_of_bed="20:30", sol="10", waso="10"), dict(), "TIB"),
    (dict(out_of_bed="10:30"), dict(out_of_bed="07:15"), "침대에 머묾"),
    (dict(waso="310"), dict(waso="30"), "WASO"),
    (dict(awakenings="25"), dict(awakenings="2"), "각성"),
])
def test_each_odd_but_possible_value_warns_and_stays_included(over, under, needle):
    hot = night(**over)
    assert hot.valid, hot.errors
    assert any(needle in w for w in hot.warnings), hot.warnings
    if under:
        cold = night(**under)
        assert not any(needle in w for w in cold.warnings)
