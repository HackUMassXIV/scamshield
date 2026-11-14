"""One screened call: audio in -> VAD -> ASR -> redaction -> live events.

Data flow (all in-process, all on the edge):

  Twilio ws / mic / wav
        |  push_mulaw / push_audio        (never blocks: drops oldest if full)
        v
  _audio_q --(_audio_loop: upsample, VAD, segment)--> _utt_q
                                                        |
                                    (_asr_loop: Whisper in a worker thread)
                                                        v
                         redact -> hub.publish("transcript") -> transcript hooks

PRIVACY: raw audio lives only in these queues and is never written anywhere.
The raw (un-redacted) transcript exists only as a local variable inside
`_handle`; everything stored, logged or published is the redacted text.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from collections import Counter
from typing import TYPE_CHECKING, Optional

import numpy as np

from shared.contracts import TranscriptEvent, UserRecord

from .audio.codec import ulaw_to_f32
from .audio.resample import Upsampler2x
from .audio.vad import Segmenter, Utterance
from .redact import Redactor

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Runtime

log = logging.getLogger("guardian.session")


class CallSession:
    def __init__(self, rt: "Runtime", user: UserRecord, call_sid: Optional[str]):
        self.rt = rt
        self.user = user
        self.call_sid = call_sid
        self.id = secrets.token_urlsafe(16)  # unguessable: binds the media stream
        self.created = time.time()
        self.state = "leg_connected"  # -> screening_requested -> streaming -> ended
        self.mode = "merge"  # "merge": one mixed track; "bridge": caller + student tracks
        self.stream_sid: Optional[str] = None
        self.redactor = Redactor(rt.users.name_tokens(user))
        self.windows: list[str] = []  # redacted utterance texts, newest last
        self.transcript: list[dict] = []  # redacted events (in memory only)
        self.stats: dict = {"utterances": 0, "audio_s": 0.0, "dropped_frames": 0, "empty": 0}
        self._asr_ms: list[float] = []
        self._redactions: Counter = Counter()
        self._audio_q: asyncio.Queue = asyncio.Queue(maxsize=rt.settings.audio_queue_max)
        self._utt_q: asyncio.Queue = asyncio.Queue()
        self._up: dict[str, Upsampler2x] = {}
        self._seg: dict[str, Segmenter] = {}
        self._tasks: list[asyncio.Task] = []
        self._watchdog: Optional[asyncio.Task] = None
        self._grace: Optional[asyncio.Task] = None
        self._finishing = False
        self._done = asyncio.Event()

    # ----------------------------------------------------------- lifecycle
    async def open(self) -> None:
        """Guardian leg is up. Tell the app to prompt the student."""
        await self._emit("leg_connected", mode=self.mode)
        self._watchdog = asyncio.create_task(self._leg_watchdog())

    async def request_screening(self) -> None:
        if self.state == "leg_connected":
            self.state = "screening_requested"
            await self._emit("screening_requested")

    def accepts_stream(self, call_sid: Optional[str]) -> bool:
        """A media stream may only attach after the disclosure was requested,
        and (when Twilio tells us) only from the same call."""
        if self.state not in {"screening_requested", "streaming"}:
            return False
        return not self.call_sid or not call_sid or call_sid == self.call_sid

    async def bind_stream(self, stream_sid: Optional[str], tracks: list[str]) -> None:
        self.stream_sid = stream_sid
        self.mode = "bridge" if len({t for t in tracks if t}) > 1 else "merge"
        self._cancel(self._grace)
        self._grace = None
        await self._begin_streaming()

    async def start_local(self) -> None:
        """Skip Twilio (mic / wav tools): go straight to streaming."""
        self.state = "screening_requested"
        await self._begin_streaming()

    async def _begin_streaming(self) -> None:
        if self.state == "streaming":
            return  # reconnect of the same stream
        self.state = "streaming"
        self._cancel(self._watchdog)
        self._tasks = [
            asyncio.create_task(self._audio_loop(), name=f"audio-{self.id[:6]}"),
            asyncio.create_task(self._asr_loop(), name=f"asr-{self.id[:6]}"),
        ]
        await self._emit("screening_started", mode=self.mode)

    async def detach_stream(self, clean: bool) -> None:
        """Twilio sent `stop` (clean) or the websocket dropped (not clean)."""
        if clean:
            await self.finish("stream_stop")
        elif not self._finishing and self._grace is None:
            self._grace = asyncio.create_task(self._grace_finish())

    async def finish(self, reason: str = "ended") -> None:
        if self._finishing:
            await self._done.wait()
            return
        self._finishing = True
        self._cancel(self._watchdog)
        self._cancel(self._grace)
        try:
            if self._tasks:
                self._sentinel()  # drain what is queued, flush the segmenters
                try:
                    await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), 30)
                except asyncio.TimeoutError:
                    for t in self._tasks:
                        t.cancel()
            self.state = "ended"
            await self._emit("call_ended", reason=reason, summary=self.summary())
            for hook in self.rt.end_hooks:
                try:
                    await hook(self)
                except Exception:
                    log.exception("end hook failed")
        finally:
            self.rt.sessions.remove(self)
            self._done.set()

    def summary(self) -> dict:
        n = len(self._asr_ms)
        return {
            "utterances": self.stats["utterances"],
            "audio_s": round(self.stats["audio_s"], 1),
            "dropped_frames": self.stats["dropped_frames"],
            "avg_asr_ms": round(sum(self._asr_ms) / n, 1) if n else None,
            "max_asr_ms": round(max(self._asr_ms), 1) if n else None,
            "redactions": dict(self._redactions),
            "duration_s": round(time.time() - self.created, 1),
        }

    # -------------------------------------------------------------- ingest
    def push_mulaw(self, track: str, payload_b64: str) -> None:
        """Twilio `media` payload: base64 8 kHz mu-law -> 16 kHz float32 -> queue."""
        speaker = self._speaker_for(track)
        x8 = ulaw_to_f32(base64.b64decode(payload_b64))
        up = self._up.setdefault(speaker, Upsampler2x())
        self.push_audio(speaker, up.process(x8))

    def push_audio(self, speaker: str, x16k: np.ndarray) -> None:
        """16 kHz float32 mono. Never blocks: if the pipeline falls behind, drop
        the oldest frame rather than stalling the websocket."""
        if self._finishing or x16k.size == 0:
            return
        try:
            self._audio_q.put_nowait((speaker, x16k))
        except asyncio.QueueFull:
            try:
                self._audio_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.stats["dropped_frames"] += 1
            self._audio_q.put_nowait((speaker, x16k))

    def _speaker_for(self, track: str) -> str:
        # merge: Guardian leg inbound = mix of student + caller.
        # bridge: inbound = the caller who rang Twilio, outbound = the student we dialed.
        if self.mode == "bridge":
            return "caller" if track == "inbound" else "student"
        return "mixed"

    def _segmenter(self, speaker: str) -> Segmenter:
        if speaker not in self._seg:
            self._seg[speaker] = self.rt.new_segmenter(speaker)
        return self._seg[speaker]

    # --------------------------------------------------------------- loops
    async def _audio_loop(self) -> None:
        while True:
            speaker, x = await self._audio_q.get()
            if x is None:
                break
            try:
                for utt in self._segmenter(speaker).push(x):
                    self._utt_q.put_nowait((utt, time.monotonic()))
            except Exception:
                log.exception("audio loop error (continuing)")
        for seg in self._seg.values():  # end of call: flush what is left
            for utt in seg.flush():
                self._utt_q.put_nowait((utt, time.monotonic()))
        self._utt_q.put_nowait(None)

    async def _asr_loop(self) -> None:
        while True:
            item = await self._utt_q.get()
            if item is None:
                break
            utt, queued_at = item
            try:
                await self._handle(utt, queued_at)
            except Exception:
                log.exception("utterance failed (continuing)")

    async def _handle(self, utt: Utterance, queued_at: float) -> None:
        queue_ms = (time.monotonic() - queued_at) * 1000
        res = await self.rt.asr.transcribe(utt.audio)
        self.stats["audio_s"] += utt.audio.size / 16000
        if not res.text:
            self.stats["empty"] += 1
            return
        t0 = time.perf_counter()
        red = self.redactor.redact(res.text)  # raw text never leaves this function
        redact_ms = (time.perf_counter() - t0) * 1000
        self.stats["utterances"] += 1
        self._asr_ms.append(res.ms)
        self._redactions.update(red.counts)
        self.windows.append(red.text)
        if self.rt.settings.log_transcripts:
            log.info("[%s/%s] %s", self.user.id, utt.speaker, red.text)
        ev = TranscriptEvent(
            call_id=self.id,
            user_id=self.user.id,
            speaker=utt.speaker,  # type: ignore[arg-type]
            text=red.text,
            redactions=red.counts,
            t_start=round(utt.start_s, 2),
            t_end=round(utt.end_s, 2),
            latency={
                "asr_ms": round(res.ms, 1),
                "queue_ms": round(queue_ms, 1),
                "redact_ms": round(redact_ms, 2),
            },
        ).model_dump(exclude={"type", "user_id", "ts"})
        self.transcript.append(ev)
        await self.rt.hub.publish(self.user.id, "transcript", **ev)
        for hook in self.rt.transcript_hooks:  # sections 7/8 plug in here
            try:
                await hook(self, ev)
            except Exception:
                log.exception("transcript hook failed")

    # ------------------------------------------------------------- helpers
    def recent_text(self, n: int = 3) -> str:
        """Last n redacted utterances: the window the classifier will read."""
        return " ".join(self.windows[-n:])

    async def _emit(self, event_type: str, **data) -> None:
        await self.rt.hub.publish(self.user.id, event_type, call_id=self.id, state=self.state, **data)

    def _sentinel(self) -> None:
        if self._audio_q.full():
            try:
                self._audio_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._audio_q.put_nowait((None, None))

    @staticmethod
    def _cancel(task: Optional[asyncio.Task]) -> None:
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _leg_watchdog(self) -> None:
        try:
            await asyncio.sleep(self.rt.settings.leg_timeout_sec)
        except asyncio.CancelledError:
            return
        if self.state != "streaming":
            await self._emit("leg_timeout")
            await self.rt.gateway.hangup(self.call_sid)
            await self.finish("leg_timeout")

    async def _grace_finish(self) -> None:
        try:
            await asyncio.sleep(self.rt.settings.stream_grace_sec)
        except asyncio.CancelledError:
            return
        await self.finish("stream_lost")


class SessionManager:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self._by_id: dict[str, CallSession] = {}
        self._by_user: dict[str, CallSession] = {}

    async def create(self, user: UserRecord, call_sid: Optional[str]) -> CallSession:
        old = self._by_user.get(user.id)
        if old:  # one live session per student
            if old.call_sid and old.call_sid != call_sid:
                await self.rt.gateway.hangup(old.call_sid)
            await old.finish("replaced")
        s = CallSession(self.rt, user, call_sid)
        self._by_id[s.id] = s
        self._by_user[user.id] = s
        await s.open()
        return s

    def get(self, call_id: Optional[str]) -> Optional[CallSession]:
        return self._by_id.get(call_id or "")

    def active(self, user_id: str) -> Optional[CallSession]:
        return self._by_user.get(user_id)

    def by_call_sid(self, call_sid: Optional[str]) -> Optional[CallSession]:
        return next((s for s in self._by_id.values() if call_sid and s.call_sid == call_sid), None)

    def all(self) -> list[CallSession]:
        return list(self._by_id.values())

    def remove(self, s: CallSession) -> None:
        self._by_id.pop(s.id, None)
        if self._by_user.get(s.user.id) is s:
            self._by_user.pop(s.user.id, None)

    async def finish_all(self, reason: str = "shutdown") -> None:
        for s in self.all():
            await s.finish(reason)
