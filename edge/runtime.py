"""Wires the edge together: users, hub, ASR, VAD, Twilio gateway, sessions."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from .asr import ASRBackend, ASRService, make_asr
from .audio.vad import Segmenter, vad_factory
from .alerts import Alerts
from .classifier import Classifier, ClassifierService, load_classifier
from .llm import LLMService, make_llm
from .cloud_client import CloudClient
from .config import Settings
from .hub import Hub
from .session import CallSession, SessionManager
from .twilio_gateway import TwilioGateway
from .users import UserDirectory

log = logging.getLogger("guardian.runtime")

TranscriptHook = Callable[[CallSession, dict], Awaitable[None]]
EndHook = Callable[[CallSession], Awaitable[None]]


class Runtime:
    def __init__(
        self,
        settings: Settings,
        *,
        asr_backend: Optional[ASRBackend] = None,
        gateway_client: Any = None,
        hub: Optional[Hub] = None,
        classifier: Optional[Classifier] = None,
        clock: Optional[Callable[[], float]] = None,
        llm: Optional[LLMService] = None,
    ):
        import time as _time

        from .risk import RiskService  # avoid an import cycle

        self.settings = settings
        self.users = UserDirectory(settings.users_file)
        self.hub = hub or Hub()
        self.asr = ASRService(asr_backend or make_asr(settings), settings.whisper_prompt)
        self.gateway = TwilioGateway(settings, gateway_client)
        self.sessions = SessionManager(self)
        # Sections 7/8 register async hooks here (classifier, risk engine, ...):
        #   async def hook(session: CallSession, transcript_event: dict) -> None
        self.transcript_hooks: list[TranscriptHook] = []
        self.end_hooks: list[EndHook] = []
        self.flags: dict[str, float] = {}  # user_id -> "I'm suspicious" floor expires at
        self.clock = clock or _time.time
        self.clf = ClassifierService(classifier or load_classifier(settings))
        self.cloud = CloudClient(settings.cloud_url, settings.cloud_api_key)
        self.llm = llm or make_llm(settings)  # section 11
        self.alerts = Alerts(settings, self.gateway, self.clock)  # section 10
        self.risk = RiskService(self, clock=self.clock)
        self.transcript_hooks.append(self.risk.on_transcript)  # section 8
        self.end_hooks.append(self.risk.on_end)
        self._make_vad = vad_factory(settings.vad_backend)
        self.vad_name = "?"

    def new_segmenter(self, speaker: str) -> Segmenter:
        s = self.settings
        return Segmenter(
            speaker,
            self._make_vad(),
            threshold=s.vad_threshold,
            silence_ms=s.silence_ms,
            min_speech_ms=s.min_speech_ms,
            max_utt_s=s.max_utt_sec,
            preroll_ms=s.preroll_ms,
        )

    async def startup(self) -> None:
        self.vad_name = getattr(self._make_vad(), "name", "?")  # loads Silero once
        await self.asr.warmup()  # loads Whisper weights before the first call
        await self.clf.predict("warm up")  # load the model before the first call
        if self.llm.client:  # load the LLM into memory without delaying startup
            import asyncio

            self._llm_warm = asyncio.create_task(self.llm.client.warmup())
        log.info("edge ready: asr=%s vad=%s clf=%s llm=%s alerts=%s cloud=%s", self.asr.name, self.vad_name,
                 self.clf.name, self.settings.ollama_model if self.llm.enabled else "off",
                 self.settings.alert_channel if self.alerts.enabled else "off", self.settings.cloud_url or "offline")

    async def shutdown(self) -> None:
        await self.sessions.finish_all("shutdown")
        self.asr.close()
        self.clf.close()
        await self.llm.close()
        await self.alerts.drain()
        await self.cloud.close()
