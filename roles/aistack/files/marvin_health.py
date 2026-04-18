#!/usr/bin/env python3
"""Marvin Health — HTTP reachability, systemd status, and SSL expiry checks.

Outputs a JSON report to stdout. Marvin's heartbeat runs this, diffs against
memory/homelab-state.json, and notifies Jehan on state changes.

Exit code is always 0 — errors are reported in the JSON, not via exit code.
"""

import base64
import datetime
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

HTTP_TIMEOUT = 5   # seconds per HTTP check
SSL_TIMEOUT = 10   # seconds per SSL check
SSL_WARN_DAYS = 21 # warn when cert expires within this many days
DISK_WARN_PCT = 80 # warn when filesystem used% exceeds this
DISK_CRIT_PCT = 90 # critical when filesystem used% exceeds this

# Grafana Cloud series count thresholds — free tier limit is ~10k active series.
# Warn before hitting limits; critical means you're paying overage.
GRAFANA_CLOUD_CONFIG = Path("/home/openclaw/.openclaw/grafana-cloud.json")
SERIES_WARN = 12_000
SERIES_CRIT = 20_000

# Simulation override file — written by marvin_health_sim.py for outage testing.
# Format: {"http": {"SearXNG": "down"}, "systemd": {"nginx": "inactive"}}
SIM_FILE = Path("/tmp/marvin_health_sim.json")

# ── Configuration ─────────────────────────────────────────────────────────────

HTTP_CHECKS = [
    # AI Stack
    {"name": "Ollama",         "url": "http://10.0.1.203:11434"},
    {"name": "LiteLLM",        "url": "http://10.0.1.203:4000"},
    {"name": "SearXNG",        "url": "http://10.0.1.203:8080"},
    # Media
    {"name": "Plex",           "url": "http://plex.lan"},
    {"name": "Sonarr",         "url": "http://sonarr.lan"},
    {"name": "Radarr",         "url": "http://radarr.lan"},
    {"name": "Lidarr",         "url": "http://lidarr.lan"},
    {"name": "Readarr",        "url": "http://readarr.lan"},
    {"name": "Prowlarr",       "url": "http://prowlarr.lan"},
    {"name": "nzbget",         "url": "http://nzbget.lan"},
    {"name": "Transmission",   "url": "http://transmission.lan"},
    {"name": "Ombi",           "url": "http://ombi.lan"},
    # Home & Infrastructure
    {"name": "Home Assistant", "url": "http://homeassistant.lan"},
    {"name": "Pi-hole",        "url": "http://pihole.lan"},
    {"name": "OpenClaw",       "url": "http://openclaw.lan"},
]

# Local filesystem checks — runs on 20-size only.
# The k3s nodes (kube01-03) are covered by node-exporter → Mimir → Grafana Cloud.
DISK_CHECKS = [
    {"name": "root",       "mount": "/"},
    {"name": "seatac",     "mount": "/seatac"},
    {"name": "snoqualmie", "mount": "/snoqualmie"},
    {"name": "whidbey",    "mount": "/whidbey"},
]

SYSTEMD_CHECKS = [
    "openclaw-gateway",
    "nginx",
    "docker",
    "k3s-agent",
]

SSL_CHECKS = [
    {"name": "SearXNG (external)", "host": "searxng.alvani.me", "port": 443},
    {"name": "OpenClaw (LAN)",     "host": "openclaw.lan",      "port": 443},
]

# ── Check implementations ──────────────────────────────────────────────────────

def check_http(entry):
    try:
        req = urllib.request.Request(entry["url"])
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return {"name": entry["name"], "status": "up", "code": resp.status}
    except urllib.error.HTTPError as e:
        # Any HTTP response means the service is reachable
        return {"name": entry["name"], "status": "up", "code": e.code}
    except Exception as e:
        return {"name": entry["name"], "status": "down", "error": str(e)}


def check_disk(entry):
    mount = entry["mount"]
    try:
        usage = shutil.disk_usage(mount)
        used_pct = usage.used / usage.total * 100
        if used_pct >= DISK_CRIT_PCT:
            status = "critical"
        elif used_pct >= DISK_WARN_PCT:
            status = "warn"
        else:
            status = "ok"
        return {
            "name":       entry["name"],
            "mount":      mount,
            "status":     status,
            "used_pct":   round(used_pct, 1),
            "avail_bytes": usage.free,
            "total_bytes": usage.total,
        }
    except Exception as e:
        return {"name": entry["name"], "mount": mount, "status": "error", "error": str(e)}


