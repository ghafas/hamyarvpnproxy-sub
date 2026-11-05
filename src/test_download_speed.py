#!/usr/bin/env python3
"""Validate real proxy tunnels and rank nodes by repeated HTTPS download throughput."""
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

from build_subscriptions import SCHEMES, decode_b64, protocol_of, uri_to_singbox

PUBLIC = Path("public")
SING_BOX = os.environ.get("SING_BOX_BIN", "sing-box")
PER_PROTOCOL = max(1, int(os.environ.get("TEST_PER_PROTOCOL", "5")))
SAMPLES = max(3, int(os.environ.get("SPEED_SAMPLES", "3")))
DOWNLOAD_BYTES = max(500_000, int(os.environ.get("SPEED_TEST_BYTES", "1500000")))
DOWNLOAD_URL = os.environ.get(
    "SPEED_TEST_URL", f"https://speed.cloudflare.com/__down?bytes={DOWNLOAD_BYTES}"
)
MAX_SECONDS = 15
MIN_BYTES = 256_000
STARTUP_SECONDS = 7


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def select_candidates(items: list[str], limit: int) -> list[str]:
    """Spread a bounded test budget across the full feed, not just its first entries."""
    unique = list(dict.fromkeys(items))
    if len(unique) <= limit:
        return unique
    if limit <= 1:
        return [unique[0]]
    indexes = [round(i * (len(unique) - 1) / (limit - 1)) for i in range(limit)]
    return [unique[i] for i in dict.fromkeys(indexes)]


def load_nodes() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for proto in SCHEMES:
        path = PUBLIC / f"{proto}.txt"
        if not path.exists():
            result[proto] = []
            continue
        raw = path.read_text(encoding="ascii").strip()
        if not raw:
            result[proto] = []
            continue
        try:
            decoded = decode_b64(raw)
        except (ValueError, UnicodeError):
            decoded = ""
        result[proto] = list(dict.fromkeys(
            line.strip() for line in decoded.splitlines() if line.strip()
        ))
    return result


