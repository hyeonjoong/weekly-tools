"""WAV 읽기 — 표준 라이브러리만, 스트리밍(블록 단위)으로.

왜 직접 헤더를 파싱하는가
-------------------------
표준 `wave` 모듈은 정수 PCM(8/16/24/32비트)만 엽니다. IEEE float32
(`wFormatTag = 3`)는 `wave.Error: unknown format: 3` 으로 거절합니다.
그런데 사운드 담당자가 보내오는 마스터는 float 로 렌더되는 일이 흔합니다.
그래서 헤더는 직접 파싱하고, **정수 PCM 이면 실제 프레임 읽기는 `wave` 에
맡기고**(검증된 경로), float 면 data 청크에서 직접 스트리밍합니다.

왜 스트리밍인가
---------------
실물 자산이 24bit/48kHz 스테레오 200초(≈ 54 MB)입니다. 통째로 float 리스트에
올리면 파이썬 객체 오버헤드로 수백 MB 가 됩니다. 그래서 블록 단위로 읽고,
호출부는 블록마다 누적기만 갱신합니다(`analyze.py`). 메모리는 파일 크기가
아니라 블록 크기 + 프레임요약 길이에 비례합니다.

24비트 부호확장
---------------
`struct` 에는 3바이트 정수 포맷이 없습니다. 바이트열을 4바이트 슬롯에
왼쪽정렬로 채워 넣고(`out[1::4] = data[0::3]` — C 레벨 슬라이스라 빠릅니다)
`array('i')` 로 읽은 뒤 산술 우시프트 8 로 부호를 확장합니다.
"""
from __future__ import annotations

import os
import struct
import sys
import wave
from array import array
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence, Tuple

_LITTLE = sys.byteorder == "little"

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

#: 읽어 줄 최대 채널 수 (손상된 헤더로 메모리를 터뜨리는 것을 막습니다).
MAX_CHANNELS = 64
#: RIFF 청크 순회 상한 (0바이트 청크 수백만 개로 시간을 끄는 것을 막습니다).
MAX_CHUNKS = 4096

#: 리포트에 그대로 인쇄할 사람이 읽는 인코딩 이름.
ENCODING_LABEL = {"pcm": "정수 PCM", "float": "IEEE float"}


class WavError(Exception):
    """WAV 를 읽지 못했습니다 — 사유 문자열을 그대로 커버리지 자백에 싣습니다."""


@dataclass
class WavInfo:
    """한 파일의 포맷 정보. 신호값은 담지 않습니다(스트리밍이므로)."""

    path: str
    n_channels: int
    sample_rate: int
    bits: int
    encoding: str  # 'pcm' | 'float'
    n_frames: int
    data_offset: int
    data_size: int
    source_note: str = ""  # 예: "ffmpeg 로 디코드됨 (원본 .mp3)"

    @property
    def duration_s(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.n_frames / float(self.sample_rate)

    @property
    def format_key(self) -> Tuple[int, int, int, str]:
        """세트 내 포맷 일치 비교용 키."""
        return (self.sample_rate, self.n_channels, self.bits, self.encoding)

    def format_label(self) -> str:
        return "{} Hz · {}ch · {}bit {}".format(
            self.sample_rate, self.n_channels, self.bits, ENCODING_LABEL.get(self.encoding, self.encoding)
        )


# --------------------------------------------------------------- 헤더 파싱


def _read_chunks(fh, total: int) -> Iterator[Tuple[bytes, int, int]]:
    """RIFF 청크를 (id, 데이터오프셋, 크기) 로 훑습니다."""
    pos = 12
    seen = 0
    while pos + 8 <= total:
        seen += 1
        if seen > MAX_CHUNKS:
            return
        fh.seek(pos)
        head = fh.read(8)
        if len(head) < 8:
            return
        cid = head[0:4]
        size = struct.unpack("<I", head[4:8])[0]
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)  # 워드 정렬 패딩


