"""하드닝 라운드 2 회귀 테스트.

독립 리뷰어 패널이 재현한 결함들을 하나씩 고정합니다. 각 테스트는 "고치기 전에
실제로 무엇이 잘못 나왔는지"를 주석에 남겨, 나중에 누가 되돌리면 바로 드러나게 합니다.
"""

import math
import random

import pytest

from hrvkit import analyze_rr
from hrvkit.dataio import (detect_unit, load_manifest, load_series, parse_float,
                           unit_from_name)
from hrvkit.frequency import frequency_domain
from hrvkit.timedomain import geometric_indices

BIN = 1000.0 / 128.0


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _rr(n=120, seed=1, mean=800.0, sd=30.0):
    rng = random.Random(seed)
    return [mean + rng.gauss(0, sd) for _ in range(n)]


# --------------------------------------------------------------------------- #
# [치명] 값 열 자동 선택이 플래그/주석 열을 고르던 문제
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag_col", ["valid", "annotation", "ann", "thr",
                                      "beat_ok", "quality", "artifact_flag",
                                      "subject_id", "epoch"])
def test_value_column_never_picks_flag_column(tmp_path, flag_col):
    """`valid,rr_ms` 처럼 플래그 열이 앞에 있어도 rr_ms 를 골라야 한다.

    과거: _VALUE_KEYS 를 부분일치로 검사해 "valid"⊃"val", "annotation"⊃"nn",
    "thr"⊃"hr" 가 값 열로 뽑혔고, 전부 1인 플래그 열이 's' 단위로 감지돼
    RR=1000 ms 상수 → "SDNN 0.00, HR 60.0" 을 **경고 없이** 출력했다.
    """
    rr = [round(v, 1) for v in _rr()]
    text = f"{flag_col},rr_ms\n" + "\n".join(f"1,{v:.1f}" for v in rr) + "\n"
    path = _write(tmp_path, "dev.csv", text)
    series, meta = load_series(path)
    assert meta["column"] == "rr_ms"
    assert meta["unit"] == "ms"
    # 플래그 열을 골랐다면 상수 1 → 's' → 1000 ms 상수가 됐을 것.
    assert series == pytest.approx(rr)


def test_constant_flag_column_is_implausible_even_if_named_value(tmp_path):
    # 이름이 중립이어도(둘 다 점수 동률) 상수 열은 생리적으로 그럴듯하지 않으므로
    # 변동하는 열이 선택돼야 한다.
    rr = _rr()
    text = "colA,colB\n" + "\n".join(f"1,{v:.1f}" for v in rr) + "\n"
    path = _write(tmp_path, "neutral.csv", text)
    _series, meta = load_series(path)
    assert meta["column"] == "colB"


def test_time_value_column_still_autodetected(tmp_path):
    # 회귀 방지: 정상적인 time+value 형식은 그대로 동작해야 한다.
    rr = _rr()
    rows = "\n".join(f"{i * 0.8:.3f},{v:.1f}" for i, v in enumerate(rr))
    path = _write(tmp_path, "tv.csv", "time_s,rr_ms\n" + rows + "\n")
    _series, meta = load_series(path)
    assert meta["column"] == "rr_ms"


# --------------------------------------------------------------------------- #
# [높음] 단위 자동감지가 열 이름을 무시하던 문제
# --------------------------------------------------------------------------- #
def test_unit_from_column_name_beats_median_rule(tmp_path):
    """신생아 RR 270 ms: 중앙값 규칙(<300 → bpm)은 틀리고 열 이름이 맞다.

    과거: 270 ms → 'bpm' 으로 오판 → 60000/270 = 222 ms 라는 조용한 오답.
    """
    neo = [270.0 + (i % 5) - 2 for i in range(80)]
    rows = "\n".join(f"{v:.1f}" for v in neo)
    path = _write(tmp_path, "neonate.csv", "rr_ms\n" + rows + "\n")
    series, meta = load_series(path)
    assert meta["unit"] == "ms"
    assert meta["unit_source"] == "column-name"
    assert sum(series) / len(series) == pytest.approx(268.0, abs=2.0)
    # 이름과 중앙값 규칙이 엇갈리므로 사용자에게 알려야 한다.
    assert meta["unit_note"] and "rr_ms" in meta["unit_note"]


