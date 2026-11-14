# ScamShield: scam payment firewall

> ChatGPT can tell you it was a scam. **We stop you from paying it.**

HackUMass XIV · Nov 13–15, 2026 · UMass Amherst

This repo implements **sections 3 and 5–11** of the plan, and milestone **M1**:

| Stage | What works now |
|---|---|
| **S1: call audio in** | Twilio merge leg (silent until disclosure), media websocket, bridge backup mode, mic mode, WAV replay, full Twilio simulator |
| **S2: ASR + redaction** | VAD segmentation → faster-whisper (scam-vocabulary prompt, hallucination filter) → PII redaction → live events to the app |
| **S3: scam classifier** | Offline + Gemini data generators, ASR-noise and real-Whisper augmentation, split by call, TF-IDF baseline (edge default), DistilBERT Colab script, window- and call-level metrics |
| **S4/S8: risk engine** | Keywords with negation guard, weighted score (LLM optional), Marlowe-style stabilizer, "I'm suspicious" button, redacted RiskEvents to the cloud |
| **S9: Nessie + firewall** | Shield state per student, payment firewall (scams, typos, duplicates), holds with cool-down + typed release, MongoDB Atlas store (holds, known payees, risk events), in-memory demo bank or Nessie (seed script) |
| **S5: local LLM** | Ollama verdict (JSON schema, 3 few-shot examples, 15 s throttle, cancels stale requests, drops invented quotes) + Gemini second opinion on borderline calls |
| **S6: coaching + alerts** | 3-step playbook per scam type, escalation by time at YELLOW/RED, LLM "say this" when it fits, SMS/WhatsApp banner to the student, trusted contact alerted at RED |
| **S10: the app** | PWA with `/guardian` (screen a call, live signal + coaching, summary, report to map), `/bank` (fictional Quill Bank, hold modal with cool-down), `/ops` (timeline, latency, second opinion, holds, campus scam map); offline shell + Web Push |
| **M1** | `python run.py m1`: simulated scam call → RED → $600 to GiftCardDepot **HELD** |
| Tests | 100 tests including the full M1 loop and a full call with LLM, coaching and Gemini |

---

## 1. HackUMass rules that affect this repo

From hackumass.com (I could not open the XIV Devpost page itself, so read its "Requirements" before submitting):

- **No pre-event work.** Projects must start after the opening ceremony (Fri 8:30 PM); work done earlier means disqualification. Create the GitHub repo and commit this code *now*, during the event, so the history shows it.
- **Deadlines (Sunday Nov 15, EST):** initial Devpost submission by **12:00 AM** (midnight Saturday night), final submission by **10:00 AM**, judging at **12:30 PM**. At least one teammate must be present at judging.
- **Team size:** four or fewer for guaranteed prizes per member.
- **Code of Conduct:** MLH Code of Conduct and the UMass Code of Student Conduct.
- **It doesn't need to be finished.** Ship what works, with a backup video.

**Prizes to opt into** (listed on hackumass.com): Best Use of Nessie (Capital One) · Best Use of Gemini API · Best Use of MongoDB Atlas · Best Use of Vultr · Best Domain Name from GoDaddy Registry · Most Secure Hack · Best AI Security Hack · Most Impactful AI Hack · Best UMass Hack · Best Pitch · Build Your Hackathon A-Team with SpaceXSI. In past years only up to three `[HACKUMASS]`-tagged prizes could be chosen, while sponsor prizes did not count toward that limit. Confirm on the XIV Devpost. HPE is a gold sponsor but has no listed prize, so don't plan around one.

## 2. What you need

**Accounts (create now):**

| Service | Why | Notes |
|---|---|---|
| Twilio | Guardian phone number + media streams | **Upgrade from trial.** Trial accounts play a notice before your TwiML and restrict numbers, which breaks the merge flow. |
| Capital One Nessie | balances, transfers (section 4) | API key from the hackathon API site |
| Google AI Studio | Gemini key (second opinion, screenshots) | |
| MongoDB Atlas | holds, scam map | free cluster |
| Vultr | cloud API hosting | MLH credits |
| GoDaddy Registry | domain for the cloud API | MLH prize |
| ElevenLabs (optional) | a **consented** voice clone of a teammate for the voice-clone test | |

