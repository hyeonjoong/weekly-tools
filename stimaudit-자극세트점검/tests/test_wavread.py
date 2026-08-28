"""WAV 읽기 — 실물 자산이 24bit/48kHz 이므로 여기가 틀리면 전부 틀립니다."""
from __future__ import annotations

import math
import os
import struct
import wave

import pytest

from stimaudit import wavread
from tests.conftest import FS, sine


@pytest.mark.parametrize("bits", [8, 16, 24, 32])
def test_roundtrip_all_bit_depths(mk, bits):
    x = sine(440.0, 0.2, 0.5)
    info = wavread.probe(mk("a.wav", [x], bits=bits))
    assert info.bits == bits
    assert info.encoding == "pcm"
    got = wavread.read_all(info)[0]
    tol = {8: 0.02, 16: 1e-4, 24: 1e-6, 32: 1e-8}[bits]
    assert len(got) == len(x)
    for a, b in zip(got, x):
        assert a == pytest.approx(b, abs=tol)


def test_24bit_sign_extension(tmp_path):
    """3바이트 부호확장 — `struct` 에 포맷이 없어 손으로 하는 부분입니다.

    −8388608 (0x800000) 과 +8388607 (0x7FFFFF) 를 직접 써서, 부호 비트가
    올바르게 확장되는지 고정합니다. 잘못하면 최댓값이 최솟값으로 뒤집힙니다.
    """
    path = os.path.join(str(tmp_path), "s24.wav")
    frames = b""
    for v in (0, 1, -1, 8388607, -8388608, 4194304, -4194304):
        frames += struct.pack("<i", v)[0:3]
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(48000)
        wf.writeframes(frames)
    got = wavread.read_all(wavread.probe(path))[0]
    assert got[0] == pytest.approx(0.0)
    assert got[1] == pytest.approx(1 / 8388608.0)
    assert got[2] == pytest.approx(-1 / 8388608.0)
    assert got[3] == pytest.approx(8388607 / 8388608.0)
    assert got[4] == pytest.approx(-1.0)
    assert got[5] == pytest.approx(0.5)
    assert got[6] == pytest.approx(-0.5)


