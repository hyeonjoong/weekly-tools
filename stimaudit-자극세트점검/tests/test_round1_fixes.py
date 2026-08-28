"""라운드 1 적대적 검토에서 나온 결함을 **하나씩 다시 못 나게** 고정합니다.

여기 있는 테스트는 전부 "고치기 전에는 실패했던" 것들입니다. 각 테스트의
독스트링에 어떤 사고였는지 적어 두었습니다 — 나중에 이 파일을 읽는 사람이
"이 검사 왜 있지?" 하고 지우는 일을 막기 위해서입니다.
"""
from __future__ import annotations

import json
import math
import os
import struct

import pytest

from stimaudit import analyze, claims, cli, design as design_mod, filters
from stimaudit import findings as F
from stimaudit import refs, report, safeio, setcheck, wavread
from tests.conftest import LCG, fade, noise, sine, sine_rms

FS = 44100


def _write(tmp_path, name, channels, fs=FS, bits=16):
    p = os.path.join(str(tmp_path), name)
    wavread.write_wav(p, channels, fs, bits)
    return p


def _an(tmp_path, name, channels, fs=FS, bits=16, block=65536):
    return analyze.analyze_file(wavread.probe(_write(tmp_path, name, channels, fs, bits)),
                                block_frames=block)


# ------------------------------------------------- 발견 1: EOF 클리핑 구간

def test_clipping_run_reaching_end_of_file_is_counted(tmp_path):
    """파일 **전체**가 클리핑된 자극이 "클리핑 0건"으로 통과하던 사고.

    `_scan_clipping` 은 문턱 아래 샘플이 나와야 구간을 닫았기 때문에, 마지막
    샘플까지 만점에 붙어 있으면 그 구간이 영원히 열린 채로 버려졌습니다.
    치명 판정 넷 중 하나가 정확히 그 병리에서 침묵했습니다.
    """
    n = FS  # 1초
    square = [1.0 if math.sin(2 * math.pi * 100 * i / FS) >= 0 else -1.0
              for i in range(n)]
    m = _an(tmp_path, "square.wav", [square])
    assert m.clip_run_count >= 1
    assert m.clip_sample_count == n


@pytest.mark.parametrize("run_len,expected", [(1, 0), (2, 0), (3, 1), (17, 1)])
def test_clip_run_at_eof_respects_minimum_length(tmp_path, run_len, expected):
    """EOF 구간도 최소 길이 규칙(연속 3샘플)을 그대로 따릅니다."""
    x = fade(sine(300.0, 0.5, 0.2), FS, ms=50.0)
    x = x[:-run_len] + [1.0] * run_len
    m = _an(tmp_path, "eof{}.wav".format(run_len), [x])
    assert m.clip_run_count == expected
    assert m.clip_sample_count == (run_len if expected else 0)


def test_eof_clip_flush_does_not_double_count(tmp_path):
    """정상적으로 닫힌 구간이 EOF 플러시에서 한 번 더 세어지면 안 됩니다."""
    x = fade(sine(300.0, 0.5, 0.2), FS, ms=50.0)
    x[1000:1010] = [1.0] * 10          # 파일 한가운데 (정상 종료)
    m = _an(tmp_path, "mid.wav", [x])
    assert m.clip_run_count == 1
    assert m.clip_sample_count == 10


@pytest.mark.parametrize("block", [1024, 4096, 65536, 100000])
def test_eof_clipping_is_block_size_invariant(tmp_path, block):
    """블록 크기를 바꿔도 EOF 구간 계수가 같아야 합니다."""
    x = fade(sine(300.0, 0.4, 0.2), FS, ms=40.0)[:-8] + [1.0] * 8
    m = _an(tmp_path, "blk{}.wav".format(block), [x], block=block)
    assert (m.clip_run_count, m.clip_sample_count) == (1, 8)


# ------------------------------------------- 발견 2: 국소 두드러짐 (반송음)

def _pink(seconds, amp=0.2, fs=FS, seed=5):
    """1/f 근사 — 1차 저역통과 누적으로 만든 분홍 잡음."""
    rng = LCG(seed)
    y, acc = [], 0.0
    for _ in range(int(fs * seconds)):
        acc = 0.99 * acc + rng.uniform()
        y.append(acc)
    peak = max(abs(v) for v in y) or 1.0
    return [amp * v / peak for v in y]


