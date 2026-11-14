"""Cloud settings (env / .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CloudSettings:
    api_key: Optional[str] = None  # X-Cloud-Key for every endpoint, ?key= for the websocket
    bank_backend: str = "fake"  # fake | nessie
    nessie_api_key: str = ""
    nessie_base: str = "http://api.nessieisreal.com"
    nessie_ids_file: str = str(ROOT / "cloud" / "data" / "nessie_ids.json")
    cooldown_sec: int = 600  # a held payment can be released after this; use 60 for the demo
    shield_window_sec: int = 3600  # a scam call affects payments for this long
    allowed_origins: tuple[str, ...] = ("*",)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:team@example.com"
    seed_reports: bool = True
    static_dir: str = ""  # serve the built PWA (app/dist) from the cloud too (Vultr)
    mongodb_uri: Optional[str] = None  # holds + payees + riskEvents in MongoDB Atlas; unset = memory
    mongo_db: str = "scamshield"


def load_cloud_settings(env: Optional[Mapping[str, str]] = None) -> CloudSettings:
    e = os.environ if env is None else env
    return CloudSettings(
        api_key=e.get("CLOUD_API_KEY") or None,
        bank_backend=(e.get("BANK_BACKEND") or "fake").lower(),
        nessie_api_key=e.get("NESSIE_API_KEY", ""),
        nessie_base=(e.get("NESSIE_BASE") or "http://api.nessieisreal.com").rstrip("/"),
        nessie_ids_file=e.get("NESSIE_IDS_FILE") or str(ROOT / "cloud" / "data" / "nessie_ids.json"),
        cooldown_sec=int(e.get("COOLDOWN_SEC") or 600),
        shield_window_sec=int(e.get("SHIELD_WINDOW_SEC") or 3600),
        allowed_origins=tuple(o.strip() for o in (e.get("ALLOWED_ORIGINS") or "*").split(",") if o.strip()),
        mongodb_uri=e.get("MONGODB_URI") or None,
        mongo_db=e.get("MONGO_DB") or "scamshield",
    )
