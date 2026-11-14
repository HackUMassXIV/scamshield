"""Edge service (runs on your laptop, behind a tunnel for Twilio).

  uvicorn edge.main:app --host 0.0.0.0 --port 8000

Endpoints
  POST /twilio/voice       Guardian leg answers (Twilio "A call comes in")
  POST /twilio/status      call status changes (Twilio "Call status changes")
  POST /twilio/bridge      BACKUP mode: caller rings Twilio, we forward to the student
  WS   /ws/twilio          Twilio Media Streams
  POST /api/screen/start   PWA: "I've merged, start screening"
  POST /api/screen/stop    PWA: stop and hang up the Guardian leg
  POST /api/flag           PWA: "I'm suspicious" (floor = YELLOW, tells the cloud)
  GET  /api/sessions       live sessions + latency stats
  WS   /ws/ui              live events for the PWA (transcripts, state)
  WS   /ws/audio           raw 16 kHz PCM16 from the mic / replay tools (no Twilio)
  GET  /api/app-config     what the PWA needs (users, Guardian number, features)
  *    /cloud/{path}       proxy to the cloud API (adds X-Cloud-Key server-side)
  WS   /cloud-ws           proxy to the cloud websocket
  GET  /                   the built PWA (app/dist), if present
  GET  /health
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Optional

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from shared.contracts import StartBody
from shared.spa import mount_spa

from .config import Settings, load_settings
from .runtime import Runtime

log = logging.getLogger("guardian.main")
XML = "text/xml"
TERMINAL = {"completed", "failed", "busy", "no-answer", "canceled"}


def create_app(
    settings: Optional[Settings] = None,
    *,
    asr_backend: Any = None,
    gateway_client: Any = None,
    classifier: Any = None,
) -> FastAPI:
    load_dotenv()
    s = settings or load_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt = Runtime(s, asr_backend=asr_backend, gateway_client=gateway_client, classifier=classifier)
        app.state.rt = rt
        await rt.startup()
        yield
        await rt.shutdown()

    app = FastAPI(title="ScamShield Guardian (edge)", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(s.allowed_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def rt_of(obj) -> Runtime:
        return obj.app.state.rt

    def require_key(request: Request) -> None:
        key = rt_of(request).settings.api_key
        if key and request.headers.get("x-guardian-key") != key:
            raise HTTPException(401, "bad or missing X-Guardian-Key")

    def ws_key_ok(ws: WebSocket, key: str) -> bool:
        expected = ws.app.state.rt.settings.api_key
        return not expected or key == expected

    async def signed_form(request: Request) -> dict:
        """Parse a Twilio webhook and verify its signature."""
        rt = rt_of(request)
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        urls = rt.gateway.candidate_urls(request.url.path, request.url.query, dict(request.headers))
        if not rt.gateway.validate(urls, params, request.headers.get("X-Twilio-Signature")):
            log.warning("rejected webhook with bad signature (tried %s)", urls)
            raise HTTPException(403, "invalid Twilio signature")
        return params

    # ------------------------------------------------------------------ health
    @app.get("/health")
    async def health(request: Request):
        rt = rt_of(request)
        return {
            "ok": True,
            "asr": rt.asr.name,
            "vad": rt.vad_name,
            "sessions": len(rt.sessions.all()),
            "public_base_url": rt.settings.public_base_url,
            "twilio_validate": rt.settings.twilio_validate,
            "twilio_dry_run": rt.settings.twilio_dry_run,
            "users": [u.id for u in rt.users.all()],
            "classifier": rt.clf.name,
            "cloud": rt.settings.cloud_url or None,
        }

    # ---------------------------------------------------------- Twilio webhooks
    @app.post("/twilio/voice")
    async def twilio_voice(request: Request):
        """The student merged the Guardian number into their call. Answer silently:
        NO audio is streamed until the student presses Start and the disclosure plays."""
        rt = rt_of(request)
        params = await signed_form(request)
        user = rt.users.by_phone(params.get("From"))
        if not user and rt.settings.demo_fallback_user:
            user = rt.users.by_id(rt.settings.demo_fallback_user)
        if not user:
            log.warning("unregistered caller on Guardian line")
            return Response(rt.gateway.reject_twiml("This number is not registered with Guardian."), media_type=XML)
        await rt.sessions.create(user, params.get("CallSid"))
        return Response(rt.gateway.leg_twiml(), media_type=XML)

    @app.post("/twilio/bridge")
    async def twilio_bridge(request: Request, user: str = ""):
        """BACKUP mode. Point a Twilio number at /twilio/bridge?user=alex. The caller
        hears the disclosure first; both sides stream as separate tracks."""
        rt = rt_of(request)
        params = await signed_form(request)
        u = rt.users.by_id(user)
        if not u:
            return Response(rt.gateway.reject_twiml("Unknown Guardian user."), media_type=XML)
        sess = await rt.sessions.create(u, params.get("CallSid"))
        await sess.request_screening()
        return Response(rt.gateway.bridge_screen_twiml(sess.id, u.phone), media_type=XML)

    @app.post("/twilio/status")
    async def twilio_status(request: Request):
        rt = rt_of(request)
        params = await signed_form(request)
        sess = rt.sessions.by_call_sid(params.get("CallSid"))
        status = params.get("CallStatus", "").lower()
        if sess and status in TERMINAL:
            await sess.finish("call_" + status)
        return Response(status_code=204)

    # ----------------------------------------------------------- app-facing API
    @app.post("/api/screen/start")
    async def screen_start(body: StartBody, request: Request):
        """PWA: "I've merged, start screening". Tells Twilio to play the disclosure
        into the merged call and then open the media stream."""
        require_key(request)
        rt = rt_of(request)
        sess = rt.sessions.active(body.user_id)
        if not sess:
            raise HTTPException(
                409, "No Guardian leg is connected. Add the Guardian number to your call and tap Merge first."
            )
        if sess.state == "leg_connected":
            try:
                await rt.gateway.update_call(sess.call_sid, twiml=rt.gateway.screen_twiml(sess.id))
            except Exception as exc:
                log.exception("Twilio call update failed")
                raise HTTPException(502, f"Twilio could not update the call: {exc}") from exc
            await sess.request_screening()
        return {"call_id": sess.id, "state": sess.state}

    @app.post("/api/screen/stop")
    async def screen_stop(body: StartBody, request: Request):
        require_key(request)
        rt = rt_of(request)
        sess = rt.sessions.active(body.user_id)
        if not sess:
            return {"state": "none"}
        await rt.gateway.hangup(sess.call_sid)
        await sess.finish("user_stop")
        return {"state": "ended"}

    @app.post("/api/flag")
    async def flag(body: StartBody, request: Request):
        """PWA: "I'm suspicious" button. Works with or without a screened call:
        keeps the color at least YELLOW for FLAG_SECONDS and tells the cloud, which
        adds it to the firewall score for payments in that window."""
        require_key(request)
        rt = rt_of(request)
        if not rt.users.by_id(body.user_id):
            raise HTTPException(404, "unknown user")
        secs = rt.settings.flag_seconds
        until = rt.clock() + secs
        rt.flags[body.user_id] = until
        await rt.hub.publish(body.user_id, "user_flag", until=until)
        await rt.risk.on_flag(body.user_id)
        rt.cloud.send_flag(body.user_id, secs)
        return {"flagged_until": until}

    @app.get("/api/sessions")
    async def sessions(request: Request):
        require_key(request)
        rt = rt_of(request)
        return [
            {"call_id": x.id, "user": x.user.id, "state": x.state, "mode": x.mode, **x.summary()}
            for x in rt.sessions.all()
        ]

    # --------------------------------------------------------------- websockets
    @app.websocket("/ws/ui")
    async def ws_ui(ws: WebSocket, user_id: str = "", key: str = ""):
        rt = ws.app.state.rt
        if not ws_key_ok(ws, key) or not rt.users.by_id(user_id):
            await ws.close(code=1008)
            return
        await rt.hub.connect(user_id, ws)
        try:
            while True:
                await ws.receive_text()  # keep-alive; the app sends nothing
        except WebSocketDisconnect:
            pass
        finally:
            rt.hub.disconnect(user_id, ws)

    @app.websocket("/ws/twilio")
    async def ws_twilio(ws: WebSocket):
        """Twilio Media Streams. The stream is bound to a session by the unguessable
        call_id we put in the TwiML <Parameter>; Twilio echoes it in `start`."""
        rt = ws.app.state.rt
        await ws.accept()
        sess = None
        clean = False
        try:
            while True:
                m = json.loads(await ws.receive_text())
                ev = m.get("event")
                if ev == "start":
                    st = m.get("start", {})
                    cand = rt.sessions.get((st.get("customParameters") or {}).get("call_id"))
                    if not cand or not cand.accepts_stream(st.get("callSid")):
                        log.warning("rejecting media stream with unknown/invalid call_id")
                        await ws.close(code=1008)
                        return
                    sess = cand
                    await sess.bind_stream(st.get("streamSid"), st.get("tracks") or ["inbound"])
                elif ev == "media" and sess:
                    media = m.get("media", {})
                    sess.push_mulaw(media.get("track", "inbound"), media.get("payload", ""))
                elif ev == "stop":
                    clean = True
                    break
                # "connected", "mark", "dtmf" need no handling
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("media websocket error")
        finally:
            if sess:
                await sess.detach_stream(clean)

    @app.websocket("/ws/audio")
    async def ws_audio(ws: WebSocket, user_id: str = "", key: str = "", speaker: str = "mixed"):
        """Local audio source (mic / replay tools): binary frames of 16 kHz mono
        PCM16. Goes through exactly the same VAD -> ASR -> redact pipeline."""
        rt = ws.app.state.rt
        user = rt.users.by_id(user_id)
        if not ws_key_ok(ws, key) or not user:
            await ws.close(code=1008)
            return
        await ws.accept()
        sess = await rt.sessions.create(user, None)
        await sess.start_local()
        await ws.send_json({"call_id": sess.id})
        spk = speaker if speaker in {"mixed", "caller", "student"} else "mixed"
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    pcm = np.frombuffer(msg["bytes"], dtype=np.int16)
                    sess.push_audio(spk, pcm.astype(np.float32) / 32768.0)
                elif msg.get("text") == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await sess.finish("local_stop")
            try:
                await ws.send_json({"ended": True, "summary": sess.summary()})
                await ws.close()
            except Exception:
                pass

    # ------------------------------------------------- PWA support (section 9)
    @app.get("/api/app-config")
    async def app_config(request: Request):
        rt = rt_of(request)
        return {
            "users": [{"id": u.id, "name": u.name, "has_trusted_contact": bool(u.trusted_contact)} for u in rt.users.all()],
            "guardian_number": rt.settings.twilio_number,
            "needs_key": bool(rt.settings.api_key),
            "cloud": bool(rt.settings.cloud_url),
            "llm": rt.settings.ollama_model if rt.llm.enabled else None,
            "classifier": rt.clf.name,
            "asr": rt.asr.name,
            "alerts": rt.settings.alert_channel if rt.alerts.enabled else None,
        }

    def _cloud_headers(rt) -> dict:
        h = {"content-type": "application/json"}
        if rt.settings.cloud_api_key:
            h["X-Cloud-Key"] = rt.settings.cloud_api_key
        return h

    @app.api_route("/cloud/{path:path}", methods=["GET", "POST"])
    async def cloud_proxy(path: str, request: Request):
        """The phone only reaches the laptop (through the tunnel); the edge forwards
        bank/hold/map calls to the cloud and adds the cloud key itself."""
        require_key(request)
        rt = rt_of(request)
        if not rt.settings.cloud_url:
            raise HTTPException(503, "CLOUD_URL is not set on the edge")
        import httpx

        url = f"{rt.settings.cloud_url.rstrip('/')}/{path}"
        async with httpx.AsyncClient(timeout=20) as c:
            try:
                r = await c.request(request.method, url, params=dict(request.query_params),
                                    content=await request.body(), headers=_cloud_headers(rt))
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"cloud unreachable: {exc.__class__.__name__}")
        return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))

    @app.websocket("/cloud-ws")
    async def cloud_ws(ws: WebSocket, user_id: str = "", key: str = ""):
        rt = ws.app.state.rt
        if not ws_key_ok(ws, key) or not rt.settings.cloud_url:
            await ws.close(code=1008)
            return
        import asyncio

        import websockets

        base = rt.settings.cloud_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        upstream_url = f"{base}/ws/cloud?user_id={user_id}&key={rt.settings.cloud_api_key or ''}"
        await ws.accept()
        try:
            async with websockets.connect(upstream_url) as up:
                async def down():
                    async for msg in up:
                        await ws.send_text(msg if isinstance(msg, str) else msg.decode())

                async def upward():
                    while True:
                        await up.send(await ws.receive_text())

                tasks = [asyncio.create_task(down()), asyncio.create_task(upward())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in tasks:
                    t.cancel()
        except Exception as exc:
            log.info("cloud-ws closed: %s", exc.__class__.__name__)
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    dist = Path(s.app_dist) if s.app_dist else Path(__file__).resolve().parent.parent / "app" / "dist"
    if dist.is_dir():
        mount_spa(app, dist)
        log.info("serving the PWA from %s", dist)
    return app


app = create_app()
