#!/usr/bin/env python3
"""Build compact, protocol-separated Base64 subscriptions from @SOSkeyNET."""
from __future__ import annotations

import base64
import re
import time
from collections import defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CHANNEL = "SOSkeyNET"
BASE_URL = f"https://t.me/s/{CHANNEL}"
OUTPUT_DIR = Path("public")
MAX_PAGES = 100
MAX_PER_PROTOCOL = 50
TIMEOUT = 25

# Generic http(s) links are deliberately excluded: they caused ordinary web
# links to be mistaken for proxy configurations and inflated the old list.
SCHEMES = (
    "vless", "vmess", "trojan", "ss", "ssr",
    "hysteria", "hysteria2", "tuic", "wireguard", "socks",
)
URI_RE = re.compile(
    r"(?i)(?:" + "|".join(map(re.escape, SCHEMES)) + r")://[^\s<>\\"']+"
)
TRAILING = ".,;!)]}،؛\"'"


def clean_uri(value: str) -> str:
    return value.rstrip(TRAILING)


def protocol_of(uri: str) -> str | None:
    scheme = uri.split("://", 1)[0].lower()
    return scheme if scheme in SCHEMES else None


def plausible_uri(uri: str) -> bool:
    """Reject obvious text fragments and malformed links without probing them."""
    if len(uri) < 12 or any(ch.isspace() for ch in uri):
        return False
    scheme, payload = uri.split("://", 1)
    if not payload or payload.startswith(("/", "?", "#")):
        return False
    if scheme.lower() == "vmess":
        # VMess links carry a base64-encoded JSON document.
        try:
            padded = payload + "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            return decoded.lstrip().startswith("{") and '"add"' in decoded and '"port"' in decoded
        except (ValueError, UnicodeError):
            return False
    if scheme.lower() in {"vless", "trojan", "hysteria", "hysteria2", "tuic"}:
        authority = payload.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        return "@" in authority and ":" in authority.rsplit("@", 1)[-1]
    if scheme.lower() in {"ss", "ssr"}:
        return len(payload) >= 16
    if scheme.lower() == "wireguard":
        return len(payload) >= 12
    if scheme.lower() == "socks":
        return "@" in payload and ":" in payload.rsplit("@", 1)[-1]
    return False


def to_base64(items: list[str]) -> str:
    raw = ("\n".join(items) + ("\n" if items else "")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return encoded + ("\n" if encoded else "")


def fetch_page(session: requests.Session, before: int | None = None) -> tuple[str, list[int]]:
    params = {"before": before} if before else None
    response = session.get(BASE_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    chunks: list[str] = []
    message_ids: list[int] = []
    for node in soup.select(".tgme_widget_message"):
        match = re.search(r"/(\d+)$", node.get("data-post", ""))
        if match:
            message_ids.append(int(match.group(1)))
        text_node = node.select_one(".tgme_widget_message_text")
        if text_node:
            chunks.append(text_node.get_text(" ", strip=True))
        # Telegram sometimes stores a proxy URI as an anchor href.
        for anchor in node.select("a[href]"):
            href = anchor.get("href", "")
            if re.match(r"(?i)^(?:" + "|".join(SCHEMES) + r")://", href):
                chunks.append(href)

    return "\n".join(chunks), message_ids


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; soskeynet-sub/1.1; +https://github.com/ghafas/soskeynet-sub)"
    })

    # Keep first-seen entries: Telegram preview pages are traversed newest first.
    found: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    before = None
    pages = 0
    for _ in range(MAX_PAGES):
        try:
            page_text, ids = fetch_page(session, before)
        except requests.RequestException as exc:
            print(f"Fetch stopped after {pages} pages: {exc}")
            break

        pages += 1
        for raw_uri in URI_RE.findall(page_text):
            uri = clean_uri(raw_uri)
            protocol = protocol_of(uri)
            if not protocol or uri in seen[protocol] or not plausible_uri(uri):
                continue
            if len(found[protocol]) >= MAX_PER_PROTOCOL:
                continue
            seen[protocol].add(uri)
            found[protocol].append(uri)

        if not ids:
            break
        oldest = min(ids)
        if before is not None and oldest >= before:
            break
        before = oldest
        time.sleep(0.35)

    # One Base64 subscription per protocol, plus a compact combined list.
    for protocol in SCHEMES:
        (OUTPUT_DIR / f"{protocol}.txt").write_text(
            to_base64(found.get(protocol, [])), encoding="ascii"
        )

    combined = [uri for protocol in SCHEMES for uri in found.get(protocol, [])]
    (OUTPUT_DIR / "all.txt").write_text(to_base64(combined), encoding="ascii")

    # No proxy is labelled tested until a real tunnel + data-transfer test exists.
    (OUTPUT_DIR / "tested.txt").write_text("", encoding="ascii")

    links = "\n".join(
        f'<li><a href="{p}.txt">{p.upper()} ({len(found.get(p, []))})</a></li>'
        for p in SCHEMES
    )
    (OUTPUT_DIR / "index.html").write_text(
        f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>SOSkeyNET subscriptions</title>
<style>body{{font:16px system-ui;max-width:680px;margin:3rem auto;padding:0 1rem;line-height:1.6}}a{{display:block;margin:.4rem 0}}</style>
<h1>SOSkeyNET subscriptions</h1>
<p>Latest unique configurations, separated by protocol. Maximum {MAX_PER_PROTOCOL} per protocol.</p>
<a href="all.txt">ALL protocols ({len(combined)})</a>
<a href="tested.txt">Real-transfer-tested (currently empty)</a>
<ul>{links}</ul>
<p>Source: <a href="https://t.me/SOSkeyNET">@SOSkeyNET</a></p>
</html>""",
        encoding="utf-8",
    )
    counts = ", ".join(f"{p}={len(found.get(p, []))}" for p in SCHEMES)
    print(f"Scanned {pages} pages; kept {len(combined)} valid unique URIs (cap {MAX_PER_PROTOCOL}/protocol).")
    print(counts)
    print("Wrote protocol-specific Base64 files, public/all.txt and empty public/tested.txt.")


if __name__ == "__main__":
    main()
