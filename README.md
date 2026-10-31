# SOSkeyNET subscriptions

Automatically collect proxy links published in the public [@SOSkeyNET](https://t.me/SOSkeyNET) and [@meliproxyy](https://t.me/meliproxyy) Telegram channels.

## Subscription links

- All configurations (Base64): https://ghafas.github.io/soskeynet-sub/all.txt
- All configurations except Shadowsocks (Base64): https://ghafas.github.io/soskeynet-sub/all-no-ss.txt
- All configurations except Shadowsocks (plain text): https://ghafas.github.io/soskeynet-sub/all-no-ss-plain.txt
- All configurations (plain text, all collected protocols): https://ghafas.github.io/soskeynet-sub/all-plain.txt
- **Clash / Mihomo YAML:** https://ghafas.github.io/soskeynet-sub/clash.yaml
- Real-transfer-tested configurations (Base64): https://ghafas.github.io/soskeynet-sub/tested.txt

Protocol-specific Base64 subscriptions are linked individually on the [GitHub Pages subscriptions page](https://ghafas.github.io/soskeynet-sub/), including VLESS, VMess, Trojan, Shadowsocks, SSR, Hysteria, Hysteria2, TUIC, WireGuard and SOCKS.

The Clash/Mihomo output converts supported VLESS, VMess, Trojan, Shadowsocks, Hysteria, Hysteria2, TUIC and SOCKS links into proxy entries, with PROXY and AUTO groups. WireGuard and SSR are retained in the protocol-specific/raw subscriptions but are not currently converted into Clash nodes.

The workflow runs every 30 minutes and can also be started manually from **Actions**.

**Important:** `tested.txt` is intentionally empty until a real proxy tunnel is established and actual data transfer/download speed is measured. The AUTO group performs client-side URL latency checks; this is not a real download-speed benchmark, and availability may differ by network and location.
