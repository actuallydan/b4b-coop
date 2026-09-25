# B4B private co-op — engineering notes

Build analysed: Steam buildid 14216215, changelist 1108676. Codename "Gobi". Modified UE 4.25 ("4.25-plus").

## Plan
Offline mode already has full local progression (`Saved/SaveGames/PlayerProfileSettings.json`: supply points,
decks, unlocks, caravans, campaign runs). Turn an offline session into a listen server that up to 3 others join
(Seamless Co-op model). No WB/TRS services involved. Fort Hope is already P2P listen-hosted by the party leader in
retail, so client-hosted listen servers are a shipped code path. Steam, IP, and EOS net drivers are all in the client.

## Launching
- `launch/run.sh` runs `Gobi/Binaries/Win64/Back4Blood.exe` directly under Proton (no EAC bootstrapper).
  Or a plain Steam launch: the agent is `X3DAudio1_7.dll` (Wine's builtin is prefer-native, so the copy in the game
  dir loads without overrides). Dev-only legacy `dwmapi.dll` (`launch/install.sh --legacy`, not shipped): launch
  options `WINEDLLOVERRIDES="dwmapi=n,b" %command%`. `-b4bcoop=off` on the command line: the agent starts nothing.
  Launch chain, EAC and the Windows redirect: docs/investigations/launch.md.
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
Built with zig cc + MinHook as an X3DAudio1_7.dll proxy (and a dev-only legacy dwmapi.dll proxy). Dev builds (`native/build.sh`) take commands over 127.0.0.1:47112(+n per
instance) via `tools/b4b.py`; player builds (`native/build.sh --release`, `B4B_RELEASE`) have no command server:
`status | players | host | join <steam:id64 | ip> | exec <console cmd> | find <substr> | call <Class> <Func> [cdo] | peek <hex> [n]`.
Hooks: UGameEngine::Tick (game-thread command queue), UEngine::SetClientTravel 0x144130880, FMsg::Logf_Internal
0x142411DB0 (engine log -> b4bcoop-<pid>.log). Global UE_LOG gate byte at 0x1469BD96D (shipping leaves it 0).

Proven: offline Fort Hope `?listen` host; client `open ip:7777` joins camp; host mission start redirected to
`servertravel ...?listen`; client joins mission, PlayerSlotManager gives it a slot and TakeOverBot (retail hot-join path).
PreLogin options carry HydraPublicId=offline.<steamid64>. B4B's PreLogin (0x1419FE9C0) gets the login URL options
already parsed: a TArray of 32-byte {FString key, FString value} entries (admin.c `options_str`); the
client's own `open` URL options (e.g. our `?b4bcoop=<protocol>`) arrive there too.
Known: offline mission start logs "Kicking remote clients with EDisconnectError::HostStartedSoloGame" (harmless now,
travel redirect wins); client that follows too early fails the DTLS handshake -> agent retries 3s later.
Transport: PacketRelayNetDriver (IpNetDriver subclass) on UDP 7777 with DTLSHandlerComponent encryption.

## Unattended sign-in / mission start (native/src/testing.c)
- `ui.AutoSignIn` etc. are real cvars (Engine.ini `[ConsoleVariables]` works). `ui.AutoSignInOffline 1`,
  `Offline.Enable 1`, `SignIn.SkipStartupOptions 1` exist only as strings in a profiling exec list (0x141C53930);
  no cvar is registered under those names, so they do nothing.
- Sign-in screen (`SignInScreen`, state byte +0x568 = ESignInScreenState, set by 0x141D37600): `StartSignIn()` =
  pressing Sign in. `SignInTask_OnlineOfflinePopup` (+0x30 task state, 1 = Running) binds its `OnPopupClosed` to
  the popup; `PopupUserWidget::Close("Offline")` → OnlineModeSubsystem SetOnlineMode(Offline) (0x141B43CA0).
- War table start → `Matchmaking::JoinRun` 0x141AE9240 (Map, RunId, OwnerId, Difficulty, Pool, …): offline it logs
  `set local campaign run id`, builds `?Difficulty=?PoolConfig=?game=…?RunOwner=1` and calls SetClientTravel.
  `Matchmaking.Dev_JoinPool` (BlueprintCallable, Coop, not private/quickplay) calls it with run id 0 = new run.
  A bare `servertravel` of that URL loads the mission, but no campaign run is created (no NewRun init).
