# Steam P2P (no port forwarding)

Build 14216215. Static analysis of `Back4Blood.exe` / `steam_api64.dll` (Steamv147) plus single-account live runs.
Implementation: `native/src/steamnet.c` (+ small hooks in `cmds.c`, `uelog.c`, `main.c`, `presence.c`).

Status 2026-09-24: **verified between two real Steam accounts** on one machine (join, mission follow, per-player
rewards; "Two-account result" at the end). One account alone can't test it reliably: Steam hands each packet addressed
to "our own" SteamID to either copy, so single-account runs lost 30-70 % of packets ("Local checks").
Since 0.3.0 Steam P2P is **the** join path for players: hosting is on by default, joins arrive through Steam "Join
Game" (presence.c) or `steam:<id64>`, and the game's UDP socket is bound to 127.0.0.1 ("Loopback binding").

## Usage

| Where | What |
|---|---|
| host | nothing to do: hosting and Steam P2P are on by default. UDP joins only from this machine (127.0.0.1) unless `host_ip=1`, then from the network too. `status` shows `steam: id=7656... p2p=on (join me: steam:7656...)`. |
| client | Steam Join Game (presence.c), or `join=steam:<host SteamID64>` in `b4bcoop.ini` (`join=1.2.3.4[:port]` needs `host_ip=1` on both sides, except 127.0.0.1; `join=steam:<id>,1.2.3.4` tries both) |
| agent commands | `join steam:<id64>`, `join <ip>[:port]`, `steamnet` (per-peer P2P session state), `steamnet on\|off` |
| chat | `/join steam:<id64>` (chat.c → `coop_join`) |
| Steam "Join Game" | presence.c advertises `+b4bcoop_join steam:<id64> proto:<n> ver:<x.y.z>` (+ `addr:<ip:port>` with `host_ip=1`) |
| C API (`cmds.h`) | `void coop_join(const char *target)` (`ip[:port]` or `steam:<id64>`), `void coop_host(void)`; any thread (queued to the game thread) |
| ini | `steam_p2p=0` turns Steam P2P off (also `transport=ip`); `host_ip=1` (advanced) re-enables IP hosting/joining |

The host's SteamID64 is on its Steam profile URL, in `status`, and in the agent log.

## How it works

A UDP shim under the retail net driver, which stays untouched (`PacketRelayNetDriver` = IpNetDriver + DTLS):

- Every Steam peer gets a fake IPv4 in `198.18.0.0/15` (RFC 2544 benchmarking range, not routed): `198.18.0.1`,
  `198.18.0.2`, ... in order of first contact, per process.
- `join steam:<id>` = `open 198.18.0.N:7777`. The game's `sendto()` to a fake address is turned into
  `ISteamNetworking::SendP2PPacket(id, ..., channel 27)` (unreliable ≤ 1200 bytes, reliable above, logged).
- `recvfrom()` on the game's socket first returns pending P2P packets (`IsP2PPacketAvailable`/`ReadP2PPacket`,
  channel 27) with the peer's fake address as source, then falls through to the real socket. The game's socket is
  the one bound to the game net driver's listen port (host; port taken from the engine log line
  `GameNetDriver ... listening on port N`, bound ports recorded by a `bind()` hook) or the one that sent to the joined
  fake address (client).
- The source port reported for a peer is the port the game last sent to it (client: 7777), or `20000+N` for a peer
  that contacted us first (host), so UE's stateless handshake and connection lookup see a stable address.
- P2P session requests (`P2PSessionRequest_t`, callback 1202) are accepted while Steam P2P is on and the join policy
  allows the remote SteamID (below); connect failures
  (1203) are logged with the `EP2PSessionError`. The callbacks are registered through presence.c's hook of
  `SteamAPI_RunCallbacks` (same thread and same reason as its own join-request callback).
