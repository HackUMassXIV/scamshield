"""Print the live events the PWA would receive (use until the app exists).

  python -m edge.tools.ui_listen --user alex
"""
import argparse
import asyncio
import json
import os

import websockets
from dotenv import load_dotenv

from edge.tools.common import http_to_ws, print_event


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="alex")
    ap.add_argument("--url", default="http://localhost:8000")
    a = ap.parse_args()
    key = os.getenv("GUARDIAN_API_KEY", "")
    uri = f"{http_to_ws(a.url)}/ws/ui?user_id={a.user}&key={key}"
    async with websockets.connect(uri) as ws:
        print(f"listening as {a.user} on {a.url}")
        async for raw in ws:
            print_event(json.loads(raw))


if __name__ == "__main__":
    asyncio.run(main())
