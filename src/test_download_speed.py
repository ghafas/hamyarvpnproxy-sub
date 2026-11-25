#!/usr/bin/env python3
"""Check real Sing-box tunnel connectivity; publish passing nodes separately."""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from build_subscriptions import SCHEMES, decode_b64, protocol_of, uri_to_singbox

PUBLIC = Path("public")
SING_BOX = os.environ.get("SING_BOX_BIN", "sing-box")
PER_PROTOCOL = max(1, int(os.environ.get("TEST_PER_PROTOCOL", "5")))
CONNECTIVITY_URL = os.environ.get("CONNECTIVITY_URL", "https://www.gstatic.com/generate_204")
CONNECT_TIMEOUT = 12
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


def test_one(uri: str) -> dict | None:
    """Return a result only after HTTPS succeeds through the running proxy tunnel."""
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

            started = time.monotonic()
            response = requests.get(
                CONNECTIVITY_URL,
                proxies={"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"},
                timeout=(7, CONNECT_TIMEOUT),
                headers={"Cache-Control": "no-cache", "User-Agent": "HamyarVPNProxy-connectivity/1.0"},
            )
            elapsed_ms = round((time.monotonic() - started) * 1000)
            if response.status_code not in (200, 204):
                return None
            return {
                "uri": uri,
                "protocol": protocol_of(uri),
                "connectivity": "passed",
                "http_status": response.status_code,
                "latency_ms": elapsed_ms,
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
        current.sort(key=lambda item: item["latency_ms"])
        protocol_results[proto] = current
        uris = [item["uri"] for item in current]
        write_b64(PUBLIC / f"tested-{proto}.txt", uris)
        write_plain(PUBLIC / f"tested-{proto}-plain.txt", uris)
        # Candidate protocol feeds are never overwritten by this live check.

    passed.sort(key=lambda item: item["latency_ms"])
    tested_uris = [item["uri"] for item in passed]
    candidate_uris = list(dict.fromkeys(
        uri for proto in SCHEMES for uri in nodes.get(proto, [])
    ))[:100]

    write_b64(PUBLIC / "tested.txt", tested_uris)
    write_plain(PUBLIC / "tested-plain.txt", tested_uris)
    from build_subscriptions import parse_ss
    xray_safe = []
    for uri in tested_uris:
        if protocol_of(uri) == "ss":
            ss_node = parse_ss(uri)
            if ss_node is None or ss_node.get("cipher") in {"aes-128-cfb", "aes-192-cfb", "aes-256-cfb"}:
                continue
        xray_safe.append(uri)
    no_ss = [uri for uri in xray_safe if protocol_of(uri) != "ss"]
    write_b64(PUBLIC / "tested-xray-safe.txt", xray_safe)
    write_plain(PUBLIC / "tested-xray-safe-plain.txt", xray_safe)
    write_b64(PUBLIC / "tested-no-ss.txt", no_ss)
    write_plain(PUBLIC / "tested-no-ss-plain.txt", no_ss)
    write_b64(PUBLIC / "top100-candidates.txt", candidate_uris)
    write_plain(PUBLIC / "top100-candidates-plain.txt", candidate_uris)

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
        "method": "Sing-box tunnel followed by a normal HTTPS connectivity request",
        "connectivity_url": CONNECTIVITY_URL,
        "success_rule": "HTTP status 200 or 204 received through the proxy tunnel",
        "sampled_per_protocol": PER_PROTOCOL,
        "sampling_strategy": "evenly spaced across each full structurally valid protocol feed",
        "tested_count": tested,
        "passed_count": len(passed),
        "candidate_count": len(candidate_uris),
        "candidate_definition": "up to 100 source candidates shown separately; not used to filter canonical feeds",
        "secure_count": len(secure),
        "secure_definition": "connectivity-tested TLS/REALITY with no explicit certificate-verification bypass; not a trust guarantee",
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
            f'<p>Real connection check: {len(passed)} passed from {tested} sampled candidates. '
            f'Each passing node completed a normal HTTPS request through its own Sing-box tunnel; '
            f'only HTTP 200/204 counts as success. Latency is informational and is not a download-speed measurement. '
            f'Canonical ALL and per-protocol source feeds remain unchanged and include candidates whether or not they pass.</p>'
            f'<a href="all.txt">ALL source candidates · Base64 (unchanged)</a>'
            f'<a href="all-plain.txt">ALL source candidates · Plain text (unchanged)</a>'
            f'<a href="tested-xray-safe.txt">Connection-tested · Xray-safe · Base64 ({len(xray_safe)})</a>'
            f'<a href="tested-xray-safe-plain.txt">Connection-tested · Xray-safe · Plain text ({len(xray_safe)})</a>'
            f'<a href="tested-no-ss.txt">Connection-tested · No Shadowsocks ({len(no_ss)})</a>'
            f'<a href="tested.txt">All connection-passed nodes · Base64 ({len(passed)})</a>'
            f'<a href="tested-plain.txt">All connection-passed nodes · Plain text ({len(passed)})</a>'
            f'<a href="tested.json">Connection-check report</a><ul>'
            + "".join(
                f'<li><a href="tested-{p}.txt">{p.upper()} · {len(protocol_results[p])} passed</a> '
                f'(<a href="tested-{p}-plain.txt">plain</a>)</li>'
                for p in SCHEMES if protocol_results[p]
            )
            + "</ul></section>"
        )
        if marker in html:
            before, after = html.split(marker, 1)
            if "</section>" in after:
                after = after[after.index("</section>") + len("</section>"):]
            html = before + marker + section + after
        elif "</html>" in html:
            html = html.replace("</html>", marker + section + "\n</html>")
        else:
            html += "\n" + marker + section
        index.write_text(html, encoding="utf-8")

    print(f"Finished connectivity check: {len(passed)}/{tested} passed.", flush=True)



if __name__ == "__main__":
    main()