**On the demo laptop:** Python 3.10+ (3.13 works; no `audioop` needed), `cloudflared` or `ngrok`, Ollama with `ollama pull qwen2.5:3b` (section 11), PortAudio for mic mode (`brew install portaudio` / `apt install libportaudio2`).

**Phones:** two teammates' phones with call waiting / three-way calling enabled. Test the merge on your carriers tonight.

## 3. One way to run it on Windows, macOS and Linux

Everything goes through **`run.py`** (standard library only), so the commands are identical on every OS. On Windows type `py` instead of `python` if `python` isn't found. Mac/Linux users can also type `make <command>`.

### Install once per laptop

| | Windows | macOS | Linux (Ubuntu/Debian) |
|---|---|---|---|
| Python 3.10–3.12 | `winget install Python.Python.3.12` (tick *Add to PATH* if using the python.org installer) | `brew install python@3.12` | `sudo apt install python3 python3-venv python3-pip` |
| Tunnel | `winget install Cloudflare.cloudflared` | `brew install cloudflared` | download the `.deb` from Cloudflare's releases page |
| Mic mode only | nothing | `brew install portaudio` | `sudo apt install libportaudio2` |
| Demo voices | built in (System.Speech) | built in (`say`) | `sudo apt install espeak-ng` |
| Node.js (the app) | `winget install OpenJS.NodeJS.LTS` | `brew install node` | `sudo apt install nodejs npm` (or nodesource for v20+) |
| Local LLM (optional) | Ollama installer from ollama.com | `brew install ollama` | `curl -fsSL https://ollama.com/install.sh \| sh` |

Python 3.13 also works for this code, but 3.12 has the smoothest wheels for `torch` (pulled in by Silero VAD).

### Steps

```bash
python run.py setup     # 1. venv, dependencies, .env, Whisper weights, builds the app, tests (~5-10 min)
python run.py doctor    # 2. checks Python, packages, Node, app build, Ollama, .env, tunnel, users.json
python run.py train     # 3. trains the scam classifier (~1 min)
python run.py llm       # 4. optional: pulls and warms the local LLM in Ollama
python run.py m1        # 5. no phones, no accounts: scam call -> RED -> $600 transfer HELD
```

Then for **real calls**, edit `.env` (Twilio SID/token/number, `GUARDIAN_API_KEY`, `CLOUD_API_KEY`) and `edge/data/users.json` (real numbers of consenting teammates), and use three terminals:

```bash
python run.py cloud     # terminal 1: shield state, payment firewall, scam map (:9000)
python run.py tunnel    # terminal 2: writes PUBLIC_BASE_URL into .env and prints the Twilio webhook URLs
python run.py run       # terminal 3: the edge + the app (start AFTER the tunnel so it reads the new URL)
```

Open `https://xxxx.trycloudflare.com/guardian` on the student's phone, tap the gear, enter the `GUARDIAN_API_KEY`, then **Share → Add to Home Screen** (needed for notifications on iPhone). Judges open `/ops` on the laptop.

**Twilio console** → Phone Numbers → your number → Voice configuration:
- *A call comes in* → Webhook → `https://xxxx.trycloudflare.com/twilio/voice` (HTTP POST)
- *Call status changes* → `https://xxxx.trycloudflare.com/twilio/status`
- Backup bridge mode (optional second number): `https://xxxx.trycloudflare.com/twilio/bridge?user=alex`

A quick tunnel gets a new URL every restart: rerun `tunnel`, restart `run`, update the Twilio webhook.

### A real merged call
1. Teammate B calls teammate A (the "student", whose number is in users.json).
2. A taps **Add call** → dials the Guardian number → **Merge**. The edge answers silently (`<Pause>`) and emits `leg_connected`.
   (Or in the app: **Screen this call** dials Guardian for you and marks the call suspicious.)
3. The app switches to "Guardian is on the line". A presses **Start screening**.
4. Everyone hears the disclosure, **then** the stream opens. The screen turns green, amber or red, with what to say.
5. A opens **Bank** and tries to pay: the payment is held with reasons and a cool-down.