def test_pink_noise_is_not_accepted_as_a_carrier(tmp_path):
    """핑크노이즈 대조군에 **가짜 반송주파수**가 붙던 사고.

    전역 중앙값 대비 두드러짐은 사실상 스펙트럼 기울기라서, 핑크노이즈가
    32 dB 로 "뚜렷한 반송음 있음" 판정을 받고 `carrier_hz` 주장 대조에서
    "실측 5.38 Hz" 라는 **지어낸 숫자**를 내놓았습니다.
    """
    m = _an(tmp_path, "pink.wav", [_pink(4.0)])
    prom = m.spectral_peak_prominence_db[0]
    assert prom is not None and prom < claims.MIN_CARRIER_PROMINENCE_DB
    assert claims._carrier(m)[0] is None


def test_tone_is_still_accepted_as_a_carrier(tmp_path):
    """오탐을 막느라 정탐까지 죽이지 않았는지 — 순수 톤은 그대로 잡힙니다."""
    m = _an(tmp_path, "tone.wav", [fade(sine(440.0, 4.0, 0.4), FS, ms=100.0)])
    assert m.spectral_peak_prominence_db[0] > 40.0
    assert claims._carrier(m)[0] == pytest.approx(440.0, abs=2.0)


def test_carrier_candidate_never_below_audible_floor(tmp_path):
    """반송음 후보는 20 Hz 미만에서 뽑히지 않습니다 (`MIN_CARRIER_HZ`)."""
    m = _an(tmp_path, "brown.wav", [_pink(4.0, seed=11)])
    assert m.spectral_peak_hz[0] >= analyze.MIN_CARRIER_HZ - 1e-9


def test_tone_in_noise_still_wins(tmp_path):
    """잡음 위에 얹힌 톤(SNR 이 낮지 않은 경우)은 여전히 반송음입니다."""
    tone = sine(600.0, 4.0, 0.25)
    bg = _pink(4.0, amp=0.05)
    mixed = [tone[i] + bg[i] for i in range(len(tone))]
    m = _an(tmp_path, "mix.wav", [fade(mixed, FS, ms=80.0)])
    assert claims._carrier(m)[0] == pytest.approx(600.0, abs=3.0)


# --------------------------------------------------- 발견 3: NaN / Inf 표본

def _float_wav(path, values, fs=FS):
    data = b"".join(struct.pack("<f", v) for v in values)
    fmt = struct.pack("<HHIIHH", 3, 1, fs, fs * 4, 4, 32)
    chunks = (b"fmt " + struct.pack("<I", len(fmt)) + fmt
              + b"data" + struct.pack("<I", len(data)) + data)
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return path


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_sample_makes_the_file_unreadable(tmp_path, bad):
    """NaN 한 샘플이 **툴의 핵심 판정을 조용히 껐던** 사고.

    라우드니스가 NaN → `lufs_i = None` → 조건 간 대조에서 그 조건이 통째로
    빠지고, 40 LU 차이가 나는 세트가 종료코드 0 으로 통과했습니다. 전 구간
    NaN 인 파일은 "전 구간 무음"이라는 틀린 진단까지 받았습니다.
    """
    v = [0.2 * math.sin(2 * math.pi * 440 * i / FS) for i in range(FS)]
    v[500] = bad
    p = _float_wav(os.path.join(str(tmp_path), "bad.wav"), v)
    with pytest.raises(wavread.WavError) as exc:
        analyze.analyze_file(wavread.probe(p))
    assert "NaN" in str(exc.value) or "유한" in str(exc.value)


def test_clean_float_file_still_reads(tmp_path):
    """유한한 float 파일(작은 값 포함)은 그대로 읽혀야 합니다."""
    v = [1e-30 * math.sin(2 * math.pi * 440 * i / FS) for i in range(FS)]
    p = _float_wav(os.path.join(str(tmp_path), "tiny.wav"), v)
    m = analyze.analyze_file(wavread.probe(p))
    assert m.info.n_frames == FS


def test_nan_file_exits_undecidable(tmp_path, capsys):
    """NaN 파일이 섞이면 종료코드 3 — '치명 0건'이라고 말하지 않습니다."""
    good = _write(tmp_path, "good.wav", [fade(sine_rms(440.0, 2.0, -23.0), FS, ms=100.0)])
    v = [0.2 * math.sin(2 * math.pi * 440 * i / FS) for i in range(FS * 2)]
    v[10] = float("nan")
    bad = _float_wav(os.path.join(str(tmp_path), "bad.wav"), v)
    rc = cli.main([good, bad, "--inspect", "--quiet"])
    assert rc == 3
    assert "못 읽음: 1" in capsys.readouterr().out


# ---------------------------------------- 발견 4: 잴 수 없는 조건은 자백한다

