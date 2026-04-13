#!/usr/bin/env python3
"""marvin-spam-check — Secondary spam/phishing scanner.

Analyzes email headers and body for indicators of phishing, BEC attacks,
advance fee fraud, tech support scams, and other social engineering patterns.
Complements Fastmail's server-side Junk filter.

Usage:
  marvin-spam-check <uid>    [--folder INBOX] [--account jehan] [--json] [--quiet]
  marvin-spam-check --stdin  [--json]
"""

import argparse
import email
import email.header
import email.utils
import html.parser
import imaplib
import json
import re
import sys
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import List

CONFIG_PATH = Path("/home/openclaw/.openclaw/marvin-mail.json")


# ── Signal model ──────────────────────────────────────────────────────────────

@dataclass
class Signal:
    name:     str
    weight:   float    # 0.0–1.0
    detail:   str
    category: str      # sender | body | links | structure

# A single signal at or above this weight alone triggers likely_scam
STRONG_SIGNAL_THRESHOLD = 0.85
LIKELY_SCAM_SCORE       = 1.5
SUSPICIOUS_SCORE        = 0.5

VERDICT_LIKELY_SCAM = "likely_scam"
VERDICT_SUSPICIOUS  = "suspicious"
VERDICT_CLEAN       = "clean"


def verdict(signals: List[Signal]):
    total = sum(s.weight for s in signals)
    if any(s.weight >= STRONG_SIGNAL_THRESHOLD for s in signals):
        return VERDICT_LIKELY_SCAM, total
    if total >= LIKELY_SCAM_SCORE:
        return VERDICT_LIKELY_SCAM, total
    if total >= SUSPICIOUS_SCORE:
        return VERDICT_SUSPICIOUS, total
    return VERDICT_CLEAN, total


# ── Reference data ────────────────────────────────────────────────────────────

# Commonly impersonated brands → their legitimate tld+1 sending domains
BRAND_DOMAINS = {
    "paypal":              ["paypal.com"],
    "apple":               ["apple.com", "icloud.com"],
    "icloud":              ["apple.com", "icloud.com"],
    "google":              ["google.com", "gmail.com"],
    "youtube":             ["google.com", "youtube.com"],
    "microsoft":           ["microsoft.com", "outlook.com", "live.com", "hotmail.com",
                            "office.com"],
    "office 365":          ["microsoft.com", "office.com"],
    "outlook":             ["microsoft.com", "outlook.com"],
    "amazon":              ["amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de",
                            "amazon.fr", "amazon.co.jp"],
    "aws":                 ["amazon.com", "amazonaws.com"],
    "netflix":             ["netflix.com"],
    "spotify":             ["spotify.com"],
    "facebook":            ["facebook.com", "meta.com", "facebookmail.com"],
    "instagram":           ["instagram.com", "facebookmail.com"],
    "twitter":             ["twitter.com", "x.com"],
    "linkedin":            ["linkedin.com"],
    "dropbox":             ["dropbox.com"],
    "docusign":            ["docusign.com", "docusign.net"],
    "chase":               ["chase.com"],
    "bank of america":     ["bankofamerica.com"],
    "wells fargo":         ["wellsfargo.com"],
    "citibank":            ["citibank.com", "citi.com"],
    "capital one":         ["capitalone.com"],
    "american express":    ["americanexpress.com"],
    "amex":                ["americanexpress.com"],
    "venmo":               ["venmo.com"],
    "zelle":               ["zellepay.com"],
    "coinbase":            ["coinbase.com"],
    "irs":                 ["irs.gov"],
    "fbi":                 ["fbi.gov"],
    "social security":     ["ssa.gov"],
    "medicare":            ["medicare.gov", "cms.gov"],
    "usps":                ["usps.com", "usps.gov"],
    "fedex":               ["fedex.com"],
    "ups":                 ["ups.com"],
    "dhl":                 ["dhl.com"],
    "geek squad":          ["bestbuy.com", "geeksquad.com"],
    "best buy":            ["bestbuy.com"],
    "norton":              ["norton.com", "nortonlifelock.com"],
    "mcafee":              ["mcafee.com", "trellix.com"],
}

# tld+1 domains of legitimate package carriers (used to suppress false positives)
CARRIER_DOMAINS = {
    "ups.com", "fedex.com", "usps.com", "usps.gov", "dhl.com",
    "ontrac.com", "amazon.com", "amazon.co.uk", "lasership.com",
}

