# B4B private co-op — engineering notes

Build analysed: Steam buildid 14216215, changelist 1108676. Codename "Gobi". Modified UE 4.25 ("4.25-plus").

## Plan
Offline mode already has full local progression (`Saved/SaveGames/PlayerProfileSettings.json`: supply points,
decks, unlocks, caravans, campaign runs). Turn an offline session into a listen server that up to 3 others join
(Seamless Co-op model). No WB/TRS services involved. Fort Hope is already P2P listen-hosted by the party leader in
retail, so client-hosted listen servers are a shipped code path. Steam, IP, and EOS net drivers are all in the client.

## Launching
- `launch/run.sh` runs `Gobi/Binaries/Win64/Back4Blood.exe` directly under Proton (no EAC bootstrapper).
  Or Steam launch options: `WINEDLLOVERRIDES="dwmapi=n,b" %command%`.
- A game started by Wine is reparented to systemd, so `/proc/<pid>/mem` is unreadable under yama ptrace_scope=1.
  Use `launch/probed.sh` (Windows Python in the same prefix, ReadProcessMemory) + `tools/probe.py '<code>'`.

## Anti-tamper / layout changes vs stock 4.25 (why UE4SS fails)
- GUObjectArray @ 0x14667C740: MaxElements +0x38, NumElements +0x3C, MaxChunks +0x40, NumChunks +0x44,
  Objects (FUObjectItem**) +0x48 **XOR 0x8375**. Chunk = 64K items.
- FUObjectItem (0x18): Flags +0, ClusterRootIndex +4, Object +8, Serial +0x10.
- FNamePool @ 0x146986C80, Blocks[] at +0x10. Entry = Blocks[id >> 18] + (id & 0xFFFF) * 2; header len = hdr >> 6.
- UObject is 0x30 (extra 8-byte field at +0x28), so every UField/UStruct/UClass/UFunction offset is stock + 8.
- FField/FProperty are shuffled: Class 0x8, Owner 0x10, Flags 0x20, Name 0x24, Next 0x30,
  PropertyFlags 0x38, ElementSize 0x40, ArrayDim 0x44, Offset 0x4C, subtype ptr 0x78.
- Full table in `tools/sdkdump.py` docstring.

## Key functions (VA, current build)
- StaticConstructObject_Internal 0x1426E4840 (4.25 signature; 826 direct callers)
- FName::ToString 0x1424BCB50, FName::FName(wchar*) 0x1424BC8E0, GameEngineTick 0x143C955B0
- Native UFunction pointers are in `sdk/*.txt` (`native=`).

## SDK
`sdk/` (local, gitignored) — reflection dump of all /Script classes (4158 classes, 2969 structs, 1442 enums). Regenerate:
`tools/probe.py 'import sys; sys.path.insert(0, memprobe.__file__.rsplit(chr(92),1)[0]); import sdkdump; sdkdump.run(g, only_script=True)'`

Relevant Gobi classes: GobiGameInstance, MainMenuGameMode, HeroGameMode, MissionGameMode (+Legendary/Challenge),
Matchmaking, MatchmakingSetHostTaskData, GobiSession*, DedicatedServerManager, CampaignRunManager,
PlayerProfileData (OfflineData), EOnlineMode {Offline, Online}, *SeamlessTravelData.

## Agent DLL (native/) — status 2026-09-23
dwmapi.dll proxy built with zig cc + MinHook. Commands over 127.0.0.1:47112(+n per instance) via `tools/b4b.py`:
`status | players | host | join <ip> | exec <console cmd> | find <substr> | call <Class> <Func> [cdo] | peek <hex> [n]`.
Hooks: UGameEngine::Tick (game-thread command queue), UEngine::SetClientTravel 0x144130880, FMsg::Logf_Internal
0x142411DB0 (engine log -> b4bcoop-<pid>.log). Global UE_LOG gate byte at 0x1469BD96D (shipping leaves it 0).

Proven: offline Fort Hope `?listen` host; client `open ip:7777` joins camp; host mission start redirected to
`servertravel ...?listen`; client joins mission, PlayerSlotManager gives it a slot and TakeOverBot (retail hot-join path).
PreLogin options carry HydraPublicId=offline.<steamid64>.
Known: offline mission start logs "Kicking remote clients with EDisconnectError::HostStartedSoloGame" (harmless now,
travel redirect wins); client that follows too early fails the DTLS handshake -> agent retries 3s later.
Transport: PacketRelayNetDriver (IpNetDriver subclass) on UDP 7777 with DTLSHandlerComponent encryption.
