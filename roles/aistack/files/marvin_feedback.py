#!/usr/bin/env python3
"""marvin-feedback — Process triage feedback emails and update classifier patterns.

Reads emails from marvin@packet.works INBOX with Subject matching "Feedback: *",
parses correction directives (→ Folder name), updates triage-patterns.json, and
re-moves the corrected message in Jehan's account.

Lines with explicit "→ Folder" are applied directly.
Lines without a directive are batched and sent to Claude via LiteLLM for inference.
Processed emails are archived.

Usage:
  marvin-feedback [--dry-run]
  marvin-feedback run [--dry-run]
  marvin-feedback correct <sender> <from_label> <to_label> [--dry-run]
  marvin-feedback report [--dry-run] [--min-count N]
"""

import argparse
import datetime
import email
import email.mime.multipart
import email.mime.text
import email.utils
import imaplib
import json
import re
import smtplib
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

CONFIG_PATH          = Path("/home/openclaw/.openclaw/marvin-mail.json")
ACCOUNTS_BASE        = Path("/home/openclaw/.openclaw/accounts")
FASTMAIL_CONFIG_PATH = Path("/home/openclaw/fastmail-config")

# Legacy flat paths (kept for reference during migration only)
_LEGACY_PATTERNS_PATH    = Path("/home/openclaw/.openclaw/triage-patterns.json")
_LEGACY_SIEVE_STATE_PATH = Path("/home/openclaw/.openclaw/sieve-state.json")


def _acct_dir(account_id: str) -> Path:
    return ACCOUNTS_BASE / account_id

def _patterns_path(account_id: str) -> Path:
    return _acct_dir(account_id) / "triage-patterns.json"

def _sieve_state_path_for(account_id: str) -> Path:
    return _acct_dir(account_id) / "sieve-state.json"

def _folders_path(account_id: str) -> Path:
    return _acct_dir(account_id) / "triage-folders.json"
FEEDBACK_MODEL = "claude"

# Weight added to correct-label count to outweigh accumulated misclassifications
CORRECTION_BOOST = 5

FOLDER_NAMES = [
    "INBOX", "Archive", "Reading List", "Window Shopping",
    "Orders & Receipts", "Shipping Info", "Finances",
    "Mayfield HoA", "Kickstarted", "Correspondence", "Spam", "Trash",
]

def _extend_folder_names_from(path: Path):
    """Load custom folders from a triage-folders.json and extend FOLDER_NAMES."""
    if not path.exists():
        return
    try:
        _cf = json.loads(path.read_text())
        for _f in _cf.get("folders", []):
            _name = _f.get("name", "").strip()
            if _name and _name not in FOLDER_NAMES:
                FOLDER_NAMES.append(_name)
    except Exception:
        pass

# Extend from jehan's per-account folders file at import time (feedback is jehan-scoped for now)
_extend_folder_names_from(_folders_path("jehan"))

# IMAP folder names that differ from the display label (modified UTF-7)
IMAP_FOLDER_MAP = {
    "Orders & Receipts": "Orders &- Receipts",
}


def imap_quote(name):
    if any(c in name for c in (' ', '&', '(', ')', '"', '\\')):
        return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return name


def to_imap_folder(label):
    return IMAP_FOLDER_MAP.get(label, label)

INFER_SYSTEM = """\
You are a personal email classifier. For each misclassified email below, \
output the correct destination folder — one folder name per line, in order, nothing else.

Available folders: """ + ", ".join(FOLDER_NAMES) + """

Rules (same as the triage classifier):
- INBOX: requires personal attention or action
- Archive: worth keeping but not actionable
- Reading List: intentionally subscribed newsletters and publications
- Window Shopping: retail/brand promotions
- Orders & Receipts: order confirmations, receipts, invoices
- Shipping Info: shipping notifications and tracking
- Finances: banking, investments, financial statements
- Spam: unsolicited bulk email, phishing, fundraising
- Trash: zero archival value"""


# ── Config & IMAP ─────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"Config not found: {CONFIG_PATH}")


def imap_connect(acct):
    conn = imaplib.IMAP4_SSL(acct["imap_host"], acct["imap_port"])
    conn.login(acct["email"], acct["password"])
    return conn


# ── Sender helpers ────────────────────────────────────────────────────────────

def extract_address(from_str):
    _, addr = email.utils.parseaddr(from_str)
    if addr:
        return addr.lower()
    # Fallback for malformed display names with commas (e.g. "Smith, CPAs <addr>")
    m = re.search(r'<([^>]+)>', from_str)
    return m.group(1).lower() if m else ""


def extract_domain(from_str):
    addr = extract_address(from_str)
    return addr.split("@")[-1] if "@" in addr else ""


def root_domain(domain):
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


# ── Folder resolution ─────────────────────────────────────────────────────────

def resolve_folder(raw):
    """Case-insensitive match against known folder names. Returns None if unknown."""
    raw = raw.strip()
    for name in FOLDER_NAMES:
        if name.lower() == raw.lower():
            return name
    return None


# ── IMAP move ─────────────────────────────────────────────────────────────────

