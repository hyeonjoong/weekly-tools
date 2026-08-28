"""테스트 공용 합성기 — 전부 오프라인, 전부 결정론적.

실제 회사 오디오는 테스트에 쓰지 않습니다. 필요한 신호는 여기서 계산으로 만들고,
기대값은 손으로 계산할 수 있는 것만 씁니다(사인파 RMS, 클리핑 샘플 수 등).
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stimaudit import analyze, wavread  # noqa: E402

FS = 44100


class LCG:
    """이식 가능한 결정론적 난수(테스트 재현성)."""

    def __init__(self, seed: int = 12345) -> None:
        self.s = seed & 0xFFFFFFFF

    def uniform(self) -> float:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return (self.s / 0x3FFFFFFF) - 1.0


def sine(freq: float, seconds: float, amp: float = 0.5, fs: int = FS) -> list:
    n = int(fs * seconds)
    return [amp * math.sin(2.0 * math.pi * freq * i / fs) for i in range(n)]


def sine_rms(freq: float, seconds: float, rms_dbfs: float, fs: int = FS) -> list:
    """RMS 가 정확히 `rms_dbfs` 인 사인파 (진폭 = √2 · 10^(dB/20))."""
    return sine(freq, seconds, math.sqrt(2.0) * 10.0 ** (rms_dbfs / 20.0), fs)


def noise(seconds: float, amp: float = 0.2, fs: int = FS, seed: int = 7) -> list:
    rng = LCG(seed)
    x = [amp * rng.uniform() for _ in range(int(fs * seconds))]
    mean = sum(x) / len(x)
    return [v - mean for v in x]


def fade(x: list, fs: int = FS, ms: float = 100.0) -> list:
    k = min(max(1, int(fs * ms / 1000.0)), len(x) // 2)
    y = list(x)
    for i in range(k):
        w = 0.5 - 0.5 * math.cos(math.pi * i / k)
        y[i] *= w
        y[-1 - i] *= w
    return y


def write(tmp_path, name: str, channels, fs: int = FS, bits: int = 16) -> str:
    path = os.path.join(str(tmp_path), name)
    wavread.write_wav(path, channels, fs, bits)
    return path


def metrics_of(tmp_path, name: str, channels, fs: int = FS, bits: int = 16):
    return analyze.analyze_file(wavread.probe(write(tmp_path, name, channels, fs, bits)))


@pytest.fixture
def mk(tmp_path):
    """`mk("a.wav", [signal])` → 경로."""
    def _mk(name, channels, fs=FS, bits=16):
        return write(tmp_path, name, channels, fs, bits)
    return _mk


@pytest.fixture
def analyzed(tmp_path):
    """`analyzed("a.wav", [signal])` → FileMetrics."""
    def _an(name, channels, fs=FS, bits=16):
        return metrics_of(tmp_path, name, channels, fs, bits)
    return _an


@pytest.fixture(scope="session")
def examples_dir():
    """번들 예제 폴더. 없으면 **조용히 건너뛰지 않고 만들어서** 검사합니다.

    전에는 skip 이었는데, 그러면 `--manifest`·`--baseline`·교란표 배선의 유일한
    커버리지가 아무 말 없이 사라집니다(라운드 1 테스트품질 검토 지적).
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(root, "examples")
    if not os.path.isdir(os.path.join(d, "맞은세트")):
        import subprocess
        subprocess.run([sys.executable, os.path.join(root, "_make_examples.py")],
                       check=True, cwd=root)
    assert os.path.isdir(os.path.join(d, "맞은세트")), "examples/ 를 만들지 못했습니다"
    return d