### Other modes (same pipeline)
```bash
python run.py calls                              # make demo WAVs with the OS voice
python run.py run --dry                          # server that logs Twilio updates instead of sending them
python run.py sim data/calls/scam_uscis.wav      # (other terminal) full simulated Twilio merge flow
python run.py replay data/calls/benign_pharmacy.wav   # no Twilio: WAV over /ws/audio
python run.py mic                                # speakerphone fallback (laptop mic)
python run.py listen                             # raw event stream in a terminal
python run.py ui                                 # app dev server with hot reload on :5173
```
Windows convenience wrappers also exist: `scripts\setup.ps1`, `scripts\demo.ps1`, `scripts\run.ps1` (they just call `run.py`).

### Why not Docker?
It would work for the Twilio path, but mic access from a container is awkward on macOS and Windows, and the Whisper model download would be repeated per image. A venv plus `run.py` is simpler for a 36-hour event. Docker fits the **cloud** service on Vultr (section 15).

### Tests
```bash
python run.py test   # 85 tests: codec, resampler, VAD, redaction, ASR filter, and full
              # end-to-end flows incl. forged signatures, wrong call_id, timeouts
```

## 5. How the call flow protects privacy and consent

1. **Nothing is heard before disclosure.** The leg answers with `<Pause>` only. Streaming starts only after the student presses Start, and the TwiML plays `<Say>` *before* `<Start><Stream>`, so both far parties hear it first (Massachusetts all-party consent). The disclosure itself is never transcribed.
2. **Every webhook is signature-checked** (`X-Twilio-Signature`), including the forwarded-host case behind a tunnel.
3. **Streams bind by an unguessable `call_id`**, only after disclosure, and only from the same `CallSid`.
4. **Audio is never written to disk.** It lives in an in-memory queue.
5. **Raw text never leaves `_handle()`.** Everything logged, stored, pushed or (later) sent to the cloud is redacted: SSN, card (Luhn), account, phone, email, spoken digit strings, the student's own name, and cue-introduced names. Agency names stay readable for the classifier.
6. **One live session per student**, hung up after `LEG_TIMEOUT_SEC` if Start is never pressed.

Honest limits to say out loud: audio transits Twilio's network; redaction is rule-based; in merge mode both voices are one mixed track (bridge mode separates them). This is not legal advice.

## 6. Layout

```
shared/contracts.py        RiskEvent, Transfer, Hold, TranscriptEvent, ... (source of truth)
edge/main.py               FastAPI: Twilio webhooks, /ws/twilio, /ws/ui, /ws/audio, /api/screen/*
edge/session.py            per-call pipeline: queue -> VAD -> ASR -> redact -> events + hooks
edge/twilio_gateway.py     signatures, TwiML (leg / screen / bridge), call updates, dry-run
edge/asr.py                faster-whisper service (1 worker thread), FakeASR, hallucination filter
edge/redact.py             PII redaction
edge/audio/                mu-law codec (numpy), streaming 8k->16k upsampler, Silero/energy VAD, segmenter
edge/users.py, data/       users.json directory (phone -> student)
edge/tools/                simulate_twilio, stream_audio (mic/replay), ui_listen
run.py                     cross-platform launcher (setup, doctor, demo, tunnel, run, train, m1, ...)
edge/classifier.py         DistilBERT > TF-IDF > keyword fallback, own worker thread
edge/keywords.py           keyword signals, negation guard, compliance phrases
edge/risk.py               raw_score, Shield stabilizer, RiskService (transcript hook)
edge/cloud_client.py       fire-and-forget redacted events to the cloud
shared/labels.py, textnorm.py  label set; normalization shared by training and inference
ml/                        synth_templates, gen_gemini, hf_import, asr_noise, tts_asr_aug,
                           dataset, train_baseline, train_distilbert, metrics, eval_calls
cloud/                     main (API), shield, firewall, bank (Fake | Nessie), seed_nessie
reports/                   metrics_tfidf.md, calls_tfidf.md (regenerated by `run.py train`)
data/calls/scripts/        demo call scripts (scam + benign) for TTS
.cursor/rules/             project context for Cursor (show this in the Devpost write-up)
app/                       PWA: src/pages/{Guardian,Bank,Ops}.tsx, public/sw.js, manifest
edge/coach.py, llm.py, alerts.py   coaching escalation, Ollama verdict, SMS/WhatsApp alerts
shared/playbook.json       3 coaching steps per scam type
cloud/gemini.py, reports.py, push.py   second opinion, scam map, Web Push
deploy/                    Caddyfile + docker-compose for Vultr
```

