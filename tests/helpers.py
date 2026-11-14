"""Synthetic 'speech-like' audio for pipeline tests (no models needed)."""
import numpy as np

from edge.audio.codec import f32_to_pcm16, pcm16_to_ulaw


def burst(seconds: float, sr: int = 16000, amp: float = 0.25, seed: int = 0) -> np.ndarray:
    """Noise with a syllable-rate envelope: the energy VAD sees it as speech."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)
    return (amp * env * rng.standard_normal(n)).astype(np.float32)


def silence(seconds: float, sr: int = 16000, amp: float = 0.0005, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (amp * rng.standard_normal(int(seconds * sr))).astype(np.float32)


def timeline(parts: list[tuple[str, float]], sr: int = 16000) -> np.ndarray:
    out = []
    for i, (kind, sec) in enumerate(parts):
        out.append(burst(sec, sr, seed=i) if kind == "speech" else silence(sec, sr, seed=i))
    return np.concatenate(out)


def to_mulaw_frames(x8k: np.ndarray, frame_ms: int = 20) -> list[bytes]:
    """8 kHz float -> mu-law 20 ms frames, like Twilio sends."""
    pcm = f32_to_pcm16(x8k)
    n = 8000 * frame_ms // 1000
    return [pcm16_to_ulaw(pcm[i : i + n]) for i in range(0, len(pcm), n)]