- `AllowP2PPacketRelay(true)` on every Steam join (SDR relay when NAT punching fails; Steam's default anyway).
- Every P2P datagram carries an 8-byte header: magic `B4C1` + a random per-process tag. Packets without the magic are
  dropped; packets with our own tag came back to us (only possible with one account on two copies) and are dropped.

Because packets are addressed by SteamID, the port in a Steam target doesn't matter (`steam:<id>:7787` = `steam:<id>`).
The follow/rejoin logic in `travel.c` needs no change: it reopens the same fake URL, which keeps mapping to the same
SteamID for the life of the process.

## Join policy (`native/src/joinpolicy.c`)
Hosts accept only their Steam friends by default (`ISteamFriends::HasFriend(id, k_EFriendFlagImmediate)`) and their
own SteamID; `allow_steamids=<id64>,...` always allows, `allow_joins=anyone` turns the check off. Friends list
unavailable → only the allowlist and self (fails closed).
- Steam P2P: checked when a peer first appears (session request or first packet). A refused peer's session is never
  accepted and `CloseP2PSessionWithUser` is called; its packets are read and dropped in `recvfrom` (`steamnet`:
  `refused=N`, peer `policy=REFUSED`), so nothing reaches the game. The id is Steam-authenticated.
- IP: the PreLogin hook (admin.c) checks the SteamID in the login's `FUniqueNetIdRepl` before the game's own PreLogin
  runs and refuses with "This host only accepts their Steam friends. …" (the client shows `Could not join: …` and
  retries every 60 s). Offline mode has no auth ticket check, so this id is the joiner's claim: it keeps strangers
  out, but someone who knows a friend's SteamID and the host's address could impersonate them.
- The host gets a chat line per refused SteamID ("Refused a join from <name> (steam:<id>) … allow_steamids=<id>").
- A refused Steam joiner only sees its connection time out; when Steam reports the session failed it gets a hint in
  chat.

Live (one account, so the joiner is "self"; dev knob `allow_self=0` treats self as a stranger):
- default: IP join allowed (`admin: join policy: steam:7656… allowed (the host's own account)`); release build too.
- `allow_self=0`, fake allowlist: IP join refused before PreLogin (`refused by the join policy: not on the host's
  Steam friends list`; client `PendingConnectionFailure … This host only accepts their Steam friends`), `join
  steam:<id>` refused (`steamnet: refused Steam P2P from 7656… (Hergmgurk)`, `rx=0 refused=7`).
- `allow_self=0;allow_steamids=<own id>`: P2P peer `policy=allowed`, packets delivered. The handshake itself did not
  complete in this one-account setup, exactly as with main's build (baseline run, same result): see "Local checks".

## Why not USteamNetDriver

The engine ships Unreal's Steam net driver, and it would have been the obvious route, but it can't run in this game:

- `UEngine::NetDriverDefinitions` (engine `+0xC18`, `FNetDriverDefinition` = DefName, DriverClassName,
  DriverClassNameFallback). Live retail entry: `GameNetDriver` → `OnlineSubsystemPacketRelay.PacketRelayNetDriver`,
  fallback `OnlineSubsystemUtils.IpNetDriver`. Swapping the entry to `/Script/OnlineSubsystemSteam.SteamNetDriver` is
  easy (the first revision of this branch did it).
- `USteamNetDriver` is stock 4.25 (vtable `0x14526E0F8`; IsAvailable `0x140D26000`, InitBase `0x140D262A0`,
  InitConnect `0x140D26720`, InitListen `0x140D26970`, `bIsPassthrough` at `+0x7D0`). IsAvailable needs
  `IOnlineSubsystem::Get("STEAM")` (`0x140BB07D0`) **and** `ISocketSubsystem::Get("STEAM")` (`0x1429AE6A0`).
- **Live: `ISocketSubsystem::Get("STEAM")` returns null** in every session. The Steam OSS is up (auth tickets,
  achievements, rich presence), but it never registers its socket subsystem: `FSocketSubsystemSteam::Init`'s config
  reads (`P2PConnectionTimeout` etc.) aren't in the binary, and the only reader of `bUseSteamNetworking` /
  `bAllowP2PPacketRelay` (`0x140D08A10`) builds session settings. So the engine would always fall back to IP (the
  first revision confirmed it live: `transport=steam` hosted on PacketRelayNetDriver).
  `SteamNetDriver`'s CDO has `NetConnectionClassName=SteamNetConnection`, so its config section does exist.
- Even with a socket subsystem, a Steam listen driver takes only Steam connections (IP only in passthrough), and a
  world has one game net driver: a host would be Steam-only or IP-only. The shim gives both at once.
- `DefaultPlatformService`/`NativePlatformService` live in the obfuscated paks (no standard pak footer); not needed:
  the shim talks to steam_api directly (the same `SteamUser020`/`SteamNetworking006` interfaces the game uses).

## Loopback binding (0.3.0, `host_ip=0` default)
Players' games expose nothing to the network: the `bind()` hook rewrites a wildcard bind (`0.0.0.0` / `::`) of a UDP
socket **called from the game exe** (the net driver's listen socket on a host, its connection socket on a client) to
127.0.0.1 (v6: `::ffff:127.0.0.1`, or `::1` for a v6-only socket). Log: `steamnet: game UDP socket bound to 127.0.0.1
port N instead of all interfaces`; `steamnet` shows `loopback binds=N`. Steam P2P doesn't need a reachable socket
(packets are injected in `recvfrom`), local test copies still join `127.0.0.1:7787`, and Windows shows no Firewall
prompt. Binds from other modules are left alone: on Windows `steamclient64.dll` runs in the game process and binds
its own UDP sockets for Steam's networking. Verified live 2026-09-24: `ss -ulnp` shows the host at
`127.0.0.1:7787` and the client at `127.0.0.1:<ephemeral>`; `host_ip=1` gives `0.0.0.0` again.

**Steam's own relay-ping sockets (2026-09-25, not ours).** `tools/e2e.py`'s ss check failed now and then (4 of ~20
runs since 0.3.0) on ~27 UDP sockets on `0.0.0.0:<random>` of one game process, appearing at any phase (host before
sign-in, client mid-mission; once the same second in a game on each lane, i.e. pushed by the Steam client) and then
staying open (still all 27 five minutes later). Evidence they are Steam's, not the game's:
- ss: none of them is co-held by `wineserver`. Every socket a Windows module makes (game exe, EOS, Wwise, Windows
  steamclient64) is a wineserver object, so wineserver keeps an fd of it; these are native Linux sockets. All 85
  off-loopback sockets in every post-0.3.0 sample are of that kind; every wineserver-held game UDP socket is on
  127.0.0.1.
- An `LD_PRELOAD` tracer (socket/bind/connect with `dladdr` backtraces) in the game process: 27 × `socket(v4, UDP)`
  + `bind(0.0.0.0:0)`, every frame in `~/.local/share/Steam/linux64/steamclient.so` (Proton's lsteamclient loads the
  native Steam client library into the game process; its only other native libs there are Vulkan layers).
- Reproduced on demand: dev `steamnet ping 0` = `ISteamNetworkingUtils004::CheckPingDataUpToDate(0)` (new relay
  ping measurement). Before: relay network 0 (not initialised), POPs 0. Right after: the same 27 sockets, then
  `relay network 100 ... POPs=35: OK. Relays: 25 valid ...`. It is Steam Datagram Relay pinging its relay POPs, which
  Steam does whenever relay access is initialised or refreshed (by the game, by Steam, by Steam P2P use). A Steam P2P
  join between two local copies did not trigger it (session `relay=0`).
Verdict: fine. The promise is peer-to-peer play without official game servers, not an air gap (Steam is how friends
connect; Dan, 2026-09-26); the loopback binding covers the game's own UDP sockets. Like Steam's networking in any
Steam game; on Windows the
same code is steamclient64.dll, whose binds the hook already leaves alone. Loopback-binding them would cut Steam off
from its relays, which Steam P2P joins rely on. The e2e check now reports these as
"Steam's native client" (ss line without wineserver) and fails only on sockets made through Wine's ws2_32, sampled
every second instead of every 3 s (so short-lived game sockets are harder to miss). (vtable indices for the v004 thunks were read from Proton's lsteamclient.dll
symbols: [1] GetRelayNetworkStatus, [7] CheckPingDataUpToDate, [10] GetPOPCount; [2] is GetLocalPingLocation, which
takes a 512-byte struct.) Live 2026-09-25, lane 1: `e2e.py --quick` 3 runs, 13/13 each; run 1 with `steamnet ping 0` on
the host mid-run: "83 distinct sockets, 28 of Steam's native client off loopback", PASS.
IP joins to another machine are refused in `cmd_join` (and dropped from `join=` / connect strings) with a message
pointing to Steam Join Game. Our own sockets: the dev command server is 127.0.0.1 only and absent from player builds;
the Windows launcher opens none.

## DTLS / PacketHandler

Unchanged: DTLS runs inside the net connection, above the socket, so it neither knows nor cares that the datagrams
travel through Steam. Verified live: `DTLSPSKServerCallback: Key successfully set`, `Handshaking completed`,
`Login request ... platform: Steam`, `Join succeeded` over the P2P path (below).

## netguard

Nothing to change:
- Fake-address datagrams never reach a real socket (the `sendto` hook consumes them), and UDP is never filtered.
- Steam's networking runs in steamclient (Windows: `steamclient64.dll` in the game process; Proton: `lsteamclient` →
  native `steamclient.so`, outside Wine's ws2_32). TCP/DNS from Steam modules (`steam*`, `lsteamclient`,
  `gameoverlayrenderer`, `tier0_s`, `vstdlib_s`) is `trusted-caller` → allowed; SDR gets its relay config over the
  Steam client connection.
- A Steam join adds no DNS allow (no hostname).

## Fallback

- Steam P2P needs: steam_api64 loaded, `SteamUser020` logged on, `SteamNetworking006`, our ws2_32 hooks, and
  `steam_p2p` not 0. Otherwise `status` says `p2p=<reason>` and:
  - host: only local (127.0.0.1) UDP joins, or network ones with `host_ip=1`; presence advertises nothing when there
    is neither Steam P2P nor `host_ip=1`;
  - client: `join steam:<id>` is refused (`cannot join steam:<id>: Steam P2P unavailable here (<reason>)`, also as a
    local chat line through `coop_join`) and starts no travel, so a target list (`steam:<id>,1.2.3.4:7777`, Steam
    Join Game) falls through to the address at once.
- A P2P session that can't be established shows `steamnet: P2P session with <id> failed, EP2PSessionError N` and the
  usual connect timeout; `steamnet` shows the session state (`active`, `connecting`, `relay`, `error`).

## Local checks (one Steam account, two copies, Proton)

Both copies have the same SteamID, so every P2P packet is addressed to "ourselves" and Steam delivers it to one of
the two processes, not necessarily the other one. Runs with `launch/multi.sh 2` on 2026-09-24:

| Check | Result |
|---|---|
| IP regression, Steam P2P on (default) | host 2/2 players over `127.0.0.1:7787`, `PacketRelayNetConnection`, unchanged |
| `status` | `steam: id=76561198063588550 p2p=on (join me: steam:76561198063588550)` on the host |
| Client `leave`, then `join steam:<own id>` | `open 198.18.0.1:7777`; host: `NotifyAcceptedConnection`, DTLS `Handshaking completed`, `Login request`, `Join succeeded` 3 s later; host 2 players; `steamnet` on both: `session: active=1 relay=0 error=0`, rx/tx ≈ 5000 packets each way after 60 s |
| `steamnet off` + `join steam:` | refused, `Steam P2P unavailable here (disabled (steam_p2p=0))` |
| IP join after the Steam session | works (`open 127.0.0.1:7787`, host 2 players) |
| ini `join=steam:<id>` auto-join (run 2) | several DTLS handshake failures (`unexpected_message`), then `Handshaking completed`, `Welcomed by server`, host 2 players. Host `rx` ≫ client `tx`: the host was reading its own packets, which garbled DTLS. That led to the header tag above. |
| same, then `mission Easy` (run 2) | host travelled, the client's follow kept failing DTLS (same self-delivery), and it fell back to its own camp after the retries |
| ini auto-join with the tag (run 3) | no more garbled handshakes (`dropped: own=113` on the host, `own=322` on the client), but only ~35 % of the host's packets reached the client: DTLS timed out, no join |
| IP regression with the final build + mission follow over IP | host 2/2 over `127.0.0.1:7787`; `mission Easy`: the client followed into Evansburgh (`server_conn=yes`) |
| Steam callbacks | `presence: registered Steam callback 1202/1203 for another module` in every run |

Conclusion: the shim's data path (fake address → SendP2PPacket → ReadP2PPacket → the game's socket, DTLS and login
on top) works; what's left for two accounts is session acceptance and Steam's routing between different SteamIDs,
which one account can't show. Not covered locally: `P2PSessionRequest_t` (never fires for our own SteamID), relay
(`relay=1`), NAT, a Windows client, a peer on another network.

