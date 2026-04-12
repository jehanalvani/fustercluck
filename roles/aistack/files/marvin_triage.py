#!/usr/bin/env python3
"""marvin-triage — Stage 2 email classifier.

Reads a mail folder (default: INBOX), classifies each message with the local LLM,
moves it to the correct destination, and records sender→folder patterns.

Patterns accumulate in triage-patterns.json. High-confidence ones are
promoted to Sieve rules (Stage 1) over time — run `patterns` to review.

Window Shopping: known retail brands land here instead of Archive. Deduped
to one message per brand on arrival; flushed to Archive every Monday morning.

Usage:
  marvin-triage run            [--account jehan] [--folder INBOX] [--batch 20] [--dry-run]
  marvin-triage window-shopping [--account jehan] [--dry-run]
  marvin-triage patterns       [--min-count 3] [--min-confidence 0.8]

Config:   /home/openclaw/.openclaw/marvin-mail.json
Patterns: /home/openclaw/.openclaw/triage-patterns.json
"""

import argparse
import datetime
import email
import email.header
import email.mime.multipart
import email.mime.text
import email.utils
import html as _html
import imaplib
import json
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

CONFIG_PATH = Path("/home/openclaw/.openclaw/marvin-mail.json")
PATTERNS_PATH = Path("/home/openclaw/.openclaw/triage-patterns.json")
CUSTOM_FOLDERS_PATH = Path("/home/openclaw/.openclaw/triage-folders.json")

LABELS = [
    "Spam",
    "Archive",
    "INBOX",
    "Reading List",
    "Window Shopping",
    "Orders & Receipts",
    "Shipping Info",
    "Finances",
    "Healthcare and Insurance",
    "Travel",
    "Household",
    "Mayfield HoA",
    "Kickstarted",
    "Correspondence",
    "Trash",
]

FOLDER_MAP = {
    "Spam":                       "Spam",
    "Archive":                    "Archive",
    "INBOX":                      "INBOX",
    "Reading List":               "Reading List",
    "Window Shopping":            "Window Shopping",
    "Orders & Receipts":          "Orders &- Receipts",
    "Shipping Info":              "Shipping Info",
    "Finances":                   "Finances",
    "Healthcare and Insurance":   "Healthcare and Insurance",
    "Travel":                     "Travel",
    "Household":                  "Household",
    "Mayfield HoA":               "Mayfield HoA",
    "Kickstarted":                "Kickstarted",
    "Correspondence":             "Correspondence",
    "Trash":                      "Trash",
}

SYSTEM_PROMPT = """\
You are an email classifier. Given a From address and Subject, output exactly one \
destination folder from this list. Output the folder name only — no explanation, \
no punctuation, nothing else.

Folders:
- Spam: obvious unsolicited bulk email, phishing, hard spam, retail/brand promotions, \
political fundraising/campaign emails (candidates, party orgs, PACs — even personal-sounding \
subjects like "Good morning"), social network notifications (LinkedIn job alerts, profile views), \
anything bulk and impersonal that has no informational value
- Archive: content with some archival value that isn't actionable — general newsletters from \
media orgs or nonprofits where the sender isn't a specifically followed writer or publication
- Reading List: newsletters and publications the recipient has intentionally subscribed to for \
their content — tech/engineering newsletters, curated reads, specific writers or niche topics \
where the content itself is worth saving even if not acted on immediately
- INBOX: anything requiring attention or follow-up — personal messages, work email, \
active applications (credit, loan, insurance, membership), car purchase correspondence, \
dealership emails, any organization writing about an in-progress matter, important account \
or security notices, Virtru/secure email. When uncertain, prefer INBOX.
- Orders & Receipts: COMPLETED transactions only — a purchase receipt, a confirmed order, \
an invoice for something already delivered. NOT applications, status updates, or anything \
with an open next step.
- Shipping Info: shipping notifications, package tracking, USPS Daily Digest, delivery updates
- Finances: bank statements, credit card notices, billing, tax documents
- Healthcare and Insurance: medical, healthcare, insurance communications
- Travel: flight bookings, hotel reservations, car rentals, itineraries
- Household: home improvement, utilities, repair/maintenance
- Mayfield HoA: HOA correspondence
- Kickstarted: crowdfunding updates (Kickstarter, Indiegogo, Backerkit)
- Correspondence: personal letters from a named individual writing directly to Jehan — \
NOT campaigns, orgs, brands, or mailing lists, even if the subject sounds personal
- Trash: content with zero archival value — disabled accounts, bounces, confirmed phishing

Key rules:
- Transactional email about active decisions (buying a car, applying for a loan, ongoing services) → INBOX
- Retail promotions, brand marketing, political email → Spam
- Tech/engineering/niche newsletters the recipient chose → Reading List
- Generic media newsletters → Archive
- INBOX only when it needs a human response or tracks an active matter\
"""


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        sys.exit(f"Config not found: {CONFIG_PATH}")


def decode_header_value(value):
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return " ".join(out)


def extract_domain(from_header):
    _, addr = email.utils.parseaddr(from_header)
    if "@" in addr:
        return addr.split("@", 1)[1].lower().strip(">")
    return ""


def extract_address(from_header):
    _, addr = email.utils.parseaddr(from_header)
    return addr.lower().strip("<>") if addr else ""


def root_domain(domain):
    """Return the registrable domain (last two labels) for subdomain matching."""
    parts = domain.rstrip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def imap_connect(acct):
    conn = imaplib.IMAP4_SSL(acct["imap_host"], acct["imap_port"])
    conn.login(acct["email"], acct["password"])
    return conn


def parse_date_short(date_str):
    """Parse an RFC 2822 date string to MM/DD, fallback to raw string."""
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        return dt.strftime("%m/%d")
    except Exception:
        return (date_str or "")[:5]


def fetch_batch(acct, folder, batch):
    """Fetch up to `batch` messages from `folder`. Returns list of dicts with UIDs."""
    conn = imap_connect(acct)
    conn.select(folder, readonly=True)

    _, data = conn.uid("SEARCH", None, "ALL")
    uids = list(reversed(data[0].split()[-batch:])) if data[0] else []

    messages = []
    for uid in uids:
        _, hdr_data = conn.uid("FETCH", uid, "(RFC822.HEADER)")
        if (not hdr_data
                or hdr_data[0] is None
                or not isinstance(hdr_data[0], tuple)
                or not isinstance(hdr_data[0][1], bytes)):
            continue
        parsed = email.message_from_bytes(hdr_data[0][1])
        date_str = parsed.get("Date", "")
        try:
            timestamp = email.utils.parsedate_to_datetime(date_str).timestamp()
        except Exception:
            timestamp = 0.0
        messages.append({
            "uid": uid.decode(),
            "from": decode_header_value(parsed.get("From", "")),
            "subject": decode_header_value(parsed.get("Subject", "(no subject)")),
            "date": parse_date_short(date_str),
            "timestamp": timestamp,
            "unsubscribe": _parse_list_unsubscribe(parsed.get("List-Unsubscribe", "")),
            "message_id": (parsed.get("Message-ID") or "").strip(),
        })

    conn.logout()
    return messages


