"""Section 4's firewall test cases, over real HTTP against a FRESH cloud.

  python run.py firewall          (starts its own cloud on --cloud-port, then runs this)
  python -m cloud.firewall_cases --url http://localhost:9000 --key KEY   (against a running, fresh cloud)

The cases build on each other in order (balances and payee history change), so
run them against a cloud with no prior transfers.
"""
from __future__ import annotations

import argparse
import sys

import httpx

CASES = [
    # (label, user, payee, amount, extra, expected)
    ("1  Rent to the landlord, no call", "alex", "landlord", 900, {}, "OK"),
    ("2  $600 to GiftCardDepot, no call", "alex", "giftcarddepot", 600, {}, "HELD"),
    ("3  $40 to the roommate", "alex", "roommate", 40, {}, "OK"),
    ("4  Same $40 again within 5 min (duplicate)", "alex", "roommate", 40, {}, "WARN"),
    ("5  $400 to the roommate (x10 typo)", "alex", "roommate", 400, {}, "HELD"),
    ("6  $25 to a new friend", "alex", "newfriend", 25, {}, "WARN"),
    ("6b ...student taps Continue", "alex", "newfriend", 25, {"confirm": True}, "OK"),
    ("7  RED scam call, then $600 to GiftCardDepot", "sam", "giftcarddepot", 600, {"red": True}, "HELD"),
    ("8  'I'm suspicious' pressed, then $250 to the bursar", "alex", "bursar", 250, {"flag": True}, "WARN"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:9000")
    ap.add_argument("--key", default="")
    a = ap.parse_args()
    h = {"X-Cloud-Key": a.key} if a.key else {}
    c = httpx.Client(base_url=a.url, headers=h, timeout=15)
    ok = True
    sam_hold = None
    print(f"\n{'case':<55} {'expected':<9} {'got':<9} points  reasons")
    print("-" * 130)
    for label, user, payee, amount, extra, expected in CASES:
        if extra.get("red"):
            c.post("/api/risk-event", json={"call_id": "demo-call", "user_id": user, "level": "RED", "score": 88,
                                            "scam_type": "immigration_uscis", "red_flags": ["gift cards"], "features": {}})
        if extra.get("flag"):
            c.post("/api/flag", json={"user_id": user, "seconds": 600})
        r = c.post("/api/transfer" + ("?confirm=1" if extra.get("confirm") else ""),
                   json={"user_id": user, "payee_id": payee, "amount": amount, "memo": "firewall test"}).json()
        got = r.get("status", "?")
        if user == "sam" and r.get("hold"):
            sam_hold = r["hold"]["id"]
        mark = "PASS" if got == expected else "FAIL"
        ok &= got == expected
        print(f"{mark} {label:<50} {expected:<9} {got:<9} {r.get('points', '-'):<6}  {'; '.join(r.get('reasons', []))}")

    if sam_hold:  # hold lifecycle: too early, then cancel
        early = c.post(f"/api/holds/{sam_hold}/release", json={"confirm": "RELEASE"})
        cancel = c.post(f"/api/holds/{sam_hold}/cancel").json()
        good = early.status_code == 425 and cancel.get("status") == "CANCELLED"
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} 9  Release case 7 right away -> {early.status_code} (cool-down), "
              f"then cancel -> {cancel.get('status')}")
    holds = c.get("/api/holds/alex").json()
    print(f"\nalex has {len(holds)} hold(s): " + ", ".join(f"{x['transfer']['payee_id']} ${x['transfer']['amount']:.0f} "
                                                    f"(release in {x['release_in']} s)" for x in holds))
    print("\nALL PASS\n" if ok else "\nSOME CASES FAILED (was the cloud fresh?)\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
