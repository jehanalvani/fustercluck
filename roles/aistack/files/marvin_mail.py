#!/usr/bin/env python3
"""marvin-mail — IMAP/SMTP CLI for Marvin and Jehan's Fastmail accounts.

Usage:
  marvin-mail [--account marvin|jehan] list [--folder FOLDER] [--limit N]
  marvin-mail [--account marvin|jehan] read <uid> [--folder FOLDER]
  marvin-mail [--account marvin|jehan] send --to ADDR --subject SUBJ --body TEXT
  marvin-mail [--account marvin|jehan] search QUERY [--folder FOLDER] [--limit N]
  marvin-mail [--account marvin|jehan] move <uid> --from FOLDER --to FOLDER
  marvin-mail [--account marvin|jehan] delete <uid> [--folder FOLDER]
  marvin-mail [--account marvin|jehan] folders

Credentials: /home/openclaw/.openclaw/marvin-mail.json (mode 0600)
"""

import argparse
import email
import email.header
import email.mime.multipart
import email.mime.text
import email.utils
import imaplib
import json
import smtplib
import ssl
import sys
from pathlib import Path

CONFIG_PATH = Path("/home/openclaw/.openclaw/marvin-mail.json")

# Fastmail trash folder name
TRASH_FOLDER = "Trash"


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"Config not found: {CONFIG_PATH}")
    except json.JSONDecodeError as e:
        sys.exit(f"Config parse error: {e}")


def get_account(config, account_name):
    accounts = config.get("accounts", {})
    if account_name not in accounts:
        sys.exit(f"Unknown account '{account_name}'. Valid: {list(accounts.keys())}")
    return accounts[account_name]


def imap_connect(acct):
    conn = imaplib.IMAP4_SSL(acct["imap_host"], acct["imap_port"])
    conn.login(acct["email"], acct["password"])
    return conn


def decode_header_value(value):
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def cmd_list(acct, folder, limit):
    conn = imap_connect(acct)
    try:
        conn.select(folder, readonly=True)
        _, data = conn.search(None, "ALL")
        uids = data[0].split() if data[0] else []
        uids = uids[-limit:]
        results = []
        for uid in reversed(uids):
            _, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if not msg_data or msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            results.append({
                "uid": uid.decode(),
                "from": decode_header_value(msg.get("From", "")),
                "subject": decode_header_value(msg.get("Subject", "(no subject)")),
                "date": msg.get("Date", ""),
            })
    finally:
        conn.logout()
    print(json.dumps(results, indent=2, ensure_ascii=False))