def test_unit_from_name_parsing():
    assert unit_from_name("rr_ms") == "ms"
    assert unit_from_name("RR Interval (ms)") == "ms"
    assert unit_from_name("rr_s") == "s"
    assert unit_from_name("hr_bpm") == "bpm"
    assert unit_from_name("rr") is None          # 단위 정보 없음
    assert unit_from_name("hr") is None          # 모호 → 중앙값 규칙에 맡김
    # 단위 토큰이 둘 이상 섞이면 모호 → None
    assert unit_from_name("rr_ms_and_s") is None


def test_detect_unit_median_rule_still_applies_without_name():
    assert detect_unit([0.8] * 10) == "s"
    assert detect_unit([60.0] * 10) == "bpm"
    assert detect_unit([800.0] * 10) == "ms"
    # 이름이 있으면 이름 우선
    assert detect_unit([0.8] * 10, "rr_ms") == "ms"


# --------------------------------------------------------------------------- #
# [중간] 쉼표 표기: 천단위 대 소수점
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("token,dc,expected", [
    # 유럽식 파일(구분자가 쉼표가 아님) → 쉼표는 **항상** 소수점.
    ("0,803", True, 0.803),       # 3자리 소수. 한때 천단위로 오해해 803.0 을 냈다.
    ("1,017", True, 1.017),
    ("1,010", True, 1.010),
    ("0,82", True, 0.82),
    ("812,5", True, 812.5),
    ("1.234,5", True, 1234.5),    # 유럽식: 점=천단위, 쉼표=소수점
    # 쉼표 구분 파일 → 토큰 안의 쉼표는 따옴표로 감싼 영미식 천단위뿐.
    ("1,010", False, 1010.0),
    ("12,345,678", False, 12345678.0),
    ("812.5", False, 812.5),      # 평범한 소수점은 그대로
    ("812", False, 812.0),
])
def test_number_formats(token, dc, expected):
    assert parse_float(token, dc) == pytest.approx(expected)


@pytest.mark.parametrize("token", ["inf", "-inf", "Infinity", "1e400", "nan",
                                   "NaN", "-Infinity"])
def test_nonfinite_tokens_rejected(token):
    """float()는 inf/nan 을 받아주지만, 지표 계산을 통째로 오염시킨다.

    특히 '1e400'(float 익스포트 오버플로)은 조용히 inf 가 되어 median/평균을 망친다.
    """
    assert parse_float(token) is None
    assert parse_float(token, True) is None


# --------------------------------------------------------------------------- #
# [낮음] 인코딩 / 오류 메시지 위생 / --col 검증
# --------------------------------------------------------------------------- #
def test_utf16_is_read(tmp_path):
    # 과거: cp949 가 UTF-16 바이트를 예외 없이 디코드해 NUL 섞인 쓰레기가 됐다.
    p = tmp_path / "u16.csv"
    body = "rr_ms\n" + "\n".join(str(800 + i % 7) for i in range(60)) + "\n"
    p.write_bytes(body.encode("utf-16"))
    series, meta = load_series(str(p))
    assert meta["column"] == "rr_ms"
    assert len(series) == 60


def test_error_does_not_dump_file_contents(tmp_path):
    """CSV가 아닌 파일을 지정해도 그 내용을 통째로 stderr에 흘리지 않아야 한다."""
    secret = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"
    path = _write(tmp_path, "secret.env", secret + "\nDB_PASSWORD=hunter2\n")
    with pytest.raises(ValueError) as exc:
        load_series(path)
    msg = str(exc.value)
    assert secret not in msg
    assert "hunter2" not in msg
    assert len(msg) < 200


def test_duplicate_column_name_is_rejected(tmp_path):
    # 과거: header.index() 가 조용히 첫 열을 골라 틀린 값을 냈다.
    rows = "\n".join(f"999,{800 + i % 7}" for i in range(60))
    path = _write(tmp_path, "dup.csv", "rr_ms,rr_ms\n" + rows + "\n")
    with pytest.raises(ValueError, match="중복"):
        load_series(path, col="rr_ms")