def check_systemd(service):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        state = result.stdout.strip()
        return {
            "name": service,
            "status": "active" if state == "active" else "inactive",
            "state": state,
        }
    except Exception as e:
        return {"name": service, "status": "error", "error": str(e)}


def check_ssl(entry):
    host, port = entry["host"], entry["port"]
    try:
        # Use openssl s_client to fetch the cert (works with homelab CA without
        # needing to trust the chain). -verify_quiet suppresses chain warnings.
        s_client = subprocess.run(
            ["openssl", "s_client", "-connect", f"{host}:{port}",
             "-servername", host, "-verify_quiet"],
            input="Q\n",
            capture_output=True, text=True, timeout=SSL_TIMEOUT,
        )
        x509 = subprocess.run(
            ["openssl", "x509", "-noout", "-enddate"],
            input=s_client.stdout,
            capture_output=True, text=True, timeout=5,
        )
        line = x509.stdout.strip()
        if not line.startswith("notAfter="):
            return {"name": entry["name"], "host": host, "port": port,
                    "status": "error", "error": f"could not parse cert: {x509.stderr.strip()}"}
        expiry_str = line.removeprefix("notAfter=")
        expiry = datetime.datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
        days = (expiry - datetime.datetime.utcnow()).days
        if days < 0:
            status = "expired"
        elif days < SSL_WARN_DAYS:
            status = "expiring_soon"
        else:
            status = "ok"
        return {"name": entry["name"], "host": host, "port": port,
                "status": status, "expires_in_days": days}
    except Exception as e:
        return {"name": entry["name"], "host": host, "port": port,
                "status": "error", "error": str(e)}


# ── Grafana Cloud series count ────────────────────────────────────────────────

def check_grafana_cloud_series():
    if not GRAFANA_CLOUD_CONFIG.exists():
        return {"name": "Grafana Cloud series", "status": "skip", "reason": "no config at grafana-cloud.json"}
    try:
        cfg = json.loads(GRAFANA_CLOUD_CONFIG.read_text())
        base_url = cfg["prometheus_url"].rstrip("/").removesuffix("/push")
        query_url = f"{base_url}/api/v1/query"
        params = urllib.parse.urlencode({"query": "count({__name__=~'.+'})"})
        req = urllib.request.Request(f"{query_url}?{params}")
        auth = base64.b64encode(f"{cfg['prometheus_user']}:{cfg['api_key']}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = data.get("data", {}).get("result", [])
        if not results:
            return {"name": "Grafana Cloud series", "status": "error", "error": "empty result"}
        count = int(float(results[0]["value"][1]))
        if count >= SERIES_CRIT:
            status = "critical"
        elif count >= SERIES_WARN:
            status = "warn"
        else:
            status = "ok"
        return {"name": "Grafana Cloud series", "status": status, "series": count}
    except Exception as e:
        return {"name": "Grafana Cloud series", "status": "error", "error": str(e)}


# ── Simulation overrides ──────────────────────────────────────────────────────

def apply_sim_overrides(report: dict) -> dict:
    """Merge simulation overrides from SIM_FILE into the report, if present."""
    if not SIM_FILE.exists():
        return report
    try:
        overrides = json.loads(SIM_FILE.read_text())
        for entry in report.get("http", []):
            if entry["name"] in overrides.get("http", {}):
                entry["status"] = overrides["http"][entry["name"]]
                entry.pop("code", None)
                entry["simulated"] = True
        for entry in report.get("systemd", []):
            if entry["name"] in overrides.get("systemd", {}):
                entry["status"] = overrides["systemd"][entry["name"]]
                entry["simulated"] = True
    except Exception as e:
        print(f"[warn] could not apply sim overrides: {e}", file=sys.stderr)
    return report


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    report = {
        "timestamp": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "http":           [check_http(e)    for e in HTTP_CHECKS],
        "systemd":        [check_systemd(s) for s in SYSTEMD_CHECKS],
        "ssl":            [check_ssl(e)     for e in SSL_CHECKS],
        "disk":           [check_disk(e)    for e in DISK_CHECKS],
        "grafana_cloud":  check_grafana_cloud_series(),
    }
    report = apply_sim_overrides(report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
