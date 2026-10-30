#!/usr/bin/env python3
"""Build protocol-separated Base64 subscriptions and a Mihomo/Clash YAML config."""
from __future__ import annotations

import base64
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import requests
import yaml
from bs4 import BeautifulSoup

CHANNEL = "SOSkeyNET"
BASE_URL = f"https://t.me/s/{CHANNEL}"
OUTPUT_DIR = Path("public")
MAX_PAGES = 100
MAX_PER_PROTOCOL = 100
TIMEOUT = 25
SCHEMES = (
    "vless", "vmess", "trojan", "ss", "ssr",
    "hysteria", "hysteria2", "tuic", "wireguard", "socks",
)
CLASH_SCHEMES = {"vless", "vmess", "trojan", "ss", "hysteria", "hysteria2", "tuic", "socks"}
# Mihomo-supported Shadowsocks methods; never silently rename a cipher.
SUPPORTED_SS_CIPHERS = set("""aes-128-ctr aes-192-ctr aes-256-ctr aes-128-cfb aes-192-cfb aes-256-cfb aes-128-gcm aes-192-gcm aes-256-gcm aes-128-ccm aes-192-ccm aes-256-ccm aes-128-gcm-siv aes-256-gcm-siv chacha20-ietf chacha20 xchacha20 chacha20-ietf-poly1305 xchacha20-ietf-poly1305 chacha8-ietf-poly1305 xchacha8-ietf-poly1305 2022-blake3-aes-128-gcm 2022-blake3-aes-256-gcm 2022-blake3-chacha20-poly1305 lea-128-gcm lea-192-gcm lea-256-gcm rabbit128-poly1305 aegis-128l aegis-256 aez-384 deoxys-ii-256-128 rc4-md5 none""".split())
URI_RE = re.compile(r"(?i)(?:" + "|".join(map(re.escape, SCHEMES)) + r")://\S+")
TRAILING = ".,;!)]}،؛" + chr(34) + chr(39)


def clean_uri(value: str) -> str:
    return value.rstrip(TRAILING)


def protocol_of(uri: str) -> str | None:
    scheme = uri.split("://", 1)[0].lower()
    return scheme if scheme in SCHEMES else None


def plausible_uri(uri: str) -> bool:
    if len(uri) < 12 or any(ch.isspace() for ch in uri):
        return False
    scheme, payload = uri.split("://", 1)
    if not payload or payload.startswith(("/", "?", "#")):
        return False
    if scheme.lower() == "vmess":
        try:
            decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
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


def decode_b64(value: str) -> str:
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")


def node_name(uri: str, host: str) -> str:
    fragment = urlsplit(uri).fragment
    name = unquote(fragment).strip() if fragment else host
    return (name or host or "proxy")[:80]


def query_options(parts):
    return {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}


def add_transport(node: dict, q: dict, default_network: str = "tcp") -> None:
    network = q.get("type", default_network).lower()
    if network in {"ws", "grpc", "http", "h2"}:
        node["network"] = network
    if network == "ws":
        opts = {"path": q.get("path", "/")}
        if q.get("host"):
            opts["headers"] = {"Host": q["host"]}
        node["ws-opts"] = opts
    elif network == "grpc":
        node["grpc-opts"] = {"grpc-service-name": q.get("serviceName", q.get("service-name", ""))}