@pytest.mark.parametrize("col", ["1_0", "-1", "1.0", "abc", "1e0", "٣"])
def test_non_canonical_col_index_rejected(tmp_path, col):
    # 과거: int("1_0") == 10 이라 12열 파일에서 조용히 11번째 열이 뽑혔다.
    rows = "\n".join(f"{800 + i % 7},{i}" for i in range(60))
    path = _write(tmp_path, "two.csv", "a,b\n" + rows + "\n")
    with pytest.raises(ValueError):
        load_series(path, col=col)


def test_plain_col_index_still_works(tmp_path):
    rows = "\n".join(f"{800 + i % 7},{i}" for i in range(60))
    path = _write(tmp_path, "two.csv", "a,b\n" + rows + "\n")
    _series, meta = load_series(path, col="0")
    assert meta["column"] == "a"


# --------------------------------------------------------------------------- #
# 주석/메타데이터 헤더 (Polar·Kubios 스타일 익스포트)
# --------------------------------------------------------------------------- #
def test_comment_lines_are_skipped(tmp_path):
    rr = [round(v, 1) for v in _rr(n=60)]
    text = ("# Device: Polar H10\n"
            "# Subject: S01\n"
            "# Recorded: 2026-01-01\n"
            "rr_ms\n" + "\n".join(f"{v:.1f}" for v in rr) + "\n")
    path = _write(tmp_path, "polar.csv", text)
    series, meta = load_series(path)
    assert meta["column"] == "rr_ms"
    assert meta["n_comment_lines"] == 3
    assert series == pytest.approx(rr)


def test_comment_only_file_errors_clearly(tmp_path):
    path = _write(tmp_path, "onlyc.csv", "# nothing here\n# at all\n")
    with pytest.raises(ValueError, match="주석"):
        load_series(path)


def test_hash_inside_quoted_field_not_treated_as_comment(tmp_path):
    # 줄 '시작'이 아닌 '#' 은 주석이 아니다.
    rows = "\n".join(f'"note #{i}",{800 + i % 7}' for i in range(60))
    path = _write(tmp_path, "hash.csv", "note,rr_ms\n" + rows + "\n")
    _series, meta = load_series(path)
    assert meta["column"] == "rr_ms"
    assert meta["n_comment_lines"] == 0


def test_examples_are_marked_synthetic():
    """예제 CSV는 합성임을 파일 자체가 밝혀야 한다(문서와 어긋나지 않도록)."""
    import os
    ex = os.path.join(os.path.dirname(__file__), "..", "examples")
    for name in ("resting.csv", "slow_breathing.csv"):
        with open(os.path.join(ex, name), encoding="utf-8") as fh:
            head = fh.read(400)
        assert head.startswith("#")
        assert "합성" in head or "synthetic" in head


# --------------------------------------------------------------------------- #
# [중간] 매니페스트 유사반복(pseudo-replication)
# --------------------------------------------------------------------------- #
def test_manifest_rejects_duplicate_subject(tmp_path):
    for name in ("b1.csv", "i1.csv", "b2.csv", "i2.csv"):
        _write(tmp_path, name, "rr_ms\n" + "\n".join("800" for _ in range(5)))
    man = _write(tmp_path, "m.csv",
                 "baseline,intervention,subject\n"
                 "b1.csv,i1.csv,S1\n"
                 "b1.csv,i1.csv,S1\n"
                 "b2.csv,i2.csv,S2\n")
    with pytest.raises(ValueError, match="중복"):
        load_manifest(man)


def test_manifest_rejects_identical_unlabelled_pair(tmp_path):
    for name in ("b1.csv", "i1.csv"):
        _write(tmp_path, name, "rr_ms\n" + "\n".join("800" for _ in range(5)))
    man = _write(tmp_path, "m2.csv",
                 "baseline,intervention\nb1.csv,i1.csv\nb1.csv,i1.csv\n")
    with pytest.raises(ValueError, match="중복"):
        load_manifest(man)


