"""Serve a built single-page app (app/dist) with client-side routing."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

NO_CACHE = {"Cache-Control": "no-cache"}


def mount_spa(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        f = (dist / path).resolve()
        if path and f.is_file() and root in f.parents:
            # the service worker and manifest must never be served stale
            return FileResponse(f, headers=NO_CACHE if f.name in ("sw.js", "manifest.webmanifest") else None)
        return FileResponse(index, headers=NO_CACHE)