def classify(from_addr, subject, litellm_cfg):
    """Call the local LLM and return a label from LABELS. Falls back to INBOX on failure."""
    payload = json.dumps({
        "model": litellm_cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"From: {from_addr}\nSubject: {subject}"},
        ],
        "temperature": 0,
        "max_tokens": 32,
    }).encode()

    req = urllib.request.Request(
        litellm_cfg["url"],
        data=payload,
        headers={
            "Authorization": f"Bearer {litellm_cfg['key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        raw = result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [warn] LLM error: {e}", file=sys.stderr)
        return "INBOX"

    if raw in LABELS:
        return raw
    raw_lower = raw.lower()
    for label in LABELS:
        if label.lower() == raw_lower:
            return label
    for label in LABELS:
        if label.lower() in raw_lower:
            return label

    print(f"  [warn] unrecognised label '{raw}', defaulting to INBOX", file=sys.stderr)
    return "INBOX"


def imap_quote(name):
    """Quote an IMAP mailbox name that contains spaces or special characters."""
    if any(c in name for c in (' ', '&', '(', ')', '"', '\\')):
        return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return name


def move_uid(conn, uid, to_folder):
    """Move an already-selected message UID to to_folder using open connection."""
    quoted = imap_quote(to_folder)
    typ, _ = conn.uid("MOVE", uid, quoted)
    if typ != "OK":
        typ, _ = conn.uid("COPY", uid, quoted)
        if typ != "OK":
            raise RuntimeError(f"Failed to move UID {uid} to {to_folder}")
        conn.uid("STORE", uid, "+FLAGS", "\\Deleted")
        conn.expunge()


def move_message(acct, uid, from_folder, to_folder):
    """Move a single message by UID between folders."""
    conn = imap_connect(acct)
    conn.select(from_folder)
    move_uid(conn, uid, to_folder)
    conn.logout()


def dedup_window_shopping(acct, sender_domain, ws_imap_folder, dry_run=False):
    """Archive any existing messages in Window Shopping from the same root domain."""
    root = root_domain(sender_domain)
    conn = imap_connect(acct)
    conn.select(ws_imap_folder)

    _, data = conn.uid("SEARCH", None, f'FROM "@{root}"')
    uids = data[0].split() if data[0] else []

    archived = 0
    for uid in uids:
        uid_str = uid.decode()
        if dry_run:
            print(f"    [dry-run] would archive existing UID {uid_str} from {root}", file=sys.stderr)
        else:
            try:
                move_uid(conn, uid_str, FOLDER_MAP["Archive"])
                archived += 1
            except Exception as e:
                print(f"    [warn] could not archive UID {uid_str}: {e}", file=sys.stderr)

    conn.logout()
    return archived


def load_patterns():
    if PATTERNS_PATH.exists():
        return json.loads(PATTERNS_PATH.read_text())
    return {"domains": {}, "addresses": {}}


def save_patterns(patterns):
    PATTERNS_PATH.write_text(json.dumps(patterns, indent=2, ensure_ascii=False))


def record_pattern(from_header, label, patterns):
    """Record a sender→label observation. Only call when a message is actually moved."""
    domain = extract_domain(from_header)
    address = extract_address(from_header)
    root = root_domain(domain) if domain else ""
    # Record at address level; fall back to root domain for noreply-like senders
    noreply_like = not address or re.search(r'noreply|no-reply|mailer|bounce|do-not-reply', address)
    if noreply_like and root:
        d = patterns["domains"].setdefault(root, {})
        d[label] = d.get(label, 0) + 1
    elif address:
        a = patterns["addresses"].setdefault(address, {})
        a[label] = a.get(label, 0) + 1


def brand_label(from_header, cfg, label):
    """Return label if sender matches a domain or address in cfg, else None.

    cfg: dict with optional 'domains' and 'addresses' lists, or a plain list of domains.
    """
    if isinstance(cfg, list):
        cfg = {"domains": cfg}
    sender_domain = extract_domain(from_header)
    sender_root = root_domain(sender_domain)
    sender_address = extract_address(from_header)
    for d in cfg.get("domains", []):
        if sender_root == root_domain(d) or sender_domain == d:
            return label
    for a in cfg.get("addresses", []):
        if sender_address == a.lower():
            return label
    return None


LABEL_EMOJI = {
    "INBOX":                      "✔️",
    "Spam":                       "🚫",
    "Archive":                    "📁",
    "Reading List":               "📨",
    "Window Shopping":            "🛍️",
    "Orders & Receipts":          "📦",
    "Shipping Info":              "📬",
    "Finances":                   "💰",
    "Healthcare and Insurance":   "🏥",
    "Travel":                     "✈️",
    "Household":                  "🏠",
    "Mayfield HoA":               "🏘️",
    "Kickstarted":                "🎯",
    "Correspondence":             "💌",
    "Trash":                      "🗑️",
}

PENDING_PATH   = Path("/home/openclaw/.openclaw/triage-pending.json")
SPAM_LOG_PATH  = Path("/home/openclaw/.openclaw/spam-log.json")

# Labels shown individually in proposal email; bulk labels get count-only
BULK_LABELS = {"Spam", "Archive", "Trash"}

# Labels acted on by the spam fast-pass
SPAM_FAST_LABELS = {"Spam", "Trash"}

# Labels that bypass the age gate — moved immediately even if message is recent
IMMEDIATE_LABELS = {"Spam", "Trash", "Archive", "Shipping Info", "Window Shopping", "Reading List"}


def append_spam_log(entries):
    """Append moved-spam entries to the daily spam log."""
    existing = []
    if SPAM_LOG_PATH.exists():
        try:
            existing = json.loads(SPAM_LOG_PATH.read_text())
        except Exception:
            pass
    existing.extend(entries)
    SPAM_LOG_PATH.write_text(json.dumps(existing, ensure_ascii=False))


def pop_spam_log():
    """Read and delete the spam log. Returns list of entries (may be empty)."""
    if not SPAM_LOG_PATH.exists():
        return []
    try:
        entries = json.loads(SPAM_LOG_PATH.read_text())
        SPAM_LOG_PATH.unlink()
        return entries
    except Exception:
        return []


def _apply_custom_folders():
    """Load triage-folders.json and extend LABELS, FOLDER_MAP, LABEL_EMOJI, BULK_LABELS, SYSTEM_PROMPT."""
    global SYSTEM_PROMPT
    if not CUSTOM_FOLDERS_PATH.exists():
        return
    try:
        data = json.loads(CUSTOM_FOLDERS_PATH.read_text())
    except Exception as exc:
        print(f"[warn] Could not load {CUSTOM_FOLDERS_PATH}: {exc}", file=sys.stderr)
        return
    extras = []
    for f in data.get("folders", []):
        name      = f.get("name", "").strip()
        imap_name = f.get("imap", name).strip()
        desc      = f.get("description", "").strip()
        emoji     = f.get("emoji", "📂")
        bulk      = bool(f.get("bulk", False))
        if not name or not imap_name:
            continue
        if name not in LABELS:
            LABELS.append(name)
        FOLDER_MAP[name]   = imap_name
        LABEL_EMOJI[name]  = emoji
        if bulk:
            BULK_LABELS.add(name)
        if desc and not desc.startswith("TODO:"):
            extras.append(f"- {name}: {desc}")
    if extras:
        SYSTEM_PROMPT = SYSTEM_PROMPT + "\n" + "\n".join(extras)


def save_pending(batch_id, account, source_folder, results):
    data = {
        "batch_id": batch_id,
        "account": account,
        "source_folder": source_folder,
        "status": "pending",
        "messages": results,
    }
    PENDING_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_pending():
    if not PENDING_PATH.exists():
        return None
    return json.loads(PENDING_PATH.read_text())


_EMAIL_CSS = """\
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; color-scheme: light dark; }
body {
  font-family: Georgia, "Times New Roman", Times, serif;
  font-size: 18px; line-height: 1.7; color: #1a1a1a; background-color: #ffffff;
  max-width: 660px; margin: 0 auto; padding: 28px 28px 52px;
  -webkit-font-smoothing: antialiased; word-wrap: break-word;
  overflow-wrap: break-word; box-sizing: border-box;
}
@media (max-width: 480px) {
  body { padding: 20px 16px 40px; font-size: 17px; }
  .col-date { display: none; }
  td { padding: 7px 10px 7px 0; font-size: 15px; }
  th { padding: 7px 10px 7px 0; }
}
h1, h2, h3, h4, h5, h6 {
  font-family: Georgia, "Times New Roman", Times, serif; font-weight: 700;
  line-height: 1.25; margin-top: 2rem; margin-bottom: 0.5rem; color: #1a1a1a;
}
h1 { font-size: 28px; } h2 { font-size: 22px; } h3 { font-size: 18px; }
h1:first-child, h2:first-child { margin-top: 0; }
p { margin-top: 0; margin-bottom: 20px; }
a { color: #1a1a1a; text-decoration: underline; text-decoration-color: #cccccc; text-underline-offset: 3px; }
ul, ol { padding-left: 24px; margin-bottom: 20px; }
li { margin-bottom: 6px; }
hr { border: none; border-top: 1px solid #e0e0e0; margin: 40px 0; }
code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.875em; background-color: #f5f5f5; padding: 0.15em 0.35em;
  border-radius: 3px; color: #1a1a1a;
}
table { width: 100%; border-collapse: collapse; font-size: 16px; margin: 16px 0; }
th {
  text-align: left;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  color: #666666; border-bottom: 2px solid #e0e0e0; padding: 8px 16px 8px 0;
  white-space: nowrap;
}
td { border-bottom: 1px solid #e0e0e0; padding: 8px 16px 8px 0; vertical-align: middle; }
td:last-child, th:last-child { padding-right: 0; }
tr:last-child td { border-bottom: none; }
.col-date  { width: 46px; white-space: nowrap; }
.col-from  { width: 26%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 180px; }
@media screen and (max-width: 600px) {
  .col-date { display: none; }
  .col-from { width: 34%; max-width: none; }
}
details { margin: 28px 0; }
details > summary {
  font-family: Georgia, "Times New Roman", Times, serif;
  font-size: 22px; font-weight: 700; line-height: 1.25; color: #1a1a1a;
  cursor: pointer; list-style: none; padding: 4px 0; user-select: none;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: "\\25B6\\FE0E"; font-size: 0.55em; color: #999999; margin-right: 0.5em; vertical-align: middle; }
details[open] > summary::before { content: "\\25BC\\FE0E"; }
.msg-link { color: inherit; text-decoration: none; }
.msg-link:hover { text-decoration: underline; text-decoration-color: #999999; }
.row-feedback {
  font-size: 0.8em; color: #bbbbbb; text-decoration: none;
  margin-left: 0.4em; vertical-align: middle;
}
.row-feedback:hover { color: #888888; }
.row-unsub {
  font-size: 0.72em; color: #bbbbbb; text-decoration: none;
  margin-left: 0.4em; vertical-align: middle;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
.row-unsub:hover { color: #888888; }
@media (prefers-color-scheme: dark) {
  body { color: #e8e8e8; background-color: #1c1c1e; }
  h1, h2, h3, h4, h5, h6 { color: #e8e8e8; }
  details > summary { color: #e8e8e8; }
  details > summary::before { color: #777777; }
  a { color: #e8e8e8; text-decoration-color: #555555; }
  hr { border-top-color: #333333; }
  code { background-color: #2a2a2c; color: #e8e8e8; }
  th { color: #aaaaaa; border-bottom-color: #333333; }
  td { border-bottom-color: #333333; }
  .from-addr { color: #666666; }
  .msg-link:hover { text-decoration-color: #555555; }
}
"""


def _md_to_html(text):
    try:
        import markdown
        return markdown.markdown(text, extensions=["tables", "fenced_code"])
    except ImportError:
        import html
        return "<pre>" + html.escape(text) + "</pre>"


def _wrap_html(body_html):
    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        f"<style>{_EMAIL_CSS}</style>"
        f"</head><body>{body_html}</body></html>"
    )