# --------------------------------------------------------------------------- #
# [중간] TINN 이 데이터 범위에 갇히던 문제
# --------------------------------------------------------------------------- #
def test_tinn_exact_on_perfect_triangle():
    """꼬리 없는 완전 삼각형 히스토그램에서 TINN 이 참값과 일치해야 한다.

    과거: centers 가 min(nn)~max(nn) 만 덮어 밑변이 관측범위에 갇혔고,
    참값 156.25 ms 에 대해 140.625 ms (정확히 2빈 부족)를 냈다.
    """
    nn = []
    for i in range(21):
        height = int(round(100 * (1 - abs(i - 10) / 10.0)))
        nn += [500.0 + i * BIN] * height
    g = geometric_indices(nn)
    assert g["tinn"] == pytest.approx(20 * BIN, rel=1e-9)
    assert g["hti"] == pytest.approx(len(nn) / 100.0, rel=1e-9)


def test_tinn_still_reasonable_on_real_like_data():
    g = geometric_indices(_rr(n=500, seed=3))
    assert 50.0 < g["tinn"] < 600.0


# --------------------------------------------------------------------------- #
# [높음] VLF 침묵의 0.0 / 해상도 정직성
# --------------------------------------------------------------------------- #
def test_vlf_is_nan_not_zero_when_unresolvable():
    """짧은 기록의 VLF는 0.0(=진짜 0처럼 보임)이 아니라 NaN이어야 한다."""
    f = frequency_domain([800.0] * 80, fs=4.0)      # 64 s
    assert f["vlf_bins"] == 0
    assert math.isnan(f["vlf_power"])
    assert f["vlf_reliable"] is False
    assert math.isnan(f["total_power"])


def test_vlf_recovered_with_long_segment():
    """구간이 충분히 길면 알려진 진폭의 VLF 성분을 회수해야 한다.

    0.008 Hz, 진폭 60 ms 정현파의 참 분산 = A²/2 = 1800 ms².
    기본 구간(64 s)으로는 ~23% 밖에 못 잡지만, 512 s 구간이면 ~100% 회수한다.
    """
    fs = 4.0
    amp, f0 = 60.0, 0.008
    n = 800
    rr = []
    t = 0.0
    for _ in range(n):
        v = 800.0 + amp * math.sin(2 * math.pi * f0 * t)
        rr.append(v)
        t += v / 1000.0
    f = frequency_domain(rr, fs=fs, nperseg=2048)   # 512 s 구간
    assert f["vlf_reliable"] is True
    assert f["vlf_bins"] >= 2
    assert f["vlf_power"] == pytest.approx(amp ** 2 / 2.0, rel=0.10)


def test_short_record_raises_instead_of_returning_zeros():
    # 과거: 4박동(3.2 s)이 가드를 통과해 모든 대역 0.0, lf_hf_ratio=inf 를 반환했다.
    with pytest.raises(ValueError, match="짧"):
        frequency_domain([800.0, 810.0, 790.0, 805.0], fs=4.0)


def test_freq_reports_resolution_metadata():
    f = frequency_domain(_rr(n=400, seed=5), fs=4.0)
    assert f["welch_segment_sec"] == pytest.approx(
        f["welch_nperseg"] / f["resample_fs"])
    assert f["freq_resolution_hz"] == pytest.approx(
        f["resample_fs"] / f["welch_nfft"])
    assert f["hf_bins"] > f["vlf_bins"]


# --------------------------------------------------------------------------- #
# [중간] 평탄 신호에서 느린호흡 레짐 오탐
# --------------------------------------------------------------------------- #
def test_flat_signal_is_not_slow_breathing_regime():
    """RR이 전부 동일하면 PSD가 전부 0 → 어떤 대역에도 피크가 없어야 한다.

    과거: _peak 의 시작값이 -1.0 이라 파워 0인 대역에서도 첫 빈이 '피크'로 뽑혀,
    평탄 신호가 '느린/공명 호흡 레짐'으로 오탐되고 경고까지 떴다.
    """
    res = analyze_rr([800.0] * 60)
    assert res.freq["slow_breathing_regime"] is False
    assert res.freq["peak_hf"] is None
    assert res.freq["peak_lf"] is None
    assert res.freq["resp_rate_brpm"] is None
    assert not any("레짐" in w for w in res.warnings)


