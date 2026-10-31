# HamyarVPNProxy subscriptions

Automatically collect and publish proxy configurations from the public Telegram channels [@SOSkeyNET](https://t.me/SOSkeyNET) and [@meliproxyy](https://t.me/meliproxyy).

## Subscription links

Base URL: https://ghafas.github.io/hamyarvpnproxy-sub/

### Combined subscriptions
- All supported protocols (Base64): https://ghafas.github.io/hamyarvpnproxy-sub/all.txt
- All supported protocols (plain text): https://ghafas.github.io/hamyarvpnproxy-sub/all-plain.txt
- All protocols except Shadowsocks (Base64): https://ghafas.github.io/hamyarvpnproxy-sub/all-no-ss.txt
- All protocols except Shadowsocks (plain text): https://ghafas.github.io/hamyarvpnproxy-sub/all-no-ss-plain.txt
- Clash / Mihomo configuration: https://ghafas.github.io/hamyarvpnproxy-sub/clash.yaml
- Real-transfer-tested configurations: https://ghafas.github.io/hamyarvpnproxy-sub/tested.txt

### Protocol-specific subscriptions (Base64)
- [VLESS](https://ghafas.github.io/hamyarvpnproxy-sub/vless.txt)
- [VMess](https://ghafas.github.io/hamyarvpnproxy-sub/vmess.txt)
- [Trojan](https://ghafas.github.io/hamyarvpnproxy-sub/trojan.txt)
- [Shadowsocks](https://ghafas.github.io/hamyarvpnproxy-sub/ss.txt)
- [ShadowsocksR](https://ghafas.github.io/hamyarvpnproxy-sub/ssr.txt)
- [Hysteria](https://ghafas.github.io/hamyarvpnproxy-sub/hysteria.txt)
- [Hysteria2](https://ghafas.github.io/hamyarvpnproxy-sub/hysteria2.txt)
- [TUIC](https://ghafas.github.io/hamyarvpnproxy-sub/tuic.txt)
- [WireGuard](https://ghafas.github.io/hamyarvpnproxy-sub/wireguard.txt)
- [SOCKS](https://ghafas.github.io/hamyarvpnproxy-sub/socks.txt)

## Update and compatibility

GitHub Actions refreshes the subscriptions every 30 minutes and can also be started manually from the Actions tab. The collector keeps up to 100 unique configurations per protocol.

The combined Xray-friendly feeds omit legacy Shadowsocks CFB entries that can prevent some Xray clients from loading the subscription. The dedicated `ss.txt` feed remains available separately. The no-SS feeds remove the `ss://` protocol while retaining other protocols, including SSR.

The Clash/Mihomo YAML is generated from supported protocols. Some protocol URI variants or unsupported Shadowsocks methods may be skipped if they cannot be translated safely.

**Important:** `tested.txt` remains empty until a real proxy tunnel is established and actual data transfer is measured. The Clash/Mihomo AUTO group uses URL latency checks; that is not a real download-speed benchmark. Proxy availability and performance vary by network and location.
