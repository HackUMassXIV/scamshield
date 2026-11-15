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
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger("cloud.bank")

# Descriptions written by cloud/seed_nessie.py for the demo history. Any other
# "to:" transfer on the student's account was made by this app (Nessie may change
# a transfer's status after creation, so status can't be used to tell them apart).
SEED_DESCRIPTIONS = {"to:landlord | October rent", "to:landlord | November rent",
                     "to:bursar | parking permit", "to:roommate | groceries", "to:roommate | internet"}


def is_app_transfer(row: dict) -> bool:
    desc = str(row.get("description", "")).strip()
    return desc.startswith("to:") and desc not in SEED_DESCRIPTIONS


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
    """Same interface; account data and approved transfers go to Nessie.

    What the live sandbox does (other teams' 2026 tests; verify with your key):
      * base URL must be https://api.nessieisreal.com
      * writes never change an account balance -> we keep a local ledger
      * transfers may have no payee field -> payee goes in description "to:<payee> | memo"
      * transfers need status/transaction_date/amount/description

    cloud/data/nessie_ids.json (written by cloud/seed_nessie.py):
      {"users": {"alex": "<account id>"}, "payees": {"landlord": "<account id>", ...},
       "names": {...}, "known": [...], "payee_field": false}
    "payee_field": true only if Nessie accepted medium + payee_id in your curl test.
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
        base = base.replace("http://", "https://", 1)  # plain http is refused by Nessie
        self._http = httpx.AsyncClient(base_url=base, params={"key": api_key}, timeout=10)
        self._spent: dict[str, float] = {}  # local ledger: Nessie never lowers balances
        self._hidden: set[str] = set()  # app transfers from before this start (reset couldn't delete them)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _key(row: dict) -> str:
        """Nessie's list responses may omit _id, so fall back to the row's contents."""
        return str(row.get("_id") or row.get("id") or
                   f'{row.get("amount")}|{row.get("description")}|{row.get("transaction_date")}|{row.get("status")}')

    @staticmethod
    def _ts(row: dict) -> float:
        try:
            return datetime.fromisoformat(str(row.get("transaction_date", ""))[:10]).timestamp()
        except ValueError:
            return 0.0

    def _payee_of(self, row: dict, by_nessie_id: dict[str, str]) -> Optional[str]:
        pid = by_nessie_id.get(row.get("payee_id") or "")
        if pid:
            return pid
        desc = str(row.get("description", ""))
        if desc.startswith("to:"):
            cand = desc[3:].split(" |")[0].strip()
            return cand if cand in self.ids["payees"] else None
        return None

    # ---------------------------------------------------------------- reads
    async def balance(self, user_id: str) -> float:
        """Opening balance from Nessie minus every transfer this app made.

        Nessie never lowers a balance, so we compute it. App transfers are the "to:"
        transfers that are not the seeded history (see is_app_transfer). Survives restarts."""
        acct = self.ids["users"][user_id]
        r = await self._http.get(f"/accounts/{acct}")
        r.raise_for_status()
        opening = float(r.json()["balance"])
        try:
            t = await self._http.get(f"/accounts/{acct}/transfers", params={"type": "payer"})
            t.raise_for_status()
            sent = sum(float(x["amount"]) for x in t.json()
                       if is_app_transfer(x) and self._key(x) not in self._hidden)
        except Exception as exc:
            log.warning("Nessie transfers read failed, using session ledger: %s", exc)
            sent = self._spent.get(user_id, 0.0)
        return round(opening - sent, 2)

    async def spend_history(self, user_id: str) -> list[float]:
        acct = self.ids["users"][user_id]
        by_nessie_id = {v: k for k, v in self.ids["payees"].items()}
        out: list[float] = []
        for path in (f"/accounts/{acct}/purchases", f"/accounts/{acct}/transfers"):
            try:
                r = await self._http.get(path, params={"type": "payer"} if "transfers" in path else None)
                r.raise_for_status()
                rows = [x for x in r.json() if "amount" in x]
            except Exception as exc:
                log.warning("Nessie %s failed: %s", path, exc)
                continue
            out += [float(x["amount"]) for x in rows]
            if "transfers" in path:  # rebuild per-payee history from Nessie
                recs = []
                for x in rows:
                    if self._key(x) in self._hidden:
                        continue  # from an earlier demo run
                    pid = self._payee_of(x, by_nessie_id)
                    if pid:
                        recs.append(TransferRecord(pid, float(x["amount"]), self._ts(x),
                                                   x.get("description", ""), x.get("_id")))
                # keep transfers made this session that Nessie may not list yet
                seen = {t.nessie_id for t in recs if t.nessie_id}
                recs += [t for t in self._a(user_id).transfers if t.nessie_id and t.nessie_id not in seen]
                self._a(user_id).transfers = recs
        return out

    # --------------------------------------------------------------- writes
    async def transfer(self, user_id: str, payee_id: str, amount: float, memo: str) -> TransferRecord:
        if amount > await self.balance(user_id):
            raise ValueError("insufficient funds")
        acct = self.ids["users"][user_id]
        body = {"status": "pending", "amount": amount, "transaction_date": date.today().isoformat(),
                "description": f"to:{payee_id} | {memo or 'ScamShield transfer'}"}
        if self.ids.get("payee_field"):
            body |= {"medium": "balance", "payee_id": self.ids["payees"][payee_id]}
        r = await self._http.post(f"/accounts/{acct}/transfers", json=body)
        if r.status_code >= 400:
            log.error("Nessie transfer rejected %s: %s", r.status_code, r.text[:300])
        r.raise_for_status()
        data = r.json()
        nid = (data.get("objectCreated") or {}).get("_id") if isinstance(data, dict) else None
        rec = TransferRecord(payee_id, amount, self.clock(), memo, nid)
        self._a(user_id).transfers.append(rec)
        self._spent[user_id] = self._spent.get(user_id, 0.0) + amount
        # mirror the money into the payee's account so it shows up on their side
        # (Nessie transfers carry no payee, and balances never change either way)
        try:
            await self._http.post(f"/accounts/{self.ids['payees'][payee_id]}/deposits", json={
                "medium": "balance", "amount": amount, "status": "completed",
                "transaction_date": date.today().isoformat(),
                "description": f"from:{user_id} | {memo or 'ScamShield transfer'}"})
        except Exception as exc:
            log.warning("Nessie deposit mirror failed: %s", exc)
        return rec

    # ---------------------------------------------------------------- reset
    async def reset(self) -> int:
        """Delete every transfer this app made ("to:" description, not seed history)
        and the mirrored deposits ("from:" description), so the demo starts fresh.
        Seeded history (status "completed") is kept. Returns how many were deleted."""
        deleted = 0
        targets = [(acct, "transfers", "to:") for acct in self.ids["users"].values()]
        targets += [(acct, "deposits", "from:") for acct in self.ids["payees"].values()]
        for acct, kind, prefix in targets:
            try:
                # same listing the balance uses (type=payer), so the app's transfers are found
                r = await self._http.get(f"/accounts/{acct}/{kind}",
                                         params={"type": "payer"} if kind == "transfers" else None)
                r.raise_for_status()
                rows = r.json()
            except Exception as exc:
                log.warning("reset: listing %s on %s failed: %s", kind, acct, exc)
                continue
            log.info("reset: %s on %s -> %d rows %s", kind, acct, len(rows),
                     [(x.get("status"), x.get("amount"), x.get("description")) for x in rows][:12])
            for x in rows:
                if kind == "transfers" and not is_app_transfer(x):
                    continue  # keep seeded history
                if kind == "deposits" and not str(x.get("description", "")).startswith(prefix):
                    continue
                ok = False
                rid = x.get("_id") or x.get("id")
                try:
                    if not rid:
                        raise ValueError("Nessie returned no id")
                    d = await self._http.delete(f"/{kind}/{rid}")
                    ok = d.status_code < 400
                    if not ok:
                        log.warning("reset: delete %s %s -> %s %s", kind, rid, d.status_code, d.text[:200])
                except Exception as exc:
                    log.info("reset: can't delete %s (%s); hiding it", kind, exc)
                if ok:
                    deleted += 1
                else:
                    self._hidden.add(self._key(x))  # can't delete: ignore it from now on
        for uid in self.ids["users"]:
            self._spent[uid] = 0.0
            self._a(uid).transfers = []
            for pid in self._a(uid).payees.values():
                pid.known = pid.id in set(self.ids.get("known", []))
        log.info("reset: deleted %d app-made Nessie records, hid %d others", deleted, len(self._hidden))
        return deleted


def make_bank(settings) -> Bank:
    if settings.bank_backend == "nessie":
        return NessieBank(settings.nessie_api_key, settings.nessie_base, settings.nessie_ids_file)
    return FakeBank()