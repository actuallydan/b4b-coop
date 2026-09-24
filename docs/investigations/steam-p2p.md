# Steam P2P (no port forwarding)

Build 14216215. Static analysis of `Back4Blood.exe` / `steam_api64.dll` (Steamv147) plus single-account live runs.
Implementation: `native/src/steamnet.c` (+ small hooks in `cmds.c`, `uelog.c`, `main.c`, `presence.c`).

Status 2026-09-24: **a Steam P2P join worked between two local copies on one Steam account** (DTLS, login, 60 s of
play traffic; see "Local checks"), but one account can't test it reliably: Steam hands each packet addressed to "our
own" SteamID to either copy, so later runs lost 30-70 % of packets. Not yet run between two accounts / machines, and
only that can show the P2P session request/accept and Steam's routing between SteamIDs. Test plan at the end.

## Usage

| Where | What |
|---|---|
| host | nothing to do: Steam P2P is on by default and the host accepts Steam joins **and** UDP 7777 at the same time. `status` shows `steam: id=7656... p2p=on (join me: steam:7656...)`. |
| client `b4bcoop.ini` | `join=steam:<host SteamID64>` (or `join=1.2.3.4[:port]` as before; `join=steam:<id>,1.2.3.4` tries both) |
| agent commands | `join steam:<id64>`, `join <ip>[:port]`, `steamnet` (per-peer P2P session state), `steamnet on\|off` |
| chat | `/join steam:<id64>` (chat.c → `coop_join`) |
| Steam "Join Game" | presence.c advertises `+b4bcoop_join steam:<id64> addr:<ip:port>`; the joiner tries `steam:` first |
| C API (`cmds.h`) | `void coop_join(const char *target)` (`ip[:port]` or `steam:<id64>`), `void coop_host(void)`; any thread (queued to the game thread) |
| ini | `steam_p2p=0` turns Steam P2P off (also `transport=ip`) |

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
- P2P session requests (`P2PSessionRequest_t`, callback 1202) are accepted while Steam P2P is on; connect failures
  (1203) are logged with the `EP2PSessionError`. The callbacks are registered through presence.c's hook of
  `SteamAPI_RunCallbacks` (same thread and same reason as its own join-request callback).
- `AllowP2PPacketRelay(true)` on every Steam join (SDR relay when NAT punching fails; Steam's default anyway).
- Every P2P datagram carries an 8-byte header: magic `B4C1` + a random per-process tag. Packets without the magic are
  dropped; packets with our own tag came back to us (only possible with one account on two copies) and are dropped.

Because packets are addressed by SteamID, the port in a Steam target doesn't matter (`steam:<id>:7787` = `steam:<id>`).
The follow/rejoin logic in `travel.c` needs no change: it reopens the same fake URL, which keeps mapping to the same
SteamID for the life of the process.

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
  - host: nothing changes, it takes UDP joins as always;
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
| 1 | A: `host=1` (default ini). Start, Offline, Fort Hope. `tools/b4b.py status`. | `steam: id=<A> p2p=on (join me: steam:<A>)`; log `steamnet: game listen port 7777; Steam P2P packets go to that socket too`, `presence: registered Steam callback 1202` and `1203`. | high (seen locally) |
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
