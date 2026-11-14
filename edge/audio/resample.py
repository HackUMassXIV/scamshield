"""Resampling helpers.

`Upsampler2x` is a *streaming* 8 kHz -> 16 kHz converter. Calling scipy's
resample_poly on every 20 ms frame would put filter transients at each frame
boundary, so we keep FIR state across calls instead.
"""
from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly


class Upsampler2x:
    def __init__(self, numtaps: int = 33):
        # zero-stuff by 2, low-pass at the source Nyquist (4 kHz of 16 kHz), gain 2
        self._h = firwin(numtaps, 0.5).astype(np.float64) * 2.0
        self._zi = np.zeros(numtaps - 1)

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        up = np.zeros(x.size * 2, dtype=np.float64)
        up[::2] = x
        y, self._zi = lfilter(self._h, [1.0], up, zi=self._zi)
        return y.astype(np.float32)


def resample_offline(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Whole-array resample for files (not for streaming)."""
    if src_sr == dst_sr:
        return x.astype(np.float32, copy=False)
    g = gcd(src_sr, dst_sr)
    return resample_poly(x, dst_sr // g, src_sr // g).astype(np.float32)


def to_mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x
