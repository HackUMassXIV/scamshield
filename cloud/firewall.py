"""The payment firewall: should this transfer go through, get a warning, or be HELD?

Points (HOLD >= 3, WARN >= 1.5, else OK):
  +3    scam call reached RED in the last hour           (from the edge's RiskEvents)
  +1.5  call reached YELLOW in the last hour
  +1    payment made during a screened call
  +1.5  student pressed "I'm suspicious" recently
  +2    first payment to this payee
  +1.5  amount far above this student's usual (robust z > 3.5), unless it is
        in line with what they normally pay THIS payee (rent stays OK)
  +1    over half the balance
  +2    gift card / crypto / wire-service destination or memo
  +2    8x or more the last payment to this payee (extra-digit typo)
  +2    same amount to the same payee within 5 minutes (duplicate)

It catches scams AND honest mistakes, and it never blocks outright: a hold can
be released after a cool-down plus a typed confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from shared.contracts import Transfer
from shared.labels import PRETTY

from .bank import Bank
from .shield import ShieldState

RISKY = ("gift", "itunes", "google play", "steam", "bitcoin", "crypto", "coinbase",
         "western union", "moneygram", "usdt", "binance")
HOLD_AT, WARN_AT = 3.0, 1.5


@dataclass
class Decision:
    status: str  # OK | WARN | HOLD
    points: float
    reasons: list[str] = field(default_factory=list)


async def evaluate(t: Transfer, st: ShieldState, bank: Bank, now: float, window_s: int = 3600) -> Decision:
    r: list[tuple[float, str]] = []
    pretty = PRETTY.get(st.red_scam_type or st.scam_type, st.scam_type)
    if st.last_red_at and now - st.last_red_at < window_s:
        mins = int((now - st.last_red_at) // 60)
        r.append((3.0, f"Scam call flagged {'just now' if mins == 0 else f'{mins} min ago'} ({pretty})"))
    elif st.last_yellow_at and now - st.last_yellow_at < window_s:
        r.append((1.5, "Suspicious call in the last hour"))
    elif st.session_active:
        r.append((1.0, "Payment during a screened call"))
    if st.flag_until > now:
        r.append((1.5, "You marked a call as suspicious"))

    payee = bank.payee(t.user_id, t.payee_id)
    name = payee.name if payee else t.payee_id
    if not payee or not payee.known:
        r.append((2.0, f"First payment to {name}"))

    hist = np.array(await bank.spend_history(t.user_id) or [0.0])  # (Nessie: also refreshes payee history)
    prev = bank.transfers_to(t.user_id, t.payee_id)
    usual_for_payee = bool(prev) and t.amount <= 1.5 * max(p.amount for p in prev)
    med = float(np.median(hist))
    mad = float(np.median(np.abs(hist - med)) * 1.4826) or 1.0
    if (t.amount - med) / mad > 3.5 and not usual_for_payee:
        r.append((1.5, f"${t.amount:,.0f} is far above your typical ${med:,.0f}"))
    bal = await bank.balance(t.user_id)
    if bal and t.amount > 0.5 * bal:
        r.append((1.0, f"Uses over half your balance (${bal:,.0f})"))
    text = f"{name} {t.memo}".lower()
    if any(w in text for w in RISKY):
        r.append((2.0, "Gift card / crypto / wire-service destination"))
    if prev and t.amount >= 8 * prev[-1].amount:
        r.append((2.0, f"{t.amount / prev[-1].amount:.0f}x your last payment to {name} (extra digit?)"))
    if any(abs(p.amount - t.amount) < 0.01 and now - p.ts < 300 for p in prev):
        r.append((2.0, "Same amount to the same payee in the last 5 minutes"))

    pts = round(sum(p for p, _ in r), 2)
    status = "HOLD" if pts >= HOLD_AT else "WARN" if pts >= WARN_AT else "OK"
    return Decision(status, pts, [x for _, x in r])
