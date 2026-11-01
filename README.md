# HamyarVPNProxy subscriptions

Automatically collect and publish proxy configurations from the public Telegram channels [@SOSkeyNET](https://t.me/SOSkeyNET) and [@meliproxyy](https://t.me/meliproxyy), [@persianvpnhub](https://t.me/persianvpnhub), [@oneclickvpnkeys](https://t.me/oneclickvpnkeys), and [@VPNBaz](https://t.me/VPNBaz).

## Subscription links

Base URL: https://ghafas.github.io/hamyarvpnproxy-sub/

### Combined subscriptions
- All supported protocols (Base64): https://ghafas.github.io/hamyarvpnproxy-sub/all.txt
- All supported protocols (plain text): https://ghafas.github.io/hamyarvpnproxy-sub/all-plain.txt
- All protocols except Shadowsocks (Base64): https://ghafas.github.io/hamyarvpnproxy-sub/all-no-ss.txt
- All protocols except Shadowsocks (plain text): https://ghafas.github.io/hamyarvpnproxy-sub/all-no-ss-plain.txt
- Clash / Mihomo configuration: https://ghafas.github.io/hamyarvpnproxy-sub/clash.yaml
- **Universal Sing-box JSON (recommended for Sing-box-compatible apps, including AsteriskBOX): https://ghafas.github.io/hamyarvpnproxy-sub/singbox.json**
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

## One universal Sing-box link

Use this same URL in Sing-box-compatible apps, including AsteriskBOX:

`https://ghafas.github.io/hamyarvpnproxy-sub/singbox.json`

It is a full Sing-box JSON configuration containing the server `outbounds`, Android TUN inbound, proxy route, and automatic latency-based selector. AsteriskBOX's importer reads the `outbounds` from this JSON and creates its own VPN service and proxy groups; after importing there, choose the desired protocol group (for example `vless`) in **Global selector**, not `Direct`.

Some apps offer different import modes (remote profile/config versus node subscription). Choose the Sing-box JSON/remote-config import mode. The separate legacy app-specific JSON files are no longer the recommended links.

**Automatic selection limitation:** the built-in `urltest` measures reachability/latency with a small HTTP response, not sustained download throughput. Real download-speed-based selection requires a bounded download test through each candidate tunnel on the user's device and network. A static subscription file cannot measure that accurately.

## How to add another Telegram channel

To add a new public Telegram channel to the collector:

1. Open the [`src/build_subscriptions.py` file](https://github.com/ghafas/hamyarvpnproxy-sub/blob/main/src/build_subscriptions.py) in this repository.
2. Find the `CHANNELS` line near the top of the file. It currently looks like this:

```python
CHANNELS = ("SOSkeyNET", "meliproxyy", "persianvpnhub", "oneclickvpnkeys", "VPNBaz")
```

3. Add the channel's username inside the parentheses, without `@`, and put it in quotation marks. Separate names with commas. For example, to add `@NewVPNChannel`:

```python
CHANNELS = ("SOSkeyNET", "meliproxyy", "persianvpnhub", "NewVPNChannel")
```

4. Save the change with **Commit changes** on GitHub. You can do this directly in the browser; no need to download the project.
5. To run an update immediately, open the repository's **Actions** tab, select the subscription update workflow, and choose **Run workflow** if that option is available. Otherwise, the scheduled workflow checks for updates every 30 minutes.

### What kind of channels work?

- The channel must be public and its posts must be readable on Telegram's web preview.
- The collector looks for supported proxy links in ordinary message text, inline code, multiline code blocks, and direct links.
- Write only the channel username in `CHANNELS`, not the full `https://t.me/...` address.
- Private channels, invitation-only posts, and content that Telegram does not expose publicly cannot be collected by this method.

## Update and compatibility

GitHub Actions refreshes the subscriptions every 30 minutes and can also be started manually from the Actions tab. The collector keeps up to 100 unique configurations per protocol.

The combined Xray-friendly feeds omit legacy Shadowsocks CFB entries that can prevent some Xray clients from loading the subscription. The dedicated `ss.txt` feed remains available separately. The no-SS feeds remove the `ss://` protocol while retaining other protocols, including SSR.

The Clash/Mihomo YAML is generated from supported protocols. Some protocol URI variants or unsupported Shadowsocks methods may be skipped if they cannot be translated safely.

**Important:** `tested.txt` remains empty until a real proxy tunnel is established and actual data transfer is measured. The Clash/Mihomo AUTO group uses URL latency checks; that is not a real download-speed benchmark. Proxy availability and performance vary by network and location.


The collector reads proxy URIs from ordinary message text, inline code, multiline code blocks, and direct Telegram links.
