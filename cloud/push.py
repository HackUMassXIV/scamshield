"""Web Push to the installed PWA (hold created, call went RED).

Optional: needs `pip install pywebpush` and VAPID keys in .env:
  python -m cloud.push --gen-keys     -> prints VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY
On iOS, push only works once the PWA is added to the Home Screen (iOS 16.4+).
Without keys everything else works; the app falls back to its live websocket.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

log = logging.getLogger("cloud.push")


class Push:
    def __init__(self, public_key: str = "", private_key: str = "", subject: str = "mailto:team@example.com"):
        self.public_key = public_key
        self.private_key = private_key
        self.subject = subject
        self.subs: dict[str, list[dict]] = defaultdict(list)
        self.sent = 0
        try:
            import pywebpush  # noqa: F401  (availability check)
            del pywebpush

            self._lib = True
        except ImportError:
            self._lib = False

    @property
    def enabled(self) -> bool:
        return bool(self._lib and self.public_key and self.private_key)

    def subscribe(self, user_id: str, sub: dict) -> int:
        if sub.get("endpoint") and all(s.get("endpoint") != sub["endpoint"] for s in self.subs[user_id]):
            self.subs[user_id].append(sub)
        return len(self.subs[user_id])

    async def notify(self, user_id: str, title: str, body: str, url: str = "/guardian") -> int:
        if not self.enabled or not self.subs.get(user_id):
            return 0
        from pywebpush import WebPushException, webpush

        payload = json.dumps({"title": title, "body": body, "url": url})
        ok = 0
        for sub in list(self.subs[user_id]):
            try:
                await asyncio.to_thread(webpush, subscription_info=sub, data=payload,
                                        vapid_private_key=self.private_key, vapid_claims={"sub": self.subject})
                ok += 1
            except WebPushException as exc:
                if getattr(exc, "response", None) is not None and exc.response.status_code in (404, 410):
                    self.subs[user_id].remove(sub)  # expired subscription
                log.warning("push failed: %s", exc)
            except Exception as exc:
                log.warning("push failed: %s", exc)
        self.sent += ok
        return ok


def _gen_keys() -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    print(f"VAPID_PUBLIC_KEY={b64(pub)}\nVAPID_PRIVATE_KEY={b64(priv)}")


if __name__ == "__main__":
    _gen_keys()