def imap_move_by_mid(acct, message_id, from_label, to_label, dry_run=False):
    """Search from_label folder for message_id and move it to to_label.

    Returns True if the message was found and moved (or dry-run located it).
    """
    from_imap = to_imap_folder(from_label)
    to_imap   = to_imap_folder(to_label)
    try:
        conn = imaplib.IMAP4_SSL(acct["imap_host"], acct["imap_port"])
        conn.login(acct["email"], acct["password"])
        conn.select(imap_quote(from_imap))
        # Strip angle brackets — IMAP HEADER search works on the bare ID
        clean_mid = message_id.strip("<>")
        _, data = conn.search(None, f'HEADER Message-ID "{clean_mid}"')
        uids = data[0].split() if data[0] else []
        if not uids:
            conn.logout()
            return False
        uid = uids[0].decode()
        if dry_run:
            print(f"  [dry-run] UID {uid}: would move {from_label} → {to_label}")
            conn.logout()
            return True
        typ, _ = conn.uid("MOVE", uid, imap_quote(to_imap))
        if typ != "OK":
            conn.uid("COPY", uid, imap_quote(to_imap))
            conn.uid("STORE", uid, "+FLAGS", "\\Deleted")
            conn.expunge()
        conn.logout()
        return True
    except Exception as exc:
        print(f"  [warn] Move failed ({from_label} → {to_label}): {exc}", file=sys.stderr)
        return False


# ── Body parsing ──────────────────────────────────────────────────────────────

def _parse_msg_line(content):
    """Parse 'MM/DD  Name <addr>  —  Subject  [uid: NNN] [mid: <...>]' into a dict.

    Returns dict with keys: from_str, subject, uid, mid. All may be empty strings.
    """
    uid = ""
    mid = ""

    # Extract [uid: value] — remove it from content
    uid_match = re.search(r'\[uid:\s*([^\]]+)\]', content)
    if uid_match:
        uid = uid_match.group(1).strip()
        content = (content[:uid_match.start()] + content[uid_match.end():]).strip()

    # Extract [mid: value] — remove it from content
    mid_match = re.search(r'\[mid:\s*([^\]]+)\]', content)
    if mid_match:
        mid = mid_match.group(1).strip()
        content = (content[:mid_match.start()] + content[mid_match.end():]).strip()

    # Legacy bare [value] — only if uid not yet found
    if not uid:
        bare_match = re.search(r'\[([^\]]+)\]', content)
        if bare_match:
            uid = bare_match.group(1).strip()
            content = (content[:bare_match.start()] + content[bare_match.end():]).strip()

    # Split on em-dash / en-dash surrounded by whitespace
    em_parts = re.split(r'\s+[\u2014\u2013]\s+', content, maxsplit=1)
    if len(em_parts) == 2:
        left, subject = em_parts
        # left = "MM/DD  Name <addr>" — skip the date token
        left_parts = re.split(r'\s{2,}', left.strip(), maxsplit=1)
        from_str = left_parts[1].strip() if len(left_parts) == 2 else left.strip()
    else:
        from_str = content.strip()
        subject  = ""

    return {"from_str": from_str, "subject": subject, "uid": uid, "mid": mid}


def parse_feedback_body(body):
    """Parse a feedback email body into explicit corrections and ambiguous items.

    Supports two formats:

    New (two-line per message):
      MM/DD  Name <addr>  —  Subject  [uid: NNN]
      Label -> CorrectFolder

    Legacy (bullet list with section headers):
      Reading List:
        • MM/DD  From Name <addr>  —  Subject  [uid]  → Archive

    Returns (corrections, ambiguous) — lists of dicts with keys:
      section, from_str, subject, uid, target (None for ambiguous)
    """
    corrections = []
    ambiguous   = []

    lines = body.splitlines()
    i = 0

    # ── New two-line format ───────────────────────────────────────────────────
    # Detect: line N matches a message header (contains em-dash + [uid:]),
    # line N+1 matches "Label -> ..."
    MSG_HDR = re.compile(r'\[(?:uid:\s*)?\S+\]')
    ACTION  = re.compile(r'^(.+?)\s*->\s*(.*)$')

    used = set()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not MSG_HDR.search(stripped):
            continue
        # Look ahead for the action line
        if idx + 1 >= len(lines):
            continue
        action_line = lines[idx + 1].strip()
        m = ACTION.match(action_line)
        if not m:
            continue
        section_raw = m.group(1).strip()
        target_raw  = m.group(2).strip()
        section = resolve_folder(section_raw)
        if not section:
            continue
        # Valid two-line block
        used.add(idx)
        used.add(idx + 1)
        parsed = _parse_msg_line(stripped)
        target = resolve_folder(target_raw) if target_raw else None
        item = {
            "section":  section,
            "from_str": parsed["from_str"],
            "subject":  parsed["subject"],
            "uid":      parsed["uid"],
            "mid":      parsed.get("mid", ""),
            "target":   target,
        }
        if target:
            corrections.append(item)
        else:
            ambiguous.append(item)

    if corrections or ambiguous:
        return corrections, ambiguous

    # ── Legacy bullet-list format ─────────────────────────────────────────────
    current_section = None
    for line in lines:
        stripped = line.strip()

        # Section header: "Reading List:" etc.
        if re.match(r'^[A-Z][\w &]+:$', stripped):
            candidate = stripped.rstrip(":").strip()
            if resolve_folder(candidate):
                current_section = candidate
            continue

        # Bullet line
        if not stripped.startswith(("•", "-", "\u2022")):
            continue
        if current_section is None:
            continue

        content = re.sub(r'^[•\-\u2022]\s*', '', stripped)

        # Extract → target directive
        target = None
        arrow_match = re.search(r'\s*[\u2192]\s*(.+)$', content)
        if not arrow_match:
            arrow_match = re.search(r'\s*->\s*(.+)$', content)
        if arrow_match:
            target = resolve_folder(arrow_match.group(1).strip())
            content = content[:arrow_match.start()].strip()

        parsed = _parse_msg_line(content)
        item = {
            "section":  current_section,
            "from_str": parsed["from_str"],
            "subject":  parsed["subject"],
            "uid":      parsed["uid"],
            "mid":      parsed.get("mid", ""),
            "target":   target,
        }
        if target:
            corrections.append(item)
        else:
            ambiguous.append(item)

    return corrections, ambiguous


