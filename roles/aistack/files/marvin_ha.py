#!/usr/bin/env python3
"""Marvin HA — Home Assistant control tool for Marvin.

Usage:
  marvin_ha.py states [domain]               List entity states, optionally filtered by domain
  marvin_ha.py state <entity_id>             Get full state of a specific entity
  marvin_ha.py call <domain> <service> [json] Call a HA service with optional JSON payload
"""

import json
import sys
import urllib.request
import urllib.error

CONFIG_FILE = "/home/openclaw/marvin-ha.json"


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def ha_request(config, method, path, data=None):
    url = f"{config['url']}/api{path}"
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def cmd_states(config, argv):
    domain = argv[0] if argv else None
    states = ha_request(config, "GET", "/states")
    if domain:
        states = [s for s in states if s["entity_id"].startswith(f"{domain}.")]
    for s in sorted(states, key=lambda x: x["entity_id"]):
        name = s.get("attributes", {}).get("friendly_name", "")
        label = f"  ({name})" if name and name != s["entity_id"] else ""
        print(f"{s['entity_id']}  [{s['state']}]{label}")


def cmd_state(config, argv):
    if not argv:
        print("Usage: marvin_ha.py state <entity_id>", file=sys.stderr)
        sys.exit(1)
    result = ha_request(config, "GET", f"/states/{argv[0]}")
    print(json.dumps(result, indent=2))


def cmd_call(config, argv):
    if len(argv) < 2:
        print("Usage: marvin_ha.py call <domain> <service> [json_payload]", file=sys.stderr)
        sys.exit(1)
    domain, service = argv[0], argv[1]
    data = json.loads(argv[2]) if len(argv) > 2 else {}
    result = ha_request(config, "POST", f"/services/{domain}/{service}", data)
    print(json.dumps(result, indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    config = load_config()
    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "states":
        cmd_states(config, rest)
    elif cmd == "state":
        cmd_state(config, rest)
    elif cmd == "call":
        cmd_call(config, rest)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