def cmd_read(acct, uid, folder):
    conn = imap_connect(acct)
    try:
        conn.select(folder, readonly=True)
        _, msg_data = conn.fetch(uid, "(RFC822)")
        if not msg_data or msg_data[0] is None:
            sys.exit(f"Message UID {uid} not found in {folder}")
        msg = email.message_from_bytes(msg_data[0][1])
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = part.get("Content-Disposition", "")
                if ct == "text/plain" and "attachment" not in cd:
                    charset = part.get_content_charset() or "utf-8"
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                    break
        else:
            charset = msg.get_content_charset() or "utf-8"
            body = msg.get_payload(decode=True).decode(charset, errors="replace")
    finally:
        conn.logout()
    result = {
        "uid": uid,
        "from": decode_header_value(msg.get("From", "")),
        "to": decode_header_value(msg.get("To", "")),
        "subject": decode_header_value(msg.get("Subject", "(no subject)")),
        "date": msg.get("Date", ""),
        "body": body[:8000],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


_EMAIL_CSS = """\
html {
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
  color-scheme: light dark;
}
body {
  font-family: Georgia, "Times New Roman", Times, serif;
  font-size: 18px;
  line-height: 1.7;
  color: #1a1a1a;
  background-color: #ffffff;
  max-width: 660px;
  margin: 0 auto;
  padding: 28px 28px 52px;
  -webkit-font-smoothing: antialiased;
  word-wrap: break-word;
  overflow-wrap: break-word;
  box-sizing: border-box;
}
@media (max-width: 480px) {
  body { padding: 20px 16px 40px; font-size: 17px; }
  table th:first-child, table td:first-child { display: none; }
  td { padding: 7px 10px 7px 0; font-size: 15px; }
  th { padding: 7px 10px 7px 0; }
}
h1, h2, h3, h4, h5, h6 {
  font-family: Georgia, "Times New Roman", Times, serif;
  font-weight: 700;
  line-height: 1.25;
  margin-top: 2rem;
  margin-bottom: 0.5rem;
  color: #1a1a1a;
}
h1 { font-size: 28px; }
h2 { font-size: 22px; }
h3 { font-size: 18px; }
h4 { font-size: 16px; }
h5 { font-size: 14px; text-transform: uppercase; letter-spacing: 0.06em; }
h6 { font-size: 14px; color: #666666; }
h1:first-child, h2:first-child { margin-top: 0; }
p { margin-top: 0; margin-bottom: 20px; }
a {
  color: #1a1a1a;
  text-decoration: underline;
  text-decoration-color: #cccccc;
  text-underline-offset: 3px;
}
blockquote {
  margin: 24px 0;
  padding: 4px 0 4px 20px;
  border-left: 3px solid #cccccc;
  color: #666666;
  font-style: normal;
}
blockquote p { margin-bottom: 8px; }
blockquote p:last-child { margin-bottom: 0; }
ul, ol { padding-left: 24px; margin-bottom: 20px; }
li { margin-bottom: 6px; }
li > ul, li > ol { margin-top: 6px; margin-bottom: 0; }
hr { border: none; border-top: 1px solid #e0e0e0; margin: 40px 0; }
code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.875em;
  background-color: #f5f5f5;
  padding: 0.15em 0.35em;
  border-radius: 3px;
  color: #1a1a1a;
}
pre {
  background-color: #f5f5f5;
  border-radius: 5px;
  padding: 16px 20px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin: 24px 0;
  font-size: 14px;
  line-height: 1.55;
}
pre code { background: none; padding: 0; font-size: inherit; border-radius: 0; }
table { width: 100%; border-collapse: collapse; font-size: 14px; margin: 24px 0; }
th {
  text-align: left;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #666666;
  border-bottom: 2px solid #e0e0e0;
  padding: 8px 12px 8px 0;
  white-space: nowrap;
}
td { border-bottom: 1px solid #e0e0e0; padding: 10px 12px 10px 0; vertical-align: top; }
tr:last-child td { border-bottom: none; }
@media (prefers-color-scheme: dark) {
  body { color: #e8e8e8; background-color: #1c1c1e; }
  h1, h2, h3, h4, h5, h6 { color: #e8e8e8; }
  h6 { color: #aaaaaa; }
  a { color: #e8e8e8; text-decoration-color: #555555; }
  blockquote { border-left-color: #555555; color: #aaaaaa; }
  hr { border-top-color: #333333; }
  code { background-color: #2a2a2c; color: #e8e8e8; }
  pre { background-color: #2a2a2c; }
  th { color: #aaaaaa; border-bottom-color: #333333; }
  td { border-bottom-color: #333333; }
}
"""


def _md_to_html(text):
    try:
        import markdown
        return markdown.markdown(
            text,
            extensions=["tables", "fenced_code"],
        )
    except ImportError:
        # Minimal fallback: escape HTML and wrap in <pre> so it's at least readable
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


def cmd_send(acct, to, subject, body):
    html = _wrap_html(_md_to_html(body))
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["From"] = acct["email"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=acct["email"].split("@")[1])
    msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
    msg.attach(email.mime.text.MIMEText(html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(acct["smtp_host"], acct["smtp_port"]) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.login(acct["email"], acct["password"])
        smtp.sendmail(acct["email"], [to], msg.as_string())
    print(json.dumps({
        "status": "sent",
        "from": acct["email"],
        "to": to,
        "subject": subject,
    }))


def cmd_search(acct, query, folder, limit):
    conn = imap_connect(acct)
    try:
        conn.select(folder, readonly=True)
        _, data = conn.search(None, f'TEXT "{query}"')
        uids = data[0].split() if data[0] else []
        uids = uids[-limit:]
        results = []
        for uid in reversed(uids):
            _, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
            if not msg_data or msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            results.append({
                "uid": uid.decode(),
                "from": decode_header_value(msg.get("From", "")),
                "subject": decode_header_value(msg.get("Subject", "(no subject)")),
                "date": msg.get("Date", ""),
            })
    finally:
        conn.logout()
    print(json.dumps(results, indent=2, ensure_ascii=False))


def cmd_move(acct, uid, from_folder, to_folder):
    conn = imap_connect(acct)
    try:
        conn.select(from_folder)
        # Try MOVE extension first (RFC 6851), fall back to COPY + DELETE
        typ, data = conn.uid("MOVE", uid, to_folder)
        if typ != "OK":
            typ, _ = conn.uid("COPY", uid, to_folder)
            if typ != "OK":
                sys.exit(f"Failed to copy UID {uid} to {to_folder}: {data}")
            conn.uid("STORE", uid, "+FLAGS", "\\Deleted")
            conn.expunge()
    finally:
        conn.logout()
    print(json.dumps({
        "status": "moved",
        "uid": uid,
        "from": from_folder,
        "to": to_folder,
    }))


def cmd_delete(acct, uid, folder):
    """Move message to Trash."""
    conn = imap_connect(acct)
    try:
        conn.select(folder)
        typ, data = conn.uid("MOVE", uid, TRASH_FOLDER)
        if typ != "OK":
            typ, _ = conn.uid("COPY", uid, TRASH_FOLDER)
            if typ != "OK":
                sys.exit(f"Failed to move UID {uid} to Trash: {data}")
            conn.uid("STORE", uid, "+FLAGS", "\\Deleted")
            conn.expunge()
    finally:
        conn.logout()
    print(json.dumps({
        "status": "deleted",
        "uid": uid,
        "from": folder,
        "to": TRASH_FOLDER,
    }))


def decode_modified_utf7(s):
    """Decode IMAP modified UTF-7 encoded folder names (RFC 2060)."""
    parts = s.split("&")
    result = [parts[0]]
    for part in parts[1:]:
        if part.startswith("-"):
            result.append("&" + part[1:])
        else:
            end = part.find("-")
            if end == -1:
                result.append("&" + part)
            else:
                import base64
                encoded = part[:end]
                # Pad to multiple of 4
                encoded += "=" * (-len(encoded) % 4)
                try:
                    decoded = base64.b64decode(encoded).decode("utf-16-be")
                except Exception:
                    decoded = "&" + part[:end]
                result.append(decoded + part[end + 1:])
    return "".join(result)


def parse_imap_list_line(line):
    """Extract folder name from an IMAP LIST response line."""
    import re
    # Strip flags: (\Flag ...)
    line = re.sub(r"^\(.*?\)\s+", "", line)
    # Strip delimiter: "/" or NIL
    line = re.sub(r'^"[^"]*"\s+|^NIL\s+', "", line)
    name = line.strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return decode_modified_utf7(name)


def cmd_folders(acct):
    conn = imap_connect(acct)
    try:
        _, data = conn.list()
        folders = []
        for item in data:
            if item is None:
                continue
            name = parse_imap_list_line(item.decode("utf-8", errors="replace"))
            if name:
                folders.append(name)
    finally:
        conn.logout()
    print(json.dumps(sorted(folders), indent=2, ensure_ascii=False))


def cmd_create_folder(acct, folder):
    conn = imap_connect(acct)
    try:
        typ, data = conn.create(folder)
        if typ != "OK":
            sys.exit(f"Failed to create folder '{folder}': {data}")
    finally:
        conn.logout()
    print(json.dumps({"status": "created", "folder": folder}))


def main():
    parser = argparse.ArgumentParser(
        description="Marvin mail CLI — IMAP/SMTP access for Fastmail accounts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--account", default="marvin",
        help="Account to use: marvin (marvin@packet.works) or jehan (jehan@jehanalvani.com)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List messages in a folder")
    p_list.add_argument("--folder", default="INBOX", help="IMAP folder name")
    p_list.add_argument("--limit", type=int, default=20, help="Max messages to return")

    p_read = sub.add_parser("read", help="Read a message by UID")
    p_read.add_argument("uid", help="IMAP message UID")
    p_read.add_argument("--folder", default="INBOX", help="IMAP folder name")

    p_send = sub.add_parser("send", help="Send an email")
    p_send.add_argument("--to", required=True, help="Recipient address")
    p_send.add_argument("--subject", required=True, help="Subject line")
    p_send.add_argument("--body", required=True, help="Plain-text body")

    p_search = sub.add_parser("search", help="Search messages by text")
    p_search.add_argument("query", help="Search text")
    p_search.add_argument("--folder", default="INBOX", help="IMAP folder name")
    p_search.add_argument("--limit", type=int, default=10, help="Max results")

    p_move = sub.add_parser("move", help="Move a message between folders")
    p_move.add_argument("uid", help="IMAP message UID")
    p_move.add_argument("--from", dest="from_folder", required=True, help="Source folder")
    p_move.add_argument("--to", dest="to_folder", required=True, help="Destination folder")

    p_delete = sub.add_parser("delete", help="Move a message to Trash")
    p_delete.add_argument("uid", help="IMAP message UID")
    p_delete.add_argument("--folder", default="INBOX", help="Source folder")

    sub.add_parser("folders", help="List all folders in the account")

    p_create = sub.add_parser("create-folder", help="Create a new folder")
    p_create.add_argument("folder", help="Folder name to create")

    args = parser.parse_args()
    config = load_config()
    acct = get_account(config, args.account)

    if args.cmd == "list":
        cmd_list(acct, args.folder, args.limit)
    elif args.cmd == "read":
        cmd_read(acct, args.uid, args.folder)
    elif args.cmd == "send":
        cmd_send(acct, args.to, args.subject, args.body)
    elif args.cmd == "search":
        cmd_search(acct, args.query, args.folder, args.limit)
    elif args.cmd == "move":
        cmd_move(acct, args.uid, args.from_folder, args.to_folder)
    elif args.cmd == "delete":
        cmd_delete(acct, args.uid, args.folder)
    elif args.cmd == "folders":
        cmd_folders(acct)
    elif args.cmd == "create-folder":
        cmd_create_folder(acct, args.folder)


if __name__ == "__main__":
    main()
