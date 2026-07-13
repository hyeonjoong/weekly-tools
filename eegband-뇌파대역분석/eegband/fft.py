"""Radix-2 Cooley–Tukey FFT and window functions — pure standard library.

No numpy/scipy. The forward FFT is an iterative in-place radix-2 decimation-in-time
transform (length must be a power of two; use :func:`next_pow2` + zero-padding to get
there). A naive O(n²) DFT is kept as a reference oracle so the fast transform can be
verified against it in the tests.

빠른 FFT는 반드시 길이가 2의 거듭제곱이어야 합니다. 임의 길이는 :func:`next_pow2` 로
다음 2의 거듭제곱까지 0-패딩해서 넣으세요.
"""

from __future__ import annotations

import cmath
import math
from typing import List, Sequence

__all__ = ["next_pow2", "fft", "dft_naive", "hann_window", "pad_to_pow2"]


def next_pow2(n: int) -> int:
    """Smallest power of two >= n (>= 1)."""
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def pad_to_pow2(x: Sequence[float]) -> List[complex]:
    """Return x as a complex list zero-padded up to the next power-of-two length."""
    n = len(x)
    m = next_pow2(n)
    out = [complex(v, 0.0) for v in x]
    if m > n:
        out.extend([0j] * (m - n))
    return out


def fft(a: Sequence[complex]) -> List[complex]:
    """In-place iterative radix-2 Cooley–Tukey FFT.

    Parameters
    ----------
    a : sequence of complex (or real) values whose length is a power of two.

    Returns
    -------
    list of complex : the discrete Fourier transform X[k] = Σ_n a[n] e^{-2πi kn/N}.
    """
    n = len(a)
    if n == 0:
        raise ValueError("FFT of an empty sequence is undefined")
    if n & (n - 1) != 0:
        raise ValueError(f"FFT length must be a power of two, got {n} "
                         "(zero-pad with pad_to_pow2 first)")
    buf: List[complex] = [complex(v) for v in a]

    # Bit-reversal permutation.
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            buf[i], buf[j] = buf[j], buf[i]

    # Butterfly stages.
    length = 2
    while length <= n:
        wlen = cmath.exp(-2j * math.pi / length)
        half = length >> 1
        for start in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u = buf[start + k]
                v = buf[start + k + half] * w
                buf[start + k] = u + v
                buf[start + k + half] = u - v
                w *= wlen
        length <<= 1
    return buf


def dft_naive(a: Sequence[complex]) -> List[complex]:
    """Direct O(n²) DFT — reference oracle used to validate :func:`fft` in tests."""
    n = len(a)
    x = [complex(v) for v in a]
    out: List[complex] = []
    for k in range(n):
        acc = 0j
        coef = -2j * math.pi * k / n
        for m in range(n):
            acc += x[m] * cmath.exp(coef * m)
        out.append(acc)
    return out


def hann_window(n: int, periodic: bool = True) -> List[float]:
    """Hann window of length ``n``.

    periodic=True  (default) -> w[k] = 0.5 - 0.5 cos(2πk/n)   (DFT-even; matches
                   scipy.signal.get_window('hann', n) / welch's default).
    periodic=False -> w[k] = 0.5 - 0.5 cos(2πk/(n-1))         (symmetric).
    """
    if n <= 0:
        raise ValueError("window length must be positive")
    if n == 1:
        return [1.0]
    denom = n if periodic else (n - 1)
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * k / denom) for k in range(n)]