def download_sample(port: int) -> tuple[float, int]:
    proxy = f"http://127.0.0.1:{port}"
    proxies = {"http": proxy, "https": proxy}
    started = time.monotonic()
    received = 0
    with requests.get(
        DOWNLOAD_URL,
        proxies=proxies,
        stream=True,
        timeout=(7, MAX_SECONDS),
        headers={"Cache-Control": "no-cache", "User-Agent": "HamyarVPNProxy-speedtest/1.0"},
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_content(64 * 1024):
            if chunk:
                received += len(chunk)
            elapsed = time.monotonic() - started
            if received >= DOWNLOAD_BYTES or elapsed >= MAX_SECONDS:
                break
    elapsed = max(time.monotonic() - started, 0.001)
    return received * 8 / elapsed / 1_000_000, received


def test_one(uri: str) -> dict | None:
    try:
        outbound = uri_to_singbox(uri)
    except Exception as exc:
        print(f"conversion failed ({type(exc).__name__})", flush=True)
        return None
    if not isinstance(outbound, dict):
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

    with tempfile.TemporaryDirectory(prefix="hamyar-proxy-check-") as temp:
        cfg = Path(temp) / "config.json"
        cfg.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        try:
            check = subprocess.run(
                [SING_BOX, "check", "-c", str(cfg)],
                capture_output=True, text=True, timeout=12,
            )
            if check.returncode:
                return None
            proc = subprocess.Popen(
                [SING_BOX, "run", "-c", str(cfg)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        try:
            deadline = time.monotonic() + STARTUP_SECONDS
            ready = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    return None
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                        ready = True
                        break
                except OSError:
                    time.sleep(0.15)
            if not ready:
                return None

            speeds: list[float] = []
            byte_counts: list[int] = []
            for _ in range(SAMPLES):
                speed, received = download_sample(port)
                if received < MIN_BYTES:
                    return None
                speeds.append(speed)
                byte_counts.append(received)

            median_speed = statistics.median(speeds)
            stability_ratio = min(speeds) / median_speed if median_speed else 0
            # Throughput remains dominant; stability can adjust ranking by at most 15%.
            ranking_score = median_speed * (0.85 + 0.15 * max(0.0, min(1.0, stability_ratio)))
            return {
                "uri": uri,
                "protocol": protocol_of(uri),
                "samples_mbps": [round(v, 3) for v in speeds],
                "download_mbps": round(median_speed, 3),
                "min_mbps": round(min(speeds), 3),
                "max_mbps": round(max(speeds), 3),
                "stability_ratio": round(stability_ratio, 3),
                "ranking_score_mbps": round(ranking_score, 3),
                "bytes": sum(byte_counts),
                "samples": len(speeds),
            }
        except (requests.RequestException, OSError, subprocess.TimeoutExpired):
            return None
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


def write_b64(path: Path, uris: list[str]) -> None:
    raw = ("\n".join(uris) + ("\n" if uris else "")).encode("utf-8")
    path.write_text(
        base64.b64encode(raw).decode("ascii") + ("\n" if raw else ""),
        encoding="ascii",
    )


def write_plain(path: Path, uris: list[str]) -> None:
    path.write_text("\n".join(uris) + ("\n" if uris else ""), encoding="utf-8")


def main() -> None:
    if not shutil.which(SING_BOX):
        raise SystemExit(f"sing-box binary not found: {SING_BOX}")

    nodes = load_nodes()
    passed: list[dict] = []
    tested = 0
    protocol_results: dict[str, list[dict]] = {}

    for proto in SCHEMES:
        candidates = select_candidates(nodes.get(proto, []), PER_PROTOCOL)
        current: list[dict] = []
        for uri in candidates:
            tested += 1
            result = test_one(uri)
            if result:
                passed.append(result)
                current.append(result)
            print(
                f"tested={tested} passed={len(passed)} protocol={proto}",
                flush=True,
            )
        current.sort(key=lambda item: item["ranking_score_mbps"], reverse=True)
        protocol_results[proto] = current
        uris = [item["uri"] for item in current]
        write_b64(PUBLIC / f"tested-{proto}.txt", uris)
        write_plain(PUBLIC / f"tested-{proto}-plain.txt", uris)

    passed.sort(key=lambda item: item["ranking_score_mbps"], reverse=True)
    tested_uris = [item["uri"] for item in passed]
    fast = [item for item in passed if item["download_mbps"] >= 5.0]

    # Candidate list is independent of live speed-test success: take up to 100
    # unique source nodes across all supported feed protocols.
    candidate_uris = list(dict.fromkeys(
        uri for proto in SCHEMES for uri in nodes.get(proto, [])
    ))[:100]

    write_b64(PUBLIC / "tested.txt", tested_uris)
    write_plain(PUBLIC / "tested-plain.txt", tested_uris)
    write_b64(PUBLIC / "fast.txt", [item["uri"] for item in fast])
    # top100 remains the best live-tested nodes; candidates are unverified.
    write_b64(PUBLIC / "top100.txt", tested_uris[:100])
    write_plain(PUBLIC / "top100-plain.txt", tested_uris[:100])
    write_b64(PUBLIC / "top100-candidates.txt", candidate_uris)
    write_plain(PUBLIC / "top100-candidates-plain.txt", candidate_uris)

    # TLS profile: live-tested nodes with TLS/REALITY and no explicit bypass.
    def tls_verified(uri: str) -> bool:
        from urllib.parse import parse_qs, urlsplit
        scheme = protocol_of(uri)
        try:
            if scheme == "vmess":
                data = json.loads(decode_b64(uri.split("://", 1)[1].split("#", 1)[0]))
                return str(data.get("tls", "")).lower() in {"tls", "true"}
            query = parse_qs(urlsplit(uri).query)
            security = (query.get("security") or [""])[-1].lower()
            insecure = (query.get("insecure") or query.get("allowInsecure") or ["0"])[-1].lower()
            return scheme in {"vless", "trojan", "tuic", "hysteria", "hysteria2"} and (
                security in {"tls", "reality"} or scheme in {"trojan", "tuic", "hysteria", "hysteria2"}
            ) and insecure not in {"1", "true", "yes"}
        except (ValueError, TypeError, KeyError, UnicodeError):
            return False

    secure = [item for item in passed if tls_verified(item["uri"])]
    write_b64(PUBLIC / "secure.txt", [item["uri"] for item in secure])
    write_plain(PUBLIC / "secure-plain.txt", [item["uri"] for item in secure])

    report = {
        "tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "Sing-box tunnel with repeated HTTPS download throughput measurements",
        "download_url": DOWNLOAD_URL,
        "samples_per_node": SAMPLES,
        "sampled_per_protocol": PER_PROTOCOL,
        "sampling_strategy": "evenly spaced across the full structurally valid feed",
        "stability_metric": "minimum sample Mbps divided by median sample Mbps",
        "ranking_method": "median throughput multiplied by (0.85 + 0.15 * stability_ratio); raw throughput remains dominant",
        "tested_count": tested,
        "passed_count": len(passed),
        "candidate_count": len(candidate_uris),
        "candidate_definition": "up to 100 unique source nodes across supported protocols; not live speed-tested",
        "fast_threshold_mbps": 5.0,
        "secure_count": len(secure),
        "secure_definition": "live-tested TLS/REALITY with no explicit certificate-verification bypass; not a trust guarantee",
        "results": passed,
        "protocol_counts": {proto: len(items) for proto, items in protocol_results.items()},
    }
    (PUBLIC / "tested.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    index = PUBLIC / "index.html"
    if index.exists():
        html = index.read_text(encoding="utf-8")
        marker = "<!-- REAL_SPEED_TESTS -->"
        section = (
            f'<section id="speed-tests"><h2>Real download-speed tests</h2>'
            f'<p>{len(passed)} passed from {tested} candidates. Each passing node completed '
            f'{SAMPLES} HTTPS download samples; displayed speed is the median and ranking rewards stability (maximum 15% adjustment). '
            f'"Fast" means at least 5 Mbps in this run.</p>'
            f'<a href="tested.txt">Tested · Base64 ({len(passed)})</a>'
            f'<a href="tested-plain.txt">Tested · Plain text ({len(passed)})</a>'
            f'<a href="fast.txt">Fast · 5+ Mbps ({len(fast)})</a>'
            f'<a href="top100.txt">Top 100 by measured speed ({min(100, len(tested_uris))})</a>'
            f'<a href="top100-candidates.txt">Top 100 source candidates · Base64 ({len(candidate_uris)})</a>'
            f'<a href="top100-candidates-plain.txt">Top 100 source candidates · Plain text ({len(candidate_uris)})</a>'
            f'<a href="tested.json">Detailed speed report</a><ul>'
            + "".join(
                f'<li><a href="tested-{p}.txt">{p.upper()} · {len(protocol_results[p])} tested</a></li>'
                for p in SCHEMES if protocol_results[p]
            )
            + "</ul></section>"
        )
        if marker in html:
            html = html.split(marker)[0] + marker + section + html.split(marker, 1)[1]
        elif "</html>" in html:
            html = html.replace("</html>", marker + section + "\n</html>")
        else:
            html += "\n" + marker + section
        index.write_text(html, encoding="utf-8")

    print(
        f"Finished: {len(passed)}/{tested} passed; "
        f"{len(fast)} measured at 5+ Mbps.",
        flush=True,
    )


if __name__ == "__main__":
    main()
