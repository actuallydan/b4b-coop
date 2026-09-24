# Steam P2P transport (no port forwarding)

Build 14216215. Static analysis of `Back4Blood.exe` / `steam_api64.dll` (Steamv147) plus single-account live checks.
Implementation: `native/src/steamnet.c` (+ small hooks in `cmds.c`, `travel.c`, `uelog.c`).

Status 2026-09-24: **implemented, single-account checks only** (see "Local checks"). Nothing has crossed Steam's
network between two accounts yet; the two-account test plan is at the end.

## Usage

| Where | What |
|---|---|
| host `b4bcoop.ini` | `transport=steam` (default `ip`). `host=1` then opens the camp on the Steam net driver. |
| host log / `status` | `steamnet: hosting on Steam P2P: join steam:7656...` and `steam: id=... (share: steam:...)` |
| client `b4bcoop.ini` | `join=steam:<host steamid64>` (auto-join), or `join=1.2.3.4[:port]` as before |
| agent commands | `join steam:<id64>[:port]`, `join <ip>[:port]`, `host`, `steamnet` (details, P2P state per peer), `steamnet transport steam\|ip` |
| C API (`cmds.h`) | `void coop_join(const char *target)` (same targets as `join`), `void coop_host(void)`; callable from any thread (queued to the game thread) |

`:port` on a Steam target is the P2P channel = the host's listen port (7777 unless `-Port=`; the test instances
use 7787, so locally it is `steam:<id>:7787`).

## Findings

### How the game net driver is chosen
- `UEngine::NetDriverDefinitions` (engine property `+0xC18`, `FNetDriverDefinition` = `DefName`, `DriverClassName`,
  `DriverClassNameFallback`, 3 FNames). `CreateNamedNetDriver(World, GameNetDriver, GameNetDriver)` looks up the
  `GameNetDriver` entry on the live `GEngine`, loads `DriverClassName`, and if the class is missing or its CDO
  says `!IsAvailable()` it loads `DriverClassNameFallback`. Both a listen world (`open map?listen`, `servertravel`
  to a new map) and a client's pending net game (`open <url>`, a follow) go through this entry.
- Retail entry (live, logged by the agent at the first switch): `GameNetDriver` →
  `OnlineSubsystemPacketRelay.PacketRelayNetDriver`, fallback `OnlineSubsystemUtils.IpNetDriver`. PacketRelayNetDriver is TRS's IpNetDriver subclass for
  their relay service; offline it runs as a plain IP driver ("PacketRelay API disabled").
- The ini files live in the obfuscated paks (no standard pak magic in the footer), so `DefaultEngine.ini` could
  not be read statically; everything below comes from code and live reflection.

### USteamNetDriver in this build is stock 4.25
Vtable `0x14526E0F8` (IpNetDriver's is `0x145246408`); overrides:

| Slot | VA | Function | Notes |
|---|---|---|---|
| 79 | `0x140D26000` | `IsAvailable` | `IOnlineSubsystem::Get("STEAM")` (`0x140BB07D0`) && `ISocketSubsystem::Get("STEAM")` (`0x1429AE6A0`) |
| 80 | `0x140D262A0` | `InitBase` | `bIsPassthrough` (`+0x7D0`) → `UIpNetDriver::InitBase` (`0x140BF0740`) |
| 81 | `0x140D26720` | `InitConnect` | `steam.` URL → Steam socket, else passthrough |
| 82 | `0x140D26970` | `InitListen` | STEAM socket subsystem present and no `bIsLanMatch` option → `SteamClientSocket`, else passthrough; then `UIpNetDriver::InitListen` (`0x140BF1B70`) |

- **No IP fallback inside one Steam listen driver**: with the Steam socket it listens only on Steam P2P. Passthrough
  (plain IP) only happens when the STEAM socket subsystem is missing or `?bIsLanMatch` is on the URL. A world has one
  game net driver, so **a host is reachable over Steam or over IP, not both**. Hence `transport=steam|ip`.
- The engine fallback covers "Steam OSS/socket subsystem missing" (IsAvailable false → retail driver).
- The Steam socket subsystem uses legacy `ISteamNetworking` (`SteamNetworking006`, `SendP2PPacket`/`ReadP2PPacket`,
  channel = port). Modern Steam clients run it on top of Steam Datagram Relay; relay fallback is on by default per
  the Steamworks docs, and the agent calls `AllowP2PPacketRelay(true)` again when it selects Steam and after the
  listen socket exists, in case the game's `[OnlineSubsystemSteam] bAllowP2PPacketRelay` is false (the only reader of
  that key, `0x140D08A10`, also reads `bUseSteamNetworking`; its value is not visible statically).