def test_undecidable_condition_is_reported_not_skipped(tmp_path, capsys):
    """400 ms 미만 자극이 **음량 판정 자체를 조용히 끄던** 사고.

    게이팅 블록이 하나도 안 나오면 LUFS 가 None 인데, 전에는 그 조건이 낀
    쌍을 '허용 안'과 똑같이 건너뛰어서 38 dB 차이가 나는 세트가 "치명 0건"
    으로 통과했습니다.
    """
    short = _write(tmp_path, "short.wav", [sine(440.0, 0.2, 0.9)])
    long_quiet = _write(tmp_path, "quiet.wav",
                        [fade(sine(440.0, 5.0, 0.01), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"A": ["short.wav"], "B": ["quiet.wav"]}}, fh)
    out = os.path.join(str(tmp_path), "out")
    cli.main([short, long_quiet, "--design", d, "--out-dir", out, "--quiet"])
    text = capsys.readouterr().out
    assert F.KIND_LEVEL_UNDECIDABLE in text
    assert "short.wav" in text


def test_measurable_set_has_no_undecidable_finding(tmp_path, capsys):
    """정상 세트에는 이 자백이 붙지 않습니다 (오탐 억제)."""
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"A": ["a.wav"], "B": ["b.wav"]}}, fh)
    cli.main([a, b, "--design", d, "--out-dir", os.path.join(str(tmp_path), "o"),
              "--quiet"])
    assert F.KIND_LEVEL_UNDECIDABLE not in capsys.readouterr().out


# ------------------------------------------------------- 발견 5: 죽은 채널

def test_fully_silent_channel_is_critical(tmp_path, capsys):
    """한쪽 채널이 전부 0 이면 좌우 검사가 통째로 건너뛰어지던 사고.

    `dbfs(0)` 이 None 이라 `lr_rms_diff_db` 가 None 이 되고, "좌우 균형
    검사함 · 경고 0건"이 나왔습니다. **가장 나쁜 경우만** 빠져나갔습니다.
    """
    live = fade(sine_rms(440.0, 2.0, -23.0), FS, ms=100.0)
    dead = [0.0] * len(live)
    p = _write(tmp_path, "dead.wav", [live, dead])
    q = _write(tmp_path, "ok.wav", [live, live])
    rc = cli.main([p, q, "--out-dir", os.path.join(str(tmp_path), "o"), "--quiet"])
    text = capsys.readouterr().out
    assert "전 구간 무음" in text and "dead.wav" in text
    assert rc == 1


def test_mono_file_does_not_trigger_dead_channel(tmp_path, capsys):
    """모노 파일에는 '죽은 채널' 판정이 붙지 않습니다."""
    a = _write(tmp_path, "m1.wav", [fade(sine_rms(440.0, 2.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "m2.wav", [fade(sine_rms(500.0, 2.0, -23.0), FS, ms=100.0)])
    rc = cli.main([a, b, "--out-dir", os.path.join(str(tmp_path), "o"), "--quiet"])
    assert rc == 0
    assert "전 구간 무음" not in capsys.readouterr().out


# ------------------------------------ 발견 6: 출력 사고를 판정으로 보고하지 않기

def test_encoding_hostile_console_does_not_crash(tmp_path, monkeypatch, capsys):
    """cp949 콘솔(한국 윈도우 기본)에서 트레이스백 + 종료코드 1 이던 사고.

    인코딩 사고가 "치명 발견"으로 보고되면 파이프라인이 잘못된 결론을 냅니다.
    """
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 2.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 2.0, -23.0), FS, ms=100.0)])

    class _Cp949:
        def __init__(self):
            self.buf = []

        def write(self, text):
            text.encode("cp949")     # 인코딩 불가 문자면 여기서 터집니다
            self.buf.append(text)

        def flush(self):
            pass

    import sys as _sys
    monkeypatch.setattr(_sys, "stdout", _Cp949())
    rc = cli.main([a, b, "--inspect", "--quiet"])
    assert rc in (0, 3)          # 절대 1(치명 발견)이 아닙니다


def test_report_stream_falls_back_when_stdout_is_none(tmp_path, monkeypatch):
    """`stimaudit ... >&-` 로 표준출력이 닫히면 파이썬은 sys.stdout 을 None 으로
    둡니다. 전에는 AttributeError 트레이스백 + 종료코드 1 이었습니다."""
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 2.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 2.0, -23.0), FS, ms=100.0)])
    import sys as _sys
    monkeypatch.setattr(_sys, "stdout", None)
    rc = cli.main([a, b, "--inspect", "--quiet"])
    assert rc in (0, 3)


# ------------------------------------------- 발견 7: 낮은 샘플레이트 A-가중

