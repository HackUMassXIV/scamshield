"""Fan-out of live events to the app (PWA) websockets and local listeners."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable

from fastapi import WebSocket

log = logging.getLogger("guardian.hub")

Listener = Callable[[dict], Any]


class Hub:
    def __init__(self, replay: int = 50):
        self._sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._recent: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=replay))
        self._listeners: list[Listener] = []

    def add_listener(self, fn: Listener) -> None:
        """In-process subscriber (CLI tools, tests, and later the risk engine)."""
        self._listeners.append(fn)

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._sockets[user_id].add(ws)
        for ev in list(self._recent[user_id]):  # catch a (re)connecting app up
            await ws.send_json({**ev, "replay": True})

    def disconnect(self, user_id: str, ws: WebSocket) -> None:
        self._sockets[user_id].discard(ws)

    def recent(self, user_id: str) -> list[dict]:
        return list(self._recent[user_id])

    async def publish(self, user_id: str, event_type: str, **data: Any) -> dict:
        ev = {"type": event_type, "user_id": user_id, "ts": time.time(), **data}
        self._recent[user_id].append(ev)
        for fn in self._listeners:
            try:
                r = fn(ev)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:  # a broken listener must not break the call
                log.exception("hub listener failed")
        for ws in list(self._sockets[user_id]):
            try:
                await asyncio.wait_for(ws.send_json(ev), timeout=1.0)
            except Exception:
                self._sockets[user_id].discard(ws)
        return ev
