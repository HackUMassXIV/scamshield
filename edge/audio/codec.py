"""G.711 mu-law <-> PCM16, pure numpy.

Twilio Media Streams send 8 kHz mono mu-law, base64-encoded, ~20 ms (160 bytes)
per message. We implement the codec ourselves because the stdlib `audioop`
module was removed in Python 3.13. The decoder is bit-exact with audioop.
"""
from __future__ import annotations

import numpy as np

_BIAS = 0x84
_CLIP = 32635

# Decoding table built once: all 256 mu-law codes -> int16.
_u = (~np.arange(256, dtype=np.int32)) & 0xFF
_mag = ((((_u & 0x0F) << 3) + _BIAS) << ((_u >> 4) & 0x07)) - _BIAS
_DECODE = np.where((_u & 0x80) != 0, -_mag, _mag).astype(np.int16)


def ulaw_to_pcm16(data: bytes) -> np.ndarray:
    """mu-law bytes -> int16 PCM."""
    return _DECODE[np.frombuffer(data, dtype=np.uint8)]


def ulaw_to_f32(data: bytes) -> np.ndarray:
    """mu-law bytes -> float32 in [-1, 1] (still 8 kHz)."""
    return ulaw_to_pcm16(data).astype(np.float32) / 32768.0


def pcm16_to_ulaw(pcm: np.ndarray) -> bytes:
    """int16 PCM -> mu-law bytes (used by the simulator)."""
    x = np.asarray(pcm, dtype=np.int32)
    sign = (x < 0).astype(np.int32)
    mag = np.minimum(np.abs(x), _CLIP) + _BIAS
    exp = np.clip(np.floor(np.log2(mag)).astype(np.int32) - 7, 0, 7)
    mant = (mag >> (exp + 3)) & 0x0F
    u = (~((sign << 7) | (exp << 4) | mant)) & 0xFF
    return u.astype(np.uint8).tobytes()


def f32_to_pcm16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
