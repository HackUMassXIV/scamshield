#!/usr/bin/env python3
"""ScamShield launcher: one command set for Windows, macOS and Linux.

    python run.py setup            one-time: venv, deps, .env, Whisper weights, tests
    python run.py doctor           check everything and say what is missing
    python run.py demo             no phones, no Twilio: server + simulated scam call
    python run.py tunnel           public HTTPS URL; writes PUBLIC_BASE_URL into .env
    python run.py run              edge server for real Twilio calls
    python run.py run --dry        server that logs Twilio updates instead of sending
    python run.py sim  [WAV]       simulated Twilio merge call into a running --dry server
    python run.py replay [WAV]     stream a WAV through the pipeline (no Twilio)
    python run.py mic              speakerphone fallback: stream the laptop mic
    python run.py listen           print what the app would show
    python run.py calls            synthesize demo call WAVs with the OS voice
    python run.py test             run the test suite

  Sections 7-8 (classifier, risk engine, cloud firewall):
    python run.py train            generate calls, build windows, train TF-IDF, evaluate (~1 min)
    python run.py eval             call-level report (held-out + hand-written scripts)
    python run.py cloud            cloud API on :9000 (shield state + payment firewall)
    python run.py m1               milestone M1: scam call -> RED -> $600 to GiftCardDepot HELD
    python run.py transfer PAYEE AMOUNT   send money through the firewall (cloud must be running)

  Sections 9-11 (app, coaching, LLM):
    python run.py app              build the PWA (needs Node 18+); the edge then serves it at /
    python run.py ui               PWA dev server with hot reload on :5173 (proxies to the edge)
    python run.py llm              pull the local LLM into Ollama and warm it up
    python run.py firewall         section 4 test cases against a fresh cloud (no audio needed)

Uses only the standard library, so it runs before `setup` has installed anything.
On Windows use `py run.py ...` if `python` is not on PATH.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
VPY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
ENV_FILE = ROOT / ".env"
CALLS = ROOT / "data" / "calls"
DEFAULT_WAV = CALLS / "scam_uscis.wav"
NO_COLOR = ["--no-use-colors"] if IS_WIN else []  # old Windows consoles print raw escape codes


# --------------------------------------------------------------------- helpers
def say(msg: str) -> None:
    print(f"[scamshield] {msg}", flush=True)


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV.resolve()


def reexec_in_venv() -> None:
    """Every command except setup runs with the project's own interpreter."""
    if in_venv():
        return
    if not VPY.exists():
        say("No virtual environment yet. Run:  python run.py setup")
        sys.exit(1)
    sys.exit(subprocess.call([str(VPY), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT))


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def set_env_value(key: str, value: str) -> None:
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    text = pat.sub(f"{key}={value}", text) if pat.search(text) else text + f"\n{key}={value}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_http(url: str, timeout: float = 120) -> bool:
    import urllib.request

    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def py(*args: str, env: dict | None = None) -> int:
    return subprocess.call([sys.executable, *args], cwd=ROOT, env=env)


def server_cmd(port: int) -> list[str]:
    return [sys.executable, "-m", "uvicorn", "edge.main:app", "--host", "0.0.0.0", "--port", str(port), *NO_COLOR]


# -------------------------------------------------------------------- commands
def cmd_setup(a) -> int:
    if sys.version_info < (3, 10):
        say(f"Python 3.10+ needed, you have {platform.python_version()}")
        return 1
    if not VPY.exists():
        say("creating .venv ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    say("installing dependencies (first time takes a few minutes: torch is large) ...")
    subprocess.check_call([str(VPY), "-m", "pip", "install", "-U", "pip"])
    subprocess.check_call([str(VPY), "-m", "pip", "install", "-r", str(ROOT / "requirements-dev.txt")])
    if not ENV_FILE.exists():
        shutil.copy(ROOT / ".env.example", ENV_FILE)
        say("created .env from .env.example: fill in your keys")
    model = read_env().get("WHISPER_MODEL", "base.en")
    say(f"downloading Whisper {model} now, so it is not fetched on venue Wi-Fi ...")
    subprocess.call([str(VPY), "-c",
                     f"from faster_whisper import WhisperModel; WhisperModel('{model}', device='cpu', compute_type='int8'); print('whisper ok')"])
    subprocess.call([str(VPY), "-c", "from silero_vad import load_silero_vad; load_silero_vad(); print('silero ok')"])
    if _npm():
        say("building the app ...")
        subprocess.call([sys.executable, str(Path(__file__).resolve()), "app"], cwd=ROOT)
    else:
        say("Node.js not found: skipping the app build (install Node, then: python run.py app)")
    say("running tests ...")
    rc = subprocess.call([str(VPY), "-m", "pytest", "-q"], cwd=ROOT)
    say("setup done. Next:  python run.py doctor   then   python run.py demo")
    return rc


def cmd_doctor(a) -> int:
    ok = True

    def check(cond: bool, good: str, bad: str, required: bool = True) -> None:
        nonlocal ok
        print(("  OK    " if cond else ("  FAIL  " if required else "  WARN  ")) + (good if cond else bad))
        if required and not cond:
            ok = False

    print(f"{platform.system()} {platform.release()} / Python {platform.python_version()}")
    check(sys.version_info >= (3, 10), "Python >= 3.10", "need Python 3.10+")
    for mod, req in (("fastapi", True), ("twilio", True), ("faster_whisper", True), ("sklearn", True),
                     ("silero_vad", False), ("sounddevice", False)):
        try:
            __import__(mod)
            check(True, f"{mod} installed", "")
        except Exception as e:  # sounddevice raises OSError when PortAudio is missing
            hint = {"sounddevice": "mic mode only. Linux: sudo apt install libportaudio2",
                    "silero_vad": "energy VAD fallback will be used"}.get(mod, "run: python run.py setup")
            check(False, "", f"{mod}: {e.__class__.__name__} ({hint})", req)
    env = read_env()
    check(ENV_FILE.exists(), ".env exists", ".env missing (python run.py setup)")
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "GUARDIAN_API_KEY"):
        v = env.get(k, "")
        check(bool(v) and "xxx" not in v and "your_" not in v and "change-me" not in v,
              f"{k} set", f"{k} not set in .env (needed for real calls, not for demo)", False)
    url = env.get("PUBLIC_BASE_URL", "")
    check(url.startswith("https://") and "your-tunnel" not in url,
          f"PUBLIC_BASE_URL = {url}", "PUBLIC_BASE_URL not set (python run.py tunnel writes it)", False)
    check(bool(shutil.which("cloudflared") or shutil.which("ngrok")), "tunnel tool found",
          "cloudflared/ngrok not found (needed for real calls; see README)", False)
    check(port_free(a.port), f"port {a.port} free", f"port {a.port} in use (another server running?)", False)
    check(bool(_npm()), "Node.js found", "Node.js not found (needed to build the app: nodejs.org)", False)
    check((ROOT / "app" / "dist" / "index.html").exists(), "app built (app/dist)", "app not built (python run.py app)", False)
    check(bool(shutil.which("ollama")), "Ollama found", "Ollama not found (optional local LLM: ollama.com)", False)
    check((ROOT / "models" / "clf_tfidf.joblib").exists(), "classifier trained", "classifier not trained (python run.py train)", False)
    users = ROOT / "edge" / "data" / "users.json"
    check("555010" not in users.read_text(), "users.json has real numbers",
          "users.json still has placeholder 555 numbers (fine for demo)", False)
    print("\nREADY" if ok else "\nfix the FAIL lines first")
    return 0 if ok else 1


def cmd_run(a) -> int:
    env = os.environ.copy()
    if a.dry:
        env["TWILIO_DRY_RUN"] = "1"
    if not port_free(a.port):
        say(f"port {a.port} is busy; use --port")
        return 1
    say(f"edge on http://localhost:{a.port}  (health: /health)  Ctrl+C to stop")
    try:
        return subprocess.call(server_cmd(a.port), cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 0


def cmd_tunnel(a) -> int:
    exe = shutil.which("cloudflared") or shutil.which("ngrok")
    if not exe:
        say("install cloudflared:  Windows: winget install Cloudflare.cloudflared | "
            "macOS: brew install cloudflared | Linux: see README")
        return 1
    if "ngrok" in Path(exe).name:
        say("ngrok: copy the https URL it shows into .env as PUBLIC_BASE_URL")
        return subprocess.call([exe, "http", str(a.port)])
    proc = subprocess.Popen([exe, "tunnel", "--url", f"http://localhost:{a.port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    found = False
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not found:
                found = True
                url = m.group(0)
                set_env_value("PUBLIC_BASE_URL", url)
                print(f"\n  Tunnel: {url}\n  Wrote PUBLIC_BASE_URL to .env (restart `run.py run`).\n"
                      f"  Twilio 'A call comes in'   -> {url}/twilio/voice\n"
                      f"  Twilio 'Call status changes' -> {url}/twilio/status\n"
                      "  Keep this window open.\n", flush=True)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


def _wav(a) -> str:
    wav = Path(a.wav) if a.wav else DEFAULT_WAV
    if not wav.exists():
        say(f"{wav} not found. Make demo calls first:  python run.py calls")
        sys.exit(1)
    return str(wav)


def cmd_sim(a) -> int:
    return py("-m", "edge.tools.simulate_twilio", _wav(a), "--url", f"http://localhost:{a.port}", "--user", a.user)


def cmd_replay(a) -> int:
    return py("-m", "edge.tools.stream_audio", "--wav", _wav(a), "--url", f"http://localhost:{a.port}", "--user", a.user)


def cmd_mic(a) -> int:
    extra = ["--device", a.device] if a.device else []
    return py("-m", "edge.tools.stream_audio", "--mic", "--url", f"http://localhost:{a.port}", "--user", a.user, *extra)


def cmd_listen(a) -> int:
    return py("-m", "edge.tools.ui_listen", "--url", f"http://localhost:{a.port}", "--user", a.user)


def cmd_test(a) -> int:
    return py("-m", "pytest", "-q")


def cmd_demo(a) -> int:
    """Start a dry-run server, play a demo call through the full Twilio flow, stop."""
    if not DEFAULT_WAV.exists() and not a.wav:
        cmd_calls(a)
    wav = _wav(a)
    if not port_free(a.port):
        say(f"port {a.port} busy: stop the other server or pass --port")
        return 1
    env = os.environ.copy()
    env.update({"TWILIO_DRY_RUN": "1", "PUBLIC_BASE_URL": f"http://localhost:{a.port}"})
    env.setdefault("TWILIO_AUTH_TOKEN", read_env().get("TWILIO_AUTH_TOKEN") or "demo_token")
    if env["TWILIO_AUTH_TOKEN"].startswith("your_"):
        env["TWILIO_AUTH_TOKEN"] = "demo_token"
    server = subprocess.Popen(server_cmd(a.port), cwd=ROOT, env=env)
    try:
        say("starting server (first start loads Whisper, ~10-30 s) ...")
        if not wait_http(f"http://localhost:{a.port}/health"):
            say("server did not start; see the log above")
            return 1
        return subprocess.call([sys.executable, "-m", "edge.tools.simulate_twilio", wav,
                                "--url", f"http://localhost:{a.port}", "--user", a.user], cwd=ROOT, env=env)
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()


def cmd_train(a) -> int:
    """Templates -> windows (with any other calls_*.jsonl you generated) -> TF-IDF -> call-level eval."""
    rc = py("-m", "ml.synth_templates", "--per-label", "80", "--out", "data/ml/calls_template.jsonl")
    if rc:
        return rc
    inputs = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "data" / "ml").glob("calls_*.jsonl"))
    say("training on: " + ", ".join(inputs))
    for step in (["-m", "ml.dataset", *inputs, "--out", "data/ml"], ["-m", "ml.train_baseline"], ["-m", "ml.eval_calls"]):
        rc = py(*step)
        if rc:
            return rc
    say("model: models/clf_tfidf.joblib (the edge loads it automatically). Reports in reports/")
    return 0


def _npm() -> str | None:
    return shutil.which("npm.cmd" if IS_WIN else "npm") or shutil.which("npm")


def cmd_app(a) -> int:
    npm = _npm()
    if not npm:
        say("Node.js is needed for the app: https://nodejs.org (LTS). Then rerun: python run.py app")
        return 1
    app_dir = ROOT / "app"
    rc = subprocess.call([npm, "ci" if (app_dir / "package-lock.json").exists() else "install", "--no-audit", "--no-fund"], cwd=app_dir)
    rc = rc or subprocess.call([npm, "run", "build"], cwd=app_dir)
    if not rc:
        say("built app/dist. Start the edge (python run.py run) and open http://localhost:%d/guardian" % a.port)
    return rc


def cmd_ui(a) -> int:
    npm = _npm()
    if not npm:
        say("Node.js is needed: https://nodejs.org")
        return 1
    try:
        return subprocess.call([npm, "run", "dev"], cwd=ROOT / "app")
    except KeyboardInterrupt:
        return 0


def cmd_llm(a) -> int:
    model = os.getenv("OLLAMA_MODEL") or read_env().get("OLLAMA_MODEL") or "qwen2.5:3b"
    ollama = shutil.which("ollama")
    if not ollama:
        say("Install Ollama from https://ollama.com, then rerun. (Without it the LLM is skipped; everything else works.)")
        return 1
    rc = subprocess.call([ollama, "pull", model])
    if rc:
        return rc
    say(f"warming {model} (first load can take a minute) ...")
    return subprocess.call([ollama, "run", model, "Reply with OK."])


def cmd_eval(a) -> int:
    return py("-m", "ml.eval_calls")


def cloud_env() -> dict:
    env = os.environ.copy()
    env.setdefault("CLOUD_API_KEY", read_env().get("CLOUD_API_KEY") or "demo-cloud-key")
    return env


def cmd_cloud(a) -> int:
    env = cloud_env()
    if not port_free(a.cloud_port):
        say(f"port {a.cloud_port} busy")
        return 1
    say(f"cloud on http://localhost:{a.cloud_port}  (bank: {env.get('BANK_BACKEND', 'fake')})")
    try:
        return subprocess.call([sys.executable, "-m", "uvicorn", "cloud.main:app", "--host", "0.0.0.0",
                                "--port", str(a.cloud_port), *NO_COLOR], cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 0


def _post_json(url: str, body: dict, key: str) -> dict:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "X-Cloud-Key": key})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP {e.code}", "detail": e.read().decode()}


def _print_decision(r: dict) -> None:
    print(f"\n  ==> {r.get('status')}" + (f"  ({r.get('points')} points)" if "points" in r else ""))
    for x in r.get("reasons", []):
        print(f"      - {x}")
    if r.get("hold"):
        print(f"      hold {r['hold']['id']} can be released in {r['hold']['release_in']} s")
    if "balance" in r:
        print(f"      balance now ${r['balance']:,.2f}")
    print()


def cmd_transfer(a) -> int:
    if not a.wav or a.amount is None:
        say("usage: python run.py transfer PAYEE AMOUNT   e.g.  transfer giftcarddepot 600")
        return 1
    env = cloud_env()
    r = _post_json(f"http://localhost:{a.cloud_port}/api/transfer" + ("?confirm=1" if a.confirm else ""),
                   {"user_id": a.user, "payee_id": a.wav, "amount": a.amount, "memo": "via run.py"},
                   env["CLOUD_API_KEY"])
    _print_decision(r)
    return 0


def cmd_firewall(a) -> int:
    """Start a fresh in-memory cloud and run the firewall cases over HTTP."""
    if not port_free(a.cloud_port):
        say(f"port {a.cloud_port} busy: stop the running cloud first (the cases need a fresh one)")
        return 1
    env = cloud_env()
    env.update({"COOLDOWN_SEC": "60", "MONGODB_URI": "", "BANK_BACKEND": "fake"})  # clean, repeatable state
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "cloud.main:app", "--port", str(a.cloud_port),
                          "--log-level", "warning", *NO_COLOR], cwd=ROOT, env=env)
    try:
        if not wait_http(f"http://localhost:{a.cloud_port}/health"):
            say("cloud did not start")
            return 1
        return subprocess.call([sys.executable, "-m", "cloud.firewall_cases", "--url",
                                f"http://localhost:{a.cloud_port}", "--key", env["CLOUD_API_KEY"]], cwd=ROOT, env=env)
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def cmd_m1(a) -> int:
    """Cloud + edge (dry-run Twilio) + simulated scam call + $600 transfer, then stop."""
    if not a.wav and not DEFAULT_WAV.exists():
        if cmd_calls(a):
            return 1
    wav = _wav(a)
    for port in (a.port, a.cloud_port):
        if not port_free(port):
            say(f"port {port} busy: stop the other server first")
            return 1
    env = cloud_env()
    env.setdefault("COOLDOWN_SEC", "60")
    env.update({"TWILIO_DRY_RUN": "1", "PUBLIC_BASE_URL": f"http://localhost:{a.port}",
                "CLOUD_URL": f"http://localhost:{a.cloud_port}"})
    tok = env.get("TWILIO_AUTH_TOKEN") or read_env().get("TWILIO_AUTH_TOKEN") or ""
    env["TWILIO_AUTH_TOKEN"] = "demo_token" if (not tok or tok.startswith("your_")) else tok
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "cloud.main:app", "--port", str(a.cloud_port), *NO_COLOR], cwd=ROOT, env=env),
        subprocess.Popen(server_cmd(a.port), cwd=ROOT, env=env),
    ]
    try:
        say("starting cloud + edge (first start loads Whisper and the classifier) ...")
        if not (wait_http(f"http://localhost:{a.cloud_port}/health") and wait_http(f"http://localhost:{a.port}/health")):
            say("a server did not start; see the log above")
            return 1
        say("1/2  the scam call (simulated Twilio merge, real pipeline)")
        rc = subprocess.call([sys.executable, "-m", "edge.tools.simulate_twilio", wav,
                              "--url", f"http://localhost:{a.port}", "--user", a.user], cwd=ROOT, env=env)
        if rc:
            return rc
        time.sleep(1.0)
        say("2/2  the panicked student sends $600 to GiftCardDepot")
        _print_decision(_post_json(f"http://localhost:{a.cloud_port}/api/transfer",
                                   {"user_id": a.user, "payee_id": "giftcarddepot", "amount": 600, "memo": "USCIS fee"},
                                   env["CLOUD_API_KEY"]))
        return 0
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


