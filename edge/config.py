"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PROMPT = "IRS, USCIS, SEVIS, visa, gift cards, Zelle, bitcoin, warrant, social security"
DEFAULT_DISCLOSURE = (
    "This call is being screened by an automated scam filter. No audio is saved."
)


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    v = env.get(key)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, "") or default)
    except ValueError:
        return default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- network / Twilio -------------------------------------------------
    public_base_url: str = "http://localhost:8000"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_number: str = ""
    twilio_validate: bool = True  # verify X-Twilio-Signature on webhooks
    twilio_dry_run: bool = False  # log TwiML updates instead of calling Twilio
    twilio_say_voice: Optional[str] = None
    disclosure_text: str = DEFAULT_DISCLOSURE
    leg_timeout_sec: int = 300  # hang up the leg if student never presses Start
    stream_pause_sec: int = 3600
    stream_grace_sec: int = 20  # wait this long for a dropped stream to come back
    # --- app-facing API ---------------------------------------------------
    api_key: Optional[str] = None  # if set, PWA must send X-Guardian-Key
    allowed_origins: tuple[str, ...] = ("*",)
    users_file: str = str(ROOT / "edge" / "data" / "users.json")
    demo_fallback_user: Optional[str] = None  # map unknown callers to this user
    cloud_url: Optional[str] = None  # cloud API (section 8); unset = offline-only
    cloud_api_key: Optional[str] = None  # sent as X-Cloud-Key
    flag_seconds: int = 600  # "I'm suspicious" keeps the floor at YELLOW this long
    app_dist: str = ""  # built PWA to serve (default: app/dist)
    # --- LLM verdict (section 11) -----------------------------------------
    llm_backend: str = "off"  # load_settings() defaults to "ollama"; tests construct Settings directly
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    llm_timeout: float = 20.0
    llm_min_interval: float = 15.0
    # --- coaching alerts (section 10) ---------------------------------------
    alert_channel: str = "sms"  # sms | whatsapp | off
    whatsapp_from: str = ""  # e.g. whatsapp:+14155238886 (Twilio sandbox)
    alert_cooldown_sec: int = 20
    trusted_cooldown_sec: int = 600
    # --- classifier (section 7) -------------------------------------------
    clf_backend: str = "auto"  # auto | hf | tfidf | keyword
    clf_dir: str = "models/clf"  # fine-tuned DistilBERT (HF format)
    clf_tfidf: str = "models/clf_tfidf.joblib"
    # --- audio / ASR ------------------------------------------------------
    asr_backend: str = "faster-whisper"  # or "fake"
    whisper_model: str = "base.en"
    whisper_device: str = "auto"
    whisper_compute: str = "auto"
    whisper_prompt: str = DEFAULT_PROMPT
    whisper_beam: int = 1
    whisper_vad_filter: bool = False  # our own VAD already segments
    asr_language: str = "en"
    fake_asr_lines: tuple[str, ...] = ()
    vad_backend: str = "auto"  # auto | silero | energy
    vad_threshold: float = 0.5
    silence_ms: int = 400
    min_speech_ms: int = 250
    max_utt_sec: float = 6.0
    preroll_ms: int = 200
    audio_queue_max: int = 1500  # ~30 s of 20 ms Twilio frames
    log_transcripts: bool = False  # logs REDACTED text only

    @property
    def ws_base_url(self) -> str:
        u = self.public_base_url.rstrip("/")
        if u.startswith("https://"):
            return "wss://" + u[len("https://"):]
        if u.startswith("http://"):
            return "ws://" + u[len("http://"):]
        return u


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    e = os.environ if env is None else env
    lines: tuple[str, ...] = ()
    raw = e.get("FAKE_ASR_LINES", "")
    if raw:
        try:
            lines = tuple(str(x) for x in json.loads(raw))
        except (ValueError, TypeError):
            lines = ()
    origins = tuple(
        o.strip() for o in (e.get("ALLOWED_ORIGINS", "*") or "*").split(",") if o.strip()
    ) or ("*",)
    return Settings(
        public_base_url=(e.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/"),
        twilio_account_sid=e.get("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=e.get("TWILIO_AUTH_TOKEN", ""),
        twilio_number=e.get("TWILIO_NUMBER", ""),
        twilio_validate=_bool(e, "TWILIO_VALIDATE_SIGNATURE", True),
        twilio_dry_run=_bool(e, "TWILIO_DRY_RUN", False),
        twilio_say_voice=e.get("TWILIO_SAY_VOICE") or None,
        disclosure_text=e.get("DISCLOSURE_TEXT") or DEFAULT_DISCLOSURE,
        leg_timeout_sec=_int(e, "LEG_TIMEOUT_SEC", 300),
        stream_pause_sec=_int(e, "STREAM_PAUSE_SEC", 3600),
        stream_grace_sec=_int(e, "STREAM_GRACE_SEC", 20),
        api_key=e.get("GUARDIAN_API_KEY") or None,
        allowed_origins=origins,
        users_file=e.get("USERS_FILE") or str(ROOT / "edge" / "data" / "users.json"),
        demo_fallback_user=e.get("DEMO_FALLBACK_USER") or None,
        cloud_url=(e.get("CLOUD_URL") or None),
        cloud_api_key=e.get("CLOUD_API_KEY") or None,
        flag_seconds=_int(e, "FLAG_SECONDS", 600),
        app_dist=e.get("APP_DIST", ""),
        llm_backend=(e.get("LLM_BACKEND") or "ollama").lower(),
        ollama_url=e.get("OLLAMA_URL") or "http://localhost:11434",
        ollama_model=e.get("OLLAMA_MODEL") or "qwen2.5:3b",
        llm_timeout=_float(e, "LLM_TIMEOUT", 20.0),
        llm_min_interval=_float(e, "LLM_MIN_INTERVAL", 15.0),
        alert_channel=(e.get("ALERT_CHANNEL") or "sms").lower(),
        whatsapp_from=e.get("WHATSAPP_FROM", ""),
        alert_cooldown_sec=_int(e, "ALERT_COOLDOWN_SEC", 20),
        trusted_cooldown_sec=_int(e, "TRUSTED_COOLDOWN_SEC", 600),
        clf_backend=(e.get("CLF_BACKEND") or "auto").lower(),
        clf_dir=e.get("CLF_DIR") or "models/clf",
        clf_tfidf=e.get("CLF_TFIDF") or "models/clf_tfidf.joblib",
        asr_backend=(e.get("ASR_BACKEND") or "faster-whisper").lower(),
        whisper_model=e.get("WHISPER_MODEL") or "base.en",
        whisper_device=(e.get("WHISPER_DEVICE") or "auto").lower(),
        whisper_compute=(e.get("WHISPER_COMPUTE") or "auto").lower(),
        whisper_prompt=e.get("WHISPER_PROMPT", DEFAULT_PROMPT),
        whisper_beam=_int(e, "WHISPER_BEAM", 1),
        whisper_vad_filter=_bool(e, "WHISPER_VAD_FILTER", False),
        asr_language=e.get("ASR_LANGUAGE") or "en",
        fake_asr_lines=lines,
        vad_backend=(e.get("VAD_BACKEND") or "auto").lower(),
        vad_threshold=_float(e, "VAD_THRESHOLD", 0.5),
        silence_ms=_int(e, "SILENCE_MS", 400),
        min_speech_ms=_int(e, "MIN_SPEECH_MS", 250),
        max_utt_sec=_float(e, "MAX_UTT_SEC", 6.0),
        preroll_ms=_int(e, "PREROLL_MS", 200),
        audio_queue_max=_int(e, "AUDIO_QUEUE_MAX", 1500),
        log_transcripts=_bool(e, "LOG_TRANSCRIPTS", False),
    )
