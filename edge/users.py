"""users.json directory: phone number -> user. Lives on the edge so the webhook
works even if the cloud is unreachable. Hot-reloads when the file changes."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from shared.contracts import UserRecord


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Return E.164 or None. 10-digit and 1+10-digit US numbers are upgraded."""
    if not raw:
        return None
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return None


class UserDirectory:
    def __init__(self, path: str):
        self.path = Path(path)
        self._mtime = -1.0
        self._users: dict[str, UserRecord] = {}
        self._by_phone: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._users, self._by_phone, self._mtime = {}, {}, -1.0
            return
        data = json.loads(self.path.read_text())
        users: dict[str, UserRecord] = {}
        by_phone: dict[str, str] = {}
        for u in data.get("users", []):
            phone = normalize_phone(u.get("phone"))
            if not phone:
                continue
            u = {**u, "phone": phone}
            if u.get("trusted_contact"):
                u["trusted_contact"] = normalize_phone(u["trusted_contact"])
            rec = UserRecord(**u)
            users[rec.id] = rec
            by_phone[phone] = rec.id
        self._users, self._by_phone = users, by_phone
        self._mtime = self.path.stat().st_mtime

    def _maybe_reload(self) -> None:
        try:
            m = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if m != self._mtime:
            self._load()

    def by_phone(self, phone: Optional[str]) -> Optional[UserRecord]:
        self._maybe_reload()
        p = normalize_phone(phone)
        uid = self._by_phone.get(p) if p else None
        return self._users.get(uid) if uid else None

    def by_id(self, user_id: str) -> Optional[UserRecord]:
        self._maybe_reload()
        return self._users.get(user_id)

    def all(self) -> list[UserRecord]:
        self._maybe_reload()
        return list(self._users.values())

    @staticmethod
    def name_tokens(user: UserRecord) -> list[str]:
        """Names to scrub from transcripts (full name, parts >= 3 chars, aliases)."""
        out = {user.name.strip()}
        out.update(p for p in re.split(r"\s+", user.name) if len(p) >= 3)
        out.update(a.strip() for a in user.aliases if len(a.strip()) >= 3)
        return sorted((t for t in out if t), key=len, reverse=True)
