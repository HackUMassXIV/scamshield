"""Cloud API (Vultr later; runs on the laptop for now).

  uvicorn cloud.main:app --port 9000

Receives ONLY redacted RiskEvents from the edge. Holds the shield state per
student and sits in front of the bank (Nessie): every transfer goes through
`evaluate()` first.

  POST /api/risk-event        edge -> shield state
  POST /api/session-end       edge: screened call finished
  POST /api/flag              "I'm suspicious"
  GET  /api/shield/{user}
  POST /api/transfer          bank page "Send money"  (?confirm=1 passes a WARN)
  GET  /api/holds/{user}
  POST /api/holds/{id}/release   after cool-down, body {"confirm": "RELEASE"}
  POST /api/holds/{id}/cancel
  GET  /api/account/{user}    balance, payees, recent transfers (bank page)
  POST /api/second-opinion    edge: borderline call -> Gemini (redacted last 90 s)
  POST /api/reports           scam map report      GET /api/reports/map
  GET  /api/push/key          POST /api/push/subscribe   (Web Push, optional)
  WS   /ws/cloud?user_id=&key=   shield / hold / transfer / second_opinion events
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared.contracts import Hold, RiskEvent, Transfer
from shared.spa import mount_spa

from .bank import Bank, make_bank
from .config import CloudSettings, load_cloud_settings
from .firewall import evaluate
from .gemini import Gemini
from .push import Push
from .reports import ReportIn, ReportStore
from .shield import ShieldStore
from .store import Store, make_store

log = logging.getLogger("cloud")


class SessionEnd(BaseModel):
    user_id: str
    call_id: str
    max_level: str = "GREEN"


class FlagBody(BaseModel):
    user_id: str
    seconds: int = 600


class ReleaseBody(BaseModel):
    confirm: str = ""


class SecondOpinionBody(BaseModel):
    user_id: str
    call_id: str
    local_llm: Optional[float] = None
    local_clf: float = 0.0
    scam_type: str = "benign"
    context: str = ""  # REDACTED last ~90 s


class PushSub(BaseModel):
    user_id: str
    subscription: dict


class CloudState:
    def __init__(self, s: CloudSettings, bank: Bank, store: Store, clock=time.time):
        self.s = s
        self.bank = bank
        self.store = store
        self.clock = clock
        self.shield = ShieldStore(clock)
        self.sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self.log: list[dict] = []  # recent events (ops view / tests)
        self.reports = ReportStore(seed=s.seed_reports, clock=clock)
        self.gemini = Gemini(s.gemini_api_key, s.gemini_model)
        self.push_svc = Push(s.vapid_public_key, s.vapid_private_key, s.vapid_subject)
        self.second: dict[str, dict] = {}  # call_id -> latest second opinion
        self._so_last: dict[str, float] = {}
        self.tasks: set[asyncio.Task] = set()

    async def push(self, user_id: str, typ: str, /, **data: Any) -> None:
        ev = {**data, "type": typ, "user_id": user_id, "ts": self.clock()}
        self.log.append(ev)
        del self.log[:-500]
        for ws in list(self.sockets[user_id]):
            try:
                await asyncio.wait_for(ws.send_json(ev), timeout=1.0)
            except Exception:
                self.sockets[user_id].discard(ws)


async def sync_payees(cs: "CloudState") -> None:
    """payees collection <-> bank: seed it from the bank's known payees once, then
    the collection is the source of truth for who a student has paid before."""
    for uid in getattr(cs.bank, "acc", {}):
        for p in cs.bank.payees(uid):
            await cs.store.upsert_payee(uid, p.id, p.name, p.known, only_if_new=True)
        known = await cs.store.known_payees(uid)
        for p in cs.bank.payees(uid):
            p.known = p.id in known


def create_cloud_app(settings: Optional[CloudSettings] = None, *, bank: Optional[Bank] = None,
                     store: Optional[Store] = None, clock=time.time, gemini: Optional[Gemini] = None) -> FastAPI:
    load_dotenv()
    s = settings or load_cloud_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.cs = CloudState(s, bank or make_bank(s), store or make_store(s.mongodb_uri, s.mongo_db), clock)
        if gemini is not None:
            app.state.cs.gemini = gemini
        await sync_payees(app.state.cs)
        log.info("cloud ready: bank=%s store=%s cooldown=%ss gemini=%s push=%s", app.state.cs.bank.name,
                 app.state.cs.store.name, s.cooldown_sec, "on" if app.state.cs.gemini.enabled else "off",
                 "on" if app.state.cs.push_svc.enabled else "off")
        yield
        if app.state.cs.tasks:
            await asyncio.wait(list(app.state.cs.tasks), timeout=5)
        await app.state.cs.gemini.close()

    app = FastAPI(title="ScamShield cloud", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=list(s.allowed_origins), allow_methods=["*"], allow_headers=["*"])

    def cs_of(req) -> CloudState:
        return req.app.state.cs

    def auth(request: Request) -> CloudState:
        cs = cs_of(request)
        if cs.s.api_key and request.headers.get("x-cloud-key") != cs.s.api_key:
            raise HTTPException(401, "bad or missing X-Cloud-Key")
        return cs

    def hold_view(h: Hold, now: float) -> dict:
        return {**h.model_dump(), "release_in": max(0, round(h.release_after - now))}

    @app.get("/health")
    async def health(request: Request):
        cs = cs_of(request)
        return {"ok": True, "bank": cs.bank.name, "store": cs.store.name, "cooldown_sec": cs.s.cooldown_sec}

    # ------------------------------------------------------------- from the edge
    @app.post("/api/risk-event")
    async def risk_event(ev: RiskEvent, request: Request):
        cs = auth(request)
        prev = cs.shield.get(ev.user_id)
        was_red = prev.level == "RED" and prev.call_id == ev.call_id
        st = cs.shield.update(ev)
        await cs.store.log_risk(ev.model_dump())  # already redacted on the edge
        await cs.push(ev.user_id, "shield", **st.public())
        if st.level == "RED" and not was_red:
            await cs.push_svc.notify(ev.user_id, "Possible scam call", "Don't send money. Open ScamShield for what to say.")
        return {"ok": True, "level": st.level}

    @app.post("/api/session-end")
    async def session_end(body: SessionEnd, request: Request):
        cs = auth(request)
        st = cs.shield.end_session(body.user_id, body.call_id)
        await cs.push(body.user_id, "shield", **st.public())
        return {"ok": True}

    @app.post("/api/flag")
    async def flag(body: FlagBody, request: Request):
        cs = auth(request)
        st = cs.shield.flag(body.user_id, body.seconds)
        await cs.push(body.user_id, "shield", **st.public())
        return {"ok": True, "flag_until": st.flag_until}

    @app.get("/api/shield/{user_id}")
    async def shield(user_id: str, request: Request):
        return auth(request).shield.get(user_id).public()

    # ---------------------------------------------------------------- the money
    @app.post("/api/transfer")
    async def transfer(t: Transfer, request: Request, confirm: bool = False):
        cs = auth(request)
        if t.amount <= 0:
            raise HTTPException(422, "amount must be positive")
        try:
            await cs.bank.balance(t.user_id)
        except KeyError:
            raise HTTPException(404, "unknown user")
        now = cs.clock()
        d = await evaluate(t, cs.shield.get(t.user_id), cs.bank, now, cs.s.shield_window_sec)
        if d.status == "HOLD":
            h = Hold(transfer=t, reasons=d.reasons, points=d.points, created=now, release_after=now + cs.s.cooldown_sec)
            await cs.store.save_hold(h)
            await cs.push(t.user_id, "hold_created", hold=hold_view(h, now))
            await cs.push_svc.notify(t.user_id, "Payment held", f"${t.amount:,.0f} is on hold: " + d.reasons[0], "/bank")
            log.info("HELD %s -> %s $%.2f (%s pts): %s", t.user_id, t.payee_id, t.amount, d.points, "; ".join(d.reasons))
            return {"status": "HELD", "hold": hold_view(h, now), "reasons": d.reasons, "points": d.points}
        if d.status == "WARN" and not confirm:
            return {"status": "WARN", "reasons": d.reasons, "points": d.points,
                    "message": "Please double-check. Send again with confirm=1 to continue."}
        return await _execute(cs, t, d.reasons, d.points)

    async def _execute(cs: CloudState, t: Transfer, reasons: list[str], points: float) -> dict:
        try:
            rec = await cs.bank.transfer(t.user_id, t.payee_id, t.amount, t.memo)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"bank error: {exc}")
        cs.bank.mark_known(t.user_id, t.payee_id)
        p = cs.bank.payee(t.user_id, t.payee_id)
        await cs.store.upsert_payee(t.user_id, t.payee_id, p.name if p else t.payee_id, True)
        bal = await cs.bank.balance(t.user_id)
        await cs.push(t.user_id, "transfer_ok", payee_id=t.payee_id, amount=t.amount, balance=bal)
        return {"status": "OK", "reasons": reasons, "points": points, "balance": bal, "bank_ref": rec.nessie_id}

    @app.get("/api/holds/{user_id}")
    async def holds(user_id: str, request: Request):
        cs = auth(request)
        now = cs.clock()
        return [hold_view(h, now) for h in await cs.store.holds_for(user_id)]

    @app.post("/api/holds/{hold_id}/release")
    async def release(hold_id: str, body: ReleaseBody, request: Request):
        cs = auth(request)
        h = await cs.store.get_hold(hold_id) or _404()
        now = cs.clock()
        if h.status != "HELD":
            raise HTTPException(409, f"hold is {h.status}")
        if now < h.release_after:
            raise HTTPException(425, f"cool-down: you can release this in {round(h.release_after - now)} s")
        if body.confirm.strip().upper() != "RELEASE":
            raise HTTPException(422, 'type RELEASE to confirm you checked this is not a scam')
        res = await _execute(cs, h.transfer, h.reasons, h.points)
        h.status = "RELEASED"
        await cs.store.save_hold(h)
        await cs.push(h.transfer.user_id, "hold_released", hold=hold_view(h, now))
        return {**res, "hold": hold_view(h, now)}

    @app.post("/api/holds/{hold_id}/cancel")
    async def cancel(hold_id: str, request: Request):
        cs = auth(request)
        h = await cs.store.get_hold(hold_id) or _404()
        if h.status == "HELD":
            h.status = "CANCELLED"
            await cs.store.save_hold(h)
            await cs.push(h.transfer.user_id, "hold_cancelled", hold=hold_view(h, cs.clock()))
        return hold_view(h, cs.clock())

    @app.get("/api/account/{user_id}")
    async def account(user_id: str, request: Request):
        cs = auth(request)
        try:
            bal = await cs.bank.balance(user_id)
        except KeyError:
            raise HTTPException(404, "unknown user")
        return {"balance": bal,
                "payees": [p.__dict__ for p in cs.bank.payees(user_id)],
                "recent": [t.__dict__ for t in cs.bank.recent_transfers(user_id)]}

    # ------------------------------------------------- section 11: Gemini second opinion
    @app.post("/api/second-opinion")
    async def second_opinion(body: SecondOpinionBody, request: Request):
        cs = auth(request)
        now = cs.clock()
        if now - cs._so_last.get(body.call_id, -1e9) < 20:
            return {"queued": False, "reason": "rate-limited"}
        cs._so_last[body.call_id] = now
        local = body.local_llm if body.local_llm is not None else body.local_clf

        async def run() -> None:
            res: dict = {"call_id": body.call_id, "local": round(local, 3), "local_clf": body.local_clf,
                         "local_llm": body.local_llm, "local_type": body.scam_type}
            try:
                g = await cs.gemini.opinion(body.context) if cs.gemini.enabled else None
                res.update({"gemini": g["score"], "gemini_type": g["scam_type"], "reason": g["reason"],
                            "red_flags": g["red_flags"], "ms": g["ms"], "status": "ok"} if g else
                           {"gemini": None, "status": "no GEMINI_API_KEY"})
            except Exception as exc:
                res.update({"gemini": None, "status": f"error: {exc.__class__.__name__}"})
            cs.second[body.call_id] = res
            await cs.push(body.user_id, "second_opinion", **res)

        t = asyncio.create_task(run())
        cs.tasks.add(t)
        t.add_done_callback(cs.tasks.discard)
        return {"queued": True}

    # ------------------------------------------------------------- scam map
    @app.post("/api/reports")
    async def add_report(rep: ReportIn, request: Request):
        cs = auth(request)
        try:
            item = cs.reports.add(rep)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        for uid in list(cs.sockets):
            await cs.push(uid, "report", report=item.model_dump(exclude={"user_id"}))
        return item.model_dump(exclude={"user_id"})

    @app.get("/api/reports/map")
    async def report_map(request: Request, days: float = 14):
        return auth(request).reports.map(days)

    # ------------------------------------------------------------- web push
    @app.get("/api/push/key")
    async def push_key(request: Request):
        cs = auth(request)
        return {"enabled": cs.push_svc.enabled, "public_key": cs.s.vapid_public_key}

    @app.post("/api/push/subscribe")
    async def push_subscribe(body: PushSub, request: Request):
        cs = auth(request)
        return {"subscriptions": cs.push_svc.subscribe(body.user_id, body.subscription), "enabled": cs.push_svc.enabled}

    @app.websocket("/ws/cloud")
    async def ws_cloud(ws: WebSocket, user_id: str = "", key: str = ""):
        cs = ws.app.state.cs
        if (cs.s.api_key and key != cs.s.api_key) or not user_id:
            await ws.close(code=1008)
            return
        await ws.accept()
        cs.sockets[user_id].add(ws)
        await ws.send_json({"type": "shield", **cs.shield.get(user_id).public(), "replay": True})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            cs.sockets[user_id].discard(ws)

    if s.static_dir and Path(s.static_dir).is_dir():
        mount_spa(app, Path(s.static_dir))
    return app


def _404():
    raise HTTPException(404, "not found")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_cloud_app()