- P2P session requests are accepted blindly by the stock handler (`0x140CEBCEC` → `AcceptP2PConnection`
  `0x140D25180`, log `Adding P2P connection information with user %s`; failure log
  `Rejected P2P connection request from %s`).

### OSS configuration
- Online subsystems in the exe: Steam, EOS, EOSPlus, PacketRelay (TRS). Every session log shows the Steam OSS alive:
  `STEAM: Obtained steam authticket`, `STEAM: Steam achievements have not been read for player 7656...`, two
  `OnlineSubsystemSteam::Shutdown()` at exit (two instances). `DefaultPlatformService`/`NativePlatformService`
  are read from config we can't see; it does not matter here: SteamNetDriver asks for the `STEAM` OSS and socket
  subsystem by name, not for the default OSS.
- The login identity (`HydraPublicId=offline.<steamid64>`, `platform: Steam`) comes from the game, not from the
  transport, so it is the same over IP and Steam.

### Driver defaults
- `SteamNetDriver` has no config section of its own in this game (to be confirmed live, `steamnet` prints its
  `NetConnectionClassName`). A stock UE setup needs
  `[/Script/OnlineSubsystemSteam.SteamNetDriver] NetConnectionClassName=/Script/OnlineSubsystemSteam.SteamNetConnection`.
  `mirror_defaults()` sets the CDO's `NetConnectionClass` (UClass*, which `UNetDriver::InitConnectionClass` uses
  before the name) to `SteamNetConnection` when the name is not already that, and copies the retail driver's
  rates/timeouts/replication driver class onto the Steam CDO (ints/floats and one class pointer; no FString
  writes). `tune_net_defaults()` also raises the Steam driver's connect timeouts like the others.

### DTLS / PacketHandler
- The connectionless handler loads `DTLSHandlerComponent` and logs `EnableEncryption: Server` right before
  `GameNetDriver ... listening on port`, and every connection logs the PSK callback. The DTLS layer sits between the
  net connection and the socket, so it does not care whether the socket is UDP or `ISteamNetworking`. Whether the
  DTLS component is configured per driver name (`GameNetDriver`, applies to Steam too) or per class: see the local
  check below. Either way both sides run the same driver and the same config, so they agree.
- Packet size: UE's default MaxPacket (1024 incl. handler overhead) is below ISteamNetworking's 1200-byte
  unreliable limit.

### netguard
Nothing to change. Steam's networking runs inside steamclient (Windows: `steamclient64.dll` in the game process;
Proton: `lsteamclient.dll` → native `steamclient.so`, which never goes through Wine's ws2_32):
- UDP is never filtered (`decide_addr`: only noted).
- TCP and DNS from a Steam module (`steam*`, `lsteamclient`, `gameoverlayrenderer`, `tier0_s`, `vstdlib_s`) are
  `trusted-caller` → allowed. SDR gets its relay list via the Steam client connection, not DNS.
- `join steam:...` does not add a runtime DNS allow (there is no hostname).

### Fallback
- Host, `transport=steam`: `steamnet_available()` checks steam_api64 loaded, `SteamUser020` present and
  `BLoggedOn()`, `SteamNetworking006`, `ISocketSubsystem::Get("STEAM")`. If any fails:
  `steamnet: Steam P2P unavailable (<reason>); hosting falls back to IP`, and the retail definition is used. If the
  engine itself still picks the retail driver (IsAvailable false), `steamnet_tick` logs
  `transport=steam but hosting on IP (...)`.
- Client, `join steam:...` without Steam P2P: nothing to fall back to (a Steam target has no IP), so the join is
  refused with `cannot reach steam.<id>:<port> without Steam P2P (<reason>); ask the host for an IP join instead`.
- A Steam P2P session that can't be established (NAT and relay both fail) shows up as the Steam OSS log
  `k_EP2PSessionError...` (mirrored as `steamnet: P2P problem: ...`) and a connect timeout; the agent does not switch
  a session between transports on its own.

### Follow / rejoin
`travel.c` stores the URL the client joined (`ip:port` or `steam.<id64>:<port>`) and its retry path reopens exactly
that URL; `steamnet_prepare_url()` re-selects the matching definition first. A server-travel follow reuses the
definition, which stays on Steam for the whole session (it is only switched back by an IP host/join).

