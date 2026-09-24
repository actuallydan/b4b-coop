# Outbound traffic in offline co-op (issue #5)

Goal: an offline co-op session contacts no third-party services. Allowed: game UDP between peers (7777, any IP),
the local Steam client (steam_api IPC, auth tickets), loopback (agent port 127.0.0.1:47112+).

Status 2026-09-23: static research + `native/src/netguard.c` implemented and tested in an isolated Wine prefix
with a harness (not in the game). Live verification pending (plan at the end).

## Inventory

Sources: strings of `Back4Blood.exe` and `EOSSDK-Win64-Shipping.dll` (1.16.3), PE imports, engine logs
`b4bcoop-*.log` from 2026-09-23 (offline sessions). IPs resolved from this machine on 2026-09-23.

| Destination | Resolves to | Component | Purpose | Offline today? | Needed offline |
|---|---|---|---|---|---|
| `api.epicgames.dev` | **104.18.124.108, 104.18.125.108** (Cloudflare) | EOS SDK (own libcurl + OpenSSL) | SDK config polls (`Updating Product SDK Config` every 300–355 s), EOS Connect login with the Steam encrypted app ticket, metrics session, anti-cheat client | **Yes**: the persistent Cloudflare HTTPS | No |
| EOS Stomp websocket (host redacted in log, from SDK config) | ? | EOS SDK messaging | `EOSSDK-LogEOSMessaging: Successfully connected to Stomp` at startup | **Yes** | No |
| `rtcp.on.epicgames.com`, `turn.rtcp.on.epicgames.com`, `stun.l.google.com` | | EOS RTC (webrtc) | voice rooms | no | No |
| `gobi-config.atuin.4vngame.net` | **3.167.69.2/.71/.86/.93** (AWS CloudFront) | exe, TRS WebServices "BuildEnvironmentAPI" (UE HTTP = libcurl 7.55.1) | build/environment config fetch at startup | **Yes**: the 3.167.69.71 connection | No |
| `prd-global-auth-service-v3-ext.atuin.4vngame.net`, `prd-{reg}-sessions-v3…`, `prd-{reg}-packet-relay-v3…`, `prd-{reg}-beacon-v3…`, `playtest-options-service…`, `%s-%s-session-cluster-…` | AWS | exe, TRS Atuin backend | auth, matchmaking, relay, region ping beacons | Sign-in tasks skipped offline; `LogAtuinBeacon: Beacon using 10 threads for resolving` at startup | No |
| `gobi-api.wbagora.com`, `int-api.wbagora.com` | 100.57.199.219, 3.224.162.186 | exe, WB Hydra/Agora (HTTP + `hydra::UE4Websocket` over libwebsockets 3.0) | profile, auth, names, allow-list, leaver penalty, global config, `CrashReportHydra` | no (all `SignInTask_Hydra*` skipped) | No |
| `event.wbinsights.com` `/v1/event?platform=` | AWS | exe, WB analytics | telemetry | no (`Was unable to get an AnalyticsProvider`) | No |
| `bfbxp.www.vivox.com/api2` | 85.236.98.207 | vivoxsdk.dll (delay-loaded; WinHTTP + sockets) | voice chat | no (`Voice interface disabled by config`; Vivox client only created for local TTS) | No |
| `gamelink.muxy.io`, `sandbox.gamelink.muxy.io` | AWS | exe, Muxy GameLink | Twitch extension | only when Twitch is linked | No |
| `taskman-relay.ovr.4vngame.net` | 13.57.172.213 | exe, TRS TaskMan | crash reporting (`Skipping CrashReportClient in favor of TaskManClient`), dev relay | not seen; on crash | No |
| CrashReportClient.exe | | separate process | UE crash reporter | not shipped, so a no-op | No |
| `datarouter/api/v1/public/data`, Micropatch/PakDownloader CDN | | exe | Epic analytics ET, hot patches | not seen (no provider; `No downloaded files available`) | No |
| `http://www.google.com` | | exe | UE HTTP test command string | no | No |
| `legal.wbgames.com`, `policies.warnerbros.com`, typeform, `dashboard.twitch.tv` | | external browser | links opened on click | not in-process | No |
| NVIDIA NGX updater | | nvngx (in-process) | DLSS model updates | Wine: `Disabling updater for WINE`; native Windows: possible | No |
| Steam client | local IPC | steam_api64 → steamclient64 / Proton lsteamclient | auth tickets, encrypted app ticket, achievements | Yes | **Yes** (allowed) |
| Peers, UDP 7777 | any | PacketRelayNetDriver (DTLS) | game traffic | Yes | **Yes** (never filtered) |
| 127.0.0.1:47112+ | loopback | agent command server | | Yes | **Yes** |