def test_a_weighted_levels_not_computed_below_floor(tmp_path):
    """fs 가 낮으면 1 kHz 재정규화가 폭주해 LAeq 300 dB 가 인쇄되던 사고."""
    fs = 4000
    x = [0.3 * math.sin(2 * math.pi * 200 * i / fs) for i in range(fs * 2)]
    m = _an(tmp_path, "low.wav", [x], fs=fs)
    assert m.laeq_dbfs is None and m.lamax_dbfs is None
    assert m.dynamic_range_db is None
    assert m.lufs_i is not None          # LUFS 는 영향을 받지 않습니다


def test_a_weighted_levels_present_at_normal_rates(tmp_path):
    for fs in (44100, 48000):
        x = fade([0.3 * math.sin(2 * math.pi * 1000 * i / fs) for i in range(fs * 2)],
                 fs, ms=100.0)
        m = _an(tmp_path, "n{}.wav".format(fs), [x], fs=fs)
        # 진폭 0.3 사인 → RMS 0.2121 → −13.46 dBFS. 1 kHz 는 A-가중 0 dB 이므로
        # LAeq 도 같은 값이어야 합니다(페이드 때문에 아주 조금 낮습니다).
        assert m.laeq_dbfs is not None
        assert -14.5 < m.laeq_dbfs < -13.0


def test_a_weighting_gain_never_absurd():
    """정규화 계수가 폭주하지 않는지 — 하한 위에서는 이득이 상식적입니다."""
    for fs in (8000, 16000, 44100, 48000, 96000):
        g = filters.sos_freq_response_db(filters.a_weighting_sos(fs), 1000.0, fs)
        assert abs(g) < 1e-6
        g200 = filters.sos_freq_response_db(filters.a_weighting_sos(fs), 200.0, fs)
        assert -14.0 < g200 < -8.0        # IEC 61672: 200 Hz ≈ −10.9 dB


# --------------------------------- 발견 8: 문장초안이 못 읽은 파일을 숨기지 않기

def _draft(tmp_path, files, design=None):
    out = os.path.join(str(tmp_path), "o")
    args = list(files) + ["--out-dir", out, "--quiet"]
    if design:
        args += ["--design", design]
    rc = cli.main(args)
    with open(os.path.join(out, report.OUT_DRAFT_MD), encoding="utf-8") as fh:
        return rc, fh.read()


def test_draft_warns_and_scopes_when_a_file_was_unreadable(tmp_path):
    """못 읽은 파일이 있는데도 초안이 "모든 자극은 …" 이라고 쓰던 사고.

    사용자가 원고에 그대로 붙이라고 안내받는 바로 그 파일에서, 세트 전체에
    대한 **거짓 문장**이 아무 경고 없이 나왔습니다.
    """
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    broken = os.path.join(str(tmp_path), "broken.wav")
    with open(broken, "wb") as fh:
        fh.write(b"RIFFxxxxWAVEjunk")
    rc, text = _draft(tmp_path, [a, b, broken])
    assert rc == 3
    assert "읽지 못한 파일이 1개" in text
    assert "모든 자극의 길이는" not in text
    assert "All stimuli were" not in text
    assert "읽은 자극" in text


def test_draft_says_between_file_when_there_are_no_conditions(tmp_path):
    """조건이 없는데 "조건 간 최대 차이" 라고 쓰던 사고 — 하지 않은 실험 통제를
    했다고 주장하는 문장이 됩니다(`--emit-design` 뼈대가 바로 이 경로입니다)."""
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -20.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -29.0), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "one.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"전부": ["a.wav", "b.wav"]}}, fh)
    _, text = _draft(tmp_path, [a, b], design=d)
    assert "파일 간 최대 차이" in text and "between-file" in text
    assert "조건 간 최대 차이" not in text


def test_draft_says_between_condition_when_conditions_exist(tmp_path):
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "two.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"A": ["a.wav"], "B": ["b.wav"]}}, fh)
    _, text = _draft(tmp_path, [a, b], design=d)
    assert "조건 간 최대 차이" in text and "between-condition" in text


def test_inspect_prints_the_undecidable_bottom_line(tmp_path, capsys):
    """사용법.md 가 "맨 아래 한 줄부터 보세요" 라고 안내하는데, --inspect 경로에는
    `판정불가 — 못 읽은 파일이 N개` 줄이 아예 없었습니다."""
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 2.0, -23.0), FS, ms=100.0)])
    broken = os.path.join(str(tmp_path), "broken.wav")
    with open(broken, "wb") as fh:
        fh.write(b"RIFFxxxxWAVEjunk")
    rc = cli.main([a, broken, "--inspect", "--quiet"])
    assert rc == 3
    assert "판정불가 — 못 읽은 파일이 1개" in capsys.readouterr().out


