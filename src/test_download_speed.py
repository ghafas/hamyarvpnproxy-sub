#!/usr/bin/env python3
"""Real tunnel and download-throughput checks for a bounded sample of collected nodes."""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import requests
import yaml

from build_subscriptions import SCHEMES, decode_b64, protocol_of, uri_to_singbox

PUBLIC = Path("public")
SING_BOX = os.environ.get("SING_BOX_BIN", "sing-box")
PER_PROTOCOL = int(os.environ.get("TEST_PER_PROTOCOL", "8"))
DOWNLOAD_URL = os.environ.get(
    "SPEED_TEST_URL", "https://speed.cloudflare.com/__down?bytes=2000000"
)
MAX_SECONDS = 12
MIN_BYTES = 256_000


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def load_nodes() -> dict[str, list[str]]:
    result = {}
    for proto in SCHEMES:
        path = PUBLIC / f"{proto}.txt"
        if not path.exists():
            result[proto] = []
            continue
        raw = path.read_text(encoding="ascii").strip()
        if not raw:
            result[proto] = []
            continue
        decoded = decode_b64(raw)
        result[proto] = [line.strip() for line in decoded.splitlines() if line.strip()]
    return result


def test_one(uri: str) -> dict | None:
    outbound = uri_to_singbox(uri)
    if not outbound:
        return None
    tag = "candidate"
    outbound["tag"] = tag
    port = free_port()
    config = {
        "log": {"level": "error"},
        "inbounds": [{
            "type": "mixed", "tag": "test-in", "listen": "127.0.0.1",
            "listen_port": port,
        }],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        "route": {"final": tag, "auto_detect_interface": True},
    }
    with tempfile.TemporaryDirectory(prefix="proxy-check-") as temp:
        cfg = Path(temp) / "config.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")
        check = subprocess.run(
            [SING_BOX, "check", "-c", str(cfg)],
            capture_output=True, text=True, timeout=8,
        )
        if check.returncode:
            return None
        proc = subprocess.Popen(
            [SING_BOX, "run", "-c", str(cfg)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    return None
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                return None

            start = time.monotonic()
            received = 0
            proxies = {"http": f"http://127.0.0.1:{port}",
                       "https": f"http://127.0.0.1:{port}"}
            with requests.get(
                DOWNLOAD_URL, proxies=proxies, stream=True, timeout=(5, MAX_SECONDS)
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_content(64 * 1024):
                    if chunk:
                        received += len(chunk)
                    if received >= 2_000_000 or time.monotonic() - start >= MAX_SECONDS:
                        break
            elapsed = max(time.monotonic() - start, .001)
            if received < MIN_BYTES:
                return None
            speed_mbps = received * 8 / elapsed / 1_000_000
            return {
                "uri": uri, "protocol": protocol_of(uri),
                "bytes": received, "seconds": round(elapsed, 3),
                "download_mbps": round(speed_mbps, 3),
            }
        except (requests.RequestException, OSError, subprocess.TimeoutExpired):
            return None
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def write_base64(path: Path, uris: list[str]) -> None:
    raw = ("\n".join(uris) + ("\n" if uris else "")).encode()
    path.write_text(base64.b64encode(raw).decode() + ("\n" if raw else ""), encoding="ascii")


def main() -> None:
    if not shutil.which(SING_BOX):
        raise SystemExit(f"sing-box binary not found: {SING_BOX}")
    nodes = load_nodes()
    passed = []
    tested = 0
    for proto in SCHEMES:
        for uri in nodes[proto][:PER_PROTOCOL]:
            tested += 1
            result = test_one(uri)
            if result:
                passed.append(result)
            print(f"tested={tested} passed={len(passed)} protocol={proto}", flush=True)

    passed.sort(key=lambda item: item["download_mbps"], reverse=True)
    tested_uris = [item["uri"] for item in passed]
    write_base64(PUBLIC / "tested.txt", tested_uris)
    fast = [item for item in passed if item["download_mbps"] >= 5.0]
    write_base64(PUBLIC / "fast.txt", [item["uri"] for item in fast])
    write_base64(PUBLIC / "top100.txt", [item["uri"] for item in passed[:100]])

    report = {
        "tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "Sing-box tunnel + HTTPS download throughput",
        "download_url": DOWNLOAD_URL,
        "sampled_per_protocol": PER_PROTOCOL,
        "tested_count": tested,
        "passed_count": len(passed),
        "fast_threshold_mbps": 5.0,
        "results": passed,
    }
    (PUBLIC / "tested.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    index = PUBLIC / "index.html"
    if index.exists():
        html = index.read_text(encoding="utf-8")
        links = (
            f'<h2>Real download-speed tests</h2>'
            f'<p>{len(passed)} passed from {tested} tested candidates. '
            f'Fast means at least 5 Mbps in this CI test run.</p>'
            f'<a href="tested.txt">Tested ({len(passed)})</a>'
            f'<a href="fast.txt">Fast, 5+ Mbps ({len(fast)})</a>'
            f'<a href="top100.txt">Top 100 by measured download speed</a>'
            f'<a href="tested.json">Test report with measured Mbps</a>'
        )
        html = html.replace("</html>", links + "\n</html>")
        index.write_text(html, encoding="utf-8")

    print(f"Finished: {len(passed)}/{tested} passed; {len(fast)} at 5+ Mbps.")


if __name__ == "__main__":
    main()
