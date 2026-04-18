#!/usr/bin/env python3
"""Marvin Notify Drain — deliver queued notifications via BlueBubbles iMessage.

Scans /home/openclaw/.openclaw/notify-queue/ for JSON files, sends each via
the BlueBubbles API, and deletes the file on success.

Queue file format:
  { "message": "Text to send" }           — sends to default notify_handle
  { "message": "Text", "to": "+15555555" } — sends to specified number

Cron: every 5 minutes (see notify.yml)
Config: /home/openclaw/.openclaw/marvin-notify.json  (shared with marvin_notify.py)
"""

import json
import sys
import urllib.request
import urllib.error
import uuid
from pathlib import Path

CONFIG_FILE = Path("/home/openclaw/.openclaw/marvin-notify.json")
QUEUE_DIR   = Path("/var/spool/marvin-notify")


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text())


def send_imessage(config: dict, message: str, to: str | None = None):
    handle = to or config["notify_handle"]
    url = (
        f"{config['bluebubbles_url']}/api/v1/message/text"
        f"?password={config['bluebubbles_password']}"
    )
    body = {
        "chatGuid": f"iMessage;-;{handle}",
        "message":  message,
        "method":   "apple-script",
        "tempGuid": str(uuid.uuid4()),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def main():
    if not QUEUE_DIR.exists():
        return  # nothing to do

    config = load_config()
    files  = sorted(QUEUE_DIR.glob("*.json"))

    if not files:
        return

    sent = failed = 0
    for f in files:
        try:
            payload = json.loads(f.read_text())
        except Exception as e:
            print(f"[marvin-drain] SKIP {f.name}: bad JSON — {e}", file=sys.stderr)
            continue

        message = payload.get("message", "").strip()
        if not message:
            print(f"[marvin-drain] SKIP {f.name}: empty message", file=sys.stderr)
            f.unlink(missing_ok=True)
            continue

        to = payload.get("to")
        try:
            status = send_imessage(config, message, to)
            print(f"[marvin-drain] Sent ({status}): {message[:60]!r}")
            f.unlink(missing_ok=True)
            sent += 1
        except Exception as e:
            print(f"[marvin-drain] FAIL {f.name}: {e}", file=sys.stderr)
            failed += 1

    if sent or failed:
        print(f"[marvin-drain] Done — sent={sent} failed={failed}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[marvin-drain] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