def parse_ss(uri: str) -> dict | None:
    parts = urlsplit(uri)
    payload = parts.netloc
    if "@" not in payload:
        try:
            decoded = decode_b64(payload.split("#", 1)[0])
            if "@" not in decoded:
                return None
            payload = decoded
        except Exception:
            return None
    userinfo, server = payload.rsplit("@", 1)
    if ":" not in server:
        return None
    host, port_text = server.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError:
        return None
    if ":" not in userinfo:
        try:
            userinfo = decode_b64(userinfo)
        except Exception:
            return None
    userinfo = unquote(userinfo)
    method, password = userinfo.split(":", 1)
    method = method.strip().lower()
    password = password.strip()
    # Never rename a cipher to a different algorithm. Unsupported methods are
    # excluded so one bad SS entry cannot invalidate the entire Clash profile.
    if method not in SUPPORTED_SS_CIPHERS or not password:
        return None
    # SIP002/SS2022 keys must be valid Base64. Some feeds use Base64URL
    # (- and _) instead of standard Base64 (+ and /); normalize that alphabet
    # and padding, then verify the exact key size required by the cipher.
    ss2022_key_sizes = {
        "2022-blake3-aes-128-gcm": 16,
        "2022-blake3-aes-256-gcm": 32,
        "2022-blake3-chacha20-poly1305": 32,
    }
    if method in ss2022_key_sizes:
        try:
            key_text = password.replace("-", "+").replace("_", "/")
            key_bytes = base64.b64decode(
                key_text + "=" * (-len(key_text) % 4), validate=True
            )
        except (ValueError, base64.binascii.Error):
            return None
        if len(key_bytes) != ss2022_key_sizes[method]:
            return None
        password = base64.b64encode(key_bytes).decode("ascii")
    if not host.strip("[]") or not (1 <= port <= 65535):
        return None
    q = query_options(parts)
    # Plugin URI syntaxes differ between clients. Skip plugin nodes until they
    # can be translated completely rather than emit a broken partial config.
    if q.get("plugin"):
        return None
    return {"name": node_name(uri, host), "type": "ss", "server": host.strip("[]"),
            "port": port, "cipher": method, "password": password, "udp": True}


def uri_to_clash(uri: str) -> dict | None:
    scheme = protocol_of(uri)
    if scheme not in CLASH_SCHEMES:
        return None
    if scheme == "vmess":
        try:
            data = json.loads(decode_b64(uri.split("://", 1)[1].split("#", 1)[0]))
            host, port = data.get("add"), int(data.get("port"))
            if not host:
                return None
            node = {"name": node_name(uri, host), "type": "vmess", "server": host,
                    "port": port, "uuid": data.get("id", ""), "alterId": int(data.get("aid", 0)),
                    "cipher": data.get("scy") or "auto", "tls": str(data.get("tls", "")).lower() in {"tls", "true"}}
            network = data.get("net", "tcp")
            add_transport(node, {"type": network, "path": data.get("path", ""),
                                 "host": data.get("host", ""), "serviceName": data.get("path", "")})
            if data.get("sni"):
                node["servername"] = data["sni"]
            if data.get("fp"):
                node["client-fingerprint"] = data["fp"]
            return node
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None
    if scheme == "ss":
        return parse_ss(uri)

    parts = urlsplit(uri)
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return None
    if not host or not port:
        return None
    q = query_options(parts)
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    name = node_name(uri, host)

    if scheme == "socks":
        node = {"name": name, "type": "socks5", "server": host, "port": port}
        if user:
            node.update({"username": user, "password": password})
        return node
    if scheme == "vless":
        node = {"name": name, "type": "vless", "server": host, "port": port,
                "uuid": user, "network": q.get("type", "tcp"),
                "tls": q.get("security", "") in {"tls", "reality"}}
        add_transport(node, q)
        if q.get("sni"):
            node["servername"] = q["sni"]
        if q.get("alpn"):
            node["alpn"] = q["alpn"].split(",")
        if q.get("fp"):
            node["client-fingerprint"] = q["fp"]
        if q.get("security") == "reality":
            node["tls"] = True
            node["reality-opts"] = {"public-key": q.get("pbk", ""), "short-id": q.get("sid", "")}
        if q.get("flow"):
            node["flow"] = q["flow"]
        return node
    if scheme == "trojan":
        node = {"name": name, "type": "trojan", "server": host, "port": port, "password": user,
                "sni": q.get("sni", host), "udp": True}
        add_transport(node, q)
        if q.get("fp"):
            node["client-fingerprint"] = q["fp"]
        return node
    if scheme in {"hysteria", "hysteria2"}:
        node = {"name": name, "type": scheme, "server": host, "port": port}
        if scheme == "hysteria2":
            node["password"] = user or password
        else:
            node["auth"] = q.get("auth", user or password)
            if q.get("obfs"):
                node["obfs"] = q["obfs"]
            if q.get("obfsParam"):
                node["obfs-password"] = q["obfsParam"]
        if q.get("sni"):
            node["sni"] = q["sni"]
        if q.get("insecure") == "1":
            node["skip-cert-verify"] = True
        if q.get("obfs"):
            node["obfs"] = q["obfs"]
        if q.get("obfs-password"):
            node["obfs-password"] = q["obfs-password"]
        if q.get("upmbps"):
            node["up"] = int(q["upmbps"])
        if q.get("downmbps"):
            node["down"] = int(q["downmbps"])
        return node
    if scheme == "tuic":
        node = {"name": name, "type": "tuic", "server": host, "port": port,
                "uuid": user, "password": password, "udp": True,
                "congestion-controller": q.get("congestion_control", "bbr"),
                "sni": q.get("sni", host), "skip-cert-verify": q.get("allowInsecure") == "1"}
        if q.get("alpn"):
            node["alpn"] = q["alpn"].split(",")
        return node
    return None


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
        for anchor in node.select("a[href]"):
            href = anchor.get("href", "")
            if re.match(r"(?i)^(?:" + "|".join(SCHEMES) + r")://", href):
                chunks.append(href)
    return "\n".join(chunks), message_ids