## 7. Sections 7–8: classifier, risk engine, cloud firewall

### Commands
```bash
python run.py train        # ~1 min: template calls -> windows -> TF-IDF -> call-level eval (reports/)
python run.py m1           # cloud + edge + simulated scam call + $600 transfer -> HELD
python run.py cloud        # cloud alone on :9000 (then `run.py run` with CLOUD_URL set)
python run.py transfer giftcarddepot 600     # try a payment against the running cloud
python run.py firewall     # section 4's test cases against a fresh cloud: OK / WARN / HELD table
python run.py eval         # re-run the call-level report
```
Better data (do this tonight, then `run.py train` again; it picks up every `data/ml/calls_*.jsonl`):
```bash
python -m ml.gen_gemini --per-label 80            # needs GEMINI_API_KEY
python -m ml.tts_asr_aug data/ml/calls_template.jsonl data/ml/calls_gemini.jsonl --n 160   # real Whisper errors
python -m ml.hf_import OWNER/DATASET --text-col ... --label-col ... --test-only           # external test set
```
DistilBERT: run `python -m ml.train_distilbert` on Colab (T4, ~10 min; steps in the file header), copy `models/clf/` to the laptop and `pip install -r requirements-ml.txt`. The edge prefers it automatically and falls back to TF-IDF, then keywords.

### How a score becomes a color
After every redacted utterance the edge classifies the last 3 utterances (`edge/risk.py`):

- **Features:** `clf` = P(scam), scaled down for windows under 6 words ("okay thanks" carries no evidence); `kw` = keyword hits / 3; `llm` (None until section 11); bonuses for a synthetic voice (section 13) and the student complying ("how many cards?").
- **Score:** a weighted mean of the available features. A missing LLM is renormalized away, not counted as 0.
- **Stabilizer:** a dead zone (±3), confidence-weighted steps (up faster than down), faster across band edges, raw ≥ 85 jumps to RED, RED latched 60 s, step down one level at a time. "I'm suspicious" (`POST /api/flag`) sets a YELLOW floor.
- Every RiskEvent (redacted) is POSTed fire-and-forget to the cloud. If the cloud is down, the color still works.

### The firewall (`cloud/firewall.py`)
HOLD at ≥ 3 points, WARN at ≥ 1.5. Points come from: a RED call in the last hour (+3), YELLOW (+1.5), paying during a screened call (+1), the suspicious flag (+1.5), a new payee (+2), an amount far above usual unless normal for that payee (+1.5), over half the balance (+1), a gift-card/crypto/wire payee or memo (+2), 8× the last payment to that payee (+2), and a duplicate within 5 minutes (+2). Holds release after `COOLDOWN_SEC` plus typing RELEASE.

**Storage:** set `MONGODB_URI` (Atlas) and the cloud keeps `holds`, `payees` (which payees a student has already paid, since Nessie has no such concept) and `riskEvents` (redacted) in MongoDB, so holds survive a restart. With it empty, it uses memory.
**Bank:** `BANK_BACKEND=fake` (default) is an in-memory demo bank. For Nessie: `python -m cloud.seed_nessie` once, then `BANK_BACKEND=nessie`. A transfer is only created in Nessie when the firewall says OK (or a hold is released).

### Results so far (TF-IDF, template data only)
| | |
|---|---|
| Window level (5,424 held-out windows) | macro F1 0.85, ECE 0.04, benign false-positive rate 0.0% |
| Held-out template calls | 84/84 scams reach RED (median 3 utterances), 0/24 benign false alarms |
| **12 hand-written calls, never trained on** | 7/7 scams reach YELLOW+, **5/7 RED**; benign: 0/5 RED, **2/5 YELLOW** (a surprise-party "don't tell anyone", a real SEVIS reminder) |