## Two-account test plan

Needs: machines A (host) and B (client), two Steam accounts that own the game, both Steam clients online, this build
on both, **no port forwarding** (remove any 7777 forward on A's router). For at least one run put B on another
network (phone hotspot / CGNAT) so NAT punching can fail and SDR relay has to carry it. Keep both agent logs
(`Gobi/Binaries/Win64/b4bcoop-*.log`); `tools/b4b.py steamnet` prints per-peer session state (`active`, `relay`,
`error`, remote IP) and packet counters.

Confidence = "works as described without code changes".

| # | Step | Expect | Confidence |
|---|---|---|---|
| 1 | A: default (no ini needed). Start, Offline, Fort Hope. `tools/b4b.py status`. | `steam: id=<A> p2p=on (join me: steam:<A>)`; log `steamnet: game listen port 7777; Steam P2P packets go to that socket too`, `presence: registered Steam callback 1202` and `1203`. | high (seen locally) |
| 2 | B: `join=steam:<A>` in the ini (or `/join steam:<A>` in chat). Start, Offline, Fort Hope. | B: `steamnet: steam:<A> -> 198.18.0.1:7777`, `open 198.18.0.1:7777`. A: `steamnet: P2P session request from <B>: accepted`, `NotifyAcceptedConnection`, DTLS `Handshaking completed`, `Login request`, `Join succeeded`. B spawns in A's camp. | medium-high (75%): the data path is verified locally; new here is the session request/accept between two accounts |
| 3 | `steamnet` on both after a minute. | `active=1`, rx/tx growing on both, `error=0`. Note `relay`. | high (given 2) |
| 4 | Repeat 2 with B on another network. | `relay=1` (or 0 if punching worked), same result. | medium (65%): SDR; the legacy API relays by default |
| 5 | A starts a mission from the war table. | B follows (server travel reopens `198.18.0.1:7777`), DTLS retry if B arrives early (`travel: rejoin attempt ... 198.18.0.1:7777`). | high (given 2; checked locally) |
| 6 | Next chapter (seamless travel), bot take-over, burn card, rewards. | as over IP (nothing transport-specific). | high (given 2) |
| 7 | B `/leave`, then `/join steam:<A>` again. | session reused or re-accepted, B back in. | medium-high |
| 8 | A third account C joins over Steam while B is in over IP (forward/VPN). | host has a Steam peer (`198.18.0.x`) and an IP peer at once. | medium-high |
| 9 | Steam Join Game (presence.c): B clicks Join Game on A in the friends list, no `presence_addr`. | B's target list `steam:<A>,<addr>`: joins over Steam. | medium (Join Game itself is presence.c's own two-account plan) |
| 10 | Fallback: B with `steam_p2p=0`, `join=steam:<A>,<A's ip>`. | `cannot join steam:... disabled`, then the address. | high |
| 11 | Play a full chapter over relay. | no rubber-banding beyond the relay latency; `steamnet` shows no growing `queued`. | medium |

