#!/usr/bin/env python3
"""Marvin mail Prometheus textfile exporter.

Writes /var/lib/node_exporter/textfile_collector/marvin_mail.prom every run.
node_exporter picks it up via --collector.textfile.directory.

Metrics emitted:
  marvin_mail_folder_messages{account,folder}     IMAP folder message count
  marvin_mail_last_run_timestamp{account,run_type} Unix ts of last triage run
  marvin_mail_last_run_sorted{account,run_type}    Emails sorted in last run
  marvin_mail_last_run_errors{account,run_type}    Errors in last run
  marvin_mail_last_run_by_label{account,label}     Label breakdown (full_sort only)
  marvin_mail_scrape_errors{account}               1 if IMAP connect failed

Privacy: accounts with role=minor in profile.json emit only INBOX count,
no per-folder breakdown.
"""

import imaplib
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

MAIL_CONFIG = Path("/home/openclaw/.openclaw/marvin-mail.json")
ACCOUNTS_BASE = Path("/home/openclaw/.openclaw/accounts")
TEXTFILE_PATH = Path("/var/lib/node_exporter/textfile_collector/marvin_mail.prom")
IMAP_HOST = "imap.fastmail.com"
IMAP_PORT = 993
SOCKET_TIMEOUT = 30  # seconds — per account


def load_config() -> dict:
    return json.loads(MAIL_CONFIG.read_text())


def load_profile(account_id: str) -> dict:
    p = ACCOUNTS_BASE / account_id / "profile.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"role": "adult", "privacy": "adult"}


def last_run_stats(account_id: str) -> dict:
    """Return {run_type: last_log_entry} by scanning triage.log from the end.

    Scans in reverse so the first hit per run_type is always the most recent.
    Stops early once all three known run types are found.
    """
    log_path = ACCOUNTS_BASE / account_id / "logs" / "triage.log"
    if not log_path.exists():
        return {}

    by_run_type = {}
    try:
        lines = log_path.read_text(errors="replace").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                rt = entry.get("run_type", "unknown")
                if rt not in by_run_type:
                    by_run_type[rt] = entry
                if len(by_run_type) >= 3:  # full_sort, spam_only, window_shopping
                    break
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return by_run_type


def imap_folder_counts(email: str, password: str, is_minor: bool) -> tuple:
    """Connect to IMAP and return ({folder: message_count}, had_error)."""
    counts = {}
    try:
        socket.setdefaulttimeout(SOCKET_TIMEOUT)
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        try:
            conn.login(email, password)

            if is_minor:
                # Privacy enforcement: only report INBOX for minor accounts
                folder_names = ["INBOX"]
            else:
                typ, data = conn.list()
                folder_names = _parse_imap_list(data or [])

            for folder in folder_names:
                # IMAP requires quoting folder names that contain spaces
                quoted = f'"{folder}"' if " " in folder else folder
                try:
                    typ, status_data = conn.status(quoted, "(MESSAGES)")
                    if typ == "OK" and status_data and status_data[0]:
                        raw = status_data[0]
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        m = re.search(r"MESSAGES (\d+)", raw)
                        if m:
                            counts[folder] = int(m.group(1))
                except Exception:
                    pass
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as exc:
        print(f"[warn] IMAP error for {email}: {exc}", file=sys.stderr)
        return counts, True

    return counts, False


def _parse_imap_list(data: list) -> list:
    """Parse IMAP LIST response into folder names, skipping \\Noselect folders."""
    folders = []
    # e.g. `(\HasNoChildren) "/" "INBOX"` or `(\HasChildren) "/" Sent`
    pattern = re.compile(
        r'\((?P<flags>[^)]*)\)\s+"[^"]*"\s+(?:"(?P<qname>[^"]+)"|(?P<bare>\S+))'
    )
    for item in data:
        if item is None:
            continue
        if isinstance(item, bytes):
            item = item.decode("utf-8", errors="replace")
        m = pattern.match(item.strip())
        if not m:
            continue
        flags = m.group("flags") or ""
        if "\\Noselect" in flags:
            continue
        name = m.group("qname") or m.group("bare") or ""
        if name:
            folders.append(name)
    return folders


