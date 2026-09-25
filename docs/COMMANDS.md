# b4b-coop: commands & options

Everything you can type in the game's chat and set in `b4bcoop.ini`, for the current release. You don't need any of
it to play: install, press Play, and your Steam friends can **Join Game** on you (see the README).

Contents: [Chat commands](#how-to-use-chat-commands) · [Commands for everyone](#commands-for-everyone) ·
[Host-only commands](#host-only-commands) · [Cheats](#cheats-sandbox) · [b4bcoop.ini options](#b4bcoopini-options) ·
[Launch options & troubleshooting](#launch-options--troubleshooting)

## How to use chat commands

1. Press **Enter** to open the chat box (Fort Hope and missions; loading and post-round screens take no input).
   On Steam Deck, open the on-screen keyboard with **STEAM + X**.
2. Type the command, starting with `/`, for example `/players`.
3. Press **Enter**.

- Commands are **never sent to other players**. They run on your own game only.
- Replies appear only in **your** chat, as lines from `coop`, like `[ALL] coop: kicked Alex`.
- Upper or lower case doesn't matter: `/Players` works.
- `//text` sends `/text` as a normal chat message. Note: normal chat messages don't reach other players in offline
  mode (the game's chat needs its online service), so this rarely matters.
- `/help` lists the commands. An unknown command replies `unknown command /xyz (/help)`.

## Commands for everyone

| Command | What it does | Example |
|---|---|---|
| `/help` | Shows your b4bcoop version and the commands | `/help` |
| `/players` | Lists everyone in the game, with numbers | `/players` |
| `/ping` | Your ping to the host | `/ping` |
| `/flashlight` | Turns your flashlight on or off | `/flashlight`, `/flashlight on` |
| `/join steam:<id>` | Joins a friend by their Steam ID | `/join steam:7656119XXXXXXXXXX` |
| `/leave` | Leaves the host's game, back to your own Fort Hope | `/leave` |
| `/host` | Starts hosting your Fort Hope (only needed with `host=0`) | `/host` |

**`/help`**: the first line is your version, e.g. `b4bcoop 0.3.0 (protocol 1)`. The host also sees the host-only
commands; a client sees `(/kick /ban /lock ... are for the host)`.

**`/players`**: one line per player: `#0 Sam (you)`, `#1 Alex`, `#2 Holly [bot]`. The number is what host commands
like `/kick 1` use. The host also sees each player's ping and Steam ID (`#1 Alex 48ms steam:7656119...`).

**`/ping`**: `ping to host: 48 ms`. On the host: `you are the host (no round trip)`.

**`/flashlight`**: no word = toggle. Also `on`, `off`, `auto`, `status`.
- The same as the flashlight key (**L** by default, see `flashlight_key`).
- Your manual choice sticks: walking into a dark or bright area no longer switches it for you, until the next map.
- `/flashlight auto` (host only) hands your light back to the game's automatic switching right away. On a client
  it replies `auto: host only (a client's override lasts until the next map)`.

**`/join steam:<id>`**: the fallback when **Join Game** in Steam doesn't work. Use it from your own Fort Hope.
- `<id>` is the host's 17-digit Steam ID. The host finds it in Steam: click your account name at the top right →
  **Account details**.
- Joining by IP address (`/join 1.2.3.4`) works only with `host_ip=1` (advanced, see below). Without it the reply
  is `Joining by IP address is off. Join through Steam instead: ...`.

**`/leave`**: only while in someone else's game (else `you are not in someone else's session`).

**`/host`**: you host automatically by default, so you only need this after setting `host=0`. Works only from your
own offline Fort Hope while playing alone. Replies:
- `hosting: others can now /join you`: done, friends can join until you quit the game.
- `already hosting (N player(s) connected)`
- `you are in someone's session: /leave first`
- `go back to Fort Hope first`

## Host-only commands

**Who is the host?** The player whose Fort Hope (and missions) everyone else is in. Everyone who joined them is a
client. If you're playing without having joined anyone, you count as the host too, so you can set things like
`/teamsize` before your friends arrive.

**On a client**, a host-only command does nothing and replies `/kick: host only (you are a client)`. Nothing is
sent to the host.

| Command | What it does | Example |
|---|---|---|
| `/kick <player>` | Removes a player from the game | `/kick 2`, `/kick Alex` |
| `/ban <player>` | Removes a player and keeps them out, also after restarts | `/ban 2` |
| `/unban <ban>` | Lifts a ban | `/unban Alex`, `/unban #0`, `/unban all` |
| `/bans` | Lists the bans, with numbers | `/bans` |
| `/lock` | No new players; those in the game now can still come back | `/lock` |
| `/unlock` | Anyone allowed may join again | `/unlock` |
| `/teamsize <N>` | Number of survivors, from the next map | `/teamsize 5` |
| `/bots on\|off\|default` | Bots in empty survivor slots or not, from the next map | `/bots off` |
| `/ready` | Readies everyone on the pre-mission screen | `/ready` |
| `/ready vote` | Readies everyone for the post-round vote | `/ready vote` |
| `/restart` | Restarts the current chapter (counts as a wipe) | `/restart` |
| `/say <message>` | A message every player sees in their chat | `/say back in 5 min` |

**`<player>`** is the number from `/players` (`2` or `#2`), or a name: exact (any case) or the start of a name, if
only one player matches. Bots and yourself can't be kicked or banned (`Holly is a bot`, `that's you`).

**`/kick`**: the player sees `You were kicked by the host.` and goes back to their own Fort Hope. They can join
again; use `/ban` or `/lock` to keep them out. `/kick` and `/ban` need you to be hosting (`not hosting a session`
otherwise).

**`/ban`**: the player sees `You were banned by the host.`. When they try again: `You are banned from this
session.`. Bans are by Steam ID and saved in `Gobi\Binaries\Win64\b4bcoop-bans.txt`, so they last until you
`/unban`. Deleting that file removes all bans.

**`/unban`**: takes a name, a `steam:<id>`, a number from `/bans` (`#0`), or `all`.

**`/lock`**: replies `session locked: no new players (current players can still rejoin)`. Anyone else trying to join
sees `The host locked the session.`. A player you kick while locked can't come back. The lock lasts until `/unlock`
or until you close the game.

**`/teamsize <N>`**: survivors per team, from the next map load (next mission, chapter or Fort Hope). The game's
normal size is 4. `5` is tested and works (5 players, or 4 players + a bot); up to `8` is accepted but untested.
Only the host's setting decides the team size. Same as the `teamsize` ini option, for this game session only.
`/teamsize 4` goes back to normal. Numbers below 4 change nothing.

**`/bots`**: `off` = empty survivor slots stay empty (e.g. play a mission with just 2 heroes). `on` = fill them with
bots. `default` = the game decides. `/bots` alone shows the setting. Applies from the next map, for this game
session only.

**`/ready`**: presses Ready for every player on the pre-mission (loadout) screen, so nobody has to click.
`/ready vote` does the same for the post-round screen's vote. Needs you to be hosting.

**`/restart`**: only in a mission. Fails the mission on purpose, like a team wipe, and the game restarts the chapter
(or goes back to the last checkpoint) for everyone. It counts as a failed attempt, like a real wipe. If a wipe would
end your run, it refuses: `restart refused: failing now would end the run (GameOver)`.

**`/say`**: every player with b4bcoop sees `<your name>: [host] back in 5 min` in their chat, you too.

## Cheats (sandbox)

> **Coming in the next version.** This section is a placeholder; it will be filled in when the cheat commands ship.
> None of them exist in this release.

<!-- TODO(orchestrator): fill in the sandbox cheat commands (#14) when they land. -->

## b4bcoop.ini options

**Where:** `Gobi\Binaries\Win64\b4bcoop.ini` in the game folder (Steam → right-click **Back 4 Blood** → **Manage** →
**Browse local files**). The zip puts it there.

**How to edit:** open it in a text editor (Notepad on Windows, Kate/KWrite on Steam Deck Desktop Mode). Every setting
is off until you remove the `;` at the start of its line. Save, then restart the game: settings are read at start.

```ini
; before: off
;teamsize=5
; after: on
teamsize=5
```

Rules:
- One setting per line, `name=value`, the name at the very start of the line, lowercase, no space before `=`.
- A line starting with `;` or `#` is ignored.
- No `b4bcoop.ini` at all = all defaults.
- **Updating b4bcoop** (extracting a new zip) replaces `b4bcoop.ini` with a fresh one: note your changes first.
  Bans (`b4bcoop-bans.txt`) are kept.

### Common options

| Option | Default | Values | What it does |
|---|---|---|---|
| `host` | `1` | `0`, `1` | `0`: don't host, your offline game stays private |
| `teamsize` | game's 4 | `5` (up to `8`) | Host: survivors per team |
| `flashlight_key` | `L` | a letter, a digit, a key code, or `off` | The flashlight toggle key |
| `flashlight_sticky` | `1` | `0`, `1` | Host: a manual flashlight choice stays until the next map |
| `allow_joins` | `friends` | `friends`, `anyone` | Host: who may join |
| `allow_steamids` | none | Steam IDs | Host: these players may always join |
| `presence` | `1` | `0`, `1` | `0`: friends don't see **Join Game** on you |
| `netguard` | `block` | `block`, `log`, `off` | Blocks the game's online services while you play |
| `join` | none | `steam:<id>` | Always join this host automatically |
| `host_ip` | `0` | `0`, `1` | **Advanced**: host and join by IP address |

**`host`**: you host by default: whenever you're in your offline Fort Hope, your Steam friends can join. `host=0`
keeps your game private; `/host` still turns hosting on for one game session. Setting `join=` also turns hosting
off, unless you add `host=1`.
```ini
host=0
```

**`teamsize`**: `5` = 5 survivors (tested: 5 players, or fewer players + bots; alone you get 4 bots). Up to `8` is
accepted but untested. Values of 4 or less change nothing. Only the host's setting decides the team size (5-player
games were tested with `teamsize=5` on every PC; setting it on a client does no harm). More players than survivor
slots get `Server full.`.
```ini
teamsize=5
```

**`flashlight_key`**: works only while the game window is in front.
- A letter or digit: `flashlight_key=F`.
- A Windows key code, written `0x..`: e.g. `0x70` = F1, `0x71` = F2 ... `0x7B` = F12.
- `off` turns the key off (the `/flashlight` command still works). Note: `flashlight_key=0` binds the **0** key; it
  doesn't turn the key off.
- Steam Deck / controller: map a button to the **L** key in Steam Input (controller settings).
```ini
flashlight_key=F
```

**`flashlight_sticky`**: host setting, applies to every player in the host's game. `1` (default): once someone
switches their light by hand, dark or bright areas no longer switch it back, until the next map (or `/flashlight auto`
on the host). `0`: the game's automatic switching always wins.

**`allow_joins`**: host only. `friends` (default): only your Steam friends can join. `anyone`: anyone who can reach
you, friends or not. Someone refused shows up in your chat: `Refused a join from Alex (steam:7656119...): not on the
host's Steam friends list. To let them in: allow_steamids=7656119... in b4bcoop.ini.`
```ini
allow_joins=anyone
```

**`allow_steamids`**: host only. Lets specific people in who aren't your Steam friends (e.g. a friend of a friend),
whatever `allow_joins` says. 17-digit Steam IDs, separated by commas; `steam:` in front is fine. Up to 32. The easiest
way to get an ID: the "Refused a join" line above.
```ini
allow_steamids=7656119XXXXXXXXXX,7656119YYYYYYYYYY
```

**`presence`**: `0` = your friends don't get **Join Game** on you in Steam. They can still join with
`/join steam:<your id>` (if your join rules let them in).

**`netguard`**: `block` (default) blocks the game's attempts to reach Epic / WB / Turtle Rock servers while you play
co-op; Steam and the co-op connection are not affected. `log` only writes what it would block to the log. `off` turns
it off. Try `netguard=off` only if something won't start or connect, and tell us.

**`join`**: optional; **Join Game** in Steam is the normal way to join. With `join=steam:<host's Steam ID>` your game
joins that host by itself whenever you're alone in your offline Fort Hope (you still sign in Offline yourself), and
tries again every 20 seconds until it gets in. Several hosts can be listed with commas; they're tried in turn. It
turns hosting off (unless `host=1`). A Steam **Join Game** click overrides it.
```ini
join=steam:7656119XXXXXXXXXX
```

**`host_ip`**: **advanced, most players never need this.** Hosts and joins by IP address instead of through Steam.
- The host needs port forwarding (UDP 7777) on their router, and Windows shows a Firewall prompt.
- Everyone in the game sets `host_ip=1`; joiners use `/join <host's IP>` (or `join=<IP>` in the ini). Port 7777 is
  assumed; another port: `/join 1.2.3.4:7778`.
- Your game is then reachable from the network, not only through Steam. Steam joins keep working.
- Without it, IP joins are refused with `Joining by IP address is off...`, and an IP-only host's **Join Game** says
  `That host only takes joins by IP address, which are off here (host_ip=0).`

### Rarely needed

| Option | Default | What it does |
|---|---|---|
| `steam_p2p` | `1` | `0`: no joins through Steam at all (then only `host_ip=1` joins work) |
| `presence_addr` | your LAN address | With `host_ip=1`: the address friends' **Join Game** connects to, e.g. `presence_addr=203.0.113.5:7777` (your public IP) |
| `netguard_eos` | `1` | `0`: don't switch off the Epic Online Services network layer (troubleshooting only) |
| `netguard_allow` | none | With `netguard=block`: host names to let through anyway, comma-separated, `*.example.com` for a whole domain (troubleshooting only) |

## Launch options & troubleshooting

### Turn the mod off without removing it: `-b4bcoop=off`

Steam → right-click **Back 4 Blood** → **Properties** → **Launch Options**, add:
```
-b4bcoop=off
```
The game then starts as if the mod weren't there (on Windows, with Easy Anti-Cheat again), e.g. to play online.
Remove it again to play co-op. The log then only says `off (-b4bcoop=off on the command line), not starting`.

### The log, and reporting a problem

- The log is `Gobi\Binaries\Win64\b4bcoop-<number>.log` in the game folder. Every game start writes a new one: take
  the **newest**. Its first lines show the version, e.g. `b4bcoop 0.3.0 (protocol 1)`.
- No new log after starting the game = the mod didn't load (check that all files from the zip are in place).
- Windows only: if the mod doesn't load, also `Gobi\Binaries\Win64\b4bcoop-launcher.log` (if it exists).
- A line `unsupported game build (signature mismatch)` means your Back 4 Blood version isn't the one this b4bcoop
  release supports; the mod then does nothing.

To report a problem, [open an issue](https://github.com/actuallydan/b4b-coop/issues) with:
1. Your b4bcoop version, and Windows / Linux / Steam Deck.
2. What you did and what happened (roughly when, and any message you saw).
3. The newest log from **your** PC, and if it's about joining, the host's and the joiner's logs from that session.

The logs contain your Steam name and Steam ID and those of the players you played with.

### Messages you may see

When a join fails, the game first shows its own popup (e.g. "Unable to join the session"); the reason appears in
your chat as `Could not join: ...` once the popup is closed.

| Message | Meaning | What to do |
|---|---|---|
| `Host runs b4bcoop X (protocol N); you have Y (protocol M). Everyone needs the same version.` | You and the host have different b4bcoop versions | Everyone installs the latest release (extract the zip again). Both versions are in the message; the host sees `<name> could not join: they have ...` |
| `Server full.` | Every survivor slot is taken by a player (bot slots count as free) | Wait for a free slot, or the host sets `teamsize=5` |
| `This host only accepts their Steam friends. ...` | You're not on the host's Steam friends list | Become Steam friends, or the host adds you with `allow_steamids=` |
| `Could not reach the host over Steam. Is it still hosting? ...` | The host quit, or refused you (not friends) | Check the host is in Fort Hope or a mission; see the line above |
| `You are banned from this session.` | The host banned you | Ask the host to `/unban` you |
| `The host locked the session.` | The host used `/lock` | Ask the host to `/unlock` |
| `You were kicked by the host.` / `You were banned by the host.` | The host removed you | You're back in your own Fort Hope |
| `Joining by IP address is off. ...` | You tried an IP join without `host_ip=1` | Use **Join Game** in Steam or `/join steam:<id>` |
| `Ignored a reward from the host that failed a safety check ...` | Your game rejected a reward outside normal mission limits, to protect your save | Nothing; if it keeps happening, report it with your log |