# ------------------------------------------- 발견: 증거 줄이 헤드라인과 맞물리기

def test_level_mismatch_evidence_shows_condition_means(tmp_path, capsys):
    """헤드라인 Δ 는 조건 평균의 차이인데 증거 줄에는 '가장 벌어진 파일 쌍'만
    있어서, 그 두 값을 빼면 헤드라인과 다른 숫자가 나왔습니다."""
    a1 = _write(tmp_path, "a1.wav", [fade(sine_rms(300.0, 3.0, -14.0), FS, ms=100.0)])
    a2 = _write(tmp_path, "a2.wav", [fade(sine_rms(320.0, 3.0, -20.0), FS, ms=100.0)])
    b1 = _write(tmp_path, "b1.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"active": ["a1.wav", "a2.wav"], "control": ["b1.wav"]}}, fh)
    cli.main([a1, a2, b1, "--design", d, "--quiet",
              "--out-dir", os.path.join(str(tmp_path), "o")])
    text = capsys.readouterr().out
    assert "조건 평균" in text and "가장 벌어진 쌍" in text


# ------------------------------------------------- 발견: 매니페스트 침묵 금지

def test_manifest_that_matches_nothing_is_confessed(tmp_path, capsys):
    """세미콜론 CSV(유럽·한국 엑셀 기본 내보내기)를 주면 아무 말 없이 조용히
    지나가던 사고 — 교란 후보 절이 통째로 사라지고 이유도 없었습니다."""
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    man = os.path.join(str(tmp_path), "m.csv")
    with open(man, "w", encoding="utf-8") as fh:
        fh.write("file,roughness_asper\n없는파일1.wav,0.1\n없는파일2.wav,0.2\n")
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"A": ["a.wav"], "B": ["b.wav"]}}, fh)
    cli.main([a, b, "--design", d, "--manifest", man, "--quiet",
              "--out-dir", os.path.join(str(tmp_path), "o")])
    text = capsys.readouterr().out
    assert "교란 후보" in text
    assert "매니페스트에 없는 파일 2개" in text and "a.wav" in text


# ------------------------------------------------- 발견: 제어문자·경로 유출

def test_control_characters_are_stripped_from_csv_cells():
    assert "\x1b" not in safeio.sanitize_cell("\x1b[31m빨강")
    assert "\x07" not in safeio.sanitize_cell("벨\x07소리")
    # 정상적인 한글·괄호·이모지는 그대로 둡니다.
    assert safeio.sanitize_cell("싱잉볼_bi_(360+400Hz).wav") == "싱잉볼_bi_(360+400Hz).wav"
    # 수식 인젝션 방어는 그대로입니다.
    assert safeio.sanitize_cell("=cmd|'/c calc'").startswith("'")
    assert safeio.sanitize_cell(" =cmd").startswith("'")