def clash_proxy(uri: str, index: int) -> dict | None:
    p = urlsplit(uri); q = parse_qs(p.query); scheme = p.scheme.lower()
    name = unquote(p.fragment) if p.fragment else f'{scheme}-{index}'
    host, port = p.hostname, p.port
    if not host or not port: return None
    def val(k, default=''): return unquote(q.get(k, [default])[0])
    sni = val('sni', val('servername', host)); network = val('type', 'tcp').lower()
    if scheme == 'vmess':
        try:
            raw = p.netloc + p.path; raw += '=' * (-len(raw) % 4)
            d = json.loads(base64.urlsafe_b64decode(raw).decode())
            n = {'name': name, 'type': 'vmess', 'server': d['add'], 'port': int(d['port']), 'uuid': d['id'], 'alterId': int(d.get('aid', 0)), 'cipher': d.get('scy', 'auto'), 'udp': True}
            net = d.get('net', 'tcp')
            if net != 'tcp': n['network'] = net
            if d.get('tls') == 'tls': n.update({'tls': True, 'servername': d.get('sni') or d.get('host') or d['add'], 'client-fingerprint': d.get('fp', 'chrome')})
            if net == 'ws':
                n['ws-opts'] = {'path': d.get('path') or '/'}
                if d.get('host'): n['ws-opts']['headers'] = {'Host': d['host']}
            if net == 'grpc': n['grpc-opts'] = {'grpc-service-name': d.get('path', '')}
            return n
        except (ValueError, KeyError, TypeError, UnicodeError): return None
    if scheme == 'vless':
        n = {'name': name, 'type': 'vless', 'server': host, 'port': port, 'uuid': unquote(p.username or ''), 'udp': True}
        security = val('security', val('tls'))
        if security in ('tls', 'reality'):
            n.update({'tls': True, 'servername': sni, 'client-fingerprint': val('fp', 'chrome')})
            if security == 'reality' or val('pbk'): n['reality-opts'] = {'public-key': val('pbk'), 'short-id': val('sid')}
        if val('flow'): n['flow'] = val('flow')
        if network == 'ws':
            n.update({'network': 'ws', 'ws-opts': {'path': val('path', '/')}})
            if val('host'): n['ws-opts']['headers'] = {'Host': val('host')}
        elif network == 'grpc': n.update({'network': 'grpc', 'grpc-opts': {'grpc-service-name': val('serviceName')}})
        return n
    if scheme == 'trojan':
        n = {'name': name, 'type': 'trojan', 'server': host, 'port': port, 'password': unquote(p.username or ''), 'sni': sni, 'udp': True}
        if val('allowInsecure') in ('1', 'true'): n['skip-cert-verify'] = True
        if network == 'ws':
            n.update({'network': 'ws', 'ws-opts': {'path': val('path', '/')}})
            if val('host'): n['ws-opts']['headers'] = {'Host': val('host')}
        return n
    if scheme == 'ss':
        try:
            user = unquote(p.username or '')
            if not user:
                raw = p.netloc.split('#')[0]; raw += '=' * (-len(raw) % 4)
                decoded = base64.urlsafe_b64decode(raw).decode(); auth, addr = decoded.rsplit('@', 1)
            else: auth, addr = user, f'{host}:{port}'
            if ':' not in auth: auth += ':' + base64.urlsafe_b64decode(auth + '=' * (-len(auth) % 4)).decode().split(':', 1)[1]
            method, password = auth.split(':', 1); sh, sp = addr.rsplit(':', 1)
            return {'name': name, 'type': 'ss', 'server': sh.strip('[]'), 'port': int(sp), 'cipher': method, 'password': password, 'udp': True}
        except (ValueError, UnicodeError, TypeError): return None
    if scheme == 'hysteria2':
        return {'name': name, 'type': 'hysteria2', 'server': host, 'port': port, 'password': unquote(p.username or ''), 'sni': sni, 'skip-cert-verify': val('insecure') == '1', 'udp': True}
    if scheme == 'hysteria':
        return {'name': name, 'type': 'hysteria', 'server': host, 'port': port, 'auth': unquote(p.username or ''), 'sni': sni, 'skip-cert-verify': val('insecure') == '1', 'udp': True}
    if scheme == 'tuic':
        return {'name': name, 'type': 'tuic', 'server': host, 'port': port, 'uuid': unquote(p.username or ''), 'password': unquote(p.password or ''), 'sni': sni, 'congestion-controller': val('congestion_control', 'bbr'), 'udp': True}
    if scheme == 'socks':
        n = {'name': name, 'type': 'socks5', 'server': host, 'port': port, 'udp': True}
        if p.username is not None: n['username'] = unquote(p.username)
        if p.password is not None: n['password'] = unquote(p.password)
        return n
    return None

