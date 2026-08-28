"""비WAV 파일의 선택적 디코드 통로 — **분석은 한 줄도 맡기지 않습니다.**

`ffmpeg` 가 PATH 에 있으면 MP3·M4A 등을 임시 WAV 로 풀어 읽습니다. 없으면
그 파일을 **읽지 못한 것으로 세고 종료코드 3** 으로 갑니다("다 못 들었으면
'치명 0건'은 거짓말이다"). ffmpeg 는 오직 컨테이너를 여는 데만 쓰이고,
레벨·위생·주장 대조는 전부 이 패키지의 자체 구현이 합니다.

임시파일은 **예외 경로를 포함해** 반드시 지웁니다(`contextlib` 대신 try/finally).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional

#: WAV 이외에 디코드를 시도하는 확장자.
DECODABLE_EXT = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff", ".wma")
#: ffmpeg 호출 제한시간(초). 걸리면 그 파일은 '못 읽음'으로 셉니다.
DECODE_TIMEOUT = 300


class DecodeError(Exception):
    """디코드 실패 — 사유가 그대로 커버리지 자백에 실립니다."""


def _tidy(stderr: bytes, path: str = "") -> str:
    """ffmpeg 의 영문 메시지를 한 줄로 줄이고 메모리 주소를 지웁니다.

    가공하지 않으면 한국어 리포트에 `[out#0/wav @ 0xac6834180] Nothing was
    written…` 같은 줄이 그대로 실립니다 — 주소는 실행할 때마다 달라져
    리포트를 비교할 수도 없습니다.
    """
    text = stderr.decode("utf-8", "replace").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return "사유 불명"
    line = re.sub(r"\[[^\]]*@\s*0x[0-9a-fA-F]+\]\s*", "", lines[-1]).strip()
    if path:
        # ffmpeg 판본에 따라 오류 문구에 **절대경로**가 통째로 들어갑니다.
        # 리포트에는 basename 만 남겨야 홈 디렉터리 이름이 새지 않습니다.
        folder = os.path.dirname(os.path.abspath(path))
        line = line.replace(os.path.abspath(path), os.path.basename(path))
        if folder:
            line = line.replace(folder + os.sep, "")
    return (line[:117] + "…") if len(line) > 120 else (line or "사유 불명")


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def needs_decode(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in DECODABLE_EXT


class TempDecoder:
    """임시 폴더 하나를 잡고, 끝나면 통째로 지웁니다."""

    def __init__(self) -> None:
        self._dir: Optional[str] = None
        self._seq = 0
        self.produced: List[str] = []

    def __enter__(self) -> "TempDecoder":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        if self._dir and os.path.isdir(self._dir):
            shutil.rmtree(self._dir, ignore_errors=True)
        self._dir = None
        self.produced = []
        self._seq = 0

    def decode(self, path: str) -> str:
        """`path` 를 WAV 로 풀어 임시 경로를 돌려줍니다."""
        exe = ffmpeg_path()
        if exe is None:
            raise DecodeError(
                "ffmpeg 가 PATH 에 없어 이 형식을 열 수 없습니다 "
                "(WAV 로 변환해 다시 시도하십시오)")
        if self._dir is None:
            self._dir = tempfile.mkdtemp(prefix="stimaudit_decode_")
        # **성공한 개수가 아니라 시도한 개수**로 이름을 붙입니다. 실패한 디코드가
        # 부분 파일을 남기면 다음 파일이 같은 이름을 재사용하게 되고, ffmpeg 는
        # 덮어쓰기를 거부하면서도 종료코드 0 을 내므로 **앞 파일의 오디오가 뒤
        # 파일의 지표로 보고**됩니다. `-y` 도 같은 이유로 반드시 필요합니다.
        self._seq += 1
        out = os.path.join(self._dir, "{:04d}.wav".format(self._seq))
        # 절대경로로 넘겨 ffmpeg 의 프로토콜 해석(`concat:` 등)을 막고,
        # 파일 프로토콜만 허용합니다 — `concat:a.mp3` 라는 이름의 파일이
        # 그 이름의 파일이 아니라 concat 프로토콜로 열리던 문제.
        cmd = [exe, "-v", "error", "-nostdin", "-y",
               "-protocol_whitelist", "file",
               "-i", os.path.abspath(path),
               "-c:a", "pcm_s24le", "-f", "wav", out]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=DECODE_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise DecodeError("ffmpeg 디코드가 {}초를 넘겨 중단했습니다".format(DECODE_TIMEOUT)) from None
        except OSError as exc:
            raise DecodeError("ffmpeg 를 실행할 수 없습니다: {}".format(exc)) from exc
        if proc.returncode != 0 or not os.path.exists(out):
            raise DecodeError("ffmpeg 로 디코드하지 못했습니다 ({})".format(
                _tidy(proc.stderr, path)))
        self.produced.append(out)
        return out
