"""Persistence for the cloud: MongoDB Atlas when MONGODB_URI is set, else memory.

Collections (database "scamshield"):
  holds        one document per held payment (status HELD/RELEASED/CANCELLED)
  payees       {user_id, payee_id, name, known}: which payees a student has
               already paid. Nessie has no notion of a trusted payee, so we keep it.
  riskEvents   every REDACTED RiskEvent from the edge (feeds the scam map later)

pymongo is synchronous, so calls run in a worker thread to keep the API async.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Protocol

from shared.contracts import Hold

log = logging.getLogger("cloud.store")


class Store(Protocol):
    name: str

    async def save_hold(self, h: Hold) -> None: ...
    async def get_hold(self, hold_id: str) -> Optional[Hold]: ...
    async def holds_for(self, user_id: str) -> list[Hold]: ...
    async def known_payees(self, user_id: str) -> set[str]: ...
    async def upsert_payee(self, user_id: str, payee_id: str, name: str, known: bool, only_if_new: bool = False) -> None: ...
    async def log_risk(self, ev: dict) -> None: ...


class MemoryStore:
    name = "memory"

    def __init__(self):
        self.holds: dict[str, Hold] = {}
        self.payees: dict[tuple[str, str], dict] = {}
        self.risk: list[dict] = []

    async def save_hold(self, h: Hold) -> None:
        self.holds[h.id] = h

    async def get_hold(self, hold_id: str) -> Optional[Hold]:
        return self.holds.get(hold_id)

    async def holds_for(self, user_id: str) -> list[Hold]:
        return sorted((h for h in self.holds.values() if h.transfer.user_id == user_id), key=lambda h: h.created)

    async def known_payees(self, user_id: str) -> set[str]:
        return {p for (u, p), d in self.payees.items() if u == user_id and d["known"]}

    async def upsert_payee(self, user_id, payee_id, name, known, only_if_new=False) -> None:
        key = (user_id, payee_id)
        if only_if_new and key in self.payees:
            return
        self.payees[key] = {"user_id": user_id, "payee_id": payee_id, "name": name, "known": known}

    async def log_risk(self, ev: dict) -> None:
        self.risk.append(ev)
        del self.risk[:-1000]


class MongoStore:
    name = "mongo"

    def __init__(self, uri: Optional[str] = None, db: str = "scamshield", client: Any = None):
        if client is None:
            from pymongo import MongoClient

            client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.db = client[db]
        self.db.holds.create_index("transfer.user_id")
        self.db.payees.create_index([("user_id", 1), ("payee_id", 1)], unique=True)
        self.db.riskEvents.create_index([("user_id", 1), ("ts", -1)])

    async def _run(self, fn, *a, **kw):
        return await asyncio.to_thread(fn, *a, **kw)

    async def save_hold(self, h: Hold) -> None:
        doc = {**h.model_dump(), "_id": h.id}
        await self._run(self.db.holds.replace_one, {"_id": h.id}, doc, upsert=True)

    async def get_hold(self, hold_id: str) -> Optional[Hold]:
        d = await self._run(self.db.holds.find_one, {"_id": hold_id})
        return Hold(**{k: v for k, v in d.items() if k != "_id"}) if d else None

    async def holds_for(self, user_id: str) -> list[Hold]:
        docs = await self._run(lambda: list(self.db.holds.find({"transfer.user_id": user_id}).sort("created", 1)))
        return [Hold(**{k: v for k, v in d.items() if k != "_id"}) for d in docs]

    async def known_payees(self, user_id: str) -> set[str]:
        docs = await self._run(lambda: list(self.db.payees.find({"user_id": user_id, "known": True}, {"payee_id": 1})))
        return {d["payee_id"] for d in docs}

    async def upsert_payee(self, user_id, payee_id, name, known, only_if_new=False) -> None:
        q = {"user_id": user_id, "payee_id": payee_id}
        doc = {**q, "name": name, "known": known}
        if only_if_new:
            await self._run(self.db.payees.update_one, q, {"$setOnInsert": doc}, upsert=True)
        else:
            await self._run(self.db.payees.update_one, q, {"$set": doc}, upsert=True)

    async def log_risk(self, ev: dict) -> None:
        await self._run(self.db.riskEvents.insert_one, dict(ev))


def make_store(uri: Optional[str], db: str = "scamshield") -> Store:
    if uri:
        try:
            st = MongoStore(uri, db)
            st.db.client.admin.command("ping")
            log.info("MongoDB connected (db=%s)", db)
            return st
        except Exception as exc:
            log.warning("MongoDB unavailable (%s); using in-memory store", exc)
    return MemoryStore()