The template numbers are optimistic (templates are easy to fit). The hand-written row is the honest one. It improves with Gemini and Whisper data, so quote that row to judges. Window macro F1 is limited by one-word windows ("okay") that can't be classified, which is why the risk engine discounts short windows.

## 8. Sections 9–11: the app, coaching, local LLM

### The app (`app/`, React + Vite + Tailwind, built by `python run.py app`)
The edge serves the built app, so the phone only ever talks to the laptop through the tunnel. The edge forwards bank, hold and map calls to the cloud (`/cloud/*`, `/cloud-ws`) and adds the cloud key itself, so it never reaches the browser. The live transcript comes from the edge and never touches the cloud.

- **`/guardian`** on the student's phone. **Screen this call** marks the call suspicious and dials Guardian. Then "tap Merge, press Start screening". During the call the whole screen turns green, amber or red, with the risk ring, the scam type, what the caller said, and a large "Say this" card. At step 3 it shows **I've hung up**. After the call: a summary, held payments and **Report to scam map**. "Read coaching aloud" in Settings uses the phone's voice; only use it with earbuds.
- **`/bank`** is Quill Bank, a made-up demo bank. It shows the balance, recent transfers and **Send money**. A WARN asks you to double-check; a HOLD opens a modal with the reasons, a cool-down countdown, **Cancel this payment**, and "type RELEASE" to send anyway.
- **`/ops`** is the judge view: the event timeline (edge and cloud), median latency (speech to text, classifier, LLM, voice, speech-end to color), the "On-device · offline-capable" badge, "Local 0.62 · Gemini 0.81", payments, and the Leaflet campus scam map with 20 seeded reports.
- **PWA:** a manifest with a "Screen this call" shortcut (long-press the icon on Android), an offline app shell, and Web Push for "call turned red" and "payment held". Push needs `pip install pywebpush` and VAPID keys from `python -m cloud.push`. On iPhone it works only after Add to Home Screen. Without push the app still updates live while open.
- **One-tap dialing:** on iPhone, make a Shortcut with the action "Call" to the Guardian number and assign it to the Action Button. On Android, long-press the app icon and use "Screen this call".

### Coaching (`edge/coach.py`, `shared/playbook.json`)
Step 1 comes when the call first reaches YELLOW. Step 2 comes after 30 s at YELLOW, or as soon as it's RED. Step 3 ("hang up") comes after 20 s at RED. Steps never repeat. The "Say this" line comes from the local LLM only if it's under 20 words and agrees on the scam type; the fact line always comes from the playbook. Coaching goes only to the student: the app card, an SMS/WhatsApp banner (`ALERT_CHANNEL`), and optional earbuds. The trusted contact in users.json gets one message when a call turns RED. Messages contain the scam type and playbook text, never anything from the call. US carriers filter unregistered SMS and trial numbers can only text verified numbers, so test tonight and keep WhatsApp sandbox or the app card as backup.

### Local LLM and second opinion (`edge/llm.py`, `cloud/gemini.py`)
At YELLOW or above, at most every 15 s, the edge sends the redacted last 90 s to Ollama. A newer request cancels the older one. The response must match a JSON schema, and quotes that aren't in the transcript are dropped (counted on `/ops`). The verdict feeds the score's `llm` feature, the coach, and the cloud. When the local score is 0.4–0.75, or the LLM and classifier disagree by more than 0.3, the cloud asks Gemini, for display only. If Ollama is down, the color keeps working from the classifier and keywords.

### Hosting on Vultr (optional)
`deploy/docker-compose.yml` runs the cloud plus Caddy, which provides HTTPS for your domain, serves the app, and proxies `/cloud` with the key added. The edge stays on the laptop; set Settings → Server addresses → Edge URL to the tunnel. The Caddyfile is untested; the laptop-only setup above is the tested path.

### Next extension points
`runtime.transcript_hooks` (per utterance) and `runtime.end_hooks` (per call). Set `rt.risk.calls[call_id].llm_score` from the LLM (section 11) and `.voice_flag` from the voice detector (section 13); the score uses them automatically.

Latency for the dashboard is in every event: transcript `latency.asr_ms/queue_ms/redact_ms`, risk `latency.clf_ms`.
