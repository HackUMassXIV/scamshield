"""Seed Capital One's Nessie sandbox with the demo student, then switch the cloud
to it with BANK_BACKEND=nessie.

  NESSIE_API_KEY=... python -m cloud.seed_nessie
  -> cloud/data/nessie_ids.json

UNVERIFIED here (Nessie wasn't reachable from the build sandbox): the paths are
Nessie's documented ones (/customers, /customers/{id}/accounts,
/accounts/{id}/purchases, /merchants), but check the request field names in the
Nessie docs and run this once early. If it fails, BANK_BACKEND=fake still runs
the whole demo.
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


def created_id(r: httpx.Response) -> str:
    r.raise_for_status()
    d = r.json()
    return (d.get("objectCreated") or d)["_id"]


def main() -> None:
    load_dotenv()
    key = os.environ["NESSIE_API_KEY"]
    base = os.getenv("NESSIE_BASE", "http://api.nessieisreal.com")
    c = httpx.Client(base_url=base, params={"key": key}, timeout=20)

    def customer(first, last):
        return created_id(c.post("/customers", json={"first_name": first, "last_name": last, "address": ADDR}))

    def account(cust, nick, bal):
        return created_id(c.post(f"/customers/{cust}/accounts",
                                 json={"type": "Checking", "nickname": nick, "rewards": 0, "balance": bal}))

    alex = account(customer("Alex", "Chen"), "Alex checking", 1800)
    ids = {"users": {"alex": alex}, "payees": {}, "names": {}, "known": KNOWN}
    for pid, (first, last) in PAYEES.items():
        ids["payees"][pid] = account(customer(first, last), f"{first} {last}", 0)
        ids["names"][pid] = f"{first} {last}"

    merchant = created_id(c.post("/merchants", json={
        "name": "Campus Store", "category": "food", "address": ADDR, "geocode": {"lat": 42.39, "lng": -72.52}}))
    r = random.Random(3)
    for i in range(40):
        c.post(f"/accounts/{alex}/purchases", json={
            "merchant_id": merchant, "medium": "balance", "amount": round(r.uniform(5, 90), 2),
            "purchase_date": (date.today() - timedelta(days=i + 1)).isoformat(), "description": "purchase"})
    for days, amt, who in ((45, 900, "landlord"), (15, 900, "landlord"), (20, 42, "roommate"), (6, 35, "roommate")):
        c.post(f"/accounts/{alex}/transfers", json={
            "medium": "balance", "payee_id": ids["payees"][who], "amount": amt,
            "transaction_date": (date.today() - timedelta(days=days)).isoformat(), "description": who})

    out = Path(__file__).parent / "data" / "nessie_ids.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(ids, indent=2))
    print(f"seeded Nessie; wrote {out}. Now set BANK_BACKEND=nessie")


if __name__ == "__main__":
    main()
