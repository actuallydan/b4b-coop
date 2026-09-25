# Survivor looks: `/model`

Change how your survivor looks for this game session, using outfits and characters the game already has (or outfits
your add-ons add).
Nothing is saved: your profile, your unlocked outfits and your campaign runs stay as they are. Quit the game (or
`/model reset`) and you look like before.

## Commands

| Command | Who | What it does |
|---|---|---|
| `/model list` | everyone | The survivors and how many looks each has |
| `/model list <survivor>` | everyone | That survivor's outfits, heads, torsos and legs |
| `/model list npc` | everyone | Fort Hope NPCs, other survivors and cultists you can wear |
| `/model list outfits` | everyone | Outfits your add-ons add (none without such add-ons) |
| `/model <name>` | everyone | You now look like that |
| `/model reset` | everyone | Back to your own look (from your profile) |
| `/model` | everyone | What you wear now, and your current `/model` |
| `/model <player> <name>` | host | Changes another player's or a bot's look; everyone is told |
| `/model <player> reset` | host | That player or bot back to their own look |
| `/models off` | host | No more model swaps for anyone; swapped looks go back to normal |
| `/models on` | host | Model swaps allowed again (the default) |

`<player>` is the number from `/players` or a name, as for `/kick`.

## Names

- **A whole survivor**: `/model holly`, `/model walker`, `/model doc` ... = that survivor's default outfit on your
  survivor. The 12 names: `evangelo walker holly mom doc hoffman jim karlee heng sharice dan tala`.
- **One outfit**: `<survivor>_elite_<nn>`, e.g. `/model karlee_elite_03`, `/model walker_elite_07`. Outfits named
  `..._v2`, `_v3` are colour variants of the same outfit.
- **One piece**: `<survivor>_head_<nn>`, `_torso_<nn>`, `_legs_<nn>`, e.g. `/model holly_head_03`. Pieces add up:
  `/model holly_head_03` then `/model walker_legs_01` gives you both, on top of your own torso. An outfit replaces
  them all.
- **An NPC**: `/model vanessa`, `/model emmett`, `/model survivor_f_03`, `/model guard_m_01`, `/model pow_male_02`,
  `/model cultistsniper` ... (`/model list npc`).

- **An add-on outfit**: add-ons can add outfits (made with the mod maker's kit, `b4bmod survivor --as <name>`).
  `/model list outfits` lists yours, e.g. `/model casual_joe`. Works on any survivor.

`/model list holly` shows names without the survivor in front: `elite_04` there is `/model holly_elite_04`.

## Examples

```
/model list
/model list karlee
/model karlee_elite_05
/model holly_head_03
/model walker_legs_01
/model emmett
/model reset
/model 2 doc_elite_03        (host: the player or bot #2 from /players)
/models off                  (host)
```

## What others see

- **Survivor outfits and pieces**: everyone sees them, also players without b4bcoop. You keep your own survivor's
  voice, perks, name and portrait; only the body changes, in third person and in your first-person arms.
- **NPC looks**: only players with b4bcoop see them. Players without it see your own survivor.
- **Add-on outfits**: only players who have the same add-on see them (third person and your first-person arms).
  Everyone else sees your survivor in their base pieces (head, torso and legs of your profile), not your outfit.
- Your look stays through map changes, chapters, death and respawn, until `/model reset` or the end of the game
  session. If you change survivor at character select, your `/model` is put on the new survivor.
- Bots: only the host can change them (`/model <bot #> <name>`). When a player takes over that bot, the player's own
  look (or their own `/model`) is used.

## Replies

- `you now look like karlee_elite_05 (/model reset to undo)`
- `no model 'xyz' (/model list)`
- `models are off (/models on)`: the host turned swaps off.
- `(an add-on outfit: players without that add-on see your survivor)`: after `/model <add-on outfit>`.
- `The host turned model swaps off for add-on outfits (addons_policy=none).` (client): the host allows no add-ons.
- `The host turned model swaps off (/models).` (client): the host refused your look; after 3 tries:
  `The host did not accept your model ...`.
- `[host] Holly now looks like doc_elite_03`: the host changed a player's or a bot's look.

## Limits

- Besides add-on outfits, only looks the game ships: survivor outfits (all of them, also ones you haven't unlocked) and NPC bodies that use
  the survivors' skeleton. Ridden and special NPCs (Hag, Sleeper, Titan) use other skeletons and are not offered.
- NPC bodies have no first-person arms: you keep your survivor's arms.
- Add-on outfits can't be picked in the customization screen, only with `/model`. The host's `/models off` also
  turns them off.
- `/models off` stops swaps between survivors and NPC looks. It can't tell a player's own outfit change from a
  `/model` of one of their own survivor's outfits, so those stay allowed.
- Mixing pieces of different survivors (a head from one, legs from another) can show seams.
