#!/usr/bin/env python3
"""
20-size uptime watchdog - runs on Mac mini (10.0.1.17).

Pings 10.0.1.203 every 5 minutes via launchd. After 3 consecutive failures
(15 min of silence) sends an iMessage via BlueBubbles to Jehan's phone.

Deploy:
  1. Copy this script to ~/Library/Scripts/20size-watchdog.py
  2. Copy com.alvani.20size-watchdog.plist to ~/Library/LaunchAgents/
  3. launchctl load ~/Library/LaunchAgents/com.alvani.20size-watchdog.plist

Config: set BB_URL, BB_PASSWORD, and NOTIFY_NUMBER below or via env vars.
Credentials are in 1Password: op://HomeLab/<item>/<field>
  BB_URL/BB_PASSWORD: op://HomeLab/BlueBubbles/... (same as marvin-notify.json on 20-size)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TARGET_HOST = "10.0.1.203"
FAILURE_THRESHOLD = 3
STATE_FILE = Path("~/.local/share/20size-watchdog/state.json").expanduser()

BB_URL = os.environ.get("BB_URL", "")
BB_PASSWORD = os.environ.get("BB_PASSWORD", "")
NOTIFY_NUMBER = os.environ.get("NOTIFY_NUMBER", "+1XXXXXXXXXX")


def ping(host: str) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "2000", host],
        capture_output=True,
    )
    return result.returncode == 0


def send_imessage(message: str) -> None:
    if not BB_URL or not BB_PASSWORD:
        print(f"ALERT (no BB config): {message}", file=sys.stderr)
        return
    payload = json.dumps({"chatGuid": f"iMessage;-;{NOTIFY_NUMBER}", "message": message}).encode()
    req = urllib.request.Request(
        f"{BB_URL}/api/v1/message/text?password={BB_PASSWORD}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"BlueBubbles send failed: {e}", file=sys.stderr)


def load_state() -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"failures": 0, "alerted": False}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def main() -> None:
    state = load_state()
    up = ping(TARGET_HOST)

    if up:
        if state["alerted"]:
            send_imessage("20-size is back up.")
        state = {"failures": 0, "alerted": False}
    else:
        state["failures"] += 1
        if state["failures"] >= FAILURE_THRESHOLD and not state["alerted"]:
            send_imessage(
                f"20-size ({TARGET_HOST}) has been unreachable for "
                f"{state['failures'] * 5} minutes."
            )
            state["alerted"] = True

    save_state(state)


if __name__ == "__main__":
    main()
