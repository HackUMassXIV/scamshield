"""Speech-to-text: faster-whisper backend, a scripted fake for tests, and a
single-thread async wrapper so one shared model serves every call safely."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

from .config import Settings

log = logging.getLogger("guardian.asr")


@dataclass
class ASRResult:
    text: str
    ms: float


class ASRBackend(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray) -> str: ...


# ----------------------------------------------------------------- filtering
_HALLUCINATIONS = {
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amaraorg community",
    "you",
    "bye",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


def clean_text(text: str, prompt: Optional[str] = None) -> str:
    """Drop typical Whisper hallucinations (YouTube outros, prompt echo, tags,
    runaway repetition). Returns "" when the whole utterance should be dropped."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return ""
    if re.fullmatch(r"[\[\(].*[\]\)]", t) or "♪" in t:  # [MUSIC], (silence), ...
        return ""
    n = _norm(t)
    if n in _HALLUCINATIONS:
        return ""
    if prompt and n and n == _norm(prompt):
        return ""
    # collapse "the the the the the ..." style loops
    t = re.sub(r"\b(\w+)(?:[ ,.]+\1\b){4,}", r"\1", t, flags=re.IGNORECASE)
    return t


# ------------------------------------------------------------------ backends
class FasterWhisperASR:
    name = "faster-whisper"

    def __init__(self, s: Settings):
        from faster_whisper import WhisperModel  # lazy: heavy import

        device = s.whisper_device
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        compute = s.whisper_compute
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        log.info("Loading faster-whisper %s on %s (%s)", s.whisper_model, device, compute)
        try:  # cached weights first: no network at startup, so the offline demo works
            self.model = WhisperModel(s.whisper_model, device=device, compute_type=compute, local_files_only=True)
        except Exception:
            log.info("Whisper %s not cached yet; downloading", s.whisper_model)
            self.model = WhisperModel(s.whisper_model, device=device, compute_type=compute)
        self.device, self.compute = device, compute
        self._prompt = s.whisper_prompt or None
        self._beam = s.whisper_beam
        self._lang = s.asr_language
        self._vad_filter = s.whisper_vad_filter

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self.model.transcribe(
            audio.astype(np.float32, copy=False),
            language=self._lang,
            beam_size=self._beam,
            temperature=0.0,
            condition_on_previous_text=False,  # avoids runaway loops
            initial_prompt=self._prompt,  # biases toward scam vocabulary
            vad_filter=self._vad_filter,
            without_timestamps=True,
            no_speech_threshold=0.6,
        )
        parts = []
        for seg in segments:
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                continue
            parts.append(seg.text.strip())
        return " ".join(p for p in parts if p)


class FakeASR:
    """Scripted ASR for tests and dry runs: returns the next line per utterance.
    Near-silent audio (e.g. the warmup decode) returns "" and consumes nothing."""

    name = "fake"

    def __init__(self, lines: tuple[str, ...] | list[str] = (), cycle: bool = True):
        self.lines = list(lines)
        self.cycle = cycle
        self._i = 0

    def transcribe(self, audio: np.ndarray) -> str:
        if not self.lines or float(np.max(np.abs(audio), initial=0.0)) < 1e-3:
            return ""
        if self._i >= len(self.lines):
            if not self.cycle:
                return ""
            self._i = 0
        line = self.lines[self._i]
        self._i += 1
        return line


def make_asr(s: Settings) -> ASRBackend:
    if s.asr_backend == "fake":
        return FakeASR(s.fake_asr_lines)
    return FasterWhisperASR(s)


# ------------------------------------------------------------------- service
class ASRService:
    """One worker thread: transcriptions are serialized and the event loop never blocks."""

    def __init__(self, backend: ASRBackend, prompt: Optional[str] = None):
        self.backend = backend
        self.prompt = prompt
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
        self.recent_ms: deque[float] = deque(maxlen=200)

    @property
    def name(self) -> str:
        return self.backend.name

    def _run(self, audio: np.ndarray) -> str:
        return clean_text(self.backend.transcribe(audio), self.prompt)

    async def transcribe(self, audio: np.ndarray) -> ASRResult:
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        text = await loop.run_in_executor(self._pool, self._run, audio)
        ms = (time.perf_counter() - t0) * 1000
        self.recent_ms.append(ms)
        return ASRResult(text=text, ms=ms)

    async def warmup(self) -> None:
        """One silent decode so the first real utterance isn't slow."""
        await self.transcribe(np.zeros(16000, dtype=np.float32))

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
