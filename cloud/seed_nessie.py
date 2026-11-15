"""Seed Capital One's Nessie sandbox with the demo student, then switch the cloud
to it with BANK_BACKEND=nessie.

  python -m cloud.seed_nessie
  -> cloud/data/nessie_ids.json

Fixes from other teams' tests of the live sandbox (verify with your key):
  * https only (plain http is refused / times out)
  * purchases and deposits need "status": "completed" (a purchase without it
    can permanently break the account's purchase list)
  * transfers may not accept medium/payee_id; this script tries once with them,
    falls back to {status, amount, transaction_date, description}, and records
    which one worked as "payee_field" for cloud/bank.py
  * the payee is always written into the description as "to:<payee> | <memo>"
"""
from __future__ import annotations

import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

ADDR = {"street_number": "650", "street_name": "North Pleasant St", "city": "Amherst", "state": "MA", "zip": "01003"}
PAYEES = {"landlord": ("Brookside", "Apartments"), "roommate": ("Jordan", "Lee"), "bursar": ("UMass", "Bursar"),
          "giftcarddepot": ("GiftCardDepot", "LLC"), "newfriend": ("Sam", "Patel")}
KNOWN = ["landlord", "roommate", "bursar"]
HISTORY = ((45, 900, "landlord", "October rent"), (15, 900, "landlord", "November rent"),
           (30, 250, "bursar", "parking permit"), (20, 42, "roommate", "groceries"), (6, 35, "roommate", "internet"))


def check(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code >= 400:
        raise SystemExit(f"Nessie rejected {what}: HTTP {r.status_code} {r.text[:300]}")
    return r


def created_id(r: httpx.Response, what: str) -> str:
    d = check(r, what).json()
    return (d.get("objectCreated") or d)["_id"]


def main() -> None:
    load_dotenv()
    key = os.environ.get("NESSIE_API_KEY") or raise_missing()
    base = (os.getenv("NESSIE_BASE") or "https://api.nessieisreal.com").replace("http://", "https://", 1)
    c = httpx.Client(base_url=base, params={"key": key}, timeout=20)
    print(f"seeding {base} ...")

    def customer(first: str, last: str) -> str:
        return created_id(c.post("/customers", json={"first_name": first, "last_name": last, "address": ADDR}),
                          f"customer {first} {last}")

    def account(cust: str, nick: str, bal: float) -> str:
        return created_id(c.post(f"/customers/{cust}/accounts",
                                 json={"type": "Checking", "nickname": nick, "rewards": 0, "balance": bal}),
                          f"account {nick}")

    alex = account(customer("Alex", "Chen"), "Alex checking", 1800)
    ids: dict = {"users": {"alex": alex}, "payees": {}, "names": {}, "known": KNOWN, "payee_field": False}
    for pid, (first, last) in PAYEES.items():
        ids["payees"][pid] = account(customer(first, last), f"{first} {last}", 0)
        ids["names"][pid] = f"{first} {last}"
    print(f"  alex account {alex} + {len(PAYEES)} payee accounts")

    merchant = created_id(c.post("/merchants", json={
        "name": "Campus Store", "category": "food", "address": ADDR, "geocode": {"lat": 42.39, "lng": -72.52}}),
        "merchant")
    rnd = random.Random(3)
    for i in range(40):
        check(c.post(f"/accounts/{alex}/purchases", json={
            "merchant_id": merchant, "medium": "balance", "amount": round(rnd.uniform(5, 90), 2),
            "purchase_date": (date.today() - timedelta(days=i + 1)).isoformat(),
            "status": "completed", "description": "purchase"}), f"purchase {i + 1}")
    print("  40 purchases")

    def transfer(days: int, amt: float, who: str, memo: str) -> None:
        body = {"status": "completed", "amount": amt,
                "transaction_date": (date.today() - timedelta(days=days)).isoformat(),
                "description": f"to:{who} | {memo}"}
        if ids["payee_field"]:
            body |= {"medium": "balance", "payee_id": ids["payees"][who]}
        check(c.post(f"/accounts/{alex}/transfers", json=body), f"transfer to {who}")

    # Probe once: does this Nessie accept medium + payee_id on transfers?
    days, amt, who, memo = HISTORY[0]
    probe = c.post(f"/accounts/{alex}/transfers", json={
        "medium": "balance", "payee_id": ids["payees"][who], "status": "completed", "amount": amt,
        "transaction_date": (date.today() - timedelta(days=days)).isoformat(), "description": f"to:{who} | {memo}"})
    if probe.status_code < 400:
        ids["payee_field"] = True
        print("  transfers accept payee_id (payee_field=true)")
    else:
        print(f"  transfers reject payee_id (HTTP {probe.status_code}); using description only")
        transfer(days, amt, who, memo)
    for h in HISTORY[1:]:
        transfer(*h)
    print(f"  {len(HISTORY)} past transfers")

    out = Path(__file__).parent / "data" / "nessie_ids.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(ids, indent=2))
    print(f"seeded Nessie; wrote {out}. Now set BANK_BACKEND=nessie and restart the cloud.")


def raise_missing() -> str:
    raise SystemExit("NESSIE_API_KEY is not set in .env")


if __name__ == "__main__":
    main()