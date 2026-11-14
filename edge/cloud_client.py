"""Edge -> cloud. Only REDACTED RiskEvents and session markers ever leave the edge.

Fire-and-forget with a short timeout: if the cloud (or venue Wi-Fi) is down the
call screening keeps working offline; only the payment hold needs the cloud.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from shared.contracts import RiskEvent

log = logging.getLogger("guardian.cloud")


class CloudClient:
    def __init__(self, base_url: Optional[str], api_key: Optional[str] = None, timeout: float = 2.0):
        self.base = (base_url or "").rstrip("/")
        self.headers = {"X-Cloud-Key": api_key} if api_key else {}
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._tasks: set[asyncio.Task] = set()
        self.sent = 0
        self.failed = 0
        self._warned = False

    @property
    def enabled(self) -> bool:
        return bool(self.base)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, headers=self.headers)
        return self._client

    async def _post(self, path: str, payload: dict) -> bool:
        if not self.enabled:
            return False
        try:
            r = await self._http().post(self.base + path, json=payload)
            r.raise_for_status()
            self.sent += 1
            return True
        except Exception as exc:
            self.failed += 1
            if not self._warned:
                log.warning("cloud unreachable (%s); screening continues offline", exc)
                self._warned = True
            return False

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def send_risk(self, ev: RiskEvent) -> None:
        self._spawn(self._post("/api/risk-event", ev.model_dump()))

    def send_session_end(self, user_id: str, call_id: str, max_level: str) -> None:
        self._spawn(self._post("/api/session-end", {"user_id": user_id, "call_id": call_id, "max_level": max_level}))

    def send_second_opinion(self, payload: dict) -> None:
        """Borderline call: ask the cloud for a Gemini second opinion on the REDACTED last 90 s."""
        self._spawn(self._post("/api/second-opinion", payload))

    def send_flag(self, user_id: str, seconds: int) -> None:
        self._spawn(self._post("/api/flag", {"user_id": user_id, "seconds": seconds}))

    async def drain(self, timeout: float = 3.0) -> None:
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)

    async def close(self) -> None:
        await self.drain()
        if self._client:
            await self._client.aclose()
