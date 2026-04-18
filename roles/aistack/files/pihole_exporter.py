#!/usr/bin/env python3
"""Pi-hole v6 Prometheus textfile exporter.

Polls the Pi-hole v6 REST API from 20-size (Pi-hole lives on kube01 at 10.0.1.250
but is reachable on the same LAN) and writes Prometheus metrics to the node_exporter
textfile collector directory. node_exporter ships them to Grafana Cloud (Mimir) via Alloy.

Run via cron as openclaw every 5 minutes:
  /usr/local/bin/marvin-pihole-exporter

Config: /home/openclaw/.openclaw/marvin-pihole.json
  {
    "pihole_url": "http://10.0.1.250",
    "password": "..."
  }

Metrics emitted:
  pihole_scrape_up                         1 if API auth succeeded, 0 if down
  pihole_queries_total                     Total DNS queries (FTL window, ~24h)
  pihole_queries_blocked                   Blocked DNS queries
  pihole_queries_cached                    Cached DNS queries
  pihole_queries_forwarded                 Forwarded DNS queries
  pihole_percent_blocked                   Percentage of queries blocked (0-100)
  pihole_unique_domains_total              Unique domains queried
  pihole_active_clients_total              Active client count
  pihole_gravity_domains_total             Domains in gravity (blocklist) database
  pihole_client_queries_total{ip,name}     Per-client query count — name is Pi-hole
                                           client alias, DHCP hostname, or IP fallback.
                                           Name devices as 'person-device' (e.g.
                                           jehan-iphone) for per-person Grafana grouping.
  pihole_client_blocked_total{ip,name}     Per-client blocked query count
  pihole_domain_blocked_total{domain}              Top-N blocked domains (global, FTL window)
  pihole_domain_queried_total{domain}              Top-N queried domains (global, FTL window)
  pihole_persona_domain_blocked_total{persona,domain}
                                                   Per-persona blocked domain counts (last 1h).
                                                   Persona label value matches the Grafana variable
                                                   value (e.g. "jehan-.*"). Fetched by a single
                                                   cursor-paginated pass through /api/queries,
                                                   filtering clients in Python. Capped at
                                                   TOP_PERSONA_DOMAINS_COUNT per persona.
  pihole_last_scrape_timestamp                     Unix timestamp of last exporter run
"""

import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path("/home/openclaw/.openclaw/marvin-pihole.json")
TEXTFILE_PATH = Path("/var/lib/node_exporter/textfile_collector/pihole.prom")
TOP_CLIENTS_COUNT = 100
TOP_DOMAINS_COUNT = 50
TOP_PERSONA_DOMAINS_COUNT = 30

# Persona → device base names (without .lan suffix).
# Keys are the Grafana variable values so {persona=~"$persona"} works directly.
PERSONA_DEVICES = {
    "jehan-.*":        ["jehan-iphone", "jehan-mbp", "jehan-gms", "jehan-work-mac", "jehan-watch"],
    "lindsay-.*":      ["lindsay-iphone", "lindsay-ipad"],
    "aubrey-.*":       ["aubrey-ipad"],
    "iain-.*":         ["iain-ipad", "iain-chromebook"],
    "charlene-.*":     ["charlene-iphone"],
    "marvinsmacmini":  ["marvinsmacmini"],
}

BLOCKED_STATUSES = {
    "GRAVITY", "REGEX", "DENYLIST",
    "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL", "EXTERNAL_BLOCKED_NXRA",
    "GRAVITY_CNAME", "REGEX_CNAME", "DENYLIST_CNAME",
}