HTTP/socket stacks in the process: UE HTTP = libcurl 7.55.1 (exe; also `LogWinHttp` present), libwebsockets 3.0
(exe), WinHTTP (exe import; Vivox; EOS imports it only for proxy config), EOS's internal libcurl. All of them
resolve names through `ws2_32!getaddrinfo`/`gethostbyname` (imported by the exe, EOSSDK and vivoxsdk). No DoH
(curl 7.55 predates it). EOSSDK and vivoxsdk are **delay-load** imports of the exe.

## What was implemented: `native/src/netguard.c`

Inline MinHook hooks on the exported functions, so every module is covered, including the delay-loaded DLLs:

- **Name resolution**: `ws2_32` `getaddrinfo`, `GetAddrInfoW`, `GetAddrInfoExW` (+`ExA` where it exists; Wine
  lacks it), `gethostbyname`, `WSAConnectByNameA/W`, plus `winhttp!WinHttpConnect`. Non-allowlisted names fail
  immediately with `WSAHOST_NOT_FOUND` / `ERROR_WINHTTP_NAME_NOT_RESOLVED`, the same failure as having no
  internet, which offline mode already has to handle. It's also faster than a real outage (no DNS timeout).
- **Raw-IP TCP**: `connect`, `WSAConnect`, and `ConnectEx` (hooked lazily when someone asks `WSAIoctl` for its
  pointer). TCP to a public IP fails with `WSAENETUNREACH` unless the IP came from an allowed lookup, is
  allowlisted, or the caller is a Steam module. **UDP is never blocked** (game traffic to any peer); UDP
  `connect`s are only recorded.
- **EOS at the source**: when `EOSSDK-Win64-Shipping.dll` maps (`LdrRegisterDllNotification`), hook
  `EOS_Platform_Create`. After the real create, call `EOS_Platform_SetNetworkStatus(h, EOS_NS_Disabled)`. The
  game doesn't import that function, so nothing flips it back. This stops the SDK config polls, Connect login
  and Stomp at the SDK level. DNS blocking is the backstop.

Allowed hostnames: IP literals (no DNS; TCP is judged at connect), `localhost`, single-label names, `.local`,
`.lan`, `.home.arpa`, `.internal`, the machine's own hostname, `netguard_allow` entries, runtime allows (`join`
target, `netguard allow <host>`), and names that a Steam module resolves. Each destination is logged once
(`netguard: BLOCK dns api.epicgames.dev via EOSSDK-Win64-Shipping.dll (not-allowlisted)`) and counted.