def cmd_calls(a) -> int:
    """Text scripts -> WAVs with the OS's built-in voice (synthetic, no consent issue)."""
    CALLS.mkdir(parents=True, exist_ok=True)
    scripts = sorted((CALLS / "scripts").glob("*.txt"))
    for f in scripts:
        out = CALLS / f"{f.stem}.wav"
        if IS_MAC:
            aiff = out.with_suffix(".aiff")
            subprocess.check_call(["say", "-r", "175", "-f", str(f), "-o", str(aiff)])
            subprocess.check_call(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", str(aiff), str(out)])
            aiff.unlink()
        elif IS_WIN:
            ps = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"$s.SetOutputToWaveFile('{out}');"
                  f"$s.Speak([IO.File]::ReadAllText('{f}'));$s.Dispose()")
            subprocess.check_call(["powershell", "-NoProfile", "-Command", ps])
        else:
            tts = shutil.which("espeak-ng") or shutil.which("espeak")
            if not tts:
                say("install a voice:  sudo apt install espeak-ng   (or record your own WAVs)")
                return 1
            subprocess.check_call([tts, "-s", "160", "-f", str(f), "-w", str(out)])
        say(f"wrote {out.relative_to(ROOT)}")
    return 0


# ------------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "doctor", "demo", "tunnel", "run", "sim", "replay",
                                        "mic", "listen", "calls", "test", "train", "eval", "cloud", "m1", "transfer",
                                        "firewall"])
    ap.add_argument("wav", nargs="?", help="audio file for sim/replay/demo/m1, or PAYEE for transfer")
    ap.add_argument("amount", nargs="?", type=float, help="transfer: amount in dollars")
    ap.add_argument("--cloud-port", type=int, default=int(os.getenv("CLOUD_PORT", "9000")))
    ap.add_argument("--confirm", action="store_true", help="transfer: proceed past a WARN")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--user", default="alex", help="student id from edge/data/users.json")
    ap.add_argument("--dry", action="store_true", help="run: don't call the real Twilio API")
    ap.add_argument("--device", default=None, help="mic: input device index or name")
    a = ap.parse_args()
    if a.command != "setup":
        reexec_in_venv()
    os.chdir(ROOT)
    fn = globals()[f"cmd_{a.command}"]
    sys.exit(fn(a) or 0)


if __name__ == "__main__":
    main()
