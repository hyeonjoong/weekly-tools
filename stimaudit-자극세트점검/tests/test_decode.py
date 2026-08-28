"""ffmpeg 디코드 통로 — 이 파일이 없어서 **기능 전체가 죽은 채로** 배포될 뻔했습니다.

라운드 1 안전성 검토가 찾은 결함: `cli._load_set` 이 분석 **전에** `info.path` 를
원본 압축 파일로 되돌려 놓는 바람에 `analyze_file` 이 임시 WAV 가 아니라 MP3 를
다시 열었고, 모든 MP3/M4A/FLAC 가 "RIFF 가 아님"으로 실패했습니다. 테스트가
424개였는데도 한 줄도 이 경로를 지나가지 않아서 전부 초록이었습니다.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from stimaudit import cli, decode

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg 가 PATH 에 없습니다")


def _mp3(tmp_path, name, freq=440.0, seconds=2.0, channels=2, rate=44100):
    """ffmpeg 로 합성음 MP3 를 만듭니다 (네트워크 없음, 파일 시스템만)."""
    p = os.path.join(str(tmp_path), name)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency={}:duration={}:sample_rate={}".format(freq, seconds, rate),
         "-ac", str(channels), "-c:a", "libmp3lame", "-b:a", "128k", p],
        check=True)
    return p


def test_needs_decode():
    assert decode.needs_decode("a.mp3") is True
    assert decode.needs_decode("A.FLAC") is True
    assert decode.needs_decode("a.wav") is False
    assert decode.needs_decode("noext") is False


def test_ffmpeg_path_found():
    assert decode.ffmpeg_path() is not None


def test_decode_produces_a_readable_wav(tmp_path):
    from stimaudit import analyze, wavread
    src = _mp3(tmp_path, "a.mp3", freq=440.0, seconds=2.0)
    with decode.TempDecoder() as dec:
        out = dec.decode(src)
        info = wavread.probe(out)
        assert info.encoding == "pcm" and info.n_channels == 2
        assert info.duration_s == pytest.approx(2.0, abs=0.2)
        m = analyze.analyze_file(info)
        assert m.spectral_peak_hz[0] == pytest.approx(440.0, abs=5.0)


def test_temp_dir_removed_on_exit(tmp_path):
    src = _mp3(tmp_path, "a.mp3")
    with decode.TempDecoder() as dec:
        out = dec.decode(src)
        tmpdir = os.path.dirname(out)
        assert os.path.isdir(tmpdir)
    assert not os.path.exists(tmpdir)


def test_temp_dir_removed_even_on_exception(tmp_path):
    src = _mp3(tmp_path, "a.mp3")
    dec = decode.TempDecoder()
    tmpdir = None
    try:
        with dec:
            tmpdir = os.path.dirname(dec.decode(src))
            raise RuntimeError("중단")
    except RuntimeError:
        pass
    assert tmpdir and not os.path.exists(tmpdir)


def test_failed_attempt_still_advances_the_output_counter(tmp_path):
    """실패한 시도가 이름을 재사용하게 두면 **엉뚱한 파일의 지표**가 보고됩니다.

    이름을 `성공한 개수`로 붙이면, 중단된 디코드가 남긴 부분 파일을 다음 파일이
    그대로 물려받습니다. ffmpeg 는 덮어쓰기를 거부하면서도 종료코드 0 을 내므로
    그 상태가 조용히 통과합니다(라운드 1 검토에서 실증). 이름은 **시도한 개수**로
    붙어야 합니다.
    """
    bad = os.path.join(str(tmp_path), "bad.mp3")
    open(bad, "wb").write(b"junk" * 100)
    good = _mp3(tmp_path, "good.mp3", freq=700.0, seconds=1.0)
    with decode.TempDecoder() as dec:
        with pytest.raises(decode.DecodeError):
            dec.decode(bad)
        out = dec.decode(good)
        assert os.path.basename(out) == "0002.wav"     # 0001 을 재사용하지 않습니다


def test_stale_output_file_is_overwritten(tmp_path):
    """`-y` 가 없으면 ffmpeg 는 기존 파일을 만나 종료코드 0 으로 아무것도 안 합니다."""
    from stimaudit import wavread
    good = _mp3(tmp_path, "good.mp3", freq=700.0, seconds=1.0)
    with decode.TempDecoder() as dec:
        first = dec.decode(good)
        stale_dir = os.path.dirname(first)
        planted = os.path.join(stale_dir, "0002.wav")
        open(planted, "wb").write(b"RUBBISH" * 100)     # 다음 이름을 미리 점거
        second = dec.decode(good)
        assert second == planted
        assert wavread.probe(second).duration_s == pytest.approx(1.0, abs=0.2)


def test_output_names_are_unique_per_attempt(tmp_path):
    a, b = _mp3(tmp_path, "a.mp3"), _mp3(tmp_path, "b.mp3", freq=600.0)
    with decode.TempDecoder() as dec:
        assert dec.decode(a) != dec.decode(b)


def test_unreadable_input_raises_decode_error(tmp_path):
    bad = os.path.join(str(tmp_path), "bad.mp3")
    open(bad, "wb").write(b"not audio at all" * 10)
    with decode.TempDecoder() as dec:
        with pytest.raises(decode.DecodeError) as e:
            dec.decode(bad)
    assert "디코드하지 못했습니다" in str(e.value)


def test_error_message_does_not_leak_the_folder_path(tmp_path):
    """ffmpeg 판본에 따라 오류에 절대경로가 통째로 들어갑니다."""
    bad = os.path.join(str(tmp_path), "bad.mp3")
    open(bad, "wb").write(b"junk" * 100)
    with decode.TempDecoder() as dec:
        with pytest.raises(decode.DecodeError) as e:
            dec.decode(bad)
    msg = str(e.value)
    assert str(tmp_path) not in msg
    assert os.path.expanduser("~") not in msg


def test_tidy_strips_memory_addresses_and_paths(tmp_path):
    raw = b"[out#0/wav @ 0xac6834180] " + str(tmp_path).encode() + b"/x.mp3: Invalid data\n"
    got = decode._tidy(raw, os.path.join(str(tmp_path), "x.mp3"))
    assert "0x" not in got
    assert str(tmp_path) not in got
    assert "Invalid data" in got


def test_tidy_handles_empty_stderr():
    assert decode._tidy(b"") == "사유 불명"


def test_protocol_prefix_filename_is_not_treated_as_a_protocol(tmp_path):
    """`concat:a.mp3` 라는 **이름의 파일**이 concat 프로토콜로 열리면 안 됩니다."""
    real = _mp3(tmp_path, "real.mp3", freq=440.0, seconds=1.0)
    tricky = os.path.join(str(tmp_path), "concat:real.mp3")
    shutil.copyfile(real, tricky)
    from stimaudit import wavread
    with decode.TempDecoder() as dec:
        out = dec.decode(tricky)
        info = wavread.probe(out)
        assert info.duration_s == pytest.approx(1.0, abs=0.2)


def test_cli_analyses_decoded_audio_end_to_end(tmp_path, capsys):
    """README 가 약속하는 경로 — MP3 두 개가 실제로 분석되어야 합니다."""
    a = _mp3(tmp_path, "a.mp3", freq=440.0)
    b = _mp3(tmp_path, "b.mp3", freq=520.0)
    assert cli.main([a, b, "--inspect", "--quiet"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "못 읽음: 0" in out
    assert "ffmpeg 로 디코드해 읽음" in out       # 커버리지 자백에 남습니다
    assert "LUFS" in out


def test_cli_reports_undecodable_file_as_unreadable(tmp_path, capsys):
    a = _mp3(tmp_path, "a.mp3")
    b = _mp3(tmp_path, "b.mp3", freq=520.0)
    bad = os.path.join(str(tmp_path), "bad.mp3")
    open(bad, "wb").write(b"junk" * 100)
    assert cli.main([a, b, bad, "--inspect", "--quiet"]) == cli.EXIT_UNDECIDABLE
    assert "bad.mp3" in capsys.readouterr().out


def test_no_temp_dirs_left_after_cli_run(tmp_path):
    import glob
    import tempfile
    a = _mp3(tmp_path, "a.mp3")
    b = _mp3(tmp_path, "b.mp3", freq=520.0)
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "stimaudit_decode_*")))
    cli.main([a, b, "--inspect", "--quiet"])
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "stimaudit_decode_*")))
    assert after == before
