"""Everything Twilio-specific: signature validation, TwiML builders and live
call updates. A `client` can be injected (tests), or set TWILIO_DRY_RUN=1 (dev)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from .config import Settings

log = logging.getLogger("guardian.twilio")


class TwilioGateway:
    def __init__(self, settings: Settings, client: Any = None):
        self.s = settings
        self._client = client
        self._validator = (
            RequestValidator(settings.twilio_auth_token) if settings.twilio_auth_token else None
        )
        self.dry_run_log: list[dict] = []  # what dry-run mode would have sent

    # ------------------------------------------------------------ signatures
    def candidate_urls(self, path: str, query: str = "", headers: Optional[dict] = None) -> list[str]:
        """Twilio signs the exact URL it called. Behind a tunnel the URL our process
        sees differs, so try the configured public URL first, then the forwarded host."""
        q = f"?{query}" if query else ""
        urls = [f"{self.s.public_base_url}{path}{q}"]
        h = {k.lower(): v for k, v in (headers or {}).items()}
        host = h.get("x-forwarded-host") or h.get("host")
        if host:
            proto = h.get("x-forwarded-proto", "https")
            urls.append(f"{proto}://{host}{path}{q}")
        return list(dict.fromkeys(urls))

    def validate(self, urls: list[str], params: dict, signature: Optional[str]) -> bool:
        if not self.s.twilio_validate:
            return True
        if not self._validator or not signature:
            return False
        return any(self._validator.validate(u, params, signature) for u in urls)

    def sign(self, url: str, params: dict) -> str:
        """Used by the simulator/tests to produce a valid signature."""
        v = self._validator or RequestValidator(self.s.twilio_auth_token or "")
        return v.compute_signature(url, params)

    # ---------------------------------------------------------------- TwiML
    def _say(self, vr: VoiceResponse, text: str) -> None:
        if self.s.twilio_say_voice:
            vr.say(text, voice=self.s.twilio_say_voice)
        else:
            vr.say(text)

    def leg_twiml(self) -> str:
        """Answer silently and wait. NO audio is streamed yet, so nothing is
        intercepted before the disclosure is played."""
        vr = VoiceResponse()
        vr.pause(length=self.s.leg_timeout_sec)
        return str(vr)

    def screen_twiml(self, call_id: str) -> str:
        """Disclosure first (heard by everyone on the merged call), then open the
        stream. <Start> comes AFTER <Say> so the disclosure is never transcribed."""
        vr = VoiceResponse()
        self._say(vr, self.s.disclosure_text)
        stream = vr.start().stream(url=f"{self.s.ws_base_url}/ws/twilio", track="inbound_track")
        stream.parameter(name="call_id", value=call_id)
        vr.pause(length=self.s.stream_pause_sec)
        return str(vr)

    def bridge_screen_twiml(self, call_id: str, dial_number: str) -> str:
        """Backup mode: the caller rings the Guardian number, we forward to the
        student and stream BOTH tracks (inbound = caller, outbound = student)."""
        vr = VoiceResponse()
        self._say(vr, self.s.disclosure_text)
        stream = vr.start().stream(url=f"{self.s.ws_base_url}/ws/twilio", track="both_tracks")
        stream.parameter(name="call_id", value=call_id)
        vr.dial(dial_number)
        return str(vr)

    def reject_twiml(self, message: str) -> str:
        vr = VoiceResponse()
        self._say(vr, message)
        vr.hangup()
        return str(vr)

    # ----------------------------------------------------------- live calls
    def _get_client(self) -> Any:
        if self._client is None:
            from twilio.rest import Client

            self._client = Client(self.s.twilio_account_sid, self.s.twilio_auth_token)
        return self._client

    async def update_call(
        self, call_sid: Optional[str], *, twiml: Optional[str] = None, status: Optional[str] = None
    ) -> None:
        if not call_sid:
            return
        kwargs = {k: v for k, v in (("twiml", twiml), ("status", status)) if v}
        if self.s.twilio_dry_run:
            self.dry_run_log.append({"call_sid": call_sid, **kwargs})
            log.info("[dry-run] update call %s %s", call_sid, kwargs)
            return
        await asyncio.to_thread(self._get_client().calls(call_sid).update, **kwargs)

    async def hangup(self, call_sid: Optional[str]) -> None:
        try:
            await self.update_call(call_sid, status="completed")
        except Exception:
            log.exception("hangup failed for %s", call_sid)