def _escape_label(v: str) -> str:
    """Escape a label value for Prometheus exposition format."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_metrics_atomic(lines: list):
    """Write the .prom file atomically — node_exporter never reads a partial file."""
    TEXTFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TEXTFILE_PATH.with_suffix(".prom.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.rename(TEXTFILE_PATH)


def main():
    cfg = load_config()
    accounts = cfg.get("accounts", {})

    out = []

    # ── IMAP folder message counts ────────────────────────────────────────────
    out.append("# HELP marvin_mail_folder_messages Number of messages in IMAP folder")
    out.append("# TYPE marvin_mail_folder_messages gauge")

    scrape_errors: dict = {}

    for acct_id, acct in accounts.items():
        email = acct.get("email", "")
        password = acct.get("password", acct.get("app_password", ""))
        if not email or not password:
            continue

        profile = load_profile(acct_id)
        is_minor = profile.get("role") == "minor"

        counts, had_error = imap_folder_counts(email, password, is_minor)
        scrape_errors[acct_id] = 1 if had_error else 0

        for folder, count in sorted(counts.items()):
            safe_folder = _escape_label(folder)
            out.append(
                f'marvin_mail_folder_messages{{account="{acct_id}",folder="{safe_folder}"}} {count}'
            )

    # ── last run statistics (derived from structured triage.log) ─────────────
    out.append("")
    out.append("# HELP marvin_mail_last_run_timestamp Unix timestamp of last triage run")
    out.append("# TYPE marvin_mail_last_run_timestamp gauge")

    last_runs: dict = {}
    for acct_id in accounts:
        stats = last_run_stats(acct_id)
        last_runs[acct_id] = stats
        for run_type, entry in stats.items():
            ts_str = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                ts = 0
            out.append(
                f'marvin_mail_last_run_timestamp{{account="{acct_id}",run_type="{run_type}"}} {ts:.0f}'
            )

    out.append("")
    out.append("# HELP marvin_mail_last_run_sorted Emails sorted in last triage run")
    out.append("# TYPE marvin_mail_last_run_sorted gauge")
    for acct_id, stats in last_runs.items():
        for run_type, entry in stats.items():
            out.append(
                f'marvin_mail_last_run_sorted{{account="{acct_id}",run_type="{run_type}"}} {entry.get("sorted", 0)}'
            )

    out.append("")
    out.append("# HELP marvin_mail_last_run_errors Errors in last triage run")
    out.append("# TYPE marvin_mail_last_run_errors gauge")
    for acct_id, stats in last_runs.items():
        for run_type, entry in stats.items():
            out.append(
                f'marvin_mail_last_run_errors{{account="{acct_id}",run_type="{run_type}"}} {entry.get("errors", 0)}'
            )

    out.append("")
    out.append("# HELP marvin_mail_last_run_spam_check_caught Emails caught by heuristic pre-filter as likely_scam (LLM call skipped)")
    out.append("# TYPE marvin_mail_last_run_spam_check_caught gauge")
    for acct_id, stats in last_runs.items():
        for run_type, entry in stats.items():
            caught = entry.get("spam_check_caught", 0)
            out.append(
                f'marvin_mail_last_run_spam_check_caught{{account="{acct_id}",run_type="{run_type}"}} {caught}'
            )

    out.append("")
    out.append("# HELP marvin_mail_last_run_spam_check_suspicious Emails flagged suspicious by heuristic pre-filter and routed to Spam")
    out.append("# TYPE marvin_mail_last_run_spam_check_suspicious gauge")
    for acct_id, stats in last_runs.items():
        for run_type, entry in stats.items():
            suspicious = entry.get("spam_check_suspicious", 0)
            out.append(
                f'marvin_mail_last_run_spam_check_suspicious{{account="{acct_id}",run_type="{run_type}"}} {suspicious}'
            )

    out.append("")
    out.append(
        "# HELP marvin_mail_last_run_by_label Emails in each label from last full_sort run"
    )
    out.append("# TYPE marvin_mail_last_run_by_label gauge")
    for acct_id, stats in last_runs.items():
        entry = stats.get("full_sort")
        if not entry:
            continue
        for lbl, count in sorted(entry.get("by_label", {}).items()):
            safe_lbl = _escape_label(lbl)
            out.append(
                f'marvin_mail_last_run_by_label{{account="{acct_id}",label="{safe_lbl}"}} {count}'
            )

    # ── scrape health ─────────────────────────────────────────────────────────
    out.append("")
    out.append(
        "# HELP marvin_mail_scrape_errors 1 if the last IMAP scrape for this account failed"
    )
    out.append("# TYPE marvin_mail_scrape_errors gauge")
    for acct_id, err in scrape_errors.items():
        out.append(f'marvin_mail_scrape_errors{{account="{acct_id}"}} {err}')

    out.append("")
    out.append(
        f"# generated by marvin-mail-exporter at {datetime.now(timezone.utc).isoformat()}"
    )

    write_metrics_atomic(out)
    print(
        f"Wrote {TEXTFILE_PATH} — {len(accounts)} accounts, "
        f"{sum(len(s) for s in last_runs.values())} run_type records"
    )


if __name__ == "__main__":
    main()
