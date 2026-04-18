#!/usr/bin/env python3
"""Netgear Orbi Prometheus textfile exporter.

Uses pynetgear to poll the Orbi SOAP API over HTTPS (port 443) and writes
Prometheus metrics to the node_exporter textfile collector directory.
node_exporter ships them to Grafana Cloud (Mimir) via Alloy.

Run via the venv Python set up by Ansible:
  /home/openclaw/.venv/netgear/bin/python3 /usr/local/bin/marvin-netgear-exporter

Config: /home/openclaw/.openclaw/marvin-netgear.json
  {
    "router_ip": "10.0.1.1",
    "soap_port": 443,
    "soap_ssl": true,
    "username": "admin",
    "password": "..."
  }

Traffic meter must be enabled in the Orbi web UI (Advanced → Advanced Setup →
Traffic Meter) or the traffic metrics will be absent.

Metrics emitted:
  netgear_scrape_up                      1 if SOAP API login succeeded, 0 if down
  netgear_cpu_utilization_percent        CPU utilization (0-100)
  netgear_mem_utilization_percent        Memory utilization (0-100)
  netgear_traffic_today_upload_bytes     WAN upload today (bytes)
  netgear_traffic_today_download_bytes   WAN download today (bytes)
  netgear_traffic_month_upload_bytes     WAN upload this month (bytes)
  netgear_traffic_month_download_bytes   WAN download this month (bytes)
  netgear_attached_devices_total         Total devices attached to the Orbi mesh
  netgear_last_scrape_timestamp          Unix timestamp of last exporter run
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from pynetgear import Netgear
except ImportError:
    print("[error] pynetgear not found — run from /home/openclaw/.venv/netgear/bin/python3", file=sys.stderr)
    sys.exit(1)

CONFIG_PATH  = Path("/home/openclaw/.openclaw/marvin-netgear.json")
TEXTFILE_PATH = Path("/var/lib/node_exporter/textfile_collector/marvin_netgear.prom")


def mb_to_bytes(v) -> int | None:
    """Convert an MB value (string or float) to integer bytes."""
    if v is None:
        return None
    try:
        return int(float(v) * 1024 * 1024)
    except (ValueError, TypeError):
        return None


def write_metrics_atomic(lines: list):
    TEXTFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TEXTFILE_PATH.with_suffix(".prom.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.rename(TEXTFILE_PATH)


def main():
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        print(f"[error] Cannot read config {CONFIG_PATH}: {e}", file=sys.stderr)
        sys.exit(1)

    router = Netgear(
        host=cfg["router_ip"],
        port=cfg.get("soap_port", 443),
        ssl=cfg.get("soap_ssl", True),
        user=cfg.get("username", "admin"),
        password=cfg["password"],
    )

    scrape_up = 1 if router.login() else 0
    now_ts = int(datetime.now(timezone.utc).timestamp())
    out = []

    # ── Scrape health ─────────────────────────────────────────────────────────
    out.append("# HELP netgear_scrape_up 1 if the Orbi SOAP API login succeeded, 0 if down")
    out.append("# TYPE netgear_scrape_up gauge")
    out.append(f"netgear_scrape_up {scrape_up}")

    if scrape_up:
        # ── System info (CPU / RAM) ───────────────────────────────────────────
        out.append("")
        out.append("# HELP netgear_cpu_utilization_percent Router CPU utilization (0-100)")
        out.append("# TYPE netgear_cpu_utilization_percent gauge")
        out.append("# HELP netgear_mem_utilization_percent Router memory utilization (0-100)")
        out.append("# TYPE netgear_mem_utilization_percent gauge")
        try:
            info = router.get_system_info()
            if info:
                cpu = info.get("CPU_Utilization")
                mem = info.get("MemoryUtilization")
                if cpu is not None:
                    out.append(f"netgear_cpu_utilization_percent {cpu}")
                if mem is not None:
                    out.append(f"netgear_mem_utilization_percent {mem}")
        except Exception as e:
            print(f"[warn] get_system_info: {e}", file=sys.stderr)

        # ── Traffic meter ─────────────────────────────────────────────────────
        out.append("")
        out.append("# HELP netgear_traffic_today_upload_bytes WAN upload bytes today (resets midnight)")
        out.append("# TYPE netgear_traffic_today_upload_bytes gauge")
        out.append("# HELP netgear_traffic_today_download_bytes WAN download bytes today (resets midnight)")
        out.append("# TYPE netgear_traffic_today_download_bytes gauge")
        out.append("# HELP netgear_traffic_month_upload_bytes WAN upload bytes this month")
        out.append("# TYPE netgear_traffic_month_upload_bytes gauge")
        out.append("# HELP netgear_traffic_month_download_bytes WAN download bytes this month")
        out.append("# TYPE netgear_traffic_month_download_bytes gauge")
        try:
            traffic = router.get_traffic_meter()
            if traffic:
                for metric, key in (
                    ("netgear_traffic_today_upload_bytes",   "NewTodayUpload"),
                    ("netgear_traffic_today_download_bytes", "NewTodayDownload"),
                    ("netgear_traffic_month_upload_bytes",   "NewMonthUpload"),
                    ("netgear_traffic_month_download_bytes", "NewMonthDownload"),
                ):
                    val = mb_to_bytes(traffic.get(key))
                    if val is not None:
                        out.append(f"{metric} {val}")
        except Exception as e:
            print(f"[warn] get_traffic_meter: {e}", file=sys.stderr)

        # ── Attached devices ──────────────────────────────────────────────────
        out.append("")
        out.append("# HELP netgear_attached_devices_total Total devices attached to the Orbi mesh")
        out.append("# TYPE netgear_attached_devices_total gauge")
        try:
            devices = router.get_attached_devices()
            if devices is not None:
                out.append(f"netgear_attached_devices_total {len(devices)}")
        except Exception as e:
            print(f"[warn] get_attached_devices: {e}", file=sys.stderr)

    # ── Scrape timestamp ──────────────────────────────────────────────────────
    out.append("")
    out.append("# HELP netgear_last_scrape_timestamp Unix timestamp of last exporter run")
    out.append("# TYPE netgear_last_scrape_timestamp gauge")
    out.append(f"netgear_last_scrape_timestamp {now_ts}")
    out.append("")
    out.append(f"# generated by marvin-netgear-exporter at {datetime.now(timezone.utc).isoformat()}")

    write_metrics_atomic(out)
    print(f"Wrote {TEXTFILE_PATH} — scrape_up={scrape_up}, ts={now_ts}")


if __name__ == "__main__":
    main()