def test_real_slow_breathing_still_detected():
    # 회귀 방지: 진짜 6회/분(0.1 Hz) 호흡은 여전히 레짐으로 감지돼야 한다.
    rr, t = [], 0.0
    for _ in range(400):
        v = 900.0 + 90.0 * math.sin(2 * math.pi * 0.1 * t)
        rr.append(v)
        t += v / 1000.0
    f = frequency_domain(rr, fs=4.0)
    assert f["slow_breathing_regime"] is True
    assert f["resp_source"] == "LF"
    assert f["resp_rate_brpm"] == pytest.approx(6.0, abs=1.0)


# --------------------------------------------------------------------------- #
# 출력 경로 커버리지 — `--paired --format csv` 는 테스트가 없어 NameError 로 깨져 있었다
# --------------------------------------------------------------------------- #
def _mk_cohort(tmp_path, n_subj=6, seed=11):
    rng = random.Random(seed)
    rows = ["baseline,intervention,subject"]
    for s in range(n_subj):
        for tag, mean in (("b", 850), ("i", 950)):
            vals = [mean + rng.gauss(0, 25) for _ in range(300)]
            _write(tmp_path, f"{tag}{s}.csv",
                   "rr_ms\n" + "\n".join(f"{v:.1f}" for v in vals) + "\n")
        rows.append(f"b{s}.csv,i{s}.csv,S{s}")
    return _write(tmp_path, "man.csv", "\n".join(rows) + "\n")


def test_cli_paired_csv_format(capsys, tmp_path):
    from hrvkit import cli
    man = _mk_cohort(tmp_path)
    rc = cli.main(["--paired", man, "--format", "csv"])
    out = capsys.readouterr().out
    assert rc == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    header = lines[0].split(",")
    assert header[0] == "metric"
    for col in ("hl_shift", "ci_low", "ci_high", "wilcoxon_p", "p_holm",
                "p_bh", "cohens_dz", "ci_method", "wilcoxon_method"):
        assert col in header
    assert len(lines) == 12                      # 헤더 + 11개 지표
    assert any(ln.startswith("rmssd,") for ln in lines)


@pytest.mark.parametrize("alpha", ["0", "1", "-0.1", "1.5", "nan"])
def test_cli_rejects_bad_alpha(capsys, tmp_path, alpha):
    from hrvkit import cli
    man = _mk_cohort(tmp_path)
    rc = cli.main(["--paired", man, "--alpha", alpha])
    assert rc == 2
    assert "alpha" in capsys.readouterr().err


def test_cli_alpha_changes_ci_width(capsys, tmp_path):
    import json
    from hrvkit import cli
    # n=12: α=0.01 에서도 유한 CI가 존재(최소 정확 p = 2/2^12 = 0.00049 < 0.01).
    # n이 작으면 α=0.01 에 유한 구간이 없어 JSON이 "-inf" 문자열을 냅니다.
    man = _mk_cohort(tmp_path, n_subj=12)
    cli.main(["--paired", man, "--json", "--alpha", "0.2"])
    narrow = json.loads(capsys.readouterr().out)["rmssd"]
    cli.main(["--paired", man, "--json", "--alpha", "0.01"])
    wide = json.loads(capsys.readouterr().out)["rmssd"]
    assert wide["ci_low"] <= narrow["ci_low"]
    assert wide["ci_high"] >= narrow["ci_high"]
    assert wide["ci_alpha"] == 0.01


def test_cli_nperseg_option_changes_resolution(capsys, tmp_path):
    import json
    from hrvkit import cli
    rng = random.Random(4)
    vals = [800 + rng.gauss(0, 30) for _ in range(1500)]
    p = _write(tmp_path, "long.csv",
               "rr_ms\n" + "\n".join(f"{v:.1f}" for v in vals) + "\n")
    cli.main([p, "--json", "--no-sampen"])
    default = json.loads(capsys.readouterr().out)["frequency_domain"]
    cli.main([p, "--json", "--no-sampen", "--nperseg", "2048"])
    long_seg = json.loads(capsys.readouterr().out)["frequency_domain"]
    assert default["welch_nperseg"] == 256
    assert long_seg["welch_nperseg"] == 2048
    assert long_seg["vlf_reliable"] is True
    assert default["vlf_reliable"] is False