Calls from a hook into the real function set a per-thread bypass flag. This means a nested call (for example,
Wine's `GetAddrInfoW` calling `getaddrinfo`) isn't judged a second time with `ws2_32` as the caller.

### Timing
`netguard_init()` runs from `DllMain(DLL_PROCESS_ATTACH)` of our `dwmapi.dll`. dwmapi is a **static import** of
`Back4Blood.exe`, so this happens during loader init, before the exe's entry point runs and before any game thread
exists. ws2_32 is already initialized because our DLL imports it. winhttp is a static import too, so it's hooked
immediately. EOSSDK is delay-loaded when the online subsystem starts (about 5 s in, `Gobi Module Startup`). The
DLL-load notification hooks it after mapping and before its first call. The rest of the agent (`init_thread`)
waits for 1000 UObjects, which is too late for this. That's why netguard doesn't live there. `main.c` now
accepts `MH_ERROR_ALREADY_INITIALIZED`.

### Config (`b4bcoop.ini`)
```
netguard=block        # block (default) | log (record "WOULD-BLOCK" but allow) | off (no hooks)
netguard_eos=1        # 0 = leave EOS network status alone (DNS blocking still applies)
netguard_allow=my.ddns.example,*.example.org,203.0.113.7   # repeatable
```
Retail online play needs `netguard=off`.

### Agent command
`b4b.py netguard` lists the mode, EOS state, allowlist, and every blocked/allowed destination with count,
calling module, first-seen time and reason. `b4b.py netguard allow <host>` allows a host at runtime.

### Harness results (Wine 11.12, fresh prefix, `LoadLibrary` of the built DLL; game not involved)
- Blocked: `api.epicgames.dev`, `gobi-config.atuin.4vngame.net` (getaddrinfo), `event.wbinsights.com`
  (gethostbyname), `bfbxp.www.vivox.com` (GetAddrInfoW), `gamelink.muxy.io` (GetAddrInfoExW), all returning
  11001. `WinHttpConnect("gobi-config…")` returned NULL with error 12007.
- Allowed: `localhost`, `127.0.0.1`, allowlisted `*.example.com`. TCP to its resolved IP passed
  (`resolved-allowed`).
- TCP `104.18.124.108:443` and `ConnectEx` to `1.1.1.1:443` returned 10051. TCP to loopback and 192.168.x went
  through. UDP connect to 8.8.8.8 went through.
- EOS 1.16.3 (the game's DLL): the hook was installed on load. `EOS_Platform_Create` with dummy IDs, then
  `SetNetworkStatus(Disabled)`, returned 0, and `GetNetworkStatus` read 1 after ticking, with no lookups. With
  `netguard_eos=0`, EOS immediately tried `api.epicgames.dev` (blocked) and fell back to status 2 (Offline).

## Risks
- **Startup with EOS disabled and DNS failing** is equivalent to a machine without internet. The offline flow
  should not care (offline sign-in skips EOS and Hydra tasks), but this is not yet verified in-game. Things to
  watch: the online/offline prompt, sign-in time, Fort Hope, host/join, mission follow, `Obtained steam
  authticket`. If something hangs, use `netguard=log` to see what would be blocked, and `netguard_eos=0` to
  isolate EOS.
- **Joining by hostname** works through `join` / `join=` in the ini (`cmd_join` allows the name). Other names
  need `netguard_allow`.
- **Native Windows**: WinHTTP (and NGX, if present) may resolve names internally without going through the hooked
  exports. Blocking still holds because of the `WinHttpConnect` gate. But an *allowlisted* host used through
  WinHTTP may have its TCP connect refused, since its IPs were never seen; allowlist the IP in that case. Steam
  in-process modules are trusted by file name (`steam*`, `lsteamclient`, `gameoverlayrenderer*`, `tier0_s*`,
  `vstdlib_s*`).
- **Hooks installed under loader lock** (MinHook freezes threads). This was tested via `LoadLibrary` in the
  harness, not yet on the real startup path.
- **UDP isn't filtered.** EOS P2P, Vivox media and beacon pings all need addresses from APIs that are now
  blocked, so they can't start. A hardcoded public IP over UDP would still get through (none found).
- **Other processes** are out of scope: no CrashReportClient ships, and the EAC bootstrapper isn't used.

## Live verification plan
1. `launch/install.sh`, default ini (block). Start the host and check `grep netguard: b4bcoop-<pid>.log`. Expect
   `armed … 9 hooks` (10 on Windows), `EOS_Platform_Create hooked`, `SetNetworkStatus(Disabled) = 0`, and
   BLOCK lines for `gobi-config.atuin.4vngame.net`, beacon hosts, etc.
2. Check the full offline flow still works: prompt → Offline → Fort Hope → auto-host → client join → mission
   follow → chapter transition.
3. Check sockets: `ss -tunap | grep -i back4blood` (or by the pid from `pgrep -f Back4Blood.exe`). Expect UDP
   `*:7777`, the 47112 listener and loopback pairs only, with no ESTAB TCP to 104.18.x / 3.167.x.
4. Capture packets for 12+ minutes (at least two EOS poll intervals):
   `sudo tcpdump -ni any 'host 104.18.124.108 or host 104.18.125.108 or net 3.167.69.0/24 or port 53'`.
   Expect no packets from the game (Steam's own DNS may show up; compare against a baseline with the game
   closed). There should be no `Updating Product SDK Config` in the engine log.
5. Run `b4b.py netguard` on the host and the client and paste the tables into this doc.
6. Windows client: `netstat -ano | findstr <pid>` plus the `netguard` output. Confirm Steam auth still works and
   the join succeeds.
