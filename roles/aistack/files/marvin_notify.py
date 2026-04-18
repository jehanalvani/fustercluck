#!/usr/bin/env python3
"""Marvin Notify — standalone homelab outage notifier.

Runs marvin_health.py, diffs against saved state, and sends an iMessage via
BlueBubbles when a service goes down, recovers, or a cert is expiring.

Does NOT involve the LLM — deterministic and reliable.

Cron: every 15 minutes (see health.yml)
Config: /home/openclaw/.openclaw/marvin-notify.json
State:  /home/openclaw/.openclaw/homelab-notify-state.json (auto-created)
"""

import json
import subprocess
import sys
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONFIG_FILE = Path("/home/openclaw/.openclaw/marvin-notify.json")


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text())


# ── Health check ──────────────────────────────────────────────────────────────

def run_health_check(config: dict) -> dict:
    result = subprocess.run(
        [sys.executable, config["health_script"]],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"health script failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


# ── State ─────────────────────────────────────────────────────────────────────

def load_state(config: dict) -> dict | None:
    path = Path(config["state_file"])
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_state(config: dict, report: dict):
    state = {
        "timestamp": report["timestamp"],
        "http":    {e["name"]: e["status"]          for e in report.get("http", [])},
        "systemd": {e["name"]: e["status"]          for e in report.get("systemd", [])},
        "ssl":     {e["name"]: e.get("status", "ok") for e in report.get("ssl", [])},
    }
    Path(config["state_file"]).write_text(json.dumps(state, indent=2))


# ── Diff ──────────────────────────────────────────────────────────────────────

def compute_changes(prev: dict, report: dict) -> list[str]:
    messages = []

    # HTTP
    prev_http = prev.get("http", {})
    for entry in report.get("http", []):
        name, status = entry["name"], entry["status"]
        old = prev_http.get(name)
        if old == "up" and status == "down":
            messages.append(f"{name} is down.")
        elif old == "down" and status == "up":
            messages.append(f"{name} recovered.")

    # systemd
    prev_sys = prev.get("systemd", {})
    for entry in report.get("systemd", []):
        name, status = entry["name"], entry["status"]
        old = prev_sys.get(name)
        if old == "active" and status != "active":
            messages.append(f"{name} service stopped ({status}).")
        elif old not in ("active", None) and status == "active":
            messages.append(f"{name} service recovered.")

    # SSL
    prev_ssl = prev.get("ssl", {})
    for entry in report.get("ssl", []):
        name, status = entry["name"], entry.get("status", "ok")
        old = prev_ssl.get(name)
        days = entry.get("expires_in_days", "?")
        if old == "ok" and status == "expiring_soon":
            messages.append(f"SSL cert for {name} expires in {days} days.")
        elif status == "expired" and old != "expired":
            messages.append(f"SSL cert for {name} has EXPIRED.")
        elif old == "expiring_soon" and status == "ok":
            messages.append(f"SSL cert for {name} renewed.")

    return messages


# ── Notify ────────────────────────────────────────────────────────────────────

def send_imessage(config: dict, message: str):
    url = (
        f"{config['bluebubbles_url']}/api/v1/message/text"
        f"?password={config['bluebubbles_password']}"
    )
    body = {
        "chatGuid": f"iMessage;-;{config['notify_handle']}",
        "message": message,
        "method": "apple-script",
        "tempGuid": str(uuid.uuid4()),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"BlueBubbles API error {e.code}: {e.read().decode()}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    report = run_health_check(config)
    prev = load_state(config)

    if prev is None:
        # First run — establish baseline, don't notify
        save_state(config, report)
        print(f"[marvin-notify] First run — baseline saved, no notifications sent.")
        return

    changes = compute_changes(prev, report)

    if changes:
        text = "Homelab: " + " ".join(changes)
        status = send_imessage(config, text)
        print(f"[marvin-notify] Notified ({status}): {text}")
    else:
        print(f"[marvin-notify] No state changes at {report['timestamp']}")

    save_state(config, report)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[marvin-notify] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
