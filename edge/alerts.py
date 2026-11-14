"""Section 10: private alerts via Twilio, with cooldowns.

  - student: an SMS banner with each new coaching step (shows over the call screen)
  - trusted contact: one message when a call reaches RED

Messages contain only playbook text and the scam TYPE, never transcript quotes.
ALERT_CHANNEL=sms (default) | whatsapp (Twilio WhatsApp sandbox; set
WHATSAPP_FROM=whatsapp:+1415...) | off. TWILIO_DRY_RUN=1 logs instead of sending.

US carriers filter unregistered SMS (A2P 10DLC) and trial accounts can only text
verified numbers: test tonight, and keep WhatsApp sandbox or the app card as backup.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from shared.contracts import UserRecord
from shared.labels import PRETTY

from .config import Settings

log = logging.getLogger("guardian.alerts")


class Alerts:
    def __init__(self, s: Settings, gateway: Any, clock=time.time):
        self.s = s
        self.gateway = gateway  # TwilioGateway (shares the client / dry-run flag)
        self.clock = clock
        self._last_student: dict[str, float] = {}
        self._last_trusted: dict[str, float] = {}
        self._red_sent_for: set[str] = set()
        self.sent: list[dict] = []  # dry-run / test log
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self.s.alert_channel in ("sms", "whatsapp") and bool(self.s.twilio_number or self.s.whatsapp_from)

    def _addr(self, number: str) -> tuple[str, str]:
        if self.s.alert_channel == "whatsapp":
            return f"whatsapp:{number}", self.s.whatsapp_from or f"whatsapp:{self.s.twilio_number}"
        return number, self.s.twilio_number

    def _send(self, to: str, body: str, kind: str) -> None:
        to_addr, from_addr = self._addr(to)
        rec = {"to": to_addr, "from": from_addr, "body": body, "kind": kind, "ts": self.clock()}
        self.sent.append(rec)
        if self.s.twilio_dry_run:
            log.info("[dry-run] %s alert to %s: %s", kind, to_addr, body)
            return

        async def go():
            try:
                client = self.gateway._get_client()
                await asyncio.to_thread(client.messages.create, to=to_addr, from_=from_addr, body=body)
            except Exception as exc:
                log.warning("%s alert failed: %s", kind, exc)

        t = asyncio.create_task(go())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def coach_step(self, user: UserRecord, card: dict) -> bool:
        if not self.enabled:
            return False
        now = self.clock()
        if now - self._last_student.get(user.id, -1e9) < self.s.alert_cooldown_sec and card.get("action") != "HANG_UP":
            return False
        self._last_student[user.id] = now
        body = f"ScamShield: {card['say']} ({card['fact']})"
        if card.get("action") == "HANG_UP":
            body = f"ScamShield: HANG UP NOW. {card['fact']}"
        self._send(user.phone, body[:300], "coach")
        return True

    def red(self, user: UserRecord, call_id: str, scam_type: str) -> bool:
        if not self.enabled or not user.trusted_contact or call_id in self._red_sent_for:
            return False
        now = self.clock()
        if now - self._last_trusted.get(user.id, -1e9) < self.s.trusted_cooldown_sec:
            return False
        self._red_sent_for.add(call_id)
        self._last_trusted[user.id] = now
        first = user.name.split()[0]
        body = (f"ScamShield alert: {first} may be on a scam call right now "
                f"({PRETTY.get(scam_type, 'possible scam')}). Please call {first} on a number you already know.")
        self._send(user.trusted_contact, body, "trusted")
        return True

    def forget(self, call_id: str) -> None:
        self._red_sent_for.discard(call_id)

    async def drain(self) -> Optional[None]:
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=5)
