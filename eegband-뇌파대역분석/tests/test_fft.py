"""FFT correctness: validate the radix-2 FFT against a naive O(n²) DFT (and numpy).

Written as unittest.TestCase so the suite runs under BOTH `pytest` and
`python3 -m unittest`. numpy is used only for an extra cross-check, behind a guarded
import that skips when numpy is absent — the stdlib DFT oracle is the primary check.
"""

import cmath
import math
import random
import unittest

from eegband.fft import dft_naive, fft, hann_window, next_pow2, pad_to_pow2

try:
    import numpy as _np
    HAVE_NUMPY = True
except ImportError:  # pragma: no cover
    HAVE_NUMPY = False


def _max_abs_diff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


class TestNextPow2(unittest.TestCase):
    def test_values(self):
        self.assertEqual(next_pow2(1), 1)
        self.assertEqual(next_pow2(2), 2)
        self.assertEqual(next_pow2(3), 4)
        self.assertEqual(next_pow2(5), 8)
        self.assertEqual(next_pow2(512), 512)
        self.assertEqual(next_pow2(513), 1024)
        self.assertEqual(next_pow2(0), 1)


class TestFFTvsDFT(unittest.TestCase):
    def test_random_complex_vector_1e9(self):
        rng = random.Random(12345)
        for n in (2, 4, 8, 16, 256, 512):
            vec = [complex(rng.uniform(-1, 1), rng.uniform(-1, 1)) for _ in range(n)]
            fast = fft(vec)
            slow = dft_naive(vec)
            self.assertLess(_max_abs_diff(fast, slow), 1e-9,
                            f"FFT vs DFT mismatch at n={n}")

    def test_random_real_vector_1e9(self):
        rng = random.Random(999)
        n = 1024
        vec = [rng.gauss(0, 1) for _ in range(n)]
        fast = fft([complex(v, 0.0) for v in vec])
        slow = dft_naive([complex(v, 0.0) for v in vec])
        self.assertLess(_max_abs_diff(fast, slow), 1e-9)

    def test_known_dc_and_impulse(self):
        # constant signal -> all energy in bin 0
        n = 8
        const = [complex(2.0, 0.0)] * n
        spec = fft(const)
        self.assertAlmostEqual(spec[0].real, 2.0 * n, places=9)
        for k in range(1, n):
            self.assertLess(abs(spec[k]), 1e-9)
        # unit impulse -> flat spectrum of magnitude 1
        imp = [complex(0.0, 0.0)] * n
        imp[0] = complex(1.0, 0.0)
        spec = fft(imp)
        for k in range(n):
            self.assertAlmostEqual(abs(spec[k]), 1.0, places=9)

    def test_non_power_of_two_rejected(self):
        with self.assertRaises(ValueError):
            fft([1 + 0j, 2 + 0j, 3 + 0j])
        with self.assertRaises(ValueError):
            fft([])

    def test_pad_to_pow2(self):
        padded = pad_to_pow2([1.0, 2.0, 3.0])
        self.assertEqual(len(padded), 4)
        self.assertEqual(padded[3], 0j)


class TestHannWindow(unittest.TestCase):
    def test_periodic_endpoints(self):
        w = hann_window(8, periodic=True)
        self.assertAlmostEqual(w[0], 0.0, places=12)
        self.assertEqual(len(w), 8)
        # periodic Hann is not symmetric to 0 at the last sample
        self.assertGreater(w[-1], 0.0)

    def test_symmetric_endpoints(self):
        w = hann_window(9, periodic=False)
        self.assertAlmostEqual(w[0], 0.0, places=12)
        self.assertAlmostEqual(w[-1], 0.0, places=12)

    def test_single(self):
        self.assertEqual(hann_window(1), [1.0])


@unittest.skipUnless(HAVE_NUMPY, "numpy not available")
class TestFFTvsNumpy(unittest.TestCase):
    def test_matches_numpy(self):
        rng = random.Random(7)
        n = 512
        vec = [rng.gauss(0, 1) for _ in range(n)]
        mine = fft([complex(v, 0.0) for v in vec])
        ref = _np.fft.fft(_np.array(vec))
        diff = max(abs(mine[k] - ref[k]) for k in range(n))
        self.assertLess(diff, 1e-9)

    def test_periodic_hann_matches_numpy(self):
        # numpy.hanning is the *symmetric* window; our periodic matches
        # np.fft.helper style w[k]=0.5-0.5cos(2πk/N)
        n = 16
        mine = hann_window(n, periodic=True)
        ref = [0.5 - 0.5 * math.cos(2 * math.pi * k / n) for k in range(n)]
        self.assertLess(_max_abs_diff([complex(v) for v in mine],
                                      [complex(v) for v in ref]), 1e-12)


if __name__ == "__main__":
    unittest.main()