# ── Claude inference ──────────────────────────────────────────────────────────

def infer_targets(items, litellm_cfg):
    """Send ambiguous items to Claude and return them with inferred targets."""
    if not items:
        return []

    user_parts = []
    for i, item in enumerate(items, 1):
        user_parts.append(
            f"{i}. Currently routed to: {item['section']}\n"
            f"   From: {item['from_str']}\n"
            f"   Subject: {item['subject']}"
        )

    payload = json.dumps({
        "model":       FEEDBACK_MODEL,
        "messages": [
            {"role": "system", "content": INFER_SYSTEM},
            {"role": "user",   "content": "\n\n".join(user_parts)},
        ],
        "temperature": 0,
        "max_tokens":  256,
    }).encode()

    req = urllib.request.Request(
        litellm_cfg["url"],
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {litellm_cfg['key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    raw_lines = data["choices"][0]["message"]["content"].strip().splitlines()

    inferred = []
    for item, raw_line in zip(items, raw_lines):
        folder_raw = re.sub(r'^\d+[:.]\s*', '', raw_line).strip()
        folder = resolve_folder(folder_raw)
        if folder:
            inferred.append({**item, "target": folder})
        else:
            print(f"  [warn] Unrecognised inferred folder '{folder_raw}' — skipping", file=sys.stderr)

    return inferred


# ── Pattern update ────────────────────────────────────────────────────────────

def load_patterns(account_id: str = "jehan"):
    p = _patterns_path(account_id)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"domains": {}, "addresses": {}}


def save_patterns(patterns, account_id: str = "jehan"):
    _patterns_path(account_id).write_text(json.dumps(patterns, indent=2, ensure_ascii=False))


def apply_corrections(corrections, patterns, jehan_acct=None, dry_run=False):
    applied = 0
    moved   = 0
    for item in corrections:
        wrong   = item["section"]
        correct = item["target"]
        mid     = item.get("mid", "")

        if wrong == correct:
            continue

        addr   = extract_address(item["from_str"])
        domain = extract_domain(item["from_str"])
        root   = root_domain(domain)

        # Prefer address-level if it looks like a real personal address;
        # otherwise use root domain (covers subdomain senders like email@mail.brand.com)
        noreply_like = not addr or re.search(r'noreply|no-reply|mailer|bounce|do-not-reply', addr)
        if noreply_like:
            key_type, key = "domains", root
        else:
            key_type, key = "addresses", addr

        if not key:
            print(f"  [warn] Could not extract sender key from: {item['from_str']!r}", file=sys.stderr)
            continue

        if dry_run:
            print(f"  [dry-run] {key}  {wrong} → {correct}")
            if mid and jehan_acct:
                imap_move_by_mid(jehan_acct, mid, wrong, correct, dry_run=True)
            applied += 1
            continue

        bucket = patterns[key_type].setdefault(key, {})
        wrong_count = bucket.get(wrong, 0)
        # Boost the correct label to outweigh the accumulated wrong-label count
        bucket[correct] = bucket.get(correct, 0) + wrong_count + CORRECTION_BOOST
        print(f"  {key}  {wrong} → {correct}  (+{wrong_count + CORRECTION_BOOST})")
        applied += 1

        # Re-move the message to the correct folder
        if mid and jehan_acct:
            if imap_move_by_mid(jehan_acct, mid, wrong, correct):
                moved += 1
                print(f"  ↪ moved from {wrong} → {correct}")
            else:
                print(f"  [warn] Could not find message to re-move (may already be in {correct})", file=sys.stderr)

    return applied, moved


# ── Sieve state management ────────────────────────────────────────────────────

def _sieve_state_key(sender):
    """Return 'domain:foo.com' or 'address:user@foo.com'."""
    return ("address" if "@" in sender else "domain") + ":" + sender


def load_sieve_state(account_id: str = "jehan"):
    p = _sieve_state_path_for(account_id)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"pending": {}, "dismissed": {}}


def save_sieve_state(state, account_id: str = "jehan"):
    _sieve_state_path_for(account_id).write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _generate_sieve_snippet(sender, label):
    """Generate a Sieve rule snippet for a sender→label promotion.

    Returns a Sieve code string (comment header + rule body).
    """
    is_addr = "@" in sender
    match_clause = (
        f'  address :is "From" "{sender}"'
        if is_addr else
        f'  address :domain :is "From" "{sender}"'
    )

    if label == "INBOX":
        return f"# {sender} \u2192 INBOX: no rule needed (INBOX is the default)\n"

    if label == "Trash":
        section = "blocked-addresses" if is_addr else "blocked-senders domains"
        return (
            f"# {sender} \u2192 Trash\n"
            f"# ADD to {section} list at top of main.sieve:\n"
            f'#   "{sender}",\n'
        )

    if label == "Spam":
        return (
            f"# {sender} \u2192 Spam\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "spam" "Y";\n'
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    if label == "Archive":
        return (
            f"# {sender} \u2192 Archive\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "read" "Y";\n'
            f'  if mailboxidexists "P3V" {{\n'
            f'    set "L1_Archive" "Y";\n'
            f'    set "skipinbox" "Y";\n'
            f"  }}\n"
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    if label == "Shipping Info":
        return (
            f"# {sender} \u2192 Shipping Info\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "L1_ShippingInfo" "Y";\n'
            f'  set "skipinbox" "Y";\n'
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    if label == "Window Shopping":
        return (
            f"# {sender} \u2192 Window Shopping\n"
            f"# NOTE: Add fileinto block to Execute section if not already present:\n"
            f'#   if string :is "${{L1_WindowShopping}}" "Y" {{\n'
            f'#     fileinto :copy "INBOX.Window Shopping";\n'
            f'#     set "hasmailbox" "Y";\n'
            f"#   }}\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "L1_WindowShopping" "Y";\n'
            f'  set "skipinbox" "Y";\n'
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    if label == "Reading List":
        return (
            f"# {sender} \u2192 Reading List\n"
            f"# NOTE: Add fileinto block to Execute section if not already present:\n"
            f'#   if string :is "${{L1_ReadingList}}" "Y" {{\n'
            f'#     fileinto :copy "INBOX.Reading List";\n'
            f'#     set "hasmailbox" "Y";\n'
            f"#   }}\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "L1_ReadingList" "Y";\n'
            f'  set "skipinbox" "Y";\n'
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    if label == "Orders & Receipts":
        return (
            f"# {sender} \u2192 Orders & Receipts\n"
            f"# NOTE: Add fileinto block to Execute section if not already present:\n"
            f'#   if string :is "${{L1_OrdersReceipts}}" "Y" {{\n'
            f'#     fileinto :copy "INBOX.Orders &- Receipts";\n'
            f'#     set "hasmailbox" "Y";\n'
            f"#   }}\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "read" "Y";\n'
            f'  set "L1_OrdersReceipts" "Y";\n'
            f'  set "skipinbox" "Y";\n'
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    if label == "Mayfield HoA":
        return (
            f"# {sender} \u2192 Mayfield HoA\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "read" "Y";\n'
            f'  set "L1_MayfieldHoA" "Y";\n'
            f'  set "skipinbox" "Y";\n'
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    if label == "Kickstarted":
        return (
            f"# {sender} \u2192 Kickstarted\n"
            f"if allof(\n"
            f'  not string :is "${{stop}}" "Y",\n'
            f"{match_clause}\n"
            f") {{\n"
            f'  set "L1_Kickstarted" "Y";\n'
            f'  set "skipinbox" "Y";\n'
            f'  set "stop" "Y";\n'
            f"}}\n"
        )

    # Custom folder fallback
    imap_path = "INBOX." + label.replace("/", ".")
    var_name  = "L2_" + re.sub(r'[^a-zA-Z0-9]', '_', label).strip("_")
    return (
        f"# {sender} \u2192 {label} (custom folder)\n"
        f"# NOTE: Add fileinto block to Execute section:\n"
        f'#   if string :is "${{{var_name}}}" "Y" {{\n'
        f'#     fileinto :copy "{imap_path}";\n'
        f'#     set "hasmailbox" "Y";\n'
        f"#   }}\n"
        f"if allof(\n"
        f'  not string :is "${{stop}}" "Y",\n'
        f"{match_clause}\n"
        f") {{\n"
        f'  set "read" "Y";\n'
        f'  set "{var_name}" "Y";\n'
        f'  set "skipinbox" "Y";\n'
        f'  set "stop" "Y";\n'
        f"}}\n"
    )


def _write_sieve_pending_file(sieve_state):
    """Regenerate sieve-pending.sieve from all pending entries in sieve_state.

    Writes to FASTMAIL_CONFIG_PATH/sieve/sieve-pending.sieve if the repo exists,
    otherwise falls back to /home/openclaw/.openclaw/sieve-pending.sieve.
    If in a git repo, commits the change.
    """
    pending = sieve_state.get("pending", {})
    if not pending:
        return

    sieve_dir = FASTMAIL_CONFIG_PATH / "sieve"
    if sieve_dir.is_dir():
        out_path = sieve_dir / "sieve-pending.sieve"
        use_git  = (FASTMAIL_CONFIG_PATH / ".git").is_dir()
    else:
        out_path = Path("/home/openclaw/.openclaw/sieve-pending.sieve")
        use_git  = False

    lines = [
        "# sieve-pending.sieve \u2014 Marvin-generated Sieve rules pending deployment",
        "# Merge into fastmail-config/sieve/main.sieve, then delete each merged rule.",
        "#",
        "# Blocked-sender additions \u2192 domain/address list blocks at TOP of main.sieve",
        "# Calculate rules \u2192 '### Calculate rule actions ###' section",
        "# Execute blocks (NEW FOLDER comments) \u2192 '### File into folder actions ###'",
        f"# Last updated: {datetime.datetime.utcnow().isoformat(timespec='seconds')}Z",
        "",
    ]
    for key in sorted(pending.keys()):
        info    = pending[key]
        snippet = info.get("snippet", "")
        lines.append(f"# \u2500\u2500\u2500 Promoted {info.get('promoted_at', '')} \u2500\u2500\u2500")
        lines.extend(snippet.rstrip("\n").splitlines())
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n")
    print(f"  sieve-pending.sieve \u2192 {out_path}", file=sys.stderr)

    if use_git:
        try:
            rel = str(out_path.relative_to(FASTMAIL_CONFIG_PATH))
            n   = len(pending)
            subprocess.run(
                ["git", "-C", str(FASTMAIL_CONFIG_PATH), "add", rel],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(FASTMAIL_CONFIG_PATH), "commit",
                 "-m", f"sieve-pending: {n} rule(s) pending deployment"],
                check=True, capture_output=True,
            )
            print(f"  git commit: sieve-pending ({n} rules)", file=sys.stderr)
        except subprocess.CalledProcessError as exc:
            print(f"  [warn] git commit failed: {exc.stderr.decode()}", file=sys.stderr)


def _promote_to_pending(entries, sieve_state, dry_run=False):
    """Promote (sender, label) pairs to sieve-pending state. Returns count."""
    promoted = 0
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    for sender, label in entries:
        key     = _sieve_state_key(sender)
        snippet = _generate_sieve_snippet(sender, label)
        if dry_run:
            print(f"  [dry-run] promote {sender} \u2192 {label} (sieve)")
            continue
        sieve_state.setdefault("dismissed", {}).pop(key, None)
        sieve_state.setdefault("pending", {})[key] = {
            "label":       label,
            "snippet":     snippet,
            "promoted_at": now,
        }
        promoted += 1
        print(f"  {sender} \u2192 {label}: marked pending Sieve deployment")
    return promoted


def _dismiss_patterns(entries, sieve_state, dry_run=False):
    """Mark (sender, label) pairs as dismissed. Returns count."""
    dismissed = 0
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    for sender, label in entries:
        key = _sieve_state_key(sender)
        if dry_run:
            print(f"  [dry-run] dismiss {sender}")
            continue
        sieve_state.setdefault("pending", {}).pop(key, None)
        sieve_state.setdefault("dismissed", {})[key] = {
            "label":        label,
            "dismissed_at": now,
        }
        dismissed += 1
        print(f"  {sender}: dismissed (won't appear in future reports)")
    return dismissed


# ── Main ──────────────────────────────────────────────────────────────────────

def cmd_run(args):
    config      = load_config()
    marvin      = config["accounts"]["marvin"]
    account_id  = getattr(args, "account", "jehan")
    jehan       = config["accounts"].get(account_id, config["accounts"]["jehan"])
    litellm     = config["litellm"]

    conn = imap_connect(marvin)
    try:
        conn.select("INBOX")
        _, d1 = conn.search(None, 'SUBJECT "Feedback: "')
        _, d2 = conn.search(None, 'SUBJECT "Re: [Marvin] Pattern Report"')
        _, d3 = conn.search(None, 'SUBJECT "Re: [Marvin] Sorted"')
        seen = set()
        uids = []
        for u in (d1[0].split() if d1[0] else []) + \
                 (d2[0].split() if d2[0] else []) + \
                 (d3[0].split() if d3[0] else []):
            if u not in seen:
                seen.add(u)
                uids.append(u)
    except Exception as exc:
        conn.logout()
        sys.exit(f"IMAP search failed: {exc}")

    if not uids:
        print("No feedback emails.")
        conn.logout()
        return

    print(f"Found {len(uids)} feedback email(s).", file=sys.stderr)
    patterns      = load_patterns(account_id)
    sieve_state   = load_sieve_state(account_id)
    total_applied = 0
    sieve_changed = False

    for uid in uids:
        _, msg_data = conn.fetch(uid, "(RFC822)")
        if (not msg_data
                or msg_data[0] is None
                or not isinstance(msg_data[0], tuple)
                or not isinstance(msg_data[0][1], bytes)):
            continue

        msg     = email.message_from_bytes(msg_data[0][1])
        subject = msg.get("Subject", "(no subject)")
        print(f"\n{subject}", file=sys.stderr)

        # Prefer plain-text part
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = part.get("Content-Disposition", "")
                if ct == "text/plain" and "attachment" not in cd:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    break
        else:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )

        if re.search(r'\[Marvin\] Pattern Report', subject, re.I):
            # Reply to a pattern report — freeform + explicit corrections + sieve/dismiss
            prev_pending   = len(sieve_state.get("pending",   {}))
            prev_dismissed = len(sieve_state.get("dismissed", {}))
            applied = handle_report_reply(
                msg, body, patterns, jehan, marvin, litellm,
                dry_run=args.dry_run, sieve_state=sieve_state,
            )
            total_applied += applied
            if (len(sieve_state.get("pending",   {})) != prev_pending or
                    len(sieve_state.get("dismissed", {})) != prev_dismissed):
                sieve_changed = True
        elif re.search(r'\[Marvin\] Sorted', subject, re.I):
            # Reply to a triage summary — check for batch request
            batch = _parse_batch_request(body)
            if batch and not args.dry_run:
                print(f"  Batch request: {batch} messages → firing triage in background", file=sys.stderr)
                subprocess.Popen(
                    ["/usr/local/bin/marvin-triage", "run",
                     "--account", account_id, "--batch", str(batch)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                ack_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject
                _smtp_send(marvin, jehan, ack_subject,
                           f"Running another sort — {batch} messages. You'll get a summary when it's done.")
            elif batch and args.dry_run:
                print(f"  [dry-run] Would fire: marvin-triage run --account {account_id} --batch {batch}")
            else:
                print(f"  No batch request detected in reply to sorted summary.", file=sys.stderr)
        else:
            # Standard triage feedback email
            corrections, ambiguous = parse_feedback_body(body)
            print(f"  {len(corrections)} explicit, {len(ambiguous)} to infer", file=sys.stderr)

            if ambiguous:
                try:
                    inferred = infer_targets(ambiguous, litellm)
                    corrections.extend(inferred)
                    print(f"  {len(inferred)} inferred via Claude", file=sys.stderr)
                except Exception as exc:
                    print(f"  [warn] Claude inference failed: {exc}", file=sys.stderr)

            applied, moved = apply_corrections(
                corrections, patterns, jehan_acct=jehan, dry_run=args.dry_run
            )
            total_applied += applied

        # Archive the processed email
        if not args.dry_run:
            typ, _ = conn.uid("MOVE", uid, "Archive")
            if typ != "OK":
                conn.uid("COPY", uid, "Archive")
                conn.uid("STORE", uid, "+FLAGS", "\\Deleted")
                conn.expunge()

    conn.logout()

    if args.dry_run:
        print(f"\n[dry-run] Would apply {total_applied} correction(s).")
    else:
        if total_applied:
            save_patterns(patterns, account_id)
            print(f"\nApplied {total_applied} correction(s) \u2192 {_patterns_path(account_id)}")
        else:
            print("\nNo corrections to apply.")
        if sieve_changed:
            save_sieve_state(sieve_state, account_id)
            _write_sieve_pending_file(sieve_state)


_BATCH_REQUEST_RE = re.compile(
    r'\b(more|run|another|next batch|next|continue|keep going|sort more|go again)\b',
    re.I,
)
_BATCH_SIZE_RE = re.compile(r'\b(\d{2,4})\b')
_DEFAULT_BATCH = 500

def _parse_batch_request(body: str):
    """Return batch size if the reply body is a batch request, else None.

    Accepts: 'more', 'run', 'another', 'next batch', 'continue', 'keep going', etc.
    Optional number in the body overrides the default batch size (e.g. 'more 200').
    Strips quoted reply lines before parsing so '>'-prefixed context doesn't match.
    """
    clean = "\n".join(
        line for line in body.splitlines()
        if not line.strip().startswith(">") and not re.match(r'^On .+ wrote:', line)
    )
    if not _BATCH_REQUEST_RE.search(clean):
        return None
    m = _BATCH_SIZE_RE.search(clean)
    return int(m.group(1)) if m else _DEFAULT_BATCH


def _smtp_send(from_acct, to_acct, subject, body):
    """Send a plain-text email."""
    msg = email.mime.text.MIMEText(body, "plain", "utf-8")
    msg["From"]    = from_acct["email"]
    msg["To"]      = to_acct["email"]
    msg["Subject"] = subject
    msg["Date"]    = email.utils.formatdate(localtime=True)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(from_acct["smtp_host"], from_acct["smtp_port"]) as smtp:
        smtp.starttls(context=ctx)
        smtp.login(from_acct["email"], from_acct["password"])
        smtp.sendmail(from_acct["email"], [to_acct["email"]], msg.as_string())


def _smtp_send_preformatted(from_acct, to_acct, subject, body):
    """Send multipart/alternative: plain text (for reply parsing) + HTML <pre> (for display).

    The HTML part wraps the body in a monospace <pre> block so column alignment
    renders correctly in Mail.app, which uses a proportional font for plain text.
    """
    import html as _html
    html_body = (
        '<!DOCTYPE html><html><head>'
        '<meta charset="utf-8">'
        '<meta name="color-scheme" content="light dark">'
        '</head><body>'
        '<pre style="font-family: \'SF Mono\', \'Menlo\', \'Courier New\', monospace; '
        'font-size: 12px; line-height: 1.5; white-space: pre;">'
        + _html.escape(body) +
        '</pre></body></html>'
    )
    alt = email.mime.multipart.MIMEMultipart("alternative")
    alt["From"]    = from_acct["email"]
    alt["To"]      = to_acct["email"]
    alt["Subject"] = subject
    alt["Date"]    = email.utils.formatdate(localtime=True)
    alt.attach(email.mime.text.MIMEText(body,      "plain", "utf-8"))
    alt.attach(email.mime.text.MIMEText(html_body, "html",  "utf-8"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(from_acct["smtp_host"], from_acct["smtp_port"]) as smtp:
        smtp.starttls(context=ctx)
        smtp.login(from_acct["email"], from_acct["password"])
        smtp.sendmail(from_acct["email"], [to_acct["email"]], alt.as_string())


_T_TYPE   = 7
_T_SENDER = 44
_T_DEST   = 28

def _trunc(s, n):
    return s if len(s) <= n else s[:n - 1] + "\u2026"

def _root_domain(addr_or_domain):
    domain = addr_or_domain.split("@")[-1] if "@" in addr_or_domain else addr_or_domain
    parts = domain.rstrip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain

def _strip_quoted(body):
    """Remove quoted reply lines (>) and email attribution lines."""
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(">"):
            continue
        if re.match(r'^On .{5,120} wrote:\s*$', s):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def cmd_report(args):
    """Email a flat-table pattern summary. Reply to correct, promote to Sieve, or dismiss."""
    config      = load_config()
    marvin      = config["accounts"]["marvin"]
    account_id  = getattr(args, "account", "jehan")
    jehan       = config["accounts"].get(account_id, config["accounts"]["jehan"])
    patterns    = load_patterns(account_id)
    sieve_state = load_sieve_state(account_id)
    min_count   = args.min_count

    raw = []
    for addr, buckets in patterns.get("addresses", {}).items():
        label = max(buckets, key=buckets.get)
        total = sum(buckets.values())
        if total >= min_count:
            raw.append(("address", addr, label, total))
    for domain, buckets in patterns.get("domains", {}).items():
        label = max(buckets, key=buckets.get)
        total = sum(buckets.values())
        if total >= min_count:
            raw.append(("domain", domain, label, total))

    if not raw:
        print(f"No patterns with >= {min_count} classifications.")
        return

    # Suppress address entries covered by a domain entry with the same label
    domain_labels = {
        sender: label
        for kind, sender, label, _ in raw
        if kind == "domain"
    }
    entries = [
        (kind, sender, label, count)
        for kind, sender, label, count in raw
        if not (kind == "address" and domain_labels.get(_root_domain(sender)) == label)
    ]
    entries.sort(key=lambda x: x[3], reverse=True)

    # Filter out pending and dismissed entries
    pending_map   = sieve_state.get("pending",   {})
    dismissed_set = set(sieve_state.get("dismissed", {}).keys())
    filtered    = []
    n_pending   = 0
    n_dismissed = 0
    for item in entries:
        key = _sieve_state_key(item[1])
        if key in pending_map:
            n_pending += 1
        elif key in dismissed_set:
            n_dismissed += 1
        else:
            filtered.append(item)

    if not filtered:
        print(f"No unreviewed patterns with >= {min_count} classifications.")
        return

    total   = len(filtered)
    subject = f"[Marvin] Pattern Report \u2014 {total} routes"
    sep     = "\u2500" * (_T_TYPE + 2 + _T_SENDER + 2 + _T_DEST + 2 + 5)

    header = (
        f"{'TYPE':<{_T_TYPE}}  "
        f"{'SENDER':<{_T_SENDER}}  "
        f"{'ROUTES TO':<{_T_DEST}}  "
        f"COUNT"
    )
    lines = [f"{total} unreviewed routes", "", header, sep]
    for kind, sender, label, count in filtered:
        lines.append(
            f"{kind:<{_T_TYPE}}  "
            f"{_trunc(sender, _T_SENDER):<{_T_SENDER}}  "
            f"{label:<{_T_DEST}}  "
            f"{count:>5}"
        )

    lines += [
        "",
        sep,
        "These are observations from past moves \u2014 they do not influence routing.",
        "Routing order: protected config \u2192 window_shopping config \u2192 reading_list config \u2192 LLM.",
        "",
        "Reply to correct, promote to Sieve, or dismiss. Explicit format:",
        "",
        "  heathera@myconsumers.org \u2192 Household/Auto/'25 EX90   (correct routing)",
        "  lemonsquad.com \u2192 sieve                                (generate a Sieve rule)",
        "  rennline.com \u2192 dismiss                                (hide from future reports)",
        "",
        "Or write freeform at the top \u2014 Claude will interpret it.",
        "If anything is unclear, Marvin will reply asking for clarification.",
    ]

    # Pending Sieve rules summary
    if n_pending:
        sieve_dir = FASTMAIL_CONFIG_PATH / "sieve"
        pending_path = (
            sieve_dir / "sieve-pending.sieve"
            if sieve_dir.is_dir()
            else Path("/home/openclaw/.openclaw/sieve-pending.sieve")
        )
        lines += [
            "",
            sep,
            f"{n_pending} rule(s) pending Sieve deployment \u2192 {pending_path}",
        ]
        for key in sorted(pending_map.keys()):
            info = pending_map[key]
            lines.append(f"  {key:<52}  \u2192  {info['label']}")
    if n_dismissed:
        lines += [f"  ({n_dismissed} sender(s) dismissed \u2014 hidden from reports)"]

    body = "\n".join(lines)

    if args.dry_run:
        print(f"Subject: {subject}\n\n{body}")
        return

    try:
        _smtp_send_preformatted(marvin, jehan, subject, body)
        print(f"Report sent to {jehan['email']} ({total} routes, {n_pending} pending, {n_dismissed} dismissed)")
    except Exception as exc:
        sys.exit(f"Failed to send report: {exc}")


def _infer_report_corrections(freeform, patterns, litellm_cfg):
    """Ask Claude to interpret freeform correction instructions.

    Returns {"corrections": [(sender, folder), ...]} or {"clarify": "question"}.
    """
    # Build pattern context: top 25 entries sorted by count
    ctx_lines = []
    combined = []
    for addr, buckets in patterns.get("addresses", {}).items():
        combined.append((addr, max(buckets, key=buckets.get), sum(buckets.values())))
    for domain, buckets in patterns.get("domains", {}).items():
        combined.append((domain, max(buckets, key=buckets.get), sum(buckets.values())))
    combined.sort(key=lambda x: x[2], reverse=True)
    for sender, label, _ in combined[:25]:
        ctx_lines.append(f"  {sender} \u2192 {label}")

    system = (
        "You are processing a reply to an email routing pattern report. "
        "The user wants to correct how certain senders are classified.\n\n"
        "Current top patterns:\n" + "\n".join(ctx_lines) + "\n\n"
        "Available folders: " + ", ".join(FOLDER_NAMES) + "\n\n"
        "The user may refer to senders by partial name or description. "
        "Map them to exact sender strings from the pattern list above.\n\n"
        "Output EITHER corrections, one per line:\n"
        "  sender \u2192 Destination Folder\n\n"
        "OR, if the instruction is too ambiguous to act on safely, output exactly:\n"
        "  CLARIFY: <your specific question>\n\n"
        "Output nothing else."
    )

    payload = json.dumps({
        "model":       FEEDBACK_MODEL,
        "messages":    [
            {"role": "system", "content": system},
            {"role": "user",   "content": freeform},
        ],
        "temperature": 0,
        "max_tokens":  256,
    }).encode()

    req = urllib.request.Request(
        litellm_cfg["url"], data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {litellm_cfg['key']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        raw = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"  [warn] LiteLLM error: {exc}", file=sys.stderr)
        return {"clarify": "I hit a technical error processing your instruction. Please try again or use explicit format: sender \u2192 Folder"}

    if raw.upper().startswith("CLARIFY:"):
        return {"clarify": raw[len("CLARIFY:"):].strip()}

    corrections = []
    for line in raw.splitlines():
        m = re.match(r'^(\S+)\s*(?:\u2192|->)\s*(.+)$', line.strip())
        if m:
            sender = m.group(1).strip().lower()
            target = resolve_folder(m.group(2).strip())
            if target:
                corrections.append((sender, target))
    return {"corrections": corrections}


def handle_report_reply(msg, body, patterns, jehan_acct, marvin_acct, litellm_cfg,
                        dry_run=False, sieve_state=None):
    """Process a reply to [Marvin] Pattern Report.

    Accepts:
      sender → Folder    — folder correction (updates patterns.json)
      sender → sieve     — promote to Sieve rule (updates sieve-state.json)
      sender → dismiss   — hide from future reports (updates sieve-state.json)
      Freeform text      — Claude infers as folder corrections

    Returns count of pattern corrections applied. sieve_state is mutated in-place.
    """
    if sieve_state is None:
        sieve_state = {}

    clean = _strip_quoted(body)

    SIEVE_KEYWORDS   = {"sieve"}
    DISMISS_KEYWORDS = {"dismiss", "never"}

    folder_corrections = []
    sieve_entries      = []
    dismiss_entries    = []
    freeform_lines     = []

    for line in clean.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r'^(\S+)\s*(?:\u2192|->)\s*(.+)$', s)
        if m:
            sender     = m.group(1).strip().lower()
            target_raw = m.group(2).strip().lower()
            if target_raw in SIEVE_KEYWORDS:
                sieve_entries.append(sender)
            elif target_raw in DISMISS_KEYWORDS:
                dismiss_entries.append(sender)
            else:
                folder_corrections.append((sender, m.group(2).strip()))
        else:
            freeform_lines.append(s)

    freeform = " ".join(freeform_lines).strip()
    applied  = 0

    # Sieve promotions — look up dominant label from patterns
    for sender in sieve_entries:
        key_type = "addresses" if "@" in sender else "domains"
        buckets  = patterns.get(key_type, {}).get(sender, {})
        label    = max(buckets, key=buckets.get) if buckets else None
        if label:
            _promote_to_pending([(sender, label)], sieve_state, dry_run=dry_run)
        else:
            print(f"  [warn] No pattern for {sender!r} — can't determine label for Sieve", file=sys.stderr)

    # Dismissals — look up dominant label for the state record
    for sender in dismiss_entries:
        key_type = "addresses" if "@" in sender else "domains"
        buckets  = patterns.get(key_type, {}).get(sender, {})
        label    = max(buckets, key=buckets.get) if buckets else "?"
        _dismiss_patterns([(sender, label)], sieve_state, dry_run=dry_run)

    # Explicit folder corrections
    for sender, target_raw in folder_corrections:
        target = resolve_folder(target_raw)
        if not target:
            print(f"  [warn] Unknown folder: {target_raw!r}", file=sys.stderr)
            continue
        key_type = "addresses" if "@" in sender else "domains"
        if dry_run:
            print(f"  [dry-run] {sender} \u2192 {target}")
        else:
            bucket = patterns[key_type].setdefault(sender, {})
            bucket[target] = bucket.get(target, 0) + CORRECTION_BOOST
            print(f"  {sender} \u2192 {target}  (+{CORRECTION_BOOST})")
        applied += 1

    # Infer freeform corrections via Claude
    if freeform:
        print(f"  Freeform: {freeform[:100]}", file=sys.stderr)
        result = _infer_report_corrections(freeform, patterns, litellm_cfg)

        if result.get("clarify"):
            question = result["clarify"]
            if dry_run:
                print(f"  [dry-run] Would ask: {question}", file=sys.stderr)
            else:
                reply_subject = "Re: " + msg.get("Subject", "[Marvin] Pattern Report")
                _smtp_send(
                    marvin_acct, jehan_acct,
                    reply_subject,
                    f"I need a bit of clarification before I can apply that:\n\n{question}\n\n"
                    f"You can also reply with explicit lines:\n  sender \u2192 Destination Folder",
                )
                print(f"  Clarification request sent to {jehan_acct['email']}", file=sys.stderr)

        for sender, target in result.get("corrections", []):
            key_type = "addresses" if "@" in sender else "domains"
            if dry_run:
                print(f"  [dry-run] (inferred) {sender} \u2192 {target}")
            else:
                bucket = patterns[key_type].setdefault(sender, {})
                bucket[target] = bucket.get(target, 0) + CORRECTION_BOOST
                print(f"  (inferred) {sender} \u2192 {target}  (+{CORRECTION_BOOST})")
            applied += 1

    return applied


def cmd_correct(args):
    """Apply a single sender correction directly to triage-patterns.json."""
    account_id = getattr(args, "account", "jehan")
    patterns = load_patterns(account_id)
    sender = args.sender.lower().strip()
    key_type = "addresses" if "@" in sender else "domains"
    bucket = patterns[key_type].setdefault(sender, {})
    wrong_count = bucket.get(args.from_label, 0)
    boost = wrong_count + CORRECTION_BOOST
    if args.dry_run:
        print(f"  [dry-run] {sender}  {args.from_label} → {args.to_label}  (+{boost})")
        return
    bucket[args.to_label] = bucket.get(args.to_label, 0) + boost
    save_patterns(patterns, account_id)
    print(f"  {sender}  {args.from_label} → {args.to_label}  (+{boost})")
    print(f"Saved → {_patterns_path(account_id)}")


def main():
    parser = argparse.ArgumentParser(description="Process triage feedback emails")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Process feedback emails from inbox (default)")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--account", default="jehan", help="Target account for pattern updates")

    p_cor = sub.add_parser("correct", help="Apply a correction directly without an email")
    p_cor.add_argument("sender", help="Email address or domain")
    p_cor.add_argument("from_label", help="Wrong label to correct from")
    p_cor.add_argument("to_label", help="Correct destination label")
    p_cor.add_argument("--dry-run", action="store_true")
    p_cor.add_argument("--account", default="jehan")

    p_rep = sub.add_parser("report", help="Email a pattern summary for config review")
    p_rep.add_argument("--dry-run", action="store_true")
    p_rep.add_argument("--account", default="jehan")
    p_rep.add_argument("--min-count", type=int, default=5,
                       help="Minimum classification count to include (default: 5)")

    args = parser.parse_args()

    # Default to 'run' when invoked with no subcommand (preserves cron compatibility)
    if args.cmd is None or args.cmd == "run":
        if args.cmd is None:
            args.dry_run = False
        cmd_run(args)
    elif args.cmd == "correct":
        cmd_correct(args)
    elif args.cmd == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