def test_float32_is_read_even_though_wave_module_refuses(tmp_path):
    """표준 `wave` 는 wFormatTag=3 을 거절합니다. 헤더를 직접 읽어 통과시킵니다."""
    path = os.path.join(str(tmp_path), "f32.wav")
    data = struct.pack("<4f", 0.0, 0.5, -0.5, 1.0)
    fmt = struct.pack("<HHIIHH", 3, 1, 48000, 48000 * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + \
        b"data" + struct.pack("<I", len(data)) + data
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(wave.Error):
        wave.open(path, "rb")
    info = wavread.probe(path)
    assert info.encoding == "float" and info.bits == 32
    assert wavread.read_all(info)[0] == pytest.approx([0.0, 0.5, -0.5, 1.0])


def test_stereo_deinterleaves_correctly(mk):
    left = [0.1, 0.2, 0.3, 0.4]
    right = [-0.1, -0.2, -0.3, -0.4]
    info = wavread.probe(mk("st.wav", [left, right], bits=24))
    got = wavread.read_all(info)
    assert info.n_channels == 2
    assert got[0] == pytest.approx(left, abs=1e-6)
    assert got[1] == pytest.approx(right, abs=1e-6)


@pytest.mark.parametrize("fs", [22050, 44100, 48000, 96000])
def test_sample_rates(mk, fs):
    info = wavread.probe(mk("r.wav", [sine(100.0, 0.1, 0.3, fs)], fs=fs))
    assert info.sample_rate == fs
    assert info.duration_s == pytest.approx(0.1, abs=0.001)


def test_streaming_blocks_equal_whole_read(mk):
    info = wavread.probe(mk("b.wav", [sine(300.0, 0.5, 0.4), sine(310.0, 0.5, 0.4)], bits=24))
    whole = wavread.read_all(info)
    chunks = [[], []]
    for block in wavread.iter_blocks(info, block_frames=97):
        for c in range(2):
            chunks[c].extend(block[c])
    assert chunks[0] == pytest.approx(whole[0])
    assert chunks[1] == pytest.approx(whole[1])
    assert len(chunks[0]) == info.n_frames


def test_format_label_and_key(mk):
    info = wavread.probe(mk("k.wav", [sine(100.0, 0.05)], fs=48000, bits=24))
    assert info.format_key == (48000, 1, 24, "pcm")
    assert "48000 Hz" in info.format_label()
    assert "24bit" in info.format_label()


def test_missing_file():
    with pytest.raises(wavread.WavError) as e:
        wavread.probe("/nonexistent/nope.wav")
    assert "열 수 없" in str(e.value)


def test_too_small_file(tmp_path):
    p = os.path.join(str(tmp_path), "tiny.wav")
    open(p, "wb").write(b"RIFF")
    with pytest.raises(wavread.WavError) as e:
        wavread.probe(p)
    assert "너무 작" in str(e.value)


def test_not_riff(tmp_path):
    p = os.path.join(str(tmp_path), "x.wav")
    open(p, "wb").write(b"NOTAWAVE" + b"\x00" * 100)
    with pytest.raises(wavread.WavError) as e:
        wavread.probe(p)
    assert "RIFF" in str(e.value)


def test_rifx_big_endian_refused(tmp_path):
    p = os.path.join(str(tmp_path), "x.wav")
    open(p, "wb").write(b"RIFX" + b"\x00" * 4 + b"WAVE" + b"\x00" * 100)
    with pytest.raises(wavread.WavError) as e:
        wavread.probe(p)
    assert "RIFX" in str(e.value)


def test_compressed_codec_refused(tmp_path):
    p = os.path.join(str(tmp_path), "c.wav")
    fmt = struct.pack("<HHIIHH", 85, 2, 44100, 176400, 4, 16)   # MPEG layer 3
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + \
        b"data" + struct.pack("<I", 8) + b"\x00" * 8
    open(p, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(wavread.WavError) as e:
        wavread.probe(p)
    assert "압축 코덱" in str(e.value)


def test_empty_data_chunk_refused(tmp_path):
    p = os.path.join(str(tmp_path), "e.wav")
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + \
        b"data" + struct.pack("<I", 0)
    open(p, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(wavread.WavError) as e:
        wavread.probe(p)
    assert "프레임이 0개" in str(e.value)


def test_truncated_data_chunk_is_read_partially(tmp_path):
    """녹음 중 끊긴 파일 — 헤더가 주장하는 길이보다 실제 바이트가 적습니다."""
    p = os.path.join(str(tmp_path), "t.wav")
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + \
        b"data" + struct.pack("<I", 40000) + b"\x01\x00" * 100
    open(p, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
    info = wavread.probe(p)
    assert info.n_frames == 100
    assert "잘려" in info.source_note
    assert len(wavread.read_all(info)[0]) == 100


def test_extensible_format_without_guid_refused(tmp_path):
    p = os.path.join(str(tmp_path), "x.wav")
    fmt = struct.pack("<HHIIHH", 0xFFFE, 2, 48000, 192000, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + \
        b"data" + struct.pack("<I", 8) + b"\x00" * 8
    open(p, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(wavread.WavError) as e:
        wavread.probe(p)
    assert "EXTENSIBLE" in str(e.value)


def test_extra_chunks_are_skipped(tmp_path):
    """LIST/fact 같은 청크가 fmt 와 data 사이에 있어도 읽어야 합니다."""
    p = os.path.join(str(tmp_path), "l.wav")
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    junk = b"LIST" + struct.pack("<I", 6) + b"INFOxx"
    data = b"\x01\x00" * 50
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + junk + \
        b"data" + struct.pack("<I", len(data)) + data
    open(p, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
    info = wavread.probe(p)
    assert info.n_frames == 50


def test_odd_sized_chunk_padding(tmp_path):
    """RIFF 청크는 홀수 크기면 1바이트 패딩이 붙습니다 — 건너뛰지 않으면 data 를 놓칩니다."""
    p = os.path.join(str(tmp_path), "o.wav")
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    junk = b"note" + struct.pack("<I", 3) + b"abc" + b"\x00"
    data = b"\x02\x00" * 20
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + junk + \
        b"data" + struct.pack("<I", len(data)) + data
    open(p, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
    assert wavread.probe(p).n_frames == 20


def test_write_wav_rejects_bad_bits(tmp_path):
    with pytest.raises(ValueError):
        wavread.write_wav(os.path.join(str(tmp_path), "b.wav"), [[0.0]], 44100, bits=12)


def test_write_wav_rejects_no_channels(tmp_path):
    with pytest.raises(ValueError):
        wavread.write_wav(os.path.join(str(tmp_path), "b.wav"), [], 44100)


def test_write_wav_clamps_out_of_range(mk):
    got = wavread.read_all(wavread.probe(mk("c.wav", [[2.0, -2.0, 0.0]], bits=16)))[0]
    assert got[0] == pytest.approx(32767 / 32768.0)
    assert got[1] == pytest.approx(-1.0)


def test_half_second_file(mk):
    info = wavread.probe(mk("h.wav", [sine(200.0, 0.5)]))
    assert info.duration_s == pytest.approx(0.5, abs=0.002)
    assert len(wavread.read_all(info)[0]) == info.n_frames


def test_six_channel_file(mk):
    chans = [sine(100.0 * (i + 1), 0.1, 0.2) for i in range(6)]
    info = wavread.probe(mk("six.wav", chans, bits=24))
    assert info.n_channels == 6
    got = wavread.read_all(info)
    assert len(got) == 6
    assert got[3] == pytest.approx(chans[3], abs=1e-6)
