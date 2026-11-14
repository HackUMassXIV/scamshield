"""Voice activity detection + utterance segmentation.

`Segmenter` consumes a continuous 16 kHz float32 stream (one per speaker) and
emits complete utterances. It closes one after `silence_ms` of silence or when
`max_utt_s` is reached, cutting at the quietest point of the last second so
words are not split mid-syllable.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import numpy as np

log = logging.getLogger("guardian.vad")

SR = 16000
FRAME = 512  # 32 ms, the frame size Silero VAD expects at 16 kHz
FRAME_MS = FRAME * 1000 / SR


class VAD(Protocol):
    name: str

    def prob(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


class EnergyVAD:
    """Adaptive RMS gate. Dependency-free fallback (also used in tests)."""

    name = "energy"

    def __init__(self, abs_floor: float = 0.004, ratio: float = 3.0):
        self.abs_floor = abs_floor
        self.ratio = ratio
        self._noise: Optional[float] = None

    def prob(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))) + 1e-9)
        if self._noise is None:
            self._noise = rms
        # minimum-statistics noise floor: falls fast, rises slowly
        if rms < self._noise:
            self._noise = 0.9 * self._noise + 0.1 * rms
        else:
            self._noise = min(rms, self._noise * 1.002 + 1e-6)
        return 1.0 if rms > max(self.abs_floor, self._noise * self.ratio) else 0.0

    def reset(self) -> None:
        self._noise = None


class SileroVAD:
    name = "silero"

    def __init__(self):
        import torch  # lazy: heavy import
        from silero_vad import load_silero_vad

        torch.set_num_threads(1)  # don't fight Whisper for cores
        self._torch = torch
        self._model = load_silero_vad()

    def prob(self, frame: np.ndarray) -> float:
        with self._torch.no_grad():
            return float(self._model(self._torch.from_numpy(frame), SR).item())

    def reset(self) -> None:
        self._model.reset_states()


def vad_factory(kind: str = "auto") -> Callable[[], VAD]:
    """Returns a zero-arg callable producing a fresh VAD (they are stateful).
    In "auto" mode a Silero failure is logged once and we stay on energy VAD."""
    state = {"kind": (kind or "auto").lower()}

    def make() -> VAD:
        if state["kind"] == "energy":
            return EnergyVAD()
        try:
            return SileroVAD()
        except Exception as exc:  # ImportError, model download failure, ...
            if state["kind"] == "silero":
                raise
            log.warning("Silero VAD unavailable (%s); using energy VAD fallback", exc)
            state["kind"] = "energy"
            return EnergyVAD()

    return make


@dataclass
class Utterance:
    audio: np.ndarray  # float32, 16 kHz
    start_s: float
    end_s: float
    speaker: str = "mixed"
    forced: bool = False  # cut because it hit max length, not because of a pause


class Segmenter:
    def __init__(
        self,
        speaker: str,
        vad: VAD,
        *,
        threshold: float = 0.5,
        silence_ms: int = 400,
        min_speech_ms: int = 250,
        max_utt_s: float = 6.0,
        preroll_ms: int = 200,
        start_frames: int = 2,
    ):
        self.speaker = speaker
        self.vad = vad
        self.threshold = threshold
        self.end_threshold = max(0.05, threshold - 0.15)  # hysteresis
        self.silence_frames = max(1, round(silence_ms / FRAME_MS))
        self.min_samples = int(min_speech_ms * SR / 1000)
        self.max_samples = int(max_utt_s * SR)
        self.start_frames = start_frames
        self._preroll: deque[np.ndarray] = deque(maxlen=max(0, round(preroll_ms / FRAME_MS)))
        self._pending = np.zeros(0, dtype=np.float32)
        self._cand: list[np.ndarray] = []
        self._buf: list[np.ndarray] = []
        self._buf_len = 0
        self._buf_start = 0
        self._in_speech = False
        self._silence_run = 0
        self._speech_frames = 0  # frames actually classified as speech
        self._pos = 0  # samples consumed so far (stream clock)

    # ------------------------------------------------------------------ API
    def push(self, x: np.ndarray) -> list[Utterance]:
        out: list[Utterance] = []
        if x.size:
            self._pending = np.concatenate([self._pending, x.astype(np.float32, copy=False)])
        n = self._pending.size // FRAME
        if n:
            frames = self._pending[: n * FRAME].reshape(n, FRAME)
            self._pending = self._pending[n * FRAME :].copy()
            for frame in frames:
                self._frame(frame, out)
        return out

    def flush(self) -> list[Utterance]:
        out: list[Utterance] = []
        if self._pending.size:
            pad = np.zeros(FRAME - self._pending.size, dtype=np.float32)
            frame = np.concatenate([self._pending, pad])
            self._pending = np.zeros(0, dtype=np.float32)
            self._frame(frame, out)
        if self._in_speech and self._buf:
            self._emit(out, trailing_silence=self._silence_run, forced=False)
        self._reset_speech()
        self._cand.clear()
        self._preroll.clear()
        return out

    # ------------------------------------------------------------- internals
    def _frame(self, frame: np.ndarray, out: list[Utterance]) -> None:
        start = self._pos
        self._pos += FRAME
        p = self.vad.prob(frame)

        if not self._in_speech:
            if p >= self.threshold:
                self._cand.append(frame)
                if len(self._cand) >= self.start_frames:
                    pre = list(self._preroll)
                    first_cand = start - (len(self._cand) - 1) * FRAME
                    self._buf = pre + self._cand
                    self._buf_len = sum(a.size for a in self._buf)
                    self._buf_start = first_cand - len(pre) * FRAME
                    self._speech_frames = len(self._cand)
                    self._cand = []
                    self._preroll.clear()
                    self._in_speech = True
                    self._silence_run = 0
            else:
                if self._cand:  # false start: treat as background
                    self._preroll.extend(self._cand)
                    self._cand = []
                self._preroll.append(frame)
            return

        self._buf.append(frame)
        self._buf_len += frame.size
        if p >= self.end_threshold:
            self._silence_run = 0
            self._speech_frames += 1
        else:
            self._silence_run += 1

        if self._silence_run >= self.silence_frames:
            self._emit(out, trailing_silence=self._silence_run, forced=False)
            self._reset_speech()
        elif self._buf_len >= self.max_samples:
            self._force_cut(out)

    def _emit(self, out: list[Utterance], trailing_silence: int, forced: bool) -> None:
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        trim = max(0, trailing_silence - 3) * FRAME  # keep ~100 ms of tail
        if trim and trim < audio.size:
            audio = audio[: audio.size - trim]
        # gate on frames that were really speech, so preroll + tail can't
        # turn a 100 ms click into an "utterance"
        if audio.size and self._speech_frames * FRAME >= self.min_samples:
            out.append(
                Utterance(
                    audio=audio,
                    start_s=self._buf_start / SR,
                    end_s=(self._buf_start + audio.size) / SR,
                    speaker=self.speaker,
                    forced=forced,
                )
            )

    def _force_cut(self, out: list[Utterance]) -> None:
        audio = np.concatenate(self._buf)
        tail_n = min(audio.size, SR)  # look at the last second
        tail = audio[audio.size - tail_n :]
        nfr = tail.size // FRAME
        if nfr >= 2:
            energy = np.mean(np.square(tail[: nfr * FRAME].reshape(nfr, FRAME)), axis=1)
            cut = audio.size - tail_n + int(np.argmin(energy)) * FRAME + FRAME // 2
        else:
            cut = audio.size
        head, rest = audio[:cut], audio[cut:]
        if head.size >= self.min_samples:
            out.append(
                Utterance(
                    audio=head,
                    start_s=self._buf_start / SR,
                    end_s=(self._buf_start + head.size) / SR,
                    speaker=self.speaker,
                    forced=True,
                )
            )
        self._buf_start += cut
        self._buf = [rest] if rest.size else []
        self._buf_len = rest.size
        self._silence_run = 0
        self._speech_frames = rest.size // FRAME

    def _reset_speech(self) -> None:
        self._buf = []
        self._buf_len = 0
        self._in_speech = False
        self._silence_run = 0
        self._speech_frames = 0
        self._cand = []
