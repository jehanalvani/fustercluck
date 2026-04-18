#!/usr/bin/env python3
"""Marvin Health Simulator — inject fake service outages for notification testing.

Usage:
  marvin_health_sim.py start <service> [--type http|systemd]
      Inject a fake outage for <service> and start a timer.
      Marvin will detect the state change on his next heartbeat (every 15 min)
      or immediately if you message him: "check the homelab".

  marvin_health_sim.py clear
      Remove the simulation. Service returns to normal on next health check.

  marvin_health_sim.py status
      Show what's currently being simulated.

HTTP services:   Ollama, LiteLLM, SearXNG, Plex, Sonarr, Radarr, Lidarr,
                 Readarr, Prowlarr, nzbget, Transmission, Ombi,
                 Home Assistant, Pi-hole, OpenClaw
systemd services: openclaw-gateway, nginx, docker, k3s-agent
"""

import datetime
import json
import sys
import time
from pathlib import Path

SIM_FILE = Path("/tmp/marvin_health_sim.json")


def cmd_start(service: str, svc_type: str):
    sim = {svc_type: {service: "down" if svc_type == "http" else "inactive"}}
    SIM_FILE.write_text(json.dumps(sim, indent=2))

    started = datetime.datetime.now()
    print(f"\n{'─' * 52}")
    print(f"  SIMULATING OUTAGE: {service} ({svc_type})")
    print(f"  Started: {started.strftime('%H:%M:%S')}")
    print(f"{'─' * 52}")
    print(f"  marvin_health.py will now report {service} as down.")
    print(f"  Marvin checks every 15 min — or message him:")
    print(f'  "check the homelab" for an immediate test.')
    print(f"{'─' * 52}\n")
    print("  Timer running — Ctrl+C to clear and stop.\n")

    try:
        while True:
            elapsed = datetime.datetime.now() - started
            secs = int(elapsed.total_seconds())
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            timer = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            print(f"\r  Elapsed: {timer}  (Ctrl+C to clear)", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n\n  Clearing simulation for {service}...")
        SIM_FILE.unlink(missing_ok=True)
        total = datetime.datetime.now() - started
        print(f"  Done. Simulation ran for {str(total).split('.')[0]}.\n")


def cmd_clear():
    if SIM_FILE.exists():
        sim = json.loads(SIM_FILE.read_text())
        SIM_FILE.unlink()
        print(f"Cleared simulation: {sim}")
    else:
        print("No active simulation.")


def cmd_status():
    if SIM_FILE.exists():
        sim = json.loads(SIM_FILE.read_text())
        print(f"Active simulation:\n{json.dumps(sim, indent=2)}")
        print(f"\nmarvin_health.py will report these services as down/inactive.")
    else:
        print("No active simulation.")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]

    if cmd == "start":
        if len(args) < 2:
            print("Usage: marvin_health_sim.py start <service> [--type http|systemd]")
            sys.exit(1)
        service = args[1]
        svc_type = "http"
        if "--type" in args:
            idx = args.index("--type")
            svc_type = args[idx + 1]
        cmd_start(service, svc_type)

    elif cmd == "clear":
        cmd_clear()

    elif cmd == "status":
        cmd_status()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