def test_paired_report_marks_insufficient_n(tmp_path):
    """n=4 코호트는 95% 유한 구간이 없다 → 가짜 구간 대신 표식이 나와야 한다."""
    from hrvkit import cli
    import io as _io
    import contextlib
    man = _mk_cohort(tmp_path, n_subj=4, seed=3)
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(["--paired", man])
    out = buf.getvalue()
    assert rc == 0
    assert "(-∞, ∞)†" in out
    assert "표본 부족" in out


# --------------------------------------------------------------------------- #
# 라운드 2b — 리뷰어가 잡은 '내가 만든 회귀' 및 남은 구멍
# --------------------------------------------------------------------------- #
def test_european_excel_export_mixed_decimals(tmp_path):
    """독일식 엑셀 익스포트(세미콜론, 2/3자리 소수 혼재)를 정확히 읽어야 한다.

    과거(내가 넣은 회귀): "0,803" 이 천단위로 오인돼 803.0 → RR 이 1000배가 되고
    평균 HR은 우연히 맞아 리포트가 그럴듯해 보이는데 RMSSD 는 11배 틀렸다.
    """
    rng = random.Random(9)
    vals = [0.8 + rng.gauss(0, 0.03) for _ in range(200)]
    rows = []
    for i, v in enumerate(vals):
        t = str(round(i * 0.8, 3)).replace(".", ",")
        # 2자리·3자리 소수를 섞는다(엑셀이 끝자리 0을 떼는 상황).
        vs = f"{v:.3f}" if i % 2 else f"{v:.2f}"
        rows.append(f"{t};{vs.replace('.', ',')}")
    path = _write(tmp_path, "euro.csv", "Zeit;RR (s)\n" + "\n".join(rows) + "\n")
    series, meta = load_series(path)
    assert meta["column"] == "RR (s)"
    assert meta["unit"] == "s"
    assert sum(series) / len(series) == pytest.approx(800.0, abs=15.0)
    assert len(series) == 200            # "1," 같은 토큰이 조용히 버려지지 않음


def test_korean_header_beats_unknown_ascii_time_column(tmp_path):
    """한글 헤더가 토큰화돼야 한다 — 안 되면 시간축이 값 열로 뽑힌다.

    과거: _tokens 가 [^A-Za-z0-9] 로 쪼개 "간격" → [] → 점수 0 → 'Zeit'(1)에 밀렸고,
    누적 시간 열이 값으로 뽑혀 SDNN 575 ms 같은 그럴듯한 오답이 나왔다.
    """
    rng = random.Random(9)
    rows = "\n".join(f"{i * 0.8:.3f},{800 + rng.gauss(0, 30):.1f}"
                     for i in range(200))
    path = _write(tmp_path, "kor.csv", "Zeit,간격\n" + rows + "\n")
    series, meta = load_series(path)
    assert meta["column"] == "간격"
    assert sum(series) / len(series) == pytest.approx(800.0, abs=20.0)


@pytest.mark.parametrize("name,expected", [
    ("rr_ms", 3), ("간격", 3), ("심박수", 3), ("nn_ms", 3), ("HR", 3),
    ("value", 3),
    ("Zeit", 2), ("sensor", 2),
    ("Pulse rate (count/min)", 2),   # 값 토큰 + 비값 토큰 → 중립(실격 아님)
    ("time_s", 1), ("경과시간", 1),
    ("peak_time", 1),                # 값 토큰 + 시간 토큰 → 발생시각 열로 강등
    ("valid", 0), ("annotation", 0), ("subject_id", 0), ("품질", 0),
])
def test_name_score_table(name, expected):
    from hrvkit.dataio import _name_score
    assert _name_score(name) == expected


