# b4b-coop: commands & options

Everything you can type in the game's chat and set in `b4bcoop.ini`, for the current release. You don't need any of
it to play: install, press Play, and your Steam friends can **Join Game** on you (see the README).

Contents: [Chat commands](#how-to-use-chat-commands) · [Commands for everyone](#commands-for-everyone) ·
[Host-only commands](#host-only-commands) · [Cheats](#cheats-sandbox) · [Add-ons](#add-ons) ·
[b4bcoop.ini options](#b4bcoopini-options) ·
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
| `/thirdperson` | Over-the-shoulder camera for your own hero | `/thirdperson`, `/thirdperson off`, `/thirdperson distance 150` |
| `/join steam:<id>` | Joins a friend by their Steam ID | `/join steam:7656119XXXXXXXXXX` |
| `/leave` | Leaves the host's game, back to your own Fort Hope | `/leave` |
| `/host` | Starts hosting your Fort Hope (only needed with `host=0`) | `/host` |
| `/model <name>` | Changes how your survivor looks, this game session only | `/model karlee_elite_03`, `/model holly` |
| `/model list [survivor\|npc]` | The looks you can use | `/model list`, `/model list walker` |
| `/model reset` | Back to your own look | `/model reset` |
| `/addons` | Lists your add-ons, on/off, and conflicts | `/addons` |
| `/addons on\|off <#>` | Switches an add-on on or off from the next game start | `/addons off 2` |
| `/addons info <#>` | Title, author, version, description, cosmetic or gameplay, id of an add-on | `/addons info 1` |

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

**`/thirdperson`**: no word = toggle. Also `on`, `off`, `status`, and the camera settings below.
- A third-person camera behind your own hero. **Aiming** (right mouse) switches to first person while you hold it,
  and back when you let go.
- Only your own view changes: the other players see nothing different, and nothing is sent to them. Host and clients
  can each use it; cheats aren't needed.
- It stays on until you type `/thirdperson` again: into the next chapter, back in Fort Hope, and in someone else's
  game. It isn't saved: after restarting the game you start in first person, unless `thirdperson=1` is in
  `b4bcoop.ini`.
- The **N** key toggles it too (see `thirdperson_key`).
- Moments where the game itself switches to a view from behind (healing, being grabbed or pounced, ...) are left to
  the game.
- There is no third-person crosshair: the normal centre-of-screen one is used. With the camera straight behind
  your hero (the default), shots land exactly under it, but your hero's head covers it; aim with right mouse for
  precise shots.
- Camera settings (only your view, applied at once, kept for every map until the game quits; put them in
  `b4bcoop.ini` to keep them):

| Setting | Default | Values | What it does |
|---|---|---|---|
| `/thirdperson distance <n>` | `180` | `50`-`600` | How far behind your hero the camera is (the game's own is 300) |
| `/thirdperson side <n>` | `40` (right shoulder) | `-150`-`150`, `left`, `right`, `swap` | Over the shoulder: positive = right, negative = left. `left`/`right` pick a shoulder (40 if it was 0), `swap` switches shoulders, `0` = centred behind your hero |
| `/thirdperson height <n>` | `0` | `-100`-`150` | Raises (or lowers) the camera |
| `/thirdperson fov <n>` | the game's | `60`-`130`, `0` = the game's | Field of view in third person |
| `/thirdperson reset` | | | Back to these defaults |

- **Aim with a side or height offset**: your shots still come from your hero's eyes, not from the camera, so they
  land that many units beside (or below) the point under the crosshair: `side 40` = 40 cm to the left of it, at
  every range (a lot up close, little far away). The command reminds you. Aiming with right mouse is always exact;
  `side 0` also puts shots exactly under the crosshair, but your hero's head then covers it.

**`/join steam:<id>`**: the fallback when **Join Game** in Steam doesn't work. Use it from your own Fort Hope.
- `<id>` is the host's 17-digit Steam ID. The host finds it in Steam: click your account name at the top right →
  **Account details**.
- Joining by IP address (`/join 1.2.3.4`) works only with `host_ip=1` (advanced, see below). Without it the reply
  is `Joining by IP address is off. Join through Steam instead: ...`.

**`/model`**: wear another survivor's outfit (`/model walker_elite_07`), a whole survivor's look (`/model holly`),
single pieces (`/model holly_head_03`) or a Fort Hope NPC (`/model vanessa`). Everyone sees survivor outfits; NPC
looks only players with b4bcoop. Kept through map changes, death and respawn; nothing is saved. `/model` alone shows
what you wear. All names, examples and limits: `docs/commands-models.md`.

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
| `/model <player> <name\|reset>` | Changes a player's or a bot's look; everyone is told | `/model 2 doc_elite_03` |
| `/models on\|off` | Allows or stops model swaps for everyone (on by default) | `/models off` |
| `/addons players` | Which add-ons each player runs (cosmetic/gameplay, ids) | `/addons players` |
| `/addons policy [any\|cosmetic\|none\|match]` | Shows or sets (this session) which add-ons joiners may have | `/addons policy none` |

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

**`/models off`**: resets every look that uses another survivor's outfit or an NPC, and refuses new ones (the player
sees `The host turned model swaps off (/models).`). Players can still wear their own survivor's outfits. `/models on`
allows swaps again. `/model <player> reset` puts one player or bot back to their own look.

**`/say`**: every player with b4bcoop sees `<your name>: [host] back in 5 min` in their chat, you too.

## Cheats (sandbox)

An opt-in sandbox for the **host**: god mode, flying, spawning ridden, forcing a horde, and so on. Everything is a chat
command (type it in the game's chat box; a message starting with `/` is never sent to the other players).

**The rules:**
- **Off until the host turns them on** with `/cheats on`. `/cheats off` turns them off again, and they switch off by
  themselves when the game goes back to Fort Hope (camp) or the menus. Moving on to the next chapter keeps them on.
- **Host only.** Every cheat command works only on the host (the player whose game the others joined, or your own
  offline game). Typed by someone who joined, it only replies `host only` and does nothing.
- **Everyone is told.** Turning cheats on or off, and every cheat that affects another player or the whole game, shows
  a `[b4bcoop] host ...` line in everyone's chat (for example `[b4bcoop] host gave god mode to Mellon`).
- **Nobody else's save is touched.** Cheats change the running game, or the host's own save (`/supply`, `/unlockall`).
  From the moment cheats are turned on, the rest of that map gives the other players **no supply points, skull
  totem points, unlocks, stats or achievements** (the host keeps its own). Their own burn cards are still charged as
  usual. The chat says so when cheats are turned on.
- Cheats that change a hero (god, fly, infinite ammo, speed, freeze) last until the map changes; `/cheats off` undoes
  them.

**Naming a player:** `[player]` is a name from `/players`, its number (`#2`), `me` (the host, the default when left
out) or `all` (every hero, players and bots). All copies of the game on one Steam account share a name; use the
number then.

### Turning cheats on and off

| Command | What it does | Example |
|---|---|---|
| `/cheats on` | Turns cheats on for this game. Everyone sees `host enabled cheats`. | `/cheats on` |
| `/cheats off` | Turns them off and undoes god mode, infinite ammo, game speed, freeze, size and the free camera. | `/cheats off` |
| `/cheats` or `/cheats help` | Lists the cheat commands and what is on right now. | `/cheats` |

### Survival

| Command | What it does | Example |
|---|---|---|
| `/god [player\|all] [on\|off]` | God mode: no damage and no death. Without `on`/`off` it toggles (`all` turns it on for everyone). Follows the player onto a new hero (a rescue, a bot take-over) until the map changes. | `/god all`, `/god Mellon off` |
| `/heal [player\|all]` | Full health, trauma included (the lost part of the health bar). Downed heroes need `/revive`. | `/heal all` |
| `/revive [player\|all]` | Gets downed (incapacitated) heroes back up with half their health. Default: everyone. Dead heroes still come back through the rescue closets as usual. | `/revive` |
| `/ammo infinite\|off` | Infinite reserve ammo on every weapon, for everyone (weapons picked up later too). You still reload. | `/ammo infinite` |
| `/copper <+N\|-N> [player\|all]` | Gives (or takes) copper. Default: every hero. Up to 100000 at once. | `/copper +500`, `/copper -100 #2` |
| `/card <card name> [player\|all]` | Adds a gameplay card to a hero's active cards during a mission, as if drawn: it stays for the rest of the run. Name as shown in the game (`amped up`) or its internal row name; spaces and case don't matter. | `/card amped up`, `/card steady aim all` |
| `/card list [filter]` | Lists card names matching the filter (up to 40). | `/card list aim` |

### Movement and camera

| Command | What it does | Example |
|---|---|---|
| `/fly [player\|all]` | Fly mode (move freely up and down with the camera). Players only, not bots. | `/fly`, `/fly #2` |
| `/noclip` | Fly through walls. Your own hero only: another player's game would still stop them at walls. | `/noclip` |
| `/walk [player\|all]` | Back to normal (ends fly and noclip). | `/walk all` |
| `/tp <player\|saferoom\|start>` | Teleports you to a player, to this map's end saferoom, or back to its start saferoom. Entering the end saferoom with the whole team can end the chapter, as walking in would. | `/tp saferoom`, `/tp Mellon` |
| `/tp <player\|all> <player\|me\|saferoom\|start>` | Teleports a player (or everyone) somewhere; each gets their own spot. | `/tp all me`, `/tp #2 start` |
| `/freecam` | A free-flying camera for your own view only; your hero stays where it is. **Press F8 to come back** (the chat doesn't work while it's on). | `/freecam` |
| `/size <0.25-4>` | Your own hero's size (1 = normal). Only you see it. | `/size 2` |

### World and director

These act on the whole mission (only during a mission).

| Command | What it does | Example |
|---|---|---|
| `/horde` | Triggers a horde now. | `/horde` |
| `/director calm\|build\|peak\|fade\|recover` | Forces the AI director's pacing phase: calm (no pressure), build (build-up), peak, peak fade, recover. | `/director peak` |
| `/spawn <type> [1-10]` | Spawns ridden in front of you. Types: `tallboy crusher bruiser reeker exploder retch stinger stalker hocker hag snitcher breaker ogre common`. | `/spawn crusher 2` |
| `/killall` | Kills every ridden in the map (common ridden, specials, mutations). Never players, bots or allies. | `/killall` |
| `/freeze` | Freezes the world except the players (ridden and bots stop). Again to resume. | `/freeze` |
| `/slomo <0.1-5>` | Game speed for everyone (1 = normal). | `/slomo 0.5` |
| `/win` | Ends the mission as a success (the normal end-of-mission flow). | `/win` |
| `/lose` | Ends the mission as a failure (on most difficulties the mission restarts). | `/lose` |

### Your own progression (permanent)

These change **your own save, permanently**. They never touch another player's save.

| Command | What it does | Example |
|---|---|---|
| `/supply <+N>` | Adds supply points to your save (1-100000). | `/supply +1000` |
| `/unlockall` | Unlocks every item in the supply lines you don't have yet (not burn cards, which are consumables, and not DLC items). `/unlockall check` only counts them. | `/unlockall check`, `/unlockall` |

Before the first `/supply` or `/unlockall` of a game session, your save is copied next to itself as
`PlayerProfileSettings-b4bcoop-backup-<date>-<time>.sav` (and `.json`), in
`%LOCALAPPDATA%\Back4Blood\Steam\Saved\SaveGames`; the reply shows the full path. To go back, quit the game and
copy the backup over `PlayerProfileSettings.sav`. If the backup can't be made, nothing is changed.

### Not included, and why

- **Game's own developer cheats**: `God`, `GiveUnlock`, `GiveSupplyPoints`, `Heal` and the other built-in cheat
  commands are empty in the released game, and it never gives a player the engine's cheat manager. The commands above
  are rebuilt from the game's own functions instead.
- **Noclip and size for other players**: collision and size aren't shared over the network, so their own game would
  keep stopping them at walls or at their normal size. Fly works for them.
- **Reviving dead heroes**: dead heroes are rescued from the rescue closets as usual; `/revive` gets downed heroes up.
- **Spawning bosses** (the Abomination, sleepers): they are scripted into their maps and don't work spawned anywhere.

## Add-ons

Add-ons change how the game looks (textures, models, UI), Left 4 Dead style: drop a file in a folder, restart. There
is no in-game browser. Add-ons are **only on your PC**: other players don't need them and don't see them. If you have
a survivor skin add-on, you see it on every survivor wearing that outfit; players without it see the normal outfit.

**Install:** an add-on is one `.pak` file. Put it in the `b4bcoop-addons` folder in the game folder (next to
`Back4Blood.exe`; create the folder if it isn't there). An add-on zip already contains that folder: extract it into
the game folder. Restart the game. **Remove:** delete the `.pak` file.

**On/off and load order:** the game writes `b4bcoop-addons\addonlist.txt`, one line per add-on:
```
holly_magenta.pak=1
holly_green.pak=0
```
`=1` on, `=0` off. New add-ons are added at the bottom, switched on. The game loads them top to bottom: when two
add-ons change the same file, **the one further down wins**. Move lines to change that. `/addons on|off` edits this
file for you. All changes apply the next time the game starts.

**`/addons`**: the list with numbers, the state of each (`on`, `off`, `on after restart`, `off after restart`,
`on, NOT LOADED`) and conflicts:
```
2 add-on(s), load order (a later one wins):
1. Holly magenta portrait 1.0 [on] holly_magenta.pak
2. Holly green portrait 1.0 [on] holly_green.pak
conflict: holly_green.pak overrides holly_magenta.pak (2 file(s))
```
`<#>` in `/addons on|off|info` is that number, the file name or the title (or a unique part of it).

**Cosmetic or gameplay:** b4bcoop looks at the files in every add-on and sorts it into one of two kinds (what the
add-on says about itself doesn't count):
- **cosmetic**: textures, materials, character, weapon and prop models (characters on the game's own skeleton),
  animations, cloth, sounds, UI, effects. Changes only what you see and hear.
- **gameplay**: anything else, e.g. physics assets, data tables, blueprints, skeletons, maps, config files. Could
  change how the game plays, so hosts refuse these by default.

`/addons` shows the kind of each add-on, `/addons info <#>` the first file that made it "gameplay".

**Joining someone with add-ons:** when you join, your game tells the host how many cosmetic and gameplay add-ons you
run and their ids (plus the titles of gameplay ones). The host's `addons_policy` decides:

| `addons_policy=` | Who may join |
|---|---|
| `cosmetic` (default) | Everyone with cosmetic add-ons only. Gameplay add-ons are refused, by name |
| `any` | Everyone, whatever they run |
| `none` | Only players with no add-ons at all |
| `match` | Cosmetic add-ons are free; gameplay add-ons must be exactly the host's (same ids) |

Refused, you see `Could not join: Host allows cosmetic add-ons only; you have gameplay add-ons: <titles>. Switch them
off (/addons off <#>) and restart the game.` (or the `none`/`match` version); the host sees `<name> could not join:
they have gameplay add-ons (<titles>); addons_policy=cosmetic.` Add-ons apply at game start, so switch them off and
restart before joining again. The host's own add-ons don't matter under `cosmetic`, `any` and `none`.

**`/addons players`** (host): every player's add-ons, e.g.
```
add-ons policy: cosmetic
you (host): 0 cosmetic, 0 gameplay
#1 Bob:
  1 cosmetic, 0 gameplay
  cosmetic 33c6d9d8 Walker checker outfit (you have it too)
```
Titles show for gameplay add-ons and for ones you have too; others show their id (`/addons info` on Bob's side shows
the same id). **`/addons policy <x>`** changes the policy until the host quits; `addons_policy=` in `b4bcoop.ini`
keeps it.

Messages (in your chat after the game starts):
- `Add-on conflict: "B" overrides "A". /addons`: both change the same files; B wins (it is further down the list).
  Fine if that's what you want; otherwise switch one off or reorder `addonlist.txt`.
- `Add-ons "A" and "B" mix parts of one asset (may crash): switch one off.`: each add-on replaces a different part of
  the same asset, which can crash the game. Switch one off.
- `N add-on(s) could not be loaded`: `/addons info <#>` says why: `damaged ... download it again`, `not a Back 4
  Blood add-on pak` (made for another game, or not with b4bcoop's tool), `rename it ... to plain letters` (file or
  folder name with accents or other special characters).
- `Add-ons are off: unsupported game build`: your Back 4 Blood version isn't the one this b4bcoop supports.

Only install add-ons from people you trust. Making add-ons: the mod maker's kit (`b4bcoop-modkit-<version>.zip` from
the releases, `modkit/` in the source repository); players don't need it.

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
| `thirdperson` | `0` | `0`, `1` | `1`: start in third person (`/thirdperson`) |
| `thirdperson_key` | `N` | a letter, a digit, a key code, or `off` | The third-person toggle key |
| `thirdperson_distance` | `180` | `50`-`600` | Third-person camera distance (`/thirdperson distance`) |
| `thirdperson_side` | `40` | `-150`-`150` | Over-the-shoulder offset, negative = left (`/thirdperson side`) |
| `thirdperson_height` | `0` | `-100`-`150` | Camera height offset (`/thirdperson height`) |
| `thirdperson_fov` | `0` (game's) | `60`-`130` | Third-person field of view (`/thirdperson fov`) |
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

**`thirdperson`** / **`thirdperson_key`**: your own camera, like `/thirdperson`. `thirdperson=1` starts every game
in third person (`/thirdperson` still switches it). `thirdperson_key` (**N** by default) takes the same values as
`flashlight_key`; `off` turns the key off.
```ini
thirdperson=1
thirdperson_key=0x74
```
(`0x74` = F5.)

**`thirdperson_distance`** / **`thirdperson_side`** / **`thirdperson_height`** / **`thirdperson_fov`**: the camera
settings of `/thirdperson distance|side|height|fov`, from the start of every game. A closer camera over the right
shoulder with a wider view:
```ini
thirdperson_distance=150
thirdperson_side=40
thirdperson_fov=100
```

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
| `addons` | `1` | `0`: load no add-ons at all (troubleshooting) |
| `addons_dir` | `<game>\b4bcoop-addons` | Another add-ons folder, a full Windows path, e.g. `addons_dir=D:\b4b-addons` |
| `addons_policy` | `cosmetic` | Host: which add-ons joiners may have: `cosmetic`, `any`, `none`, `match` (see Add-ons) |

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
| `Host allows cosmetic add-ons only; you have gameplay add-ons: ...` (or `no add-ons`, `the same gameplay add-ons`) | The host's `addons_policy` refuses some of your add-ons | `/addons off <#>` for the ones named, restart the game, join again; or the host sets `/addons policy any` |
| `Server full.` | Every survivor slot is taken by a player (bot slots count as free) | Wait for a free slot, or the host sets `teamsize=5` |
| `This host only accepts their Steam friends. ...` | You're not on the host's Steam friends list | Become Steam friends, or the host adds you with `allow_steamids=` |
| `Could not reach the host over Steam. Is it still hosting? ...` | The host quit, or refused you (not friends) | Check the host is in Fort Hope or a mission; see the line above |
| `You are banned from this session.` | The host banned you | Ask the host to `/unban` you |
| `The host locked the session.` | The host used `/lock` | Ask the host to `/unlock` |
| `You were kicked by the host.` / `You were banned by the host.` | The host removed you | You're back in your own Fort Hope |
| `Joining by IP address is off. ...` | You tried an IP join without `host_ip=1` | Use **Join Game** in Steam or `/join steam:<id>` |
| `Ignored a reward from the host that failed a safety check ...` | Your game rejected a reward outside normal mission limits, to protect your save | Nothing; if it keeps happening, report it with your log |
