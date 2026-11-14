"""MongoDB persistence (mongomock stands in for Atlas): holds, payees, riskEvents
survive a cloud restart."""
import pytest
from fastapi.testclient import TestClient

from cloud.bank import FakeBank
from cloud.config import CloudSettings
from cloud.main import create_cloud_app
from cloud.store import MongoStore

mongomock = pytest.importorskip("mongomock")
H = {"X-Cloud-Key": "ck"}


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def app_with(client, clock):
    return create_cloud_app(CloudSettings(api_key="ck", cooldown_sec=60), bank=FakeBank(clock=clock),
                            store=MongoStore(client=client), clock=clock)


def test_holds_payees_and_risk_events_persist_across_restart():
    client, clock = mongomock.MongoClient(), Clock()
    with TestClient(app_with(client, clock)) as c:
        assert c.get("/health").json()["store"] == "mongo"
        # paying a new friend (after the WARN) makes them a known payee
        ok = c.post("/api/transfer?confirm=1", headers=H, json={"user_id": "alex", "payee_id": "newfriend", "amount": 20}).json()
        assert ok["status"] == "OK"
        c.post("/api/risk-event", headers=H, json={"call_id": "c1", "user_id": "alex", "level": "RED", "score": 90,
                                                   "scam_type": "irs_gov", "red_flags": ["gift cards"], "features": {}})
        hold = c.post("/api/transfer", headers=H, json={"user_id": "alex", "payee_id": "giftcarddepot", "amount": 600}).json()["hold"]

    db = client["scamshield"]
    assert db.holds.find_one({"_id": hold["id"]})["status"] == "HELD"
    assert db.riskEvents.count_documents({"user_id": "alex", "level": "RED"}) == 1
    assert db.payees.find_one({"user_id": "alex", "payee_id": "landlord"})["known"] is True  # seeded
    assert db.payees.find_one({"user_id": "alex", "payee_id": "giftcarddepot"})["known"] is False
    assert db.payees.find_one({"user_id": "alex", "payee_id": "newfriend"})["known"] is True  # learned

    clock.t += 3700  # an hour later: the RED call no longer weighs on new payments
    with TestClient(app_with(client, clock)) as c:  # cloud restarted: fresh bank, same database
        assert [h["status"] for h in c.get("/api/holds/alex", headers=H).json()] == ["HELD"]
        r = c.post("/api/transfer", headers=H, json={"user_id": "alex", "payee_id": "newfriend", "amount": 15}).json()
        assert r["status"] == "OK"  # still a known payee after restart, no "first payment" warning
        rel = c.post(f"/api/holds/{hold['id']}/release", headers=H, json={"confirm": "RELEASE"}).json()
        assert rel["status"] == "OK" and rel["hold"]["status"] == "RELEASED"
    assert db.holds.find_one({"_id": hold["id"]})["status"] == "RELEASED"