def test_value_column_wins_over_time_axis_despite_count_token(tmp_path):
    """"count" 가 들어간 진짜 값 열이 시간축에 져서는 안 된다.

    과거: "count" 가 실격 토큰이라 'Pulse rate (count/min)' 이 후보에서 빠지고
    time_s(누적 시간)가 값 열로 뽑혀 100% 이상박동을 냈다.
    """
    rng = random.Random(9)
    rows = "\n".join(f"{i * 0.8:.3f},{75 + rng.gauss(0, 3):.1f}"
                     for i in range(200))
    path = _write(tmp_path, "pulse.csv",
                  "time_s,Pulse rate (count/min)\n" + rows + "\n")
    series, meta = load_series(path)
    assert meta["column"] == "Pulse rate (count/min)"
    assert meta["unit"] == "bpm"
    assert sum(series) / len(series) == pytest.approx(800.0, abs=25.0)


def test_manifest_condition_id_is_not_a_subject_label(tmp_path):
    """"condition_id" 의 'id' 부분일치로 조건 열이 라벨로 뽑히면 안 된다.

    과거: 모든 행이 같은 조건이라 '피험자 라벨 중복' 으로 멀쩡한 매니페스트가 거부됐다.
    """
    for s in range(3):
        for tag in ("b", "i"):
            _write(tmp_path, f"s{s}_{tag}.csv", "rr_ms\n800\n810\n")
    man = _write(tmp_path, "m.csv",
                 "base,interv,condition_id\n" +
                 "\n".join(f"s{s}_b.csv,s{s}_i.csv,slowbreath"
                           for s in range(3)) + "\n")
    pairs = load_manifest(man)
    assert len(pairs) == 3
    assert all(lab == "" for _, _, lab in pairs)   # 조건 열은 라벨이 아니다


def test_manifest_path_column_not_used_as_label(tmp_path):
    """"baseline_id"/"post_id" 는 경로 열이지 피험자 라벨이 아니다."""
    for s in range(2):
        for tag in ("b", "i"):
            _write(tmp_path, f"s{s}_{tag}.csv", "rr_ms\n800\n810\n")
    man = _write(tmp_path, "m2.csv",
                 "baseline_id,post_id\n" +
                 "\n".join(f"s{s}_b.csv,s{s}_i.csv" for s in range(2)) + "\n")
    pairs = load_manifest(man)
    assert len(pairs) == 2
    assert all(not lab.endswith(".csv") for _, _, lab in pairs)


def test_manifest_subject_label_still_detected(tmp_path):
    for s in range(2):
        for tag in ("b", "i"):
            _write(tmp_path, f"s{s}_{tag}.csv", "rr_ms\n800\n810\n")
    man = _write(tmp_path, "m3.csv",
                 "baseline,intervention,subject\n" +
                 "\n".join(f"s{s}_b.csv,s{s}_i.csv,S{s}" for s in range(2)) + "\n")
    assert [lab for _, _, lab in load_manifest(man)] == ["S0", "S1"]


def test_header_shorter_than_data_warns(tmp_path):
    """따옴표 없는 구분자가 값 안에 있으면 숫자가 잘려 읽힌다 → 반드시 경고.

    `rr_ms` 헤더 + 따옴표 없는 `1,010` → csv가 두 열로 쪼개 col0="1" → RR=1.0 ms
    상수. 헤더 이름 수(1) < 데이터 열 수(2) 가 그 신호다.
    """
    path = _write(tmp_path, "bare.csv",
                  "rr_ms\n1,010\n1,020\n998\n1,005\n1,015\n990\n")
    _series, meta = load_series(path)
    assert meta["column_note"] is not None
    assert "구분자" in meta["column_note"]


def test_quoted_thousands_in_comma_file(tmp_path):
    # 따옴표로 제대로 감싼 경우는 정확히 읽어야 한다.
    path = _write(tmp_path, "q.csv",
                  'rr_ms\n"1,010"\n"1,020"\n998\n"1,005"\n"1,015"\n990\n')
    series, meta = load_series(path)
    assert meta["column_note"] is None
    assert series == pytest.approx([1010, 1020, 998, 1005, 1015, 990])


def test_normal_file_has_no_column_note(tmp_path):
    rr = [round(v, 1) for v in _rr(n=60)]
    path = _write(tmp_path, "ok.csv",
                  "time_s,rr_ms\n" + "\n".join(f"{i*0.8:.3f},{v:.1f}"
                                               for i, v in enumerate(rr)) + "\n")
    _series, meta = load_series(path)
    assert meta["column_note"] is None