## Local checks (single Steam account)

(filled in below)

## Two-account test plan

Needs: machines A (host) and B (client), two Steam accounts that own the game, both Steam clients online, this build
on both, **no port forwarding** (remove any 7777 forward on A's router). Put B on a different network (phone
hotspot / CGNAT) for at least one run so NAT punching can fail and SDR relay has to carry it. Keep both agent logs
(`Gobi/Binaries/Win64/b4bcoop-*.log`); `tools/b4b.py steamnet` on each side prints transport and per-peer P2P state
(`active`, `relay`, `error`, remote IP).

Confidence is for "works as described without further code changes".

| # | Step | Expect (logs / commands) | Confidence |
|---|---|---|---|
| 1 | A: `b4bcoop.ini` = `host=1` + `transport=steam`. Start, Offline, Fort Hope. | `steamnet: hosting over Steam P2P (SteamNetDriver)`, `LogNet: GameNetDriver SteamNetDriver_... IpNetDriver listening on port 7777`, `steamnet: hosting on Steam P2P: join steam:<A id>`; `steamnet` → `world net driver: Steam P2P, listening`. | high (checked locally, see above) |
| 2 | B: `join=steam:<A id>`. Start, Offline, Fort Hope. | B: `steamnet: joining over Steam P2P`, `exec: open steam.<A id>:7777`. A: `Adding P2P connection information with user <B id>` / `steamnet: P2P session accepted`, `NotifyAcceptedConnection ... SteamNetConnection_...`, DTLS `Handshaking completed`, `Login request ... platform: Steam`. B spawns in A's camp. | medium (60%): first time bytes cross ISteamNetworking; risks are DTLS over Steam, connection class, P2P session acceptance |
| 3 | Same with B on another network. | `steamnet` on B: `peer <A id>: active=1 relay=1` (or relay=0 if NAT punch worked). Play for a few minutes; no timeouts. | medium (55%) |
| 4 | A starts a mission from the war table. | B follows: `SetClientTravel type=2 ... ` then a pending net game to `steam.<A id>:7777`; on a DTLS race, `travel: rejoin attempt ... open steam.<A id>:7777` and success. Host keeps `SteamNetDriver` after `servertravel` (`steamnet` on A). | medium (65%, given 2) |
| 5 | Finish chapter 1, go to chapter 2 (seamless travel). | Same driver, B stays connected. | high (given 4) |
| 6 | B takes over a bot, plays a burn card, gets rewards. | Same as over IP (transport-agnostic RPCs). | high (given 2) |
| 7 | B quits to menu and rejoins (`join steam:<A id>` or auto-join). | A: old P2P session removed (`Removing P2P Session Id`), new one accepted; B back in. | medium (60%) |
| 8 | Third account C joins too (3 players). | Two peers in `steamnet` on A, both `active=1`. | medium-high (given 2) |
| 9 | IP regression: A `transport=ip` (or no key), B `join=<A ip>` with a forward or VPN. | Retail `PacketRelayNetDriver` exactly as before. | high (checked locally) |
| 10 | Fallback: A sets Steam to offline mode (or kill Steam networking), `transport=steam`. | `steamnet: Steam P2P unavailable (<reason>); hosting falls back to IP`, listen on UDP 7777. B with a Steam target and no Steam: join refused with `cannot reach steam.<id>...`. | medium: the log lines are simple; whether the game starts at all with Steam offline is unknown |
| 11 | Wrong target: B `join steam:<A id>` while A hosts over IP. | B's connect times out after `InitialConnectTimeout` (180 s, raised by the agent); A never sees a P2P request. Expected failure, documents the either/or. | high |

What to capture if step 2 fails: both logs from the `open steam.` line on (B) and from `listening on port` on (A),
`steamnet` output on both, and whether A logged any `P2P` line at all (no line = packets never reached A's Steam
client: SDR/relay issue; a `Rejected` line = acceptance issue; a DTLS error = handshake over Steam).

Likely fixes by symptom:
- A never sees a session request: check `[OnlineSubsystemSteam]`-level P2P settings; try the client with
  `steam.<id>:7777` vs the host's actual channel (A's `last listen: port/channel`).
- Session accepted but no `NotifyAcceptedConnection`: channel mismatch or `SteamNetConnection` class issue
  (`steamnet` prints the Steam CDO's connection class).
- DTLS handshake errors only over Steam: packet size/ordering; compare with the IP path, consider disabling DTLS for
  the Steam driver only (not implemented).
