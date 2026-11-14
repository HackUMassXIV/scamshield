"""Payment firewall + cloud API (in-memory bank, controllable clock)."""
import pytest
from fastapi.testclient import TestClient

from cloud.bank import FakeBank
from cloud.config import CloudSettings
from cloud.main import create_cloud_app

KEY = "ck"
H = {"X-Cloud-Key": KEY}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    clock = Clock()
    app = create_cloud_app(CloudSettings(api_key=KEY, cooldown_sec=60), bank=FakeBank(clock=clock), clock=clock)
    with TestClient(app) as c:
        yield c, clock, app


def risk(c, level, scam="immigration_uscis", call="call1"):
    ev = {"call_id": call, "user_id": "alex", "level": level, "score": {"GREEN": 10, "YELLOW": 55, "RED": 85}[level],
          "scam_type": scam, "red_flags": ["gift cards"], "features": {}}
    assert c.post("/api/risk-event", json=ev, headers=H).status_code == 200


def send(c, payee, amount, memo="", confirm=False):
    url = "/api/transfer" + ("?confirm=1" if confirm else "")
    return c.post(url, json={"user_id": "alex", "payee_id": payee, "amount": amount, "memo": memo}, headers=H).json()


def test_auth_required(env):
    c, *_ = env
    assert c.post("/api/transfer", json={"user_id": "alex", "payee_id": "landlord", "amount": 1}).status_code == 401


def test_rent_on_a_quiet_day_is_ok(env):
    c, *_ = env
    r = send(c, "landlord", 900, "December rent")
    assert r["status"] == "OK" and r["balance"] == 900


def test_m1_scam_call_then_gift_card_payment_is_held(env):
    c, clock, _ = env
    risk(c, "YELLOW")
    risk(c, "RED")
    clock.t += 120
    r = send(c, "giftcarddepot", 600, "USCIS fee")
    assert r["status"] == "HELD"
    text = " | ".join(r["reasons"])
    assert "Scam call flagged 2 min ago (Immigration / visa threat)" in text
    assert "First payment to GiftCardDepot LLC" in text
    assert "Gift card" in text and "far above your typical" in text
    assert r["points"] >= 7
    assert c.get("/api/account/alex", headers=H).json()["balance"] == 1800  # no money moved


def test_same_payment_without_a_call_is_still_held(env):
    c, *_ = env
    assert send(c, "giftcarddepot", 600)["status"] == "HELD"  # new payee + unusual + gift cards


def test_small_roommate_payment_during_benign_call_is_ok(env):
    c, *_ = env
    risk(c, "GREEN", scam="benign")
    assert send(c, "roommate", 40, "groceries")["status"] == "OK"


def test_extra_digit_typo_is_held(env):
    c, *_ = env
    r = send(c, "roommate", 350, "internet")  # last payment was 35
    assert r["status"] == "HELD" and any("10x your last payment" in x for x in r["reasons"])


def test_duplicate_is_warned_and_confirm_passes(env):
    c, clock, _ = env
    assert send(c, "roommate", 40)["status"] == "OK"
    clock.t += 30
    r = send(c, "roommate", 40)
    assert r["status"] == "WARN" and any("last 5 minutes" in x for x in r["reasons"])
    assert send(c, "roommate", 40, confirm=True)["status"] == "OK"


def test_new_small_payee_gets_a_warning_not_a_hold(env):
    c, *_ = env
    r = send(c, "newfriend", 25, "pizza")
    assert r["status"] == "WARN"
    assert send(c, "newfriend", 25, "pizza", confirm=True)["status"] == "OK"
    assert send(c, "newfriend", 20, "coffee")["status"] == "OK"  # now a known payee


def test_red_effect_expires_after_an_hour(env):
    c, clock, _ = env
    risk(c, "RED")
    c.post("/api/session-end", json={"user_id": "alex", "call_id": "call1"}, headers=H)
    clock.t += 3700
    assert send(c, "roommate", 40)["status"] == "OK"


def test_im_suspicious_flag_adds_points(env):
    c, *_ = env
    c.post("/api/flag", json={"user_id": "alex", "seconds": 600}, headers=H)
    r = send(c, "bursar", 250, "parking")
    assert r["status"] == "WARN" and "You marked a call as suspicious" in r["reasons"]


def test_hold_release_needs_cooldown_and_typed_confirmation(env):
    c, clock, _ = env
    risk(c, "RED")
    hold = send(c, "giftcarddepot", 600)["hold"]
    url = f"/api/holds/{hold['id']}/release"
    assert c.post(url, json={"confirm": "RELEASE"}, headers=H).status_code == 425  # cool-down
    clock.t += 61
    assert c.post(url, json={"confirm": "yes"}, headers=H).status_code == 422
    r = c.post(url, json={"confirm": "release"}, headers=H).json()
    assert r["status"] == "OK" and r["balance"] == 1200 and r["hold"]["status"] == "RELEASED"
    assert c.post(url, json={"confirm": "RELEASE"}, headers=H).status_code == 409


def test_cancel_and_list_holds(env):
    c, *_ = env
    hold = send(c, "giftcarddepot", 600)["hold"]
    assert c.post(f"/api/holds/{hold['id']}/cancel", headers=H).json()["status"] == "CANCELLED"
    assert [h["status"] for h in c.get("/api/holds/alex", headers=H).json()] == ["CANCELLED"]


def test_websocket_pushes_shield_and_holds(env):
    c, *_ = env
    with c.websocket_connect(f"/ws/cloud?user_id=alex&key={KEY}") as ws:
        assert ws.receive_json()["replay"] is True
        risk(c, "RED")
        assert ws.receive_json()["level"] == "RED"
        send(c, "giftcarddepot", 600)
        assert ws.receive_json()["type"] == "hold_created"