def test_control_characters_in_condition_names_are_refused(tmp_path):
    d = os.path.join(str(tmp_path), "esc.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"\x1b[31mactive": ["a.wav"]}}, fh)
    with pytest.raises(design_mod.DesignError):
        design_mod.load(d)


def test_design_errors_do_not_leak_absolute_paths(tmp_path):
    missing = os.path.join(str(tmp_path), "깊은", "경로", "설계.json")
    with pytest.raises(design_mod.DesignError) as exc:
        design_mod.load(missing)
    assert str(tmp_path) not in str(exc.value)
    assert "설계.json" in str(exc.value)


def test_top_level_object_message_has_no_doubled_braces(tmp_path):
    d = os.path.join(str(tmp_path), "arr.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump([1, 2, 3], fh)
    with pytest.raises(design_mod.DesignError) as exc:
        design_mod.load(d)
    assert "{{" not in str(exc.value)


# ------------------------------------------------- 발견: 문헌 인용 정정 고정

def test_czempik_numbers_match_the_source_paper():
    """원문(PMC7644698 Table 2)을 직접 확인한 값입니다.

    리뷰 원고에는 LAeq20sec 이 −0.50 으로 적혀 있는데 **원문은 −0.41** 입니다.
    원고를 그대로 옮기면 오류까지 복제하므로, 이 툴은 원문 값을 씁니다.
    57.9 dB 도 exemplar 가 아니라 ROC 절단점입니다.
    """
    text = refs.LEVEL_RATIONALE
    assert "−0.64" in text and "−0.41" in text
    assert "−0.50" not in text
    assert "ROC" in text and "절단점" in text


def test_sharpness_row_names_the_right_authors():
    row = [r for r in refs.REFERENCES if "샤프니스" in r.axis][0]
    assert "Eerola & Lahdelma 2022" in row.citation
    assert "Lahdelma et al. 2022" not in row.citation
    assert "확인되지 않았습니다" in row.note


def test_reference_rows_still_carry_no_severity():
    """참조값 자료형에는 심각도 필드가 없어야 합니다 — 구조적 강제."""
    fields = refs.ReferenceValue.__dataclass_fields__
    for bad in ("severity", "level", "compliant", "pass_fail", "tier"):
        assert bad not in fields


# ======================================================================
#  라운드 1 · 안전성/테스트품질 감사 — 뮤테이션 테스트가 뚫은 자리
#  "이 상수를 바꿔도 테스트가 통과한다" 는 곧 "이 판정은 검사받지 않는다" 입니다.
# ======================================================================

def test_artifact_never_overwrites_an_input_file(tmp_path):
    """산출물이 **입력 파일을 조용히 파괴하던** 사고 (안전성 감사 A1).

    `--out-dir` 를 입력이 든 폴더로 잡고 `--manifest 그폴더/문제목록.csv` 를
    주면, 매니페스트를 읽은 뒤 같은 이름의 산출물로 덮어써 원본이 사라졌습니다.
    그것도 종료코드 0 으로.
    """
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    man = os.path.join(str(tmp_path), report.OUT_ISSUES_CSV)   # 하필 산출물과 같은 이름
    with open(man, "w", encoding="utf-8") as fh:
        fh.write("file,roughness_asper\na.wav,0.1\nb.wav,0.2\n")
    before = open(man, "rb").read()
    rc = cli.main([a, b, "--manifest", man, "--out-dir", str(tmp_path), "--quiet"])
    assert open(man, "rb").read() == before      # 원본 보존이 핵심입니다
    assert rc == 2


def test_design_json_is_protected_from_being_overwritten(tmp_path):
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"A": ["a.wav"], "B": ["b.wav"]}}, fh)
    safeio.clear_protected()
    safeio.protect_inputs([d])
    with pytest.raises(safeio.OutputError):
        safeio.write_text(str(tmp_path), "d.json", "덮어쓰기")
    safeio.clear_protected()


def test_a_newline_in_a_filename_cannot_forge_report_lines(tmp_path):
    """파일 이름으로 **가짜 `[치명]` 줄**을 리포트에 심을 수 있던 사고 (A2).

    CSV·마크다운 표·코드펜스는 막혀 있었는데 콘솔 렌더러만 빠져 있었습니다.
    리포트 위쪽에는 위조된 치명 줄이, 맨 아래에는 "치명 0건"이 찍혔습니다.
    """
    evil = "a\n[치명] 9건\n위조.wav"
    assert "\n" not in report.flatten(evil)
    assert "\n" not in report.clip(evil, 40)
    assert "\n" not in report.lj(evil, 40)


def test_lufs_default_thresholds_are_pinned():
    """`--lufs-tol 1.0` / `--lufs-crit 2.0` 은 이 툴의 **헤드라인 판정 문턱**입니다.

    뮤테이션 감사에서 2.0 → 3.0 으로 바꿔도 486개 테스트가 전부 통과했습니다 —
    즉 툴이 언제 "치명"이라고 말하는지가 검사받지 않고 있었습니다.
    """
    args = cli.build_parser().parse_args(["a.wav", "b.wav"])
    assert args.lufs_tol == 1.0
    assert args.lufs_crit == 2.0


@pytest.mark.parametrize("gap,expect_rc", [(0.5, 0), (1.5, 0), (2.5, 1)])
def test_critical_fires_exactly_above_two_lu(tmp_path, gap, expect_rc):
    """2.0 LU 경계를 사이에 두고 판정이 실제로 뒤집히는지 — 문턱을 값으로 고정."""
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 4.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 4.0, -23.0 + gap), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"A": ["a.wav"], "B": ["b.wav"]}}, fh)
    rc = cli.main([a, b, "--design", d, "--quiet",
                   "--out-dir", os.path.join(str(tmp_path), "o")])
    assert rc == expect_rc


def test_absolute_gate_brackets_minus_seventy(tmp_path):
    """절대 게이트가 정확히 −70 LUFS 인지 **양쪽에서** 확인합니다.

    기존 테스트는 −75 dBFS 신호를 써서 두 게이트 **모두** 아래였고, 그래서
    −70 → −69 로 바꿔도 통과했습니다(뮤테이션 감사 B3).
    """
    louder = _an(tmp_path, "g1.wav", [sine_rms(1000.0, 3.0, -69.0)])
    quieter = _an(tmp_path, "g2.wav", [sine_rms(1000.0, 3.0, -71.5)])
    assert louder.lufs_i is not None and louder.lufs_i > -70.0
    assert quieter.lufs_i is None        # 절대 게이트 아래 → 정의되지 않음