def _smtp_send_html(from_acct, to_acct, subject, body_md, body_html=None):
    """Send a multipart/alternative email: plain text + styled HTML."""
    html = body_html if body_html is not None else _wrap_html(_md_to_html(body_md))
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["From"] = from_acct["email"]
    msg["To"] = to_acct["email"]
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=from_acct["email"].split("@")[1])
    msg.attach(email.mime.text.MIMEText(body_md, "plain", "utf-8"))
    msg.attach(email.mime.text.MIMEText(html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(from_acct["smtp_host"], from_acct["smtp_port"]) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.login(from_acct["email"], from_acct["password"])
        smtp.sendmail(from_acct["email"], [to_acct["email"]], msg.as_string())


def _md_cell(s):
    """Escape a value for use in a Markdown table cell."""
    return str(s).replace("|", "\\|").replace("\r", "").replace("\n", " ").strip()


def _parse_from(from_str):
    """Return (display_name, address) with quotes stripped from the name."""
    name, addr = email.utils.parseaddr(from_str)
    name = name.strip('"').strip("'").strip()
    return name, addr


def _parse_list_unsubscribe(header_val):
    """Extract the best URL from a List-Unsubscribe header value.
    Prefers HTTPS over mailto:."""
    if not header_val:
        return None
    import re
    urls = re.findall(r'<([^>]+)>', header_val)
    https = [u for u in urls if u.startswith("https://") or u.startswith("http://")]
    mailto = [u for u in urls if u.startswith("mailto:")]
    return https[0] if https else (mailto[0] if mailto else None)


def _build_triage_sections(p, ref_id, source_folder, results, counts):
    """Append collapsible per-label detail sections with ↩ feedback links to p (list of HTML strings)."""
    e  = _html.escape
    ea = lambda s: _html.escape(str(s), quote=True)

    for label in LABELS:
        if label not in counts or label in BULK_LABELS:
            continue
        if FOLDER_MAP.get(label) == source_folder:
            continue
        emoji = LABEL_EMOJI.get(label, "")
        items = [r for r in results if r["label"] == label]
        p.append(f'<details open><summary>{e(emoji)} {e(label)} \u2014 {len(items)}</summary>')
        p.append('<table><thead><tr>'
                 '<th class="col-date">Date</th>'
                 '<th class="col-from">From</th>'
                 '<th>Subject</th>'
                 '</tr></thead><tbody>')
        for r in items:
            uid_raw    = r.get("uid", "")
            date_val   = r.get("date", "")
            frm_raw    = r.get("from", "")
            subj_val   = r.get("subject", "")
            msg_id_raw = r.get("message_id", "")
            name, addr = _parse_from(frm_raw)
            display_from = name if name else addr
            from_cell = f'<span title="{ea(frm_raw)}">{e(display_from[:30])}</span>'
            from_display = f"{name} <{addr}>" if name else addr
            mid_part = f" [mid: {msg_id_raw}]" if msg_id_raw else ""
            line1 = f"{date_val}  {from_display}  \u2014  {subj_val}  [uid: {uid_raw}]{mid_part}"
            row_href = (
                "mailto:marvin@packet.works?subject="
                + urllib.parse.quote(f"Feedback: {ref_id}", safe="")
                + "&body="
                + urllib.parse.quote(f"{line1}\n{label} -> ", safe="")
            )
            unsub_url = r.get("unsubscribe")
            unsub_link = (
                f' <a class="row-unsub" href="{ea(unsub_url)}" title="Unsubscribe">unsub</a>'
                if unsub_url else ""
            )
            # message:// deep-link opens the message in macOS/iOS Mail
            if msg_id_raw:
                msg_url = "message://" + urllib.parse.quote(msg_id_raw, safe="")
                subj_cell = f'<a class="msg-link" href="{ea(msg_url)}">{e(subj_val)}</a>'
            else:
                subj_cell = e(subj_val)
            p.append(
                f'<tr>'
                f'<td class="col-date">{e(date_val)}</td>'
                f'<td class="col-from">{from_cell}</td>'
                f'<td>{subj_cell}'
                f' <a class="row-feedback" href="{ea(row_href)}" title="Send feedback">\u21a9</a>'
                f'{unsub_link}</td>'
                f'</tr>'
            )
        p.append('</tbody></table></details>')

    # Bulk label counts
    bulk_parts = [
        f'{e(LABEL_EMOJI.get(lbl, ""))} <strong>{e(lbl)}</strong> \u2014 {counts[lbl]} items'
        for lbl in BULK_LABELS if lbl in counts
    ]
    if bulk_parts:
        p.append('<hr><p>' + ' &nbsp;\u00b7&nbsp; '.join(bulk_parts) + '</p>')


def _build_proposal_html(batch_id, source_folder, results, counts, moves, now):
    """Build the HTML body for a triage proposal."""
    e = _html.escape
    p = []
    p.append(f'<h1>Triage Proposal \u2014 {len(results)} messages</h1>')
    p.append(f'<p><strong>{e(now)}</strong> \u00b7 Source: {e(source_folder)}</p>')
    p.append('<table><thead><tr><th>Destination</th><th style="text-align:right">Count</th></tr></thead><tbody>')
    for label in LABELS:
        if label not in counts:
            continue
        staying_note = ' <em>(staying)</em>' if FOLDER_MAP[label] == source_folder else ''
        p.append(f'<tr><td>{e(LABEL_EMOJI.get(label,""))} {e(label)}{staying_note}</td>'
                 f'<td style="text-align:right">{counts[label]}</td></tr>')
    p.append(f'<tr><td><strong>Total</strong></td><td style="text-align:right"><strong>{len(results)}</strong></td></tr>')
    p.append('</tbody></table><hr>')
    _build_triage_sections(p, batch_id, source_folder, results, counts)
    p.append('<hr>')
    p.append(f'<p>Reply <strong>approve</strong> to execute all {moves} moves.</p>')
    p.append(f'<p>Batch ID: <code>{e(batch_id)}</code></p>')
    return _wrap_html('\n'.join(p))


def _build_summary_html(sort_id, source_folder, results, counts, now, spam_log=None):
    """Build the HTML body for a triage summary (already executed)."""
    e = _html.escape
    p = []
    total = len(results)
    p.append(f'<h1>Triage Complete \u2014 {total} messages</h1>')
    p.append(f'<p><strong>{e(now)}</strong> \u00b7 Source: {e(source_folder)}</p>')
    p.append('<table><thead><tr><th>Destination</th><th style="text-align:right">Count</th></tr></thead><tbody>')
    for label in LABELS:
        if label not in counts:
            continue
        staying_note = ' <em>(staying)</em>' if FOLDER_MAP.get(label) == source_folder else ''
        p.append(f'<tr><td>{e(LABEL_EMOJI.get(label,""))} {e(label)}{staying_note}</td>'
                 f'<td style="text-align:right">{counts[label]}</td></tr>')
    p.append(f'<tr><td><strong>Total</strong></td><td style="text-align:right"><strong>{total}</strong></td></tr>')
    p.append('</tbody></table><hr>')
    _build_triage_sections(p, sort_id, source_folder, results, counts)
    if spam_log:
        spam_counts = Counter(e2["label"] for e2 in spam_log)
        p.append(f'<hr><h2>Fast-pass \u2014 {len(spam_log)} caught overnight</h2>')
        p.append('<table><thead><tr><th>Destination</th><th style="text-align:right">Count</th></tr></thead><tbody>')
        for label, count in spam_counts.most_common():
            p.append(f'<tr><td>{_html.escape(LABEL_EMOJI.get(label,""))} {_html.escape(label)}</td>'
                     f'<td style="text-align:right">{count}</td></tr>')
        p.append('</tbody></table>')
    p.append(f'<hr><p><small>Sort ID: <code>{e(sort_id)}</code> · Use ↩ to send feedback</small></p>')
    return _wrap_html('\n'.join(p))


def send_proposal(config, batch_id, source_folder, results):
    """Send a formatted triage proposal email — no moves have happened yet."""
    marvin = config["accounts"]["marvin"]
    jehan = config["accounts"]["jehan"]

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = Counter(r["label"] for r in results)
    moves = sum(1 for r in results if FOLDER_MAP[r["label"]] != source_folder)

    lines = [
        f"# Triage Proposal — {len(results)} messages",
        "",
        f"**{now}** · Source: {source_folder}",
        "",
        "| Destination | Count |",
        "|---|---:|",
    ]
    for label in LABELS:
        if label in counts:
            emoji = LABEL_EMOJI.get(label, "")
            staying = " *(staying)*" if FOLDER_MAP[label] == source_folder else ""
            lines.append(f"| {emoji} {label}{staying} | {counts[label]} |")
    lines += [
        f"| **Total** | **{len(results)}** |",
        "",
        "---",
        "",
    ]

    # Detail sections — skip bulk labels and messages staying in place
    for label in LABELS:
        if label not in counts or label in BULK_LABELS:
            continue
        staying = FOLDER_MAP[label] == source_folder
        if staying:
            continue
        emoji = LABEL_EMOJI.get(label, "")
        items = [r for r in results if r["label"] == label]
        action = "to move"
        lines += [
            f"## {emoji} {label} — {len(items)} {action}",
            "",
            "| Date | From | Subject |",
            "|---|---|---|",
        ]
        for r in items:
            lines.append(
                f"| {_md_cell(r['date'])} "
                f"| {_md_cell(r['from'][:45])} "
                f"| {_md_cell(r['subject'][:60])} |"
            )
        lines.append("")

    # Bulk label summaries
    bulk_parts = []
    for label in BULK_LABELS:
        if label in counts:
            emoji = LABEL_EMOJI.get(label, "")
            bulk_parts.append(f"{emoji} **{label}** — {counts[label]} items")
    if bulk_parts:
        lines += ["---", ""] + bulk_parts + [""]

    lines += [
        "---",
        "",
        f"Reply **approve** to execute all {moves} moves.",
        "",
        f"Batch ID: `{batch_id}`",
    ]

    body_md = "\n".join(lines)
    body_html = _build_proposal_html(batch_id, source_folder, results, counts, moves, now)
    subject = f"[Marvin] {moves} to move · reply approve"
    try:
        _smtp_send_html(marvin, jehan, subject, body_md, body_html=body_html)
        print(f"  Proposal sent to {jehan['email']}", file=sys.stderr)
    except Exception as e:
        print(f"  [warn] Failed to send proposal: {e}", file=sys.stderr)


def send_summary(config, source_folder, results, errors, spam_log=None):
    """Send a triage summary email from marvin to jehan."""
    marvin = config["accounts"]["marvin"]
    jehan = config["accounts"]["jehan"]

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(results)
    counts = Counter(r["label"] for r in results)
    err_str = f"{errors} error{'s' if errors != 1 else ''}"

    lines = [
        f"# Triage Complete — {total} messages",
        "",
        f"**{now}** · Source: {source_folder}",
        "",
        "| Destination | Count |",
        "|---|---:|",
    ]
    for label, count in counts.most_common():
        emoji = LABEL_EMOJI.get(label, "")
        lines.append(f"| {emoji} {label} | {count} |")
    lines += [
        f"| **Total** | **{total}** |",
        "",
    ]
    if errors:
        lines += [f"*{err_str}*", ""]
    lines += ["---", ""]

    for label in LABELS:
        items = [r for r in results if r["label"] == label]
        if not items:
            continue
        staying = FOLDER_MAP[label] == source_folder
        if staying:
            continue
        emoji = LABEL_EMOJI.get(label, "")
        lines += [
            f"## {emoji} {label} — {len(items)} moved",
            "",
            "| Date | From | Subject |",
            "|---|---|---|",
        ]
        for r in items:
            lines.append(
                f"| {_md_cell(r.get('date', ''))} "
                f"| {_md_cell(r.get('from', '')[:45])} "
                f"| {_md_cell(r.get('subject', '')[:60])} |"
            )
        lines.append("")

    # Spam fast-pass section
    if spam_log:
        spam_counts = Counter(e["label"] for e in spam_log)
        spam_total = len(spam_log)
        lines += [
            "---",
            "",
            f"## Fast-pass — {spam_total} caught overnight",
            "",
            "| Destination | Count |",
            "|---|---:|",
        ]
        for label, count in spam_counts.most_common():
            emoji = LABEL_EMOJI.get(label, "")
            lines.append(f"| {emoji} {label} | {count} |")
        lines.append("")

    body_md = "\n".join(lines)
    sort_id = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    body_html = _build_summary_html(sort_id, source_folder, results, counts, now, spam_log=spam_log)
    top3 = ", ".join(f"{lbl} {c}" for lbl, c in counts.most_common(3))
    spam_note = f" · +{len(spam_log)} fast-pass" if spam_log else ""
    subject = f"[Marvin] Sorted {total}{spam_note} · {top3}"
    try:
        _smtp_send_html(marvin, jehan, subject, body_md, body_html=body_html)
        print(f"  Summary sent to {jehan['email']}", file=sys.stderr)
    except Exception as e:
        print(f"  [warn] Failed to send summary: {e}", file=sys.stderr)


def cmd_run(args, config):
    acct = config["accounts"][args.account]
    litellm_cfg = config["litellm"]
    protected_cfg = config.get("protected", {})
    ws_cfg = config.get("window_shopping", {})
    ws_imap = FOLDER_MAP["Window Shopping"]
    rl_cfg = config.get("reading_list", {})
    max_age_hours = protected_cfg.get("max_age_hours", 0)
    now_ts = time.time()

    print(f"Fetching {args.batch} messages from {args.folder}...", file=sys.stderr)
    messages = fetch_batch(acct, args.folder, args.batch)

    if not messages:
        print("No messages found.")
        return

    patterns = load_patterns()
    results = []
    errors = 0
    skipped = 0

    spam_moved = []

    for msg in messages:
        # Classify first — needed to decide whether age gate applies
        label = (
            brand_label(msg["from"], protected_cfg, "INBOX")
            or brand_label(msg["from"], ws_cfg, "Window Shopping")
            or brand_label(msg["from"], rl_cfg, "Reading List")
            or classify(msg["from"], msg["subject"], litellm_cfg)
        )

        # Age filter — bypassed in spam-only mode and for immediate-destination labels
        # (Spam, Trash, Archive, Shipping Info, Window Shopping, Reading List move at once)
        if (not args.spam_only
                and label not in IMMEDIATE_LABELS
                and max_age_hours
                and msg.get("timestamp", 0) > 0):
            age_h = (now_ts - msg["timestamp"]) / 3600
            if age_h < max_age_hours:
                print(f"  [skip] UID {msg['uid']:>6}  recent ({age_h:.0f}h)  {msg['from'][:40]}")
                skipped += 1
                continue

        # Spam-only mode: skip anything that isn't Spam/Trash
        if args.spam_only and label not in SPAM_FAST_LABELS:
            continue

        imap_folder = FOLDER_MAP[label]
        staying = (imap_folder == args.folder)
        results.append({**msg, "label": label})

        if args.dry_run:
            action = "stay" if staying else f"→  {label}"
            print(f"  [dry-run] UID {msg['uid']:>6}  {action}")
            print(f"            From: {msg['from'][:72]}")
            print(f"            Subj: {msg['subject'][:72]}")
        elif staying:
            print(f"  UID {msg['uid']:>6}  stay  {label}  ({msg['from'][:40]})")
        else:
            try:
                if label == "Window Shopping":
                    archived = dedup_window_shopping(acct, extract_domain(msg["from"]), ws_imap)
                    if archived:
                        print(f"  UID {msg['uid']:>6}  →  Window Shopping  (archived {archived} prior)", file=sys.stderr)
                move_message(acct, msg["uid"], args.folder, imap_folder)
                record_pattern(msg["from"], label, patterns)
                print(f"  UID {msg['uid']:>6}  →  {label}  ({msg['from'][:40]})")
                if args.spam_only:
                    spam_moved.append({
                        "ts": now_ts, "uid": msg["uid"],
                        "from": msg["from"], "subject": msg["subject"], "label": label,
                    })
            except Exception as e:
                print(f"  [error] UID {msg['uid']}: {e}", file=sys.stderr)
                errors += 1

    counts = Counter(r["label"] for r in results)

    if args.propose:
        batch_id = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        # Attach imap_folder to each result for execute step
        for r in results:
            r["imap_folder"] = FOLDER_MAP[r["label"]]
        save_pending(batch_id, args.account, args.folder, results)
        save_patterns(patterns)
        send_proposal(config, batch_id, args.folder, results)
        print(f"\nProposal sent. Batch ID: {batch_id}")
        print(f"Pending file: {PENDING_PATH}")
        print("Run `marvin-triage execute` after approval.")
    elif args.dry_run:
        pass  # already printed per-message above
    elif args.spam_only:
        save_patterns(patterns)
        if spam_moved:
            append_spam_log(spam_moved)
            print(f"\nSpam fast-pass: {len(spam_moved)} moved → spam log.")
        else:
            print("\nSpam fast-pass: nothing to move.")
    else:
        save_patterns(patterns)
        if not args.no_notify and results:
            spam_log = pop_spam_log()
            send_summary(config, args.folder, results, errors, spam_log=spam_log)

    print()
    print("── Summary ──────────────────────────────")
    for label, count in counts.most_common():
        print(f"  {count:>4}  {label}")
    print(f"  ----")
    print(f"  {len(results):>4}  classified  ({errors} errors)")
    if skipped:
        print(f"  {skipped:>4}  skipped (too recent)")
    if args.dry_run:
        print("  [dry-run: no messages moved, patterns not saved]")
    elif args.propose:
        print("  [propose: no messages moved, pending file saved]")


def cmd_window_shopping(args, config):
    """Flush Window Shopping → Archive. Run every Monday morning via cron."""
    acct = config["accounts"][args.account]
    ws_imap = FOLDER_MAP["Window Shopping"]

    conn = imap_connect(acct)
    conn.select(ws_imap)
    _, data = conn.uid("SEARCH", None, "ALL")
    uids = data[0].split() if data[0] else []
    conn.logout()

    if not uids:
        print("Window Shopping is empty.")
        return

    print(f"Flushing {len(uids)} messages from Window Shopping → Archive...")
    moved = 0
    errors = 0
    for uid in uids:
        uid_str = uid.decode()
        if args.dry_run:
            print(f"  [dry-run] UID {uid_str}")
        else:
            try:
                move_message(acct, uid_str, ws_imap, FOLDER_MAP["Archive"])
                moved += 1
            except Exception as e:
                print(f"  [error] UID {uid_str}: {e}", file=sys.stderr)
                errors += 1

    if args.dry_run:
        print(f"  [dry-run: {len(uids)} would be archived]")
    else:
        print(f"Done. {moved} archived, {errors} errors.")


def cmd_patterns(args):
    """Show high-confidence sender→label patterns, separated by promotion path."""
    patterns = load_patterns()

    # Load sieve state to skip already-pending/dismissed entries
    _sieve_state_path = Path("/home/openclaw/.openclaw/sieve-state.json")
    sieve_state = {}
    if _sieve_state_path.exists():
        try:
            sieve_state = json.loads(_sieve_state_path.read_text())
        except Exception:
            pass
    pending_set   = set(sieve_state.get("pending",   {}).keys())
    dismissed_set = set(sieve_state.get("dismissed", {}).keys())

    # Labels best handled via marvin-mail.json config (not Sieve)
    MARVIN_MAIL_LABELS = {"Window Shopping", "Reading List"}

    # Sieve variable names for each label
    SIEVE_VAR = {
        "Archive":                  "L1_Archive",
        "Shipping Info":            "L1_ShippingInfo",
        "Mayfield HoA":             "L1_MayfieldHoA",
        "Kickstarted":              "L1_Kickstarted",
        "Orders & Receipts":        "L1_OrdersReceipts",
        "Window Shopping":          "L1_WindowShopping",
        "Reading List":             "L1_ReadingList",
        "Finances":                 "L1_Finances",
        "Healthcare and Insurance": "L1_Healthcare",
        "Travel":                   "L1_Travel",
        "Household":                "L1_Household",
        "Correspondence":           "L1_Correspondence",
    }
    # Labels whose Execute sections don't exist in main.sieve yet
    NEW_EXECUTE_NEEDED = {
        "Window Shopping", "Reading List", "Orders & Receipts", "Finances",
        "Healthcare and Insurance", "Travel", "Household", "Correspondence",
    }

    candidates = []
    n_skipped = 0
    for key, tallies in [
        ("domain",  patterns.get("domains",   {})),
        ("address", patterns.get("addresses", {})),
    ]:
        for sender, counts in tallies.items():
            total = sum(counts.values())
            if total < args.min_count:
                continue
            top_label, top_count = max(counts.items(), key=lambda x: x[1])
            if top_label == "INBOX":
                continue  # no action needed
            confidence = top_count / total
            if confidence < args.min_confidence:
                continue
            state_key = ("address" if "@" in sender else "domain") + ":" + sender
            if state_key in pending_set or state_key in dismissed_set:
                n_skipped += 1
                continue
            candidates.append({
                "type":       key,
                "sender":     sender,
                "label":      top_label,
                "count":      total,
                "confidence": confidence,
            })

    if not candidates:
        msg = f"No actionable patterns meet threshold (min_count={args.min_count}, min_confidence={args.min_confidence:.0%})."
        if n_skipped:
            msg += f" ({n_skipped} already pending/dismissed)"
        print(msg)
        return

    candidates.sort(key=lambda x: x["count"], reverse=True)

    sieve_cands      = [c for c in candidates if c["label"] not in MARVIN_MAIL_LABELS]
    marvin_cfg_cands = [c for c in candidates if c["label"] in MARVIN_MAIL_LABELS]

    skip_note = f"  ({n_skipped} already pending/dismissed — skipped)" if n_skipped else ""

    if sieve_cands:
        print(f"── Sieve candidates ({len(sieve_cands)}) ─────────────────────────────────────────")
        print(f"   Threshold: \u2265{args.min_count} samples, \u2265{args.min_confidence:.0%} confidence{skip_note}\n")

        for c in sieve_cands:
            sender = c["sender"]
            label  = c["label"]
            pct    = int(c["confidence"] * 100)
            var    = SIEVE_VAR.get(label)

            print(f"  [{c['type']}] {sender}")
            print(f"    \u2192 {label}  ({c['count']} samples, {pct}% confidence)")

            if label == "Trash":
                section = "blocked-senders domains" if c["type"] == "domain" else "blocked-addresses"
                snippet = (
                    f"# {sender} \u2192 Trash\n"
                    f"# ADD to {section} list at top of main.sieve:\n"
                    f'#   "{sender}",\n'
                )
            elif label == "Spam":
                match = (
                    f'address :domain :is "From" "{sender}"'
                    if c["type"] == "domain"
                    else f'address :is "From" "{sender}"'
                )
                snippet = (
                    f"# {sender} \u2192 Spam\n"
                    f"if allof(\n"
                    f'  not string :is "${{stop}}" "Y",\n'
                    f"  {match}\n"
                    f") {{\n"
                    f'  set "spam" "Y";\n'
                    f'  set "stop" "Y";\n'
                    f"}}\n"
                )
            elif var:
                match = (
                    f'address :domain :is "From" "{sender}"'
                    if c["type"] == "domain"
                    else f'address :is "From" "{sender}"'
                )
                execute_note = (
                    f"\n# NEW EXECUTE BLOCK NEEDED — add to File into folder actions:\n"
                    f'# if string :is "${{{var}}}" "Y" {{\n'
                    f'#   fileinto :copy "INBOX.{label}";\n'
                    f'#   set "hasmailbox" "Y";\n'
                    f"# }}"
                    if label in NEW_EXECUTE_NEEDED else ""
                )
                snippet = (
                    f"# {sender} \u2192 {label}\n"
                    f"if allof(\n"
                    f'  not string :is "${{stop}}" "Y",\n'
                    f"  {match}\n"
                    f") {{\n"
                    f'  set "{var}" "Y";\n'
                    f'  set "skipinbox" "Y";\n'
                    f'  set "stop" "Y";\n'
                    f"}}{execute_note}\n"
                )
            else:
                snippet = f"# {sender} \u2192 {label}: no Sieve template (custom folder — use marvin-feedback report)\n"

            print()
            for line in snippet.splitlines():
                print(f"    {line}")
            print()

    if marvin_cfg_cands:
        print(f"── marvin-mail.json candidates ({len(marvin_cfg_cands)}) ────────────────────────────")
        print(f"   Add these to window_shopping / reading_list in marvin-mail.json\n")
        for c in marvin_cfg_cands:
            section = "window_shopping" if c["label"] == "Window Shopping" else "reading_list"
            print(f"  [{c['type']}] {c['sender']}  ({c['count']} samples, {int(c['confidence']*100)}%)")
            print(f'    \u2192 add to {section}["{c["sender"]}"] in marvin-mail.json')
            print()


def _imap_utf7_decode(name):
    """Decode modified UTF-7 folder name (RFC 2060). Handles &- → & for ASCII names."""
    # &- is the escape for a literal &. Non-ASCII sequences (&<base64>-) are left as-is
    # since all practical folder names here are ASCII.
    return re.sub(r'&-', '&', name)


def _list_imap_folders(acct):
    """Return sorted list of IMAP folder name strings for the account."""
    conn = imap_connect(acct)
    _, listing = conn.list()
    conn.logout()
    folders = []
    for item in listing or []:
        if not item:
            continue
        decoded = item.decode("utf-8", errors="replace")
        # (\Flags) "sep" "Folder Name"  or  (\Flags) "sep" FolderName
        m = re.match(r'\([^)]*\)\s+"[^"]+"\s+"?(.+?)"?\s*$', decoded)
        if m:
            folders.append(_imap_utf7_decode(m.group(1).strip()))
    return sorted(folders)


def cmd_folders(args, config):
    """List IMAP folders and their triage mapping status."""
    acct = config["accounts"][args.account]
    print(f"Fetching folder list for {acct['email']}…", file=sys.stderr)
    imap_folders = _list_imap_folders(acct)

    imap_to_label = {v: k for k, v in FOLDER_MAP.items()}

    # Load custom folder file to know what's already registered there
    custom_imap = set()
    if CUSTOM_FOLDERS_PATH.exists():
        try:
            cdata = json.loads(CUSTOM_FOLDERS_PATH.read_text())
            custom_imap = {f.get("imap", f.get("name", "")) for f in cdata.get("folders", [])}
        except Exception:
            pass

    unknown = []
    print(f"\n  {'Folder':<36} Status")
    print(f"  {'-'*36} ------")
    for f in imap_folders:
        if f in imap_to_label:
            print(f"  {f:<36} ✓  {imap_to_label[f]}")
        elif f in custom_imap:
            print(f"  {f:<36} +  (custom, stub — add description)")
        else:
            print(f"  {f:<36} ?  unmapped")
            unknown.append(f)

    print()
    if not unknown:
        print("All folders are mapped.")
        return

    print(f"{len(unknown)} unmapped folder(s).")

    if not args.sync:
        print(f"\nRun with --sync to add stubs to {CUSTOM_FOLDERS_PATH}")
        return

    # Load or create custom folders file
    if CUSTOM_FOLDERS_PATH.exists():
        try:
            cdata = json.loads(CUSTOM_FOLDERS_PATH.read_text())
        except Exception:
            cdata = {"folders": []}
    else:
        cdata = {"folders": []}

    existing_imap = {f.get("imap", f.get("name", "")) for f in cdata["folders"]}
    added = 0
    for f in unknown:
        if f in existing_imap:
            continue
        cdata["folders"].append({
            "name": f,
            "imap": f,
            "description": f"TODO: describe when Marvin should route messages to {f}",
            "emoji": "📂",
            "bulk": False,
        })
        added += 1

    if added:
        CUSTOM_FOLDERS_PATH.write_text(json.dumps(cdata, indent=2, ensure_ascii=False))
        print(f"Added {added} stub(s) to {CUSTOM_FOLDERS_PATH}")
        print(f"Edit the file to add descriptions, then re-run triage.")
    else:
        print("No new stubs needed.")


def cmd_execute(args, config):
    """Execute a pending proposal batch."""
    pending = load_pending()
    if not pending:
        sys.exit(f"No pending batch found at {PENDING_PATH}")
    if pending["status"] == "executed":
        sys.exit(f"Batch {pending['batch_id']} already executed.")

    acct = config["accounts"][pending["account"]]
    source = pending["source_folder"]
    messages = pending["messages"]
    ws_imap = FOLDER_MAP["Window Shopping"]

    moves = [r for r in messages if r.get("imap_folder", FOLDER_MAP[r["label"]]) != source]
    print(f"Executing batch {pending['batch_id']} — {len(moves)} moves from {source}")

    moved = 0
    errors = 0
    for r in moves:
        imap_folder = r.get("imap_folder", FOLDER_MAP[r["label"]])
        if args.dry_run:
            print(f"  [dry-run] UID {r['uid']:>6}  →  {r['label']}")
            continue
        try:
            if r["label"] == "Window Shopping":
                dedup_window_shopping(acct, extract_domain(r["from"]), ws_imap)
            move_message(acct, r["uid"], source, imap_folder)
            print(f"  UID {r['uid']:>6}  →  {r['label']}  ({r['from'][:40]})")
            moved += 1
        except Exception as e:
            print(f"  [error] UID {r['uid']}: {e}", file=sys.stderr)
            errors += 1

    if not args.dry_run:
        pending["status"] = "executed"
        PENDING_PATH.write_text(json.dumps(pending, indent=2, ensure_ascii=False))
        send_summary(config, source, messages, errors)
        print(f"\nDone. {moved} moved, {errors} errors. Summary sent.")
    else:
        print(f"\n[dry-run: {len(moves)} would be moved]")


def main():
    parser = argparse.ArgumentParser(
        description="Marvin Stage 2 email triage — classify, move, build patterns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Classify and move a batch of messages")
    p_run.add_argument("--account", default="jehan", help="Account (default: jehan)")
    p_run.add_argument("--folder", default="INBOX", help="Source IMAP folder (default: INBOX)")
    p_run.add_argument("--batch", type=int, default=20, help="Messages per run (default: 20)")
    p_run.add_argument("--dry-run", action="store_true", help="Classify only, don't move")
    p_run.add_argument("--propose", action="store_true", help="Classify, email proposal, save pending — don't move")
    p_run.add_argument("--no-notify", action="store_true", help="Skip summary email after run")
    p_run.add_argument("--spam-only", action="store_true",
                       help="Fast-pass: move only Spam/Trash, no age filter, no summary email")

    p_ws = sub.add_parser("window-shopping", help="Flush Window Shopping → Archive (Monday cron)")
    p_ws.add_argument("--account", default="jehan")
    p_ws.add_argument("--dry-run", action="store_true")

    p_exec = sub.add_parser("execute", help="Execute a pending proposal batch")
    p_exec.add_argument("--account", default="jehan")
    p_exec.add_argument("--dry-run", action="store_true")

    p_pat = sub.add_parser("patterns", help="Show high-confidence Sieve promotion candidates")
    p_pat.add_argument("--min-count", type=int, default=3)
    p_pat.add_argument("--min-confidence", type=float, default=0.8)

    p_folders = sub.add_parser("folders", help="List IMAP folders and their triage mapping status")
    p_folders.add_argument("--account", default="jehan")
    p_folders.add_argument("--sync", action="store_true",
                           help=f"Add unmapped folders as stubs to {CUSTOM_FOLDERS_PATH}")

    sub.add_parser("resend", help="Resend the pending proposal email without re-classifying")

    args = parser.parse_args()
    config = load_config()
    _apply_custom_folders()

    if args.cmd == "run":
        cmd_run(args, config)
    elif args.cmd == "execute":
        cmd_execute(args, config)
    elif args.cmd == "window-shopping":
        cmd_window_shopping(args, config)
    elif args.cmd == "patterns":
        cmd_patterns(args)
    elif args.cmd == "folders":
        cmd_folders(args, config)
    elif args.cmd == "resend":
        pending = load_pending()
        if not pending:
            sys.exit(f"No pending batch found at {PENDING_PATH}")
        print(f"Resending proposal for batch {pending['batch_id']} ({len(pending['messages'])} messages)…", file=sys.stderr)
        send_proposal(config, pending["batch_id"], pending["source_folder"], pending["messages"])
        print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
