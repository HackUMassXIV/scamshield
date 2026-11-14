"""Bank layer behind the firewall.

FakeBank   in-memory, seeded with the demo student (default; used by tests and
           `run.py m1`, so the demo never depends on venue Wi-Fi).
NessieBank Capital One's Nessie sandbox. Our server sits IN FRONT of it: a
           transfer is only created in Nessie when the firewall says OK.
           Endpoint paths (/accounts/{id}, /purchases, /transfers) are Nessie's;
           double-check request field names against the Nessie docs before demo.

Payees are tracked by OUR server (known / new), because Nessie has no notion of
a trusted payee.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class Payee:
    id: str
    name: str
    known: bool


@dataclass
class TransferRecord:
    payee_id: str
    amount: float
    ts: float
    memo: str = ""
    nessie_id: Optional[str] = None


class Bank(Protocol):
    name: str

    async def balance(self, user_id: str) -> float: ...
    async def spend_history(self, user_id: str) -> list[float]: ...
    def payee(self, user_id: str, payee_id: str) -> Optional[Payee]: ...
    def payees(self, user_id: str) -> list[Payee]: ...
    def transfers_to(self, user_id: str, payee_id: str) -> list[TransferRecord]: ...
    def recent_transfers(self, user_id: str) -> list[TransferRecord]: ...
    async def transfer(self, user_id: str, payee_id: str, amount: float, memo: str) -> TransferRecord: ...
    def mark_known(self, user_id: str, payee_id: str) -> None: ...


@dataclass
class _Account:
    balance: float
    purchases: list[float]
    payees: dict[str, Payee]
    transfers: list[TransferRecord] = field(default_factory=list)


def demo_accounts(now: Optional[float] = None) -> dict[str, _Account]:
    """Alex Chen: ~$1,800 checking, ~40 ordinary purchases, a monthly $900 rent,
    small transfers to a roommate. GiftCardDepot is a payee they've never paid."""
    now = now or time.time()
    day = 86400
    purchases = [12.5, 8.75, 23.4, 41.0, 15.99, 6.5, 62.3, 18.2, 9.99, 27.45, 33.1, 14.0, 52.8, 7.25,
                 19.6, 88.0, 11.4, 24.99, 16.75, 38.5, 5.5, 71.2, 13.3, 29.9, 21.0, 45.6, 10.8, 17.4,
                 26.3, 58.9, 12.0, 35.75, 22.2, 9.5, 48.1, 15.0, 31.6, 19.99, 64.4, 20.5]
    payees = {
        "landlord": Payee("landlord", "Brookside Apartments", True),
        "roommate": Payee("roommate", "Jordan Lee", True),
        "bursar": Payee("bursar", "UMass Bursar", True),
        "giftcarddepot": Payee("giftcarddepot", "GiftCardDepot LLC", False),
        "newfriend": Payee("newfriend", "Sam Patel", False),
    }
    transfers = [
        TransferRecord("landlord", 900.0, now - 45 * day, "October rent"),
        TransferRecord("landlord", 900.0, now - 15 * day, "November rent"),
        TransferRecord("roommate", 42.0, now - 20 * day, "groceries"),
        TransferRecord("roommate", 35.0, now - 6 * day, "internet"),
        TransferRecord("bursar", 250.0, now - 30 * day, "parking permit"),
    ]
    return {"alex": _Account(1800.0, purchases, payees, transfers),
            "sam": _Account(950.0, purchases[:20], {k: Payee(v.id, v.name, v.known) for k, v in payees.items()}, [])}