def make_clash(items: list[str]) -> tuple[str, int]:
    nodes = []; used = set()
    for i, uri in enumerate(items, 1):
        node = clash_proxy(uri, i)
        if not node: continue
        base = node['name']; name = base; j = 2
        while name in used: name = f'{base}-{j}'; j += 1
        node['name'] = name; used.add(name); nodes.append(node)
    names = [n['name'] for n in nodes]
    cfg = {'mixed-port': 7890, 'allow-lan': False, 'mode': 'rule', 'log-level': 'info', 'proxies': nodes,
           'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'proxies': ['AUTO', 'DIRECT']},
                            {'name': 'AUTO', 'type': 'url-test', 'proxies': names, 'url': 'https://www.gstatic.com/generate_204', 'interval': 300, 'tolerance': 100}],
           'rules': ['MATCH,PROXY']}
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), len(nodes)

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; soskeynet-sub/1.2; +https://github.com/ghafas/soskeynet-sub)"})
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

    for protocol in SCHEMES:
        (OUTPUT_DIR / f"{protocol}.txt").write_text(to_base64(found.get(protocol, [])), encoding="ascii")
    combined = [uri for protocol in SCHEMES for uri in found.get(protocol, [])]
    (OUTPUT_DIR / "all.txt").write_text(to_base64(combined), encoding="ascii")
    (OUTPUT_DIR / "tested.txt").write_text("", encoding="ascii")

    clash_nodes = []
    clash_seen = set()
    clash_names = set()
    clash_skipped = defaultdict(int)
    for protocol in SCHEMES:
        for uri in found.get(protocol, []):
            try:
                node = uri_to_clash(uri)
            except Exception as exc:
                print(f"Skipping malformed {protocol} URI: {type(exc).__name__}")
                node = None
            if node is None:
                clash_skipped[protocol] += 1
                continue
            key = (node.get("type"), node.get("server"), node.get("port"), node.get("name"))
            if key in clash_seen:
                continue
            clash_seen.add(key)
            # Clash/Mihomo requires proxy names to be unique across the whole list.
            base_name = str(node.get("name") or node.get("server") or "proxy")
            unique_name = base_name
            suffix = 2
            while unique_name in clash_names:
                unique_name = f"{base_name}-{suffix}"
                suffix += 1
            node["name"] = unique_name
            clash_names.add(unique_name)
            clash_nodes.append(node)

    clash_config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": True,
        "proxies": clash_nodes,
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": ["AUTO"] + [n["name"] for n in clash_nodes]},
            {"name": "AUTO", "type": "url-test", "url": "https://www.gstatic.com/generate_204",
             "interval": 300, "tolerance": 100, "proxies": [n["name"] for n in clash_nodes]},
        ] if clash_nodes else [],
        "rules": ["MATCH,PROXY"] if clash_nodes else [],
    }
    # Validate the final document before publishing. A malformed node or a
    # dangling group reference must never reach users as a broken subscription.
    clash_text = json.dumps(clash_config, ensure_ascii=False, indent=2) + "\n"
    validated = yaml.safe_load(clash_text)
    proxy_names = [item["name"] for item in validated.get("proxies", [])]
    if len(proxy_names) != len(set(proxy_names)):
        raise ValueError("Duplicate proxy names in generated Clash config")
    known_names = set(proxy_names) | {"DIRECT", "REJECT"}
    for group in validated.get("proxy-groups", []):
        missing = set(group.get("proxies", [])) - known_names - {g["name"] for g in validated.get("proxy-groups", [])}
        if missing:
            raise ValueError(f"Proxy group {group.get('name')} references missing nodes: {sorted(missing)}")
    for proxy in validated.get("proxies", []):
        if proxy.get("type") == "ss" and proxy.get("cipher") not in SUPPORTED_SS_CIPHERS:
            raise ValueError(f"Unsupported Shadowsocks cipher emitted: {proxy.get('cipher')}")
    (OUTPUT_DIR / "clash.yaml").write_text(clash_text, encoding="utf-8")

    links = "\n".join(f'<li><a href="{p}.txt">{p.upper()} ({len(found.get(p, []))})</a></li>' for p in SCHEMES)
    (OUTPUT_DIR / "index.html").write_text(
        f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>SOSkeyNET subscriptions</title>
<style>body{{font:16px system-ui;max-width:680px;margin:3rem auto;padding:0 1rem;line-height:1.6}}a{{display:block;margin:.4rem 0}}</style>
<h1>SOSkeyNET subscriptions</h1>
<p>Latest unique configurations, maximum {MAX_PER_PROTOCOL} per protocol.</p>
<a href="all.txt">ALL protocols ({len(combined)})</a>
<a href="clash.yaml">Clash / Mihomo YAML ({len(clash_nodes)} compatible nodes)</a>
<a href="tested.txt">Real-transfer-tested (currently empty)</a>
<ul>{links}</ul>
<p>Source: <a href="https://t.me/SOSkeyNET">@SOSkeyNET</a></p>
</html>""", encoding="utf-8")
    counts = ", ".join(f"{p}={len(found.get(p, []))}" for p in SCHEMES)
    skipped = ", ".join(f"{p}={n}" for p, n in sorted(clash_skipped.items()) if n)
    print(f"Scanned {pages} pages; kept {len(combined)} valid unique URIs (cap {MAX_PER_PROTOCOL}/protocol).")
    print(counts)
    print(f"Clash/Mihomo nodes: {len(clash_nodes)}; skipped unsupported/unparsed: {skipped or 'none'}.")
    print("Wrote protocol Base64 files, all.txt, clash.yaml and empty tested.txt.")


if __name__ == "__main__":
    main()
