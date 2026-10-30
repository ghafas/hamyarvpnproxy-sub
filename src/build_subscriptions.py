#!/usr/bin/env python3
"""Build Base64 Telegram proxy subscriptions from the public SOSkeyNET preview."""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CHANNEL = "SOSkeyNET"
BASE_URL = f"https://t.me/s/{CHANNEL}"
OUTPUT_DIR = Path("public")
MAX_PAGES = 100
TIMEOUT = 25

SCHEMES = (
    "vless", "vmess", "trojan", "ss", "ssr",
    "hysteria", "hysteria2", "tuic", "wireguard", "socks", "http",
)
URI_RE = re.compile(
    r"(?i)(?:" + "|".join(map(re.escape, SCHEMES)) + r")://\S+"
)


def clean_uri(value: str) -> str:
    return value.rstrip(".,;!)]}،؛")


def fetch_page(session: requests.Session, before: int | None = None) -> tuple[str, list[int]]:
    params = {"before": before} if before else None
    response = session.get(BASE_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    chunks: list[str] = []
    message_ids: list[int] = []
    for node in soup.select(".tgme_widget_message"):
        post = node.get("data-post", "")
        match = re.search(r"/(\d+)$", post)
        if match:
            message_ids.append(int(match.group(1)))
        text_node = node.select_one(".tgme_widget_message_text")
        if text_node:
            chunks.append(text_node.get_text(" ", strip=True))
        for anchor in node.select("a[href]"):
            chunks.append(anchor.get("href", ""))

    # Some preview responses may not contain message nodes; fall back to page text.
    if not chunks:
        chunks.append(soup.get_text(" ", strip=True))
    return "\n".join(chunks), message_ids


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; soskeynet-sub/1.0; +https://github.com/ghafas/soskeynet-sub)"
    })

    found: set[str] = set()
    before = None
    pages = 0
    for _ in range(MAX_PAGES):
        try:
            page_text, ids = fetch_page(session, before)
        except requests.RequestException as exc:
            print(f"Fetch stopped after {pages} pages: {exc}")
            break

        pages += 1
        for match in URI_RE.findall(page_text):
            uri = clean_uri(match)
            if uri:
                found.add(uri)

        if not ids:
            break
        oldest = min(ids)
        if before is not None and oldest >= before:
            break
        before = oldest
        time.sleep(0.35)

    ordered = sorted(found)
    raw = ("\n".join(ordered) + ("\n" if ordered else "")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    (OUTPUT_DIR / "all.txt").write_text(encoded + ("\n" if encoded else ""), encoding="ascii")

    # No entry is marked tested until a real proxy tunnel and data-transfer test exists.
    (OUTPUT_DIR / "tested.txt").write_text("", encoding="ascii")
    (OUTPUT_DIR / "index.html").write_text(
        """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>SOSkeyNET subscriptions</title>
<style>body{font:16px system-ui;max-width:680px;margin:3rem auto;padding:0 1rem;line-height:1.6}a{display:block;margin:1rem 0}</style>
<h1>SOSkeyNET subscriptions</h1>
<p>Automatically collected from the public Telegram channel.</p>
<a href="all.txt">All collected configurations (Base64)</a>
<a href="tested.txt">Real-transfer-tested configurations (Base64)</a>
<p>Tested list remains empty until a real proxy tunnel and download test is implemented.</p>
<p><a href="https://t.me/SOSkeyNET">Source: @SOSkeyNET</a></p>
</html>""",
        encoding="utf-8",
    )
    print(f"Scanned {pages} Telegram preview pages; found {len(ordered)} unique proxy URIs.")
    print("Wrote public/all.txt and an intentionally empty public/tested.txt.")


if __name__ == "__main__":
    main()