# Free/consumer email providers
FREE_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "aol.com", "icloud.com", "me.com", "protonmail.com", "proton.me",
    "yandex.com", "mail.com", "inbox.com", "zoho.com",
}

# Common URL shortening services
URL_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "short.io",
    "buff.ly", "ift.tt", "dlvr.it", "soo.gd", "cli.gs", "wp.me",
    "is.gd", "qr.ae", "tiny.cc", "rb.gy", "cutt.ly",
}

# Typosquat character substitutions to normalize before comparing domains
LOOKALIKE_SUBS = [
    (r'0', 'o'), (r'1', 'l'), (r'1', 'i'),
    (r'rn', 'm'), (r'vv', 'w'), (r'cl', 'd'),
    (r'nn', 'm'), (r'ii', 'n'),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def tld_plus_one(domain: str) -> str:
    """'mail.paypal.com' → 'paypal.com'"""
    parts = domain.lower().rstrip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


def from_domain(msg) -> str:
    """Extract tld+1 from the From header."""
    _, addr = email.utils.parseaddr(msg.get("From", ""))
    raw = addr.split("@")[-1].lower() if "@" in addr else ""
    return tld_plus_one(raw) if raw else ""


def decode_subject(msg) -> str:
    subject = msg.get("Subject", "")
    try:
        parts = email.header.decode_header(subject)
        return "".join(
            p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p
            for p, enc in parts
        )
    except Exception:
        return subject


class _LinkExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links  = []           # [(text, href)]
        self.images = []           # [src]
        self._txt_buf  = []
        self._href     = None
        self._all_text = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "a":
            self._href    = d.get("href", "")
            self._txt_buf = []
        elif tag == "img" and d.get("src"):
            self.images.append(d["src"])

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append(("".join(self._txt_buf).strip(), self._href))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._txt_buf.append(data)
        else:
            self._all_text.append(data)

    @property
    def all_text(self):
        return "".join(self._all_text)


def parse_html(html_body: str):
    p = _LinkExtractor()
    try:
        p.feed(html_body)
    except Exception:
        pass
    return p.links, p.images, p.all_text


def extract_body(msg):
    """Return (plain_text, html_body, links, images)."""
    plain = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if "attachment" in part.get("Content-Disposition", ""):
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if ct == "text/plain" and not plain:
                plain = decoded
            elif ct == "text/html" and not html_body:
                html_body = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = decoded
            else:
                plain = decoded

    links, images, html_text = parse_html(html_body) if html_body else ([], [], "")
    body_text = plain or html_text
    return body_text, html_body, links, images


# ── Heuristics ────────────────────────────────────────────────────────────────

def h_display_name_mismatch(msg) -> List[Signal]:
    """Known brand in display name but sending domain doesn't match."""
    _, addr = email.utils.parseaddr(msg.get("From", ""))
    display, _ = email.utils.parseaddr(msg.get("From", ""))
    display = display.lower()
    d1 = tld_plus_one(addr.split("@")[-1]) if "@" in addr else ""

    for brand, legit in BRAND_DOMAINS.items():
        if brand in display and d1 not in legit:
            return [Signal("DISPLAY_NAME_MISMATCH", 0.90,
                           f'"{display}" sent from {d1} (legit: {", ".join(legit)})',
                           "sender")]
    return []


def h_reply_to_mismatch(msg) -> List[Signal]:
    """Reply-To domain differs from From domain — classic BEC prep."""
    rt = msg.get("Reply-To", "")
    if not rt:
        return []
    _, from_addr = email.utils.parseaddr(msg.get("From", ""))
    _, rt_addr   = email.utils.parseaddr(rt)
    fd = tld_plus_one(from_addr.split("@")[-1]) if "@" in from_addr else ""
    rd = tld_plus_one(rt_addr.split("@")[-1])   if "@" in rt_addr   else ""
    if fd and rd and fd != rd:
        return [Signal("REPLY_TO_MISMATCH", 0.70,
                       f"From: {fd}  Reply-To: {rd}", "sender")]
    return []


def h_domain_lookalike(msg) -> List[Signal]:
    """Sending domain is a typosquat or subdomain abuse of a known brand."""
    _, addr = email.utils.parseaddr(msg.get("From", ""))
    if not addr or "@" not in addr:
        return []
    raw_domain = addr.split("@")[-1].lower()
    d1 = tld_plus_one(raw_domain)

    # Subdomain abuse: paypal.com.evil.xyz
    for brand, legit in BRAND_DOMAINS.items():
        for ld in legit:
            if ld in raw_domain and d1 != ld and d1 not in legit:
                return [Signal("SUBDOMAIN_ABUSE", 0.85,
                               f"{raw_domain} contains {ld} but routes to {d1}",
                               "sender")]

    # Typosquatting via character substitution
    normalized = d1
    for pat, rep in LOOKALIKE_SUBS:
        normalized = re.sub(pat, rep, normalized)
    for brand, legit in BRAND_DOMAINS.items():
        for ld in legit:
            if normalized == ld and d1 != ld:
                return [Signal("DOMAIN_LOOKALIKE", 0.85,
                               f"{d1} resembles {ld} after character substitution",
                               "sender")]
    return []


def h_free_email_financial(msg, body_text: str) -> List[Signal]:
    """Free email provider sending financial/security/account-related mail."""
    _, addr = email.utils.parseaddr(msg.get("From", ""))
    d1 = tld_plus_one(addr.split("@")[-1]) if "@" in addr else ""
    if d1 not in FREE_EMAIL_PROVIDERS:
        return []
    subject = decode_subject(msg).lower()
    combined = (subject + " " + body_text[:500]).lower()
    TRIGGERS = ["invoice", "payment", "bank", "wire transfer", "account",
                "billing", "security alert", "verify", "suspended",
                "unusual activity", "refund", "transaction", "your order"]
    if any(t in combined for t in TRIGGERS):
        return [Signal("FREE_EMAIL_FINANCIAL", 0.60,
                       f"Financial/account content from {d1}", "sender")]
    return []


def h_auth_failures(msg) -> List[Signal]:
    """Authentication header failures — SPF/DKIM/DMARC."""
    signals = []
    spam_status = msg.get("X-Spam-Status", "").lower()
    if spam_status.startswith("yes"):
        signals.append(Signal("SPAM_HEADER_POSITIVE", 0.75,
                               spam_status[:80], "sender"))
    auth = msg.get("Authentication-Results", "").lower()
    if auth:
        failures = [label for label, token in
                    [("SPF fail", "spf=fail"), ("DKIM fail", "dkim=fail"),
                     ("DMARC fail", "dmarc=fail")]
                    if token in auth]
        if failures:
            signals.append(Signal("AUTH_FAILURE", 0.70,
                                   "; ".join(failures), "sender"))
    return signals


def h_homograph_domain(msg) -> List[Signal]:
    """Non-ASCII (e.g. Cyrillic) characters in the From address domain."""
    _, addr = email.utils.parseaddr(msg.get("From", ""))
    domain = addr.split("@")[-1] if "@" in addr else ""
    sus = [c for c in domain if ord(c) > 127]
    if sus:
        return [Signal("HOMOGRAPH_DOMAIN", 0.80,
                       f"Non-ASCII in From domain: {''.join(sus)[:10]}", "sender")]
    return []


def h_urgency_language(subject: str, body_text: str) -> List[Signal]:
    """Time-pressure and account-threat language — #1 social engineering lever."""
    PATTERNS = [
        r'\bimmediate(ly)?\b', r'\burgent(ly)?\b', r'\bact now\b',
        r'\bwithin \d+ hours?\b', r'\bexpires? (today|soon|in \d+)\b',
        r'\b(last|final) (notice|warning|chance|reminder)\b',
        r'\baction required\b', r'\bresponse required\b',
        r'\b(your )?(account|access) (will be|has been) (suspended|disabled|terminated|locked)\b',
        r'\bverify (now|immediately|your account)\b',
        r'\bconfirm (now|immediately)\b',
        r'\bclick (here|below) (now|immediately|to verify)\b',
    ]
    combined = (subject + " " + body_text[:2000]).lower()
    hits = [m.group(0) for p in PATTERNS if (m := re.search(p, combined))]
    if not hits:
        return []
    weight = min(0.15 * len(hits), 0.55)
    return [Signal("URGENCY_LANGUAGE", weight, "; ".join(hits[:4]), "body")]


def h_threat_language(subject: str, body_text: str) -> List[Signal]:
    """Fear-based threats — legal action, hacking, account compromise."""
    PATTERNS = [
        r'\b(legal action|lawsuit|prosecution|arrest|warrant)\b',
        r'\b(hacked?|malware|virus|infected|compromised|breach(ed)?)\b',
        r'\bunauthorized (access|sign.?in|login|activity)\b',
        r'\bsuspicious (activity|login|sign.?in|access)\b',
        r'\b(account|password) (compromised|stolen|breached)\b',
        r'\bwe (have )?(detected|noticed|identified)\b',
        r'\byour (device|computer|account) (has been|is) (infected|hacked|compromised)\b',
    ]
    combined = (subject + " " + body_text[:2000]).lower()
    hits = [m.group(0) for p in PATTERNS if (m := re.search(p, combined))]
    if not hits:
        return []
    weight = min(0.20 * len(hits), 0.55)
    return [Signal("THREAT_LANGUAGE", weight, "; ".join(hits[:4]), "body")]


def h_credential_harvest(body_text: str) -> List[Signal]:
    """Explicit asks to click a link to verify/update credentials."""
    PATTERNS = [
        r'\b(click|tap) (here|below|this link) to (verify|confirm|update|validate|reset)\b',
        r'\b(verify|confirm|validate|update) your (password|credentials?|account|identity|billing|payment)\b',
        r'\b(enter|provide|submit) your (password|pin|credentials?)\b',
        r'\bsign in to (verify|confirm|update)\b',
        r'\byour (session|login) has expired\b',
        r'\bre.?enter your\b',
        r'\bconfirm (your )?(account )?(details?|information|billing)\b',
    ]
    hits = [m.group(0) for p in PATTERNS if (m := re.search(p, body_text.lower()))]
    if hits:
        return [Signal("CREDENTIAL_HARVEST", 0.88, "; ".join(hits[:3]), "body")]
    return []


def h_financial_lure(subject: str, body_text: str) -> List[Signal]:
    """Advance fee fraud, lottery/prize, inheritance, windfall promises."""
    PATTERNS = [
        r'\b(lottery|lotto|sweepstakes)\b.{0,60}\b(winner|won|prize|claim)\b',
        r'\b(winner|won)\b.{0,60}\b(lottery|lotto|prize|million)\b',
        r'\binheritance\b', r'\bbeneficiary\b',
        r'\bunclaimed (funds?|money|assets?)\b',
        r'\badvance.?fee\b',
        r'\boverpay(ment|ed)\b.*\b(check|refund|return|send back)\b',
        r'\bnigerian?\b.*\bprince\b',
        r'\b(double|triple) your (money|investment|bitcoin)\b',
        r'\bcrypto(currency)? (investment|opportunity|profit)\b',
        r'\b\$[\d,]+\b.{0,40}\b(waiting|unclaimed|available|held|release)\b',
    ]
    combined = (subject + " " + body_text[:3000]).lower()
    hits = [m.group(0) for p in PATTERNS if (m := re.search(p, combined, re.DOTALL))]
    if hits:
        return [Signal("FINANCIAL_LURE", 0.80, "; ".join(hits[:3]), "body")]
    return []


def h_bec_patterns(subject: str, body_text: str) -> List[Signal]:
    """BEC: wire transfers, gift card requests, payroll diversion — highest $ loss category."""
    combined = (subject + " " + body_text[:3000]).lower()
    signals  = []

    WIRE = [
        r'\b(wire|bank) transfer\b.{0,60}\b(request|needed|required|process|initiate)\b',
        r'\b(initiate|process|send|complete)\b.{0,40}\bwire\b',
        r'\b(new|updated?|changed?) (bank|payment|wire) (details?|account|instructions?)\b',
        r'\bchange (of|in) (bank|payment) (details?|account)\b',
    ]
    wire_hits = [m.group(0) for p in WIRE if (m := re.search(p, combined, re.DOTALL))]
    if wire_hits:
        signals.append(Signal("BEC_WIRE_TRANSFER", 0.92,
                               "; ".join(wire_hits[:2]), "body"))

    GIFT = [
        r'\bgift card(s)?\b',
        r'\b(itunes?|amazon|google play|steam|ebay) (card|code|gift)\b',
        r'\bbuy.{0,20}(cards?|vouchers?).{0,40}(send|email).{0,20}(code|number)\b',
    ]
    gift_hits = [m.group(0) for p in GIFT if (m := re.search(p, combined, re.DOTALL))]
    if gift_hits:
        signals.append(Signal("BEC_GIFT_CARD", 0.92,
                               "; ".join(gift_hits[:2]), "body"))

    PAYROLL = [
        r'\b(update|change|modify)\b.{0,50}\b(direct deposit|payroll)\b',
        r'\bdirect deposit\b.{0,50}\b(update|change|new|different)\b',
    ]
    payroll_hits = [m.group(0) for p in PAYROLL if (m := re.search(p, combined, re.DOTALL))]
    if payroll_hits:
        signals.append(Signal("BEC_PAYROLL_DIVERSION", 0.88,
                               "; ".join(payroll_hits[:2]), "body"))

    return signals


def h_authority_impersonation(subject: str, body_text: str, msg) -> List[Signal]:
    """Government/law enforcement authority invoked alongside threat language."""
    AUTHORITIES = [
        ("irs",                   "irs.gov"),
        ("internal revenue",      "irs.gov"),
        ("fbi",                   "fbi.gov"),
        ("social security",       "ssa.gov"),
        ("department of justice", "justice.gov"),
        ("homeland security",     "dhs.gov"),
        ("medicare",              "medicare.gov"),
        ("customs",               "cbp.gov"),
    ]
    THREAT_WORDS = ["owe", "arrest", "warrant", "penalty", "fine", "unpaid",
                    "suspended", "required", "notice", "delinquent", "past due"]

    _, addr  = email.utils.parseaddr(msg.get("From", ""))
    f_domain = tld_plus_one(addr.split("@")[-1]) if "@" in addr else ""
    combined = (subject + " " + body_text[:2000]).lower()

    for keyword, legit_domain in AUTHORITIES:
        if keyword in combined and legit_domain not in f_domain:
            if any(t in combined for t in THREAT_WORDS):
                return [Signal("AUTHORITY_IMPERSONATION", 0.85,
                               f'"{keyword}" invoked, not from {legit_domain}',
                               "body")]
    return []


def h_generic_greeting(body_text: str) -> List[Signal]:
    """Mass-phishing tell: impersonal greeting rather than your name."""
    PATTERNS = [
        r'^dear (customer|user|valued customer|account holder|member|client)\b',
        r'^dear (sir|madam|sir/madam|sir or madam)\b',
        r'^hello (customer|user|account holder)\b',
        r'^attention[:\s]+(account holder|user|customer)\b',
        r'^greetings,?\s*(valued |dear )?(customer|user|member)\b',
    ]
    first_300 = body_text[:300].strip().lower()
    for p in PATTERNS:
        if re.search(p, first_300, re.MULTILINE):
            return [Signal("GENERIC_GREETING", 0.30, "Impersonal greeting", "body")]
    return []


def h_pii_request(body_text: str) -> List[Signal]:
    """Explicit requests for SSN, credit card number, password, etc."""
    PATTERNS = [
        r'\b(social security|ssn|sin)\b.{0,60}\b(number|no\.?|#)\b',
        r'\bcredit card.{0,40}(number|details?|information)\b',
        r'\b(send|provide|submit|enter|reply with).{0,50}\b(password|pin|passcode)\b',
        r'\bmother.?s maiden name\b',
        r'\b(bank|account|routing) (number|details?)\b.{0,40}(send|provide|submit|enter)\b',
    ]
    hits = [m.group(0) for p in PATTERNS if (m := re.search(p, body_text.lower()))]
    if hits:
        return [Signal("PII_REQUEST", 0.85, "; ".join(hits[:3]), "body")]
    return []


def h_package_scam(subject: str, body_text: str, msg) -> List[Signal]:
    """Package delivery scam from non-carrier domain."""
    d1 = from_domain(msg)
    if d1 in CARRIER_DOMAINS:
        return []
    PATTERNS = [
        r'\b(package|parcel|shipment|delivery)\b.{0,60}\b(failed|couldn.t|unable|missed|held|pending)\b',
        r'\b(couldn.t|failed to|unable to) (deliver|complete delivery)\b',
        r'\b(reschedule|arrange).{0,30}(delivery|redelivery)\b',
        r'\b(customs|handling|redelivery).{0,20}(fee|charge)\b',
        r'\btrack.{0,20}(your|the) (package|parcel|shipment|order)\b',
    ]
    combined = (subject + " " + body_text[:1000]).lower()
    hits = [m.group(0) for p in PATTERNS if (m := re.search(p, combined, re.DOTALL))]
    if len(hits) >= 2:
        return [Signal("PACKAGE_SCAM", 0.65, "; ".join(hits[:3]), "body")]
    return []


def h_tech_support_scam(subject: str, body_text: str) -> List[Signal]:
    """Fake tech support — vendor claims your device/account is compromised."""
    PATTERNS = [
        r'\b(microsoft|apple|google|norton|mcafee|geek squad)\b.{0,80}\b(virus|malware|infected|error|warning|hacked)\b',
        r'\b(your|the) (computer|pc|device|mac)\b.{0,60}\b(virus|malware|infected|hacked|compromised)\b',
        r'\bcall.{0,30}(1.?800|toll.?free|support).{0,30}(number|line|immediately)\b',
        r'\bdo not (turn off|restart|shutdown).{0,30}(computer|pc|device)\b',
        r'\b(license|subscription).{0,30}(expired?|renewal)\b.{0,80}\bcall.{0,30}(support|us|immediately)\b',
    ]
    combined = (subject + " " + body_text[:2000]).lower()
    hits = [m.group(0) for p in PATTERNS if (m := re.search(p, combined, re.DOTALL))]
    if hits:
        return [Signal("TECH_SUPPORT_SCAM", 0.82, "; ".join(hits[:3]), "body")]
    return []


def h_link_text_mismatch(links) -> List[Signal]:
    """Link display text implies one destination, href goes elsewhere."""
    signals = []
    for text, href in links:
        if not text.strip() or not href or href.startswith("mailto:"):
            continue
        try:
            href_d1 = tld_plus_one(urllib.parse.urlparse(href).netloc.lower())
        except Exception:
            continue
        if not href_d1:
            continue

        # Text looks like a URL pointing somewhere different
        m = re.search(r'\b[\w.-]+\.(com|org|net|gov|io|co)\b', text.lower())
        if m:
            text_d1 = tld_plus_one(m.group(0))
            if text_d1 != href_d1:
                signals.append(Signal("LINK_TEXT_MISMATCH", 0.75,
                                       f'"{text[:50]}" links to {href_d1}', "links"))
                if len(signals) >= 3:
                    break
                continue

        # Text mentions a known brand but href goes elsewhere
        text_lower = text.lower()
        for brand, legit in BRAND_DOMAINS.items():
            if brand in text_lower and href_d1 not in legit:
                signals.append(Signal("LINK_TEXT_MISMATCH", 0.75,
                                       f'"{text[:50]}" links to {href_d1}', "links"))
                break

    return signals[:3]


def h_url_shortener(links, body_text: str) -> List[Signal]:
    """URL shortener in links or body — hides true destination."""
    found = set()
    for _, href in links:
        try:
            d = urllib.parse.urlparse(href).netloc.lower()
            if d in URL_SHORTENERS:
                found.add(d)
        except Exception:
            pass
    for s in URL_SHORTENERS:
        if s in body_text.lower():
            found.add(s)
    if found:
        return [Signal("URL_SHORTENER", 0.45, ", ".join(found), "links")]
    return []


def h_ip_in_url(links) -> List[Signal]:
    """Bare IP address in a link href — legitimate services use domain names."""
    IP_RE = re.compile(r'^https?://\d{1,3}(\.\d{1,3}){3}')
    for _, href in links:
        if IP_RE.match(href):
            return [Signal("IP_IN_URL", 0.80, href[:80], "links")]
    return []


def h_url_subdomain_abuse(links) -> List[Signal]:
    """URL uses legit-brand.com as a subdomain of an attacker domain."""
    for _, href in links:
        try:
            netloc = urllib.parse.urlparse(href).netloc.lower()
        except Exception:
            continue
        d1 = tld_plus_one(netloc)
        for brand, legit in BRAND_DOMAINS.items():
            for ld in legit:
                if ld in netloc and d1 != ld and d1 not in legit:
                    return [Signal("URL_SUBDOMAIN_ABUSE", 0.85,
                                   f"{netloc} → {d1}", "links")]
    return []


def h_image_only(links, images, body_text: str) -> List[Signal]:
    """Email rendered as images with no readable text — evades keyword scanners."""
    meaningful = re.sub(r'\s+', ' ', body_text).strip()
    if images and len(meaningful) < 60:
        return [Signal("IMAGE_ONLY_BODY", 0.45,
                       f"{len(images)} image(s), <60 chars text", "structure")]
    return []


# ── Scanner ───────────────────────────────────────────────────────────────────

def scan_message(msg) -> dict:
    """Run all heuristics. Returns result dict with verdict, score, signals."""
    body_text, html_body, links, images = extract_body(msg)
    subject = decode_subject(msg)

    signals: List[Signal] = []
    signals += h_display_name_mismatch(msg)
    signals += h_reply_to_mismatch(msg)
    signals += h_domain_lookalike(msg)
    signals += h_free_email_financial(msg, body_text)
    signals += h_auth_failures(msg)
    signals += h_homograph_domain(msg)
    signals += h_urgency_language(subject, body_text)
    signals += h_threat_language(subject, body_text)
    signals += h_credential_harvest(body_text)
    signals += h_financial_lure(subject, body_text)
    signals += h_bec_patterns(subject, body_text)
    signals += h_authority_impersonation(subject, body_text, msg)
    signals += h_generic_greeting(body_text)
    signals += h_pii_request(body_text)
    signals += h_package_scam(subject, body_text, msg)
    signals += h_tech_support_scam(subject, body_text)
    signals += h_link_text_mismatch(links)
    signals += h_url_shortener(links, body_text)
    signals += h_ip_in_url(links)
    signals += h_url_subdomain_abuse(links)
    signals += h_image_only(links, images, body_text)

    v, score = verdict(signals)
    return {
        "verdict": v,
        "score":   round(score, 2),
        "signals": [
            {"name": s.name, "weight": s.weight,
             "detail": s.detail, "category": s.category}
            for s in sorted(signals, key=lambda x: -x.weight)
        ],
        "subject": subject,
        "from":    msg.get("From", ""),
    }


# ── Output ────────────────────────────────────────────────────────────────────

_VERDICT_LABEL = {
    VERDICT_LIKELY_SCAM: "LIKELY SCAM",
    VERDICT_SUSPICIOUS:  "SUSPICIOUS",
    VERDICT_CLEAN:       "CLEAN",
}

def print_result(result: dict):
    v = result["verdict"]
    label = _VERDICT_LABEL.get(v, v.upper())
    prefix = {"likely_scam": "!!",  "suspicious": " ?", "clean": " ✓"}.get(v, "  ")
    print(f"{prefix}  {label:<14}  score: {result['score']:.2f}")
    print(f"    From:    {result['from']}")
    print(f"    Subject: {result['subject']}")
    if result["signals"]:
        print()
        for s in result["signals"]:
            bar   = "█" * round(s["weight"] * 10)
            print(f"    [{s['category']:9}] {s['name']:<30} {bar:<10}  {s['weight']:.2f}")
            print(f"                 {s['detail'][:80]}")


# ── Config & IMAP ─────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"Config not found: {CONFIG_PATH}")


def fetch_message(uid, folder, acct):
    conn = imaplib.IMAP4_SSL(acct["imap_host"], acct["imap_port"])
    conn.login(acct["email"], acct["password"])
    quoted = '"' + folder.replace('"', '\\"') + '"' if ' ' in folder else folder
    conn.select(quoted)
    _, data = conn.fetch(str(uid).encode(), "(RFC822)")
    conn.logout()
    if not data or not isinstance(data[0], tuple):
        sys.exit(f"Could not fetch UID {uid} from {folder}")
    return email.message_from_bytes(data[0][1])


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scan an email for phishing/scam indicators"
    )
    parser.add_argument("uid",       nargs="?",     help="IMAP UID to scan")
    parser.add_argument("--folder",  default="INBOX")
    parser.add_argument("--account", default="jehan")
    parser.add_argument("--json",    action="store_true", help="Output JSON")
    parser.add_argument("--quiet",   action="store_true",
                        help="Suppress output for clean messages (useful in pipelines)")
    parser.add_argument("--stdin",   action="store_true",
                        help="Read RFC822 message from stdin")
    args = parser.parse_args()

    if args.stdin:
        msg    = email.message_from_bytes(sys.stdin.buffer.read())
        result = scan_message(msg)
    elif args.uid:
        config = load_config()
        acct   = config["accounts"][args.account]
        msg    = fetch_message(args.uid, args.folder, acct)
        result = scan_message(msg)
    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    elif not (args.quiet and result["verdict"] == VERDICT_CLEAN):
        print_result(result)

    # Exit code reflects verdict for pipeline use
    sys.exit({"clean": 0, "suspicious": 1, "likely_scam": 2}.get(result["verdict"], 0))


if __name__ == "__main__":
    main()