def test_relative_gate_is_ten_lu_at_a_nine_lu_gap(tmp_path):
    """상대 게이트(−10 LU)를 **9 LU 간격**으로 확인합니다.

    기존 테스트는 15 LU 간격이라 −10 과 −20 만 구분했고, −10 → −8 뮤테이션은
    살아남았습니다. 9 LU 간격이면 −10 게이트는 조용한 절반을 **살리고**
    −8 게이트는 **버립니다** — 그 차이가 값으로 드러납니다.
    """
    loud = sine_rms(1000.0, 3.0, -20.0)
    quiet = sine_rms(1000.0, 3.0, -29.0)
    m = _an(tmp_path, "gap9.wav", [loud + quiet])
    # −10 게이트: 두 구간이 모두 살아 에너지 평균 ≈ −22.5 LUFS 부근.
    # −8 게이트였다면 조용한 절반이 잘려 −20 쪽으로 밀립니다.
    assert m.lufs_i is not None
    assert -24.0 < m.lufs_i < -21.5


def test_channel_cap_is_enforced(tmp_path):
    """`MAX_CHANNELS` 를 아무 테스트도 참조하지 않아, 64 → 65535 로 늘려도
    스위트가 통과했습니다. 0.26 MB 파일이 3.1 GB 를 먹는 경로였습니다."""
    assert wavread.MAX_CHANNELS == 64
    fmt = struct.pack("<HHIIHH", 1, 65535, 44100, 4294967295, 65535, 16)
    data = b"\x00" * 1024
    chunks = (b"fmt " + struct.pack("<I", len(fmt)) + fmt
              + b"data" + struct.pack("<I", len(data)) + data)
    p = os.path.join(str(tmp_path), "many.wav")
    with open(p, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    with pytest.raises(wavread.WavError):
        wavread.probe(p)


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_non_finite_thresholds_are_refused(tmp_path, bad):
    """`--lufs-tol nan` 은 부등식을 전부 False 로 만들어 판정을 조용히 끕니다.

    이 가드는 **한 번 사고가 났기 때문에** 있는 것인데, 정작 그것을 지키는
    테스트가 없었습니다(뮤테이션 감사 B7).
    """
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -14.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -30.0), FS, ms=100.0)])
    assert cli.main([a, b, "--lufs-tol=" + bad, "--quiet",
                     "--out-dir", os.path.join(str(tmp_path), "o")]) == 2
    assert cli.main([a, b, "--lufs-crit=" + bad, "--quiet",
                     "--out-dir", os.path.join(str(tmp_path), "o2")]) == 2


def test_ffmpeg_argv_keeps_the_protocol_whitelist():
    """`concat:` 같은 프로토콜 이름을 파일 이름으로 위장하면 **다른 파일의**
    지표가 나옵니다. 상대경로에서만 재현되는 구멍이라, argv 자체를 고정합니다."""
    import inspect
    src = inspect.getsource(__import__("stimaudit.decode", fromlist=["decode"]))
    assert "-protocol_whitelist" in src
    assert "file" in src


def test_within_condition_spread_and_true_peak_thresholds_are_pinned():
    """값이 바뀌면 리포트의 의미가 바뀌는 상수들 — 문헌값이 아니라 이 툴의
    방법론적 기준이므로 여기서 값으로 못 박습니다."""
    assert setcheck.WITHIN_CONDITION_SPREAD_LU == 2.0
    assert setcheck.TRUE_PEAK_CEILING_DBTP == -1.0
    assert setcheck.LR_IMBALANCE_DB == 1.0
    assert setcheck.EDGE_CLICK_MS == 5.0
    assert analyze.DC_WARN_DBFS == -60.0
    assert analyze.CLIP_MIN_RUN == 3
    assert claims.MIN_CARRIER_PROMINENCE_DB == 12.0


# ---------------------------------- 라운드 2: README 예시가 실제 출력과 어긋나지 않게