def probe(path: str) -> WavInfo:
    """WAV 헤더를 읽어 `WavInfo` 를 만듭니다. 실패하면 `WavError`."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise WavError("파일을 열 수 없음: {}".format(exc.strerror or exc)) from exc
    if size < 44:
        raise WavError("파일이 너무 작아 WAV 헤더가 성립하지 않음 ({} 바이트)".format(size))
    with open(path, "rb") as fh:
        riff = fh.read(12)
        if riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
            if riff[0:4] == b"RIFX":
                raise WavError("빅엔디언 RIFX 는 지원하지 않음")
            raise WavError("RIFF/WAVE 헤더가 아님 (WAV 파일이 맞습니까?)")
        fmt = None
        data = None
        for cid, off, csize in _read_chunks(fh, size):
            if cid == b"fmt " and fmt is None:
                fh.seek(off)
                fmt = fh.read(min(csize, 40))
            elif cid == b"data" and data is None:
                data = (off, csize)
            if fmt is not None and data is not None:
                break
        if fmt is None:
            raise WavError("fmt 청크가 없음")
        if data is None:
            raise WavError("data 청크가 없음")
        if len(fmt) < 16:
            raise WavError("fmt 청크가 짧음 ({} 바이트)".format(len(fmt)))
        tag, nch, rate, _brate, _align, bits = struct.unpack("<HHIIHH", fmt[:16])
        if tag == WAVE_FORMAT_EXTENSIBLE:
            if len(fmt) >= 40:
                tag = struct.unpack("<H", fmt[24:26])[0]
            else:
                raise WavError("WAVE_FORMAT_EXTENSIBLE 인데 SubFormat GUID 가 없음")
        if tag == WAVE_FORMAT_PCM:
            encoding = "pcm"
        elif tag == WAVE_FORMAT_IEEE_FLOAT:
            encoding = "float"
        else:
            raise WavError(
                "압축 코덱(wFormatTag={}) — 이 툴은 비압축 PCM/float 만 직접 읽습니다".format(tag)
            )
        if nch < 1:
            raise WavError("채널 수가 0")
        if nch > MAX_CHANNELS:
            # 4 MB 짜리 파일이 채널 수 65535 를 주장하면 스펙트럼 누적기만으로
            # 2.6 GB 를 먹습니다. 실험 자극에 64채널을 넘길 이유가 없습니다.
            raise WavError(
                "채널 수가 {}개입니다 — 이 툴은 {}채널까지만 읽습니다 "
                "(손상된 헤더일 가능성이 높습니다)".format(nch, MAX_CHANNELS))
        if rate <= 0:
            raise WavError("샘플레이트가 0")
        if bits not in (8, 16, 24, 32, 64):
            raise WavError("지원하지 않는 비트depth: {}".format(bits))
        if encoding == "float" and bits not in (32, 64):
            raise WavError("float 인코딩인데 비트depth 가 {}".format(bits))
        if encoding == "pcm" and bits == 64:
            raise WavError("64비트 정수 PCM 은 지원하지 않음")
        frame_bytes = nch * (bits // 8)
        off, csize = data
        # data 청크 크기가 파일 밖을 가리키면(잘린 녹음) 실제 남은 바이트로 자릅니다.
        avail = max(0, size - off)
        usable = min(csize, avail)
        truncated = usable < csize
        n_frames = usable // frame_bytes
        if n_frames == 0:
            raise WavError("오디오 프레임이 0개 (data 청크가 비었음)")
        note = "data 청크가 잘려 있어 {} 프레임만 읽음".format(n_frames) if truncated else ""
        return WavInfo(
            path=path,
            n_channels=nch,
            sample_rate=rate,
            bits=bits,
            encoding=encoding,
            n_frames=n_frames,
            data_offset=off,
            data_size=n_frames * frame_bytes,
            source_note=note,
        )


# --------------------------------------------------------------- 샘플 변환


def _decode_frames(raw: bytes, info: WavInfo) -> array:
    """인터리브된 바이트열 → [-1, 1] 스케일의 float array (인터리브 유지)."""
    bits, enc = info.bits, info.encoding
    if enc == "float":
        typecode = "f" if bits == 32 else "d"
        arr = array(typecode)
        arr.frombytes(raw)
        if not _LITTLE:
            arr.byteswap()
        return arr if typecode == "d" else array("d", arr)
    if bits == 8:
        # WAV 8비트는 부호 없는 오프셋 바이너리(0..255, 중앙 128)
        src = array("B")
        src.frombytes(raw)
        return array("d", [(v - 128) / 128.0 for v in src])
    if bits == 16:
        src = array("h")
        src.frombytes(raw)
        if not _LITTLE:
            src.byteswap()
        return array("d", [v / 32768.0 for v in src])
    if bits == 24:
        n = len(raw) // 3
        padded = bytearray(n * 4)
        # 3바이트를 상위 3바이트에 넣고 8비트 산술 우시프트 → 부호확장.
        padded[1::4] = raw[0::3]
        padded[2::4] = raw[1::3]
        padded[3::4] = raw[2::3]
        src = array("i")
        src.frombytes(bytes(padded))
        if not _LITTLE:
            src.byteswap()
        return array("d", [(v >> 8) / 8388608.0 for v in src])
    if bits == 32:
        src = array("i")
        src.frombytes(raw)
        if not _LITTLE:
            src.byteswap()
        return array("d", [v / 2147483648.0 for v in src])
    raise WavError("지원하지 않는 비트depth: {}".format(bits))


def iter_blocks(info: WavInfo, block_frames: int = 65536) -> Iterator[List[List[float]]]:
    """채널별 float 리스트의 블록을 순서대로 내놓습니다.

    반환 형태는 `[[ch0 샘플...], [ch1 샘플...], ...]` 이며 마지막 블록은 짧을 수
    있습니다. 정수 PCM 은 표준 `wave` 모듈로, float 는 data 청크에서 직접 읽습니다.
    """
    nch = info.n_channels
    if info.encoding == "pcm":
        yield from _iter_pcm(info, block_frames, nch)
    else:
        yield from _iter_float(info, block_frames, nch)


def _split_channels(interleaved: array, nch: int) -> List[List[float]]:
    if nch == 1:
        return [list(interleaved)]
    return [list(interleaved[c::nch]) for c in range(nch)]


def _iter_pcm(info: WavInfo, block_frames: int, nch: int) -> Iterator[List[List[float]]]:
    try:
        wf = wave.open(info.path, "rb")
    except Exception as exc:  # noqa: BLE001 — 사유를 그대로 자백에 싣습니다
        raise WavError("wave 모듈이 열지 못함: {}".format(exc)) from exc
    with wf:
        if wf.getnchannels() != nch or wf.getframerate() != info.sample_rate:
            raise WavError("헤더와 wave 모듈의 해석이 불일치 (손상된 파일)")
        remaining = info.n_frames
        while remaining > 0:
            want = min(block_frames, remaining)
            raw = wf.readframes(want)
            if not raw:
                break
            got = len(raw) // (nch * (info.bits // 8))
            if got == 0:
                break
            remaining -= got
            yield _split_channels(_decode_frames(raw, info), nch)


def _iter_float(info: WavInfo, block_frames: int, nch: int) -> Iterator[List[List[float]]]:
    frame_bytes = nch * (info.bits // 8)
    with open(info.path, "rb") as fh:
        fh.seek(info.data_offset)
        remaining = info.data_size
        while remaining > 0:
            want = min(block_frames * frame_bytes, remaining)
            raw = fh.read(want)
            if not raw:
                break
            usable = (len(raw) // frame_bytes) * frame_bytes
            if usable == 0:
                break
            remaining -= usable
            yield _split_channels(_decode_frames(raw[:usable], info), nch)


def read_all(info: WavInfo) -> List[List[float]]:
    """작은 파일 전용 — 테스트와 짧은 예제에서만 씁니다(전체를 메모리에 올림)."""
    chans: Optional[List[List[float]]] = None
    for block in iter_blocks(info):
        if chans is None:
            chans = [list(c) for c in block]
        else:
            for i, c in enumerate(block):
                chans[i].extend(c)
    return chans or [[] for _ in range(info.n_channels)]


def write_wav(path: str, channels: Sequence[Sequence[float]], sample_rate: int, bits: int = 16) -> None:
    """예제 자산 생성 전용 WAV 작성기 (`_make_examples.py` 와 테스트에서만 사용).

    분석 경로는 파일을 절대 쓰지 않습니다 — 이 함수는 합성 예제를 만들기 위한
    것이며, CLI 에서 호출되지 않습니다.
    """
    if bits not in (8, 16, 24, 32):
        raise ValueError("write_wav: 지원 비트depth 는 8/16/24/32")
    nch = len(channels)
    if nch == 0:
        raise ValueError("write_wav: 채널이 없습니다")
    n = len(channels[0])
    peak_int = {8: 127, 16: 32767, 24: 8388607, 32: 2147483647}[bits]
    lo = -peak_int - 1
    out = bytearray()
    for i in range(n):
        for c in range(nch):
            v = channels[c][i]
            iv = int(round(v * (peak_int + 1)))
            iv = lo if iv < lo else (peak_int if iv > peak_int else iv)
            if bits == 8:
                out.append((iv + 128) & 0xFF)
            elif bits == 16:
                out += struct.pack("<h", iv)
            elif bits == 24:
                out += struct.pack("<i", iv)[0:3]
            else:
                out += struct.pack("<i", iv)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(nch)
        wf.setsampwidth(bits // 8)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(out))