class FakeBank:
    name = "fake"

    def __init__(self, accounts: Optional[dict[str, _Account]] = None, clock=time.time):
        self.clock = clock
        self.acc = accounts or demo_accounts(clock())

    def _a(self, user_id: str) -> _Account:
        if user_id not in self.acc:
            raise KeyError(f"unknown user {user_id}")
        return self.acc[user_id]

    async def balance(self, user_id: str) -> float:
        return self._a(user_id).balance

    async def spend_history(self, user_id: str) -> list[float]:
        a = self._a(user_id)
        return a.purchases + [t.amount for t in a.transfers]

    def payee(self, user_id: str, payee_id: str) -> Optional[Payee]:
        return self._a(user_id).payees.get(payee_id)

    def payees(self, user_id: str) -> list[Payee]:
        return list(self._a(user_id).payees.values())

    def transfers_to(self, user_id: str, payee_id: str) -> list[TransferRecord]:
        return [t for t in self._a(user_id).transfers if t.payee_id == payee_id]

    def recent_transfers(self, user_id: str) -> list[TransferRecord]:
        return sorted(self._a(user_id).transfers, key=lambda t: -t.ts)[:10]

    async def transfer(self, user_id: str, payee_id: str, amount: float, memo: str) -> TransferRecord:
        a = self._a(user_id)
        if amount > a.balance:
            raise ValueError("insufficient funds")
        a.balance = round(a.balance - amount, 2)
        rec = TransferRecord(payee_id, amount, self.clock(), memo)
        a.transfers.append(rec)
        return rec

    def mark_known(self, user_id: str, payee_id: str) -> None:
        p = self.payee(user_id, payee_id)
        if p:
            p.known = True


class NessieBank(FakeBank):
    """Same interface; balance, history and transfer creation go to Nessie.

    cloud/data/nessie_ids.json (written by cloud/seed_nessie.py):
      {"users": {"alex": "<account id>"}, "payees": {"landlord": "<account id>", ...},
       "names": {"landlord": "Brookside Apartments", ...}, "known": ["landlord", ...]}
    """

    name = "nessie"

    def __init__(self, api_key: str, base: str, ids_file: str, clock=time.time):
        import httpx

        ids = json.loads(Path(ids_file).read_text())
        self.ids = ids
        accounts = {}
        for uid in ids["users"]:
            payees = {p: Payee(p, ids.get("names", {}).get(p, p), p in set(ids.get("known", [])))
                      for p in ids["payees"]}
            accounts[uid] = _Account(0.0, [], payees, [])
        super().__init__(accounts, clock)
        self._http = httpx.AsyncClient(base_url=base, params={"key": api_key}, timeout=10)

    async def balance(self, user_id: str) -> float:
        r = await self._http.get(f"/accounts/{self.ids['users'][user_id]}")
        r.raise_for_status()
        return float(r.json()["balance"])

    async def spend_history(self, user_id: str) -> list[float]:
        acct = self.ids["users"][user_id]
        by_nessie_id = {v: k for k, v in self.ids["payees"].items()}
        out: list[float] = []
        for path in (f"/accounts/{acct}/purchases", f"/accounts/{acct}/transfers"):
            try:
                r = await self._http.get(path, params={"type": "payer"} if "transfers" in path else None)
                r.raise_for_status()
                rows = [x for x in r.json() if "amount" in x]
            except Exception:
                continue
            out += [float(x["amount"]) for x in rows]
            if "transfers" in path:  # rebuild per-payee history from Nessie
                self._a(user_id).transfers = [
                    TransferRecord(by_nessie_id[x["payee_id"]], float(x["amount"]), 0.0, x.get("description", ""), x.get("_id"))
                    for x in rows if x.get("payee_id") in by_nessie_id]
        return out

    async def transfer(self, user_id: str, payee_id: str, amount: float, memo: str) -> TransferRecord:
        acct = self.ids["users"][user_id]
        body = {"medium": "balance", "payee_id": self.ids["payees"][payee_id], "amount": amount,
                "transaction_date": date.today().isoformat(), "description": memo or "ScamShield transfer"}
        r = await self._http.post(f"/accounts/{acct}/transfers", json=body)
        r.raise_for_status()
        data = r.json()
        nid = (data.get("objectCreated") or {}).get("_id") if isinstance(data, dict) else None
        rec = TransferRecord(payee_id, amount, self.clock(), memo, nid)
        self._a(user_id).transfers.append(rec)
        return rec


def make_bank(settings) -> Bank:
    if settings.bank_backend == "nessie":
        return NessieBank(settings.nessie_api_key, settings.nessie_base, settings.nessie_ids_file)
    return FakeBank()