If step 2 fails, capture both logs from the `open 198.18...` line (B) and from `listening on port` (A), `steamnet`
on both, and whether A logged `P2P session request`:
- no request on A: B's packets never arrived (B's `session: connecting/error`, `P2P session ... failed`);
- request but `accept failed`: Steam refused (app/rights);
- no `presence: registered Steam callback 1202`: callback registration (accept from the game thread instead);
- accepted but no `NotifyAcceptedConnection`: A didn't identify its game socket (`steamnet`: `game listen port=0`?),
  or the delivered address family doesn't match the socket.

## Two-account result (2026-09-24, one machine, native Steam + Flatpak Steam)
Host: account A (Hergmgurk) test copy on port 7777. Client: account B (dreamsofants) launched from Flatpak Steam with
the no-launch-option install (`X3DAudio1_7.dll` + `b4bcoop.ini` with `join=steam:<A>`, `offline=1`).
- B's agent loaded via a normal Steam launch as `X3DAudio1_7.dll` (Linux zero-script install verified on a real account).
- Auto-join over Steam P2P: host `steamnet` showed 1 session request, peer B `active=1 relay=0 policy=allowed`
  (friends-only allowed a real friend), ~2.6k packets each way in camp; B in the host's Fort Hope.
- Mission follow over Steam: `mission Easy` → B in Evansburgh_B ~20 s later, P2P session kept.
- Rewards with distinct identities: host `rewards: forwarding AdjustSupplyPoints (73) to remote player
  offline.76561198994546085`; B's own (fresh) profile `supplyPoints.acquired` 0 → 73.
- Not covered: relay path across networks (same machine → direct), Steam "Join Game" click and overlay invites (#10).