def test_readme_example_block_matches_real_output(examples_dir, tmp_path, capsys):
    """README 의 "출력 예시" 가 **실제 출력과 다르게** 굳어 있던 문제.

    라운드 2 검토가 지적한 그대로입니다: 증거 줄 형식이 바뀌고 인용 문자열이
    바뀌어도 README 는 옛 출력을 보여주고 있었고, **테스트가 하나도 README 를
    보지 않아서** 드리프트가 그대로 살아남았습니다. 발췌(`…`)를 뺀 모든 줄이
    실제 실행 결과에 그대로 있어야 합니다.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    readme = open(os.path.join(root, "README.md"), encoding="utf-8").read()
    start = readme.index("### 출력 예시")
    fence = readme.index("```", start) + 3
    end = readme.index("```", fence)
    quoted = readme[fence:end].split("\n")

    cli.main([os.path.join(examples_dir, "어긋난세트", n)
              for n in sorted(os.listdir(os.path.join(examples_dir, "어긋난세트")))
              if n.endswith(".wav")]
             + ["--design", os.path.join(examples_dir, "어긋난세트", "설계.json"),
                "--out-dir", os.path.join(str(tmp_path), "o"), "--quiet"])
    real = set(l.rstrip() for l in capsys.readouterr().out.split("\n"))

    # `분석 소요: 2.2초` 는 **벽시계 시간**이라 기계마다 다릅니다 — 골든 비교에
    # 넣으면 느린 기계에서 거짓 실패가 납니다 (라운드 2 검증이 실제로 잡았습니다).
    skip = ("$ stimaudit", "…", "exit ", "분석 소요:")
    checked = 0
    for line in quoted:
        line = line.rstrip()
        if not line or line.lstrip().startswith(skip) or line.startswith(skip):
            continue
        assert line in real, "README 예시가 실제 출력과 다릅니다:\n  {}".format(line)
        checked += 1
    assert checked > 40, "예시 블록에서 검사한 줄이 너무 적습니다 ({})".format(checked)


def test_progress_indicator_cannot_forge_lines(tmp_path, capsys):
    """진행 표시가 `flatten` 을 우회하던 경로 (라운드 2 검증 항목 8).

    리포트 본문만 막아 두면 `2>&1 | tee log` 로 남긴 로그에는 파일 이름으로
    만든 가짜 `[치명]` 줄이 그대로 들어갑니다.
    """
    evil_dir = os.path.join(str(tmp_path), "evil")
    os.makedirs(evil_dir, exist_ok=True)
    evil = os.path.join(evil_dir, "a\n[치명] 9건\nfake.wav")
    wavread.write_wav(evil, [fade(sine_rms(300.0, 1.5, -23.0), FS, ms=100.0)], FS, 16)
    ok = _write(tmp_path, "ok.wav", [fade(sine_rms(500.0, 1.5, -23.0), FS, ms=100.0)])
    cli.main([evil, ok, "--inspect"])          # --quiet 없이: 진행 표시가 켜집니다
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err):
        for line in stream.split("\n"):
            assert not line.lstrip().startswith("[치명]"), line


def test_refusal_leaves_no_partial_artifacts(tmp_path):
    """거절이 세 번째 산출물에서 걸리면 앞의 두 개가 이미 떨어져 있던 문제.

    입력 폴더에 리포트 조각을 흘려 놓고 멈추면, 다음 실행의 입력 목록이
    오염됩니다. 쓰기 **전에** 전부 검사합니다.
    """
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 2.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 2.0, -23.0), FS, ms=100.0)])
    man = os.path.join(str(tmp_path), report.OUT_ISSUES_CSV)
    with open(man, "w", encoding="utf-8") as fh:
        fh.write("file,roughness_asper\na.wav,0.1\n")
    before = set(os.listdir(str(tmp_path)))
    rc = cli.main([a, b, "--manifest", man, "--out-dir", str(tmp_path), "--quiet"])
    assert rc == 2
    assert set(os.listdir(str(tmp_path))) == before      # 조각 하나도 남기지 않습니다


def test_bottom_line_says_when_a_condition_was_dropped(tmp_path, capsys):
    """"치명 0건" 옆에 **음량 대조에서 빠진 조건 수**를 붙입니다.

    자격 조건이 판정 목록 안에만 있으면 맨 아래 한 줄만 읽고 지나갑니다.
    """
    short = _write(tmp_path, "short.wav", [sine(440.0, 0.2, 0.5)])
    a = _write(tmp_path, "a.wav", [fade(sine_rms(300.0, 3.0, -23.0), FS, ms=100.0)])
    b = _write(tmp_path, "b.wav", [fade(sine_rms(500.0, 3.0, -23.0), FS, ms=100.0)])
    d = os.path.join(str(tmp_path), "d.json")
    with open(d, "w", encoding="utf-8") as fh:
        json.dump({"conditions": {"A": ["a.wav"], "B": ["b.wav"], "C": ["short.wav"]}}, fh)
    cli.main([a, b, short, "--design", d, "--quiet",
              "--out-dir", os.path.join(str(tmp_path), "o")])
    assert "음량 대조에서 빠진 조건 1개" in capsys.readouterr().out
