#!/usr/bin/env python3
"""Push Grafana dashboard JSON files to alvani.grafana.net.

Usage:
    grafana_deploy.py <dashboard.json> [...]   deploy specific files
    grafana_deploy.py --all                    deploy all dashboards in grafana/

Auth token is read from 1Password at runtime.
"""

import json
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

GRAFANA_URL = "https://alvani.grafana.net"
DASHBOARDS_DIR = Path(__file__).parent.parent / "k8s/apps/monitoring/grafana"
OP_TOKEN_REF = "op://HomeLab/Grafana - MarvinMail Dashboarding Service Account Token/credential"

# Defensive: replace any __inputs-style datasource template vars if present
DATASOURCE_MAP = {
    "${DS_PROMETHEUS}": "grafanacloud-prom",
    "${DS_LOKI}": "grafanacloud-logs",
}


def get_token() -> str:
    result = subprocess.run(
        ["op", "read", OP_TOKEN_REF],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def get_git_sha() -> str:
    result = subprocess.run(
        ["git", "-C", str(Path(__file__).parent.parent), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _fix_datasource_vars(obj):
    if isinstance(obj, dict):
        return {k: _fix_datasource_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fix_datasource_vars(v) for v in obj]
    if isinstance(obj, str):
        for placeholder, uid in DATASOURCE_MAP.items():
            obj = obj.replace(placeholder, uid)
    return obj


def prepare(dashboard: dict) -> dict:
    d = {k: v for k, v in dashboard.items() if k not in ("__inputs", "__requires", "__elements")}
    d = _fix_datasource_vars(d)
    d["id"] = None  # Grafana resolves the numeric ID from the UID on upsert
    return d


def deploy_one(path: Path, token: str, sha: str) -> None:
    dashboard = json.loads(path.read_text())
    uid = dashboard.get("uid", path.stem)
    title = dashboard.get("title", path.stem)

    payload = json.dumps({
        "dashboard": prepare(dashboard),
        "overwrite": True,
        "message": f"{sha} — {path.name}",
    }).encode()

    req = urllib.request.Request(
        f"{GRAFANA_URL}/api/dashboards/db",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.load(resp)
        version = result.get("version", "?")
        url_path = result.get("url", f"/d/{uid}")
        print(f"  ok  {title}  v{version}  {GRAFANA_URL}{url_path}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ERR {title}  HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--all":
        paths = sorted(DASHBOARDS_DIR.glob("*.json"))
        if not paths:
            print(f"no JSON files found in {DASHBOARDS_DIR}", file=sys.stderr)
            sys.exit(1)
    else:
        paths = [Path(p).resolve() for p in sys.argv[1:]]

    token = get_token()
    sha = get_git_sha()

    for path in paths:
        deploy_one(path, token, sha)


if __name__ == "__main__":
    main()