def write_metrics_atomic(lines: list):
    TEXTFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TEXTFILE_PATH.with_suffix(".prom.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.rename(TEXTFILE_PATH)


def api_get(base_url: str, path: str, sid: str) -> dict:
    req = urllib.request.Request(
        f"{base_url}{path}",
        headers={"Accept": "application/json", "sid": sid},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def api_post(base_url: str, path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def api_delete(base_url: str, path: str, sid: str):
    req = urllib.request.Request(
        f"{base_url}{path}",
        headers={"sid": sid},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def escape_label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def fetch_persona_blocked_domains(base_url: str, sid: str) -> dict:
    """Return {persona_key: {domain: count}} for the last 1h.

    Pi-hole v6 /api/queries ignores all client/start filters (they're DataTables params,
    not time/client filters). Instead: paginate newest-first using cursor = oldest_id - 1,
    stop when query.time < now-3600, and filter clients in Python.

    ~8000 queries/hour at 100/page ≈ 80 API calls ≈ 45 seconds on LAN (Pi-hole FTL
    DB overhead per page dominates). Total cron runtime is ~50s, well within 5 min.
    """
    start_1h = time.time() - 3600

    # Map both "device" and "device.lan" → persona_key for O(1) lookups
    device_to_persona: dict = {}
    for persona_key, devices in PERSONA_DEVICES.items():
        for device in devices:
            device_to_persona[device] = persona_key
            device_to_persona[f"{device}.lan"] = persona_key

    persona_domains: dict = {pk: {} for pk in PERSONA_DEVICES}
    cursor = None

    while True:
        params = "limit=100"
        if cursor is not None:
            params += f"&cursor={urllib.parse.quote(str(cursor))}"
        try:
            resp = api_get(base_url, f"/api/queries?{params}", sid)
            queries = resp.get("queries", [])
            if not queries:
                break

            done = False
            for q in queries:
                ts = q.get("time", 0)
                if ts < start_1h:
                    done = True
                    break

                client_info = q.get("client", {})
                client_name = client_info.get("name", "")
                persona_key = device_to_persona.get(client_name)
                if persona_key is None:
                    continue

                if q.get("status", "") in BLOCKED_STATUSES:
                    domain = q.get("domain", "")
                    if domain:
                        dc = persona_domains[persona_key]
                        dc[domain] = dc.get(domain, 0) + 1

            if done:
                break

            # Advance cursor to oldest_id - 1 to get the next older page
            cursor = queries[-1]["id"] - 1
            if cursor <= 0:
                break
        except Exception as e:
            print(f"[warn] persona domains pagination: {e}", file=sys.stderr)
            break

    return persona_domains


def main():
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        print(f"[error] Cannot read config {CONFIG_PATH}: {e}", file=sys.stderr)
        sys.exit(1)

    base_url = cfg["pihole_url"].rstrip("/")
    password = cfg["password"]
    now_ts = int(datetime.now(timezone.utc).timestamp())
    out = []

    # ── Authenticate ──────────────────────────────────────────────────────────
    sid = None
    try:
        auth = api_post(base_url, "/api/auth", {"password": password})
        session = auth.get("session", {})
        if session.get("valid"):
            sid = session["sid"]
    except Exception as e:
        print(f"[error] Auth failed: {e}", file=sys.stderr)

    scrape_up = 1 if sid else 0
    out.append("# HELP pihole_scrape_up 1 if Pi-hole API auth succeeded, 0 if down")
    out.append("# TYPE pihole_scrape_up gauge")
    out.append(f"pihole_scrape_up {scrape_up}")

    if sid:
        # ── Summary stats ─────────────────────────────────────────────────────
        try:
            summary = api_get(base_url, "/api/stats/summary", sid)
            queries = summary.get("queries", {})
            clients_stat = summary.get("clients", {})
            gravity = summary.get("gravity", {})

            for metric, help_text, value in (
                ("pihole_queries_total",        "Total DNS queries (FTL window, ~24h)",    queries.get("total")),
                ("pihole_queries_blocked",       "Blocked DNS queries",                     queries.get("blocked")),
                ("pihole_queries_cached",        "Cached DNS queries",                      queries.get("cached")),
                ("pihole_queries_forwarded",     "Forwarded DNS queries",                   queries.get("forwarded")),
                ("pihole_percent_blocked",       "Percentage of queries blocked (0-100)",   queries.get("percent_blocked")),
                ("pihole_unique_domains_total",  "Unique domains queried",                  queries.get("unique_domains")),
                ("pihole_active_clients_total",  "Number of active DNS clients",            clients_stat.get("active")),
                ("pihole_gravity_domains_total", "Domains in gravity (blocklist) database", gravity.get("domains_being_blocked")),
            ):
                if value is not None:
                    out.append("")
                    out.append(f"# HELP {metric} {help_text}")
                    out.append(f"# TYPE {metric} gauge")
                    out.append(f"{metric} {value}")
        except Exception as e:
            print(f"[warn] stats/summary: {e}", file=sys.stderr)

        # ── Per-client query counts ───────────────────────────────────────────
        try:
            resp = api_get(base_url, f"/api/stats/top_clients?count={TOP_CLIENTS_COUNT}", sid)
            client_list = resp.get("clients", [])
            if client_list:
                out.append("")
                out.append("# HELP pihole_client_queries_total DNS queries per client (FTL window, ~24h)")
                out.append("# TYPE pihole_client_queries_total gauge")
                for entry in client_list:
                    ip = escape_label(str(entry.get("ip", "")))
                    name = escape_label(str(entry.get("name") or entry.get("ip", "")))
                    count = entry.get("queries", entry.get("count", 0))
                    out.append(f'pihole_client_queries_total{{ip="{ip}",name="{name}"}} {count}')
        except Exception as e:
            print(f"[warn] stats/top_clients: {e}", file=sys.stderr)

        # ── Per-client blocked counts ─────────────────────────────────────────
        try:
            resp = api_get(base_url, f"/api/stats/top_clients?blocked=true&count={TOP_CLIENTS_COUNT}", sid)
            client_list = resp.get("clients", [])
            if client_list:
                out.append("")
                out.append("# HELP pihole_client_blocked_total Blocked DNS queries per client (FTL window, ~24h)")
                out.append("# TYPE pihole_client_blocked_total gauge")
                for entry in client_list:
                    ip = escape_label(str(entry.get("ip", "")))
                    name = escape_label(str(entry.get("name") or entry.get("ip", "")))
                    count = entry.get("count", entry.get("queries", 0))
                    out.append(f'pihole_client_blocked_total{{ip="{ip}",name="{name}"}} {count}')
        except Exception as e:
            print(f"[warn] stats/top_clients?blocked: {e}", file=sys.stderr)

        # ── Top blocked domains (global) ─────────────────────────────────────
        try:
            resp = api_get(base_url, f"/api/stats/top_domains?blocked=true&count={TOP_DOMAINS_COUNT}", sid)
            domain_list = resp.get("domains", [])
            if domain_list:
                out.append("")
                out.append("# HELP pihole_domain_blocked_total Top blocked domains — global FTL window. Per-persona scope requires FTL DB.")
                out.append("# TYPE pihole_domain_blocked_total gauge")
                for entry in domain_list:
                    domain = escape_label(str(entry.get("domain", "")))
                    count = entry.get("count", 0)
                    out.append(f'pihole_domain_blocked_total{{domain="{domain}"}} {count}')
        except Exception as e:
            print(f"[warn] stats/top_domains?blocked: {e}", file=sys.stderr)

        # ── Top queried domains (global) ──────────────────────────────────────
        try:
            resp = api_get(base_url, f"/api/stats/top_domains?count={TOP_DOMAINS_COUNT}", sid)
            domain_list = resp.get("domains", [])
            if domain_list:
                out.append("")
                out.append("# HELP pihole_domain_queried_total Top queried domains — global FTL window.")
                out.append("# TYPE pihole_domain_queried_total gauge")
                for entry in domain_list:
                    domain = escape_label(str(entry.get("domain", "")))
                    count = entry.get("count", 0)
                    out.append(f'pihole_domain_queried_total{{domain="{domain}"}} {count}')
        except Exception as e:
            print(f"[warn] stats/top_domains: {e}", file=sys.stderr)

        # ── Per-persona blocked domains ───────────────────────────────────────
        try:
            persona_domains = fetch_persona_blocked_domains(base_url, sid)
            if any(persona_domains.values()):
                out.append("")
                out.append("# HELP pihole_persona_domain_blocked_total Blocked domain counts per persona over the last 1h")
                out.append("# TYPE pihole_persona_domain_blocked_total gauge")
                for persona_key, domain_counts in persona_domains.items():
                    top = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[:TOP_PERSONA_DOMAINS_COUNT]
                    for domain, count in top:
                        p = escape_label(persona_key)
                        d = escape_label(domain)
                        out.append(f'pihole_persona_domain_blocked_total{{persona="{p}",domain="{d}"}} {count}')
        except Exception as e:
            print(f"[warn] per-persona domains: {e}", file=sys.stderr)

        # ── Logout ────────────────────────────────────────────────────────────
        api_delete(base_url, "/api/auth", sid)

    # ── Scrape timestamp ──────────────────────────────────────────────────────
    out.append("")
    out.append("# HELP pihole_last_scrape_timestamp Unix timestamp of last exporter run")
    out.append("# TYPE pihole_last_scrape_timestamp gauge")
    out.append(f"pihole_last_scrape_timestamp {now_ts}")
    out.append("")
    out.append(f"# generated by marvin-pihole-exporter at {datetime.now(timezone.utc).isoformat()}")

    write_metrics_atomic(out)
    print(f"Wrote {TEXTFILE_PATH} — scrape_up={scrape_up}, ts={now_ts}")


if __name__ == "__main__":
    main()
