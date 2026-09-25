# Textures and materials

Repaint a survivor, a weapon or a UI image, or change a material's colours and values, without the Unreal editor.
Setup first: the kit's README.md (`b4bmod setup`, the AES key). Commands below are written as `b4bmod ...`: on Windows
run `b4bmod.cmd ...` from the kit folder (or add the folder to PATH), on Linux `./b4bmod.sh ...`.

## 1. Find the asset
Every game file path is in the pak indexes, which `find` reads offline (the first run indexes ~290,000 files in a few
seconds, then it's cached):
```
b4bmod find "Heroes/Walker/.*_SKM$"                      Walker's meshes: heads, torsos, legs, outfits (Elite_NN)
b4bmod find "Heroes/Walker/Meshes/Elite/Elite_00/.*_T$"   textures of one outfit
b4bmod find "Weapons/Pistol/HG01/.*_T$"                   a weapon's textures (default + skins)
```
Always put the search text in double quotes (`|`, `$`, `*` mean something to the command line otherwise).

| What | Path | Notes |
|---|---|---|
| Survivors (Walker, Holly, Mom, Doc, Hoffman, Evangelo, Jim, Karlee) | `/Game/Characters/Heroes/<Name>/Meshes/` | `Base/Heads`, `Base/Torsos`, `Base/Legs/<Piece>_NN/` (pieces), `Elite/Elite_NN/` (outfits), `Shared/` (hair, eyes, lashes) |
| Later outfits and survivors (Heng, Sharice, Tala, Dan) | `/Game/TU07`, `TU09`, `TU11`, `TU13`, `TU15` `/Characters/Heroes/<Name>/` | same layout |
| Each piece/outfit | `<folder>/3P_<Hero>_<Piece>_SKM` (third person), `FP_..._SKM` (first-person arms), `Materials/*_MI`, `Textures/*_T` | variants A/B/C = colour variants (own materials + textures) |
| Weapons | `/Game/Items/Weapons/<Class>/<Code>/` (`Pistol/HG01..05`, `Assault/AR01..06`, `SMG/SMG01..05`, `Shotgun/SG01..05`, `LMG/LMG01..02`, `Sniper/SNI01..03`, `MachineGun/MG01`, `Bow/Bow01`, `Melee/*`, `Knife/Knife01..12`); later skins under `/Game/TUxx/Items/Weapons/...` | `Meshes/<Code>_SKM`, `Textures/` (default look), `Skin_Sets/Skin_Default/*_MI`, `Skin_Sets/Skins_*/Skin_<Name>/{Materials,Textures}` (unlockable skins) |
| Which gun a code is | `/Game/UI/Textures/Common/Items/Weapons/Icon_Weapon_<Code>` | export the icon to PNG and look |

Texture name endings: `_BC_T` base colour (sRGB), `_N_T` normal map (only red/green are stored), `_PBR_T` packed
roughness/metal/occlusion-style masks (linear), `_MSK_T` / `_MM_T` masks, `_ID_T`, `_DMG_T`. `b4bmod info <texture>`
tells the format, size and whether it is sRGB.

## 2. See what a mesh uses
```
b4bmod tree /Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/3P_Walker_Elite_00_SKM
  .../Materials/Walker_Elite_00_A_Arms_LOD_MI  [MaterialInstanceConstant]
    parent: .../Materials/Walker_Elite_00_A_Arms_MI  [MaterialInstanceConstant]
      'Base Color' = .../Textures/Walker_Elite_00_A_Arms_BC_T  [Texture2D]  PF_DXT1 2048x2048
      'Normal Map' = .../Textures/Walker_Elite_00_A_Arms_N_T  [Texture2D]  PF_BC5 2048x2048
      ...
```
Commands extract what they need from the game on their own (into the extract folder, see README.md); `tree` also
extracts every material and texture it lists. To get a whole folder at once:
`b4bmod extract "/Game/Characters/Heroes/Walker/Meshes/Elite/Elite_00/*"` (`*` includes subfolders).

## 3. Paint a texture
```
b4bmod export /Game/.../Walker_Elite_00_A_Body_BC_T body.png      the original as a PNG (mip 0)
   ... edit body.png in any paint program, keep the UV layout ...
b4bmod texture /Game/.../Walker_Elite_00_A_Body_BC_T body.png -o mymod
```
- Output: `mymod/Gobi/Content/.../Walker_Elite_00_A_Body_BC_T.uasset/.uexp/.ubulk`: the game's texture in its original
  pixel format with a full mip chain.
- Size: your PNG's size is used (power-of-two sides, e.g. 1024x1024, 2048x2048). Smaller saves memory; `--resize`
  scales to the original size instead.
- `--quality fast|balanced|best` (encoder effort; a 4096x4096 BC7 texture takes seconds at `balanced`).
- Normal maps: DirectX convention (green = +Y as in Unreal). Normal maps from Blender/OpenGL tools need the green
  channel inverted. Only red and green are stored; blue is rebuilt.
- Packed masks (`_PBR_T`, `_MSK_T`): export the original first and keep its channel layout (which channel holds what
  is up to the game's material; compare the original's channels).

## 4. Change a material instance
```
b4bmod mi /Game/.../Walker_Elite_00_A_Head_MI                         list its parameters
b4bmod mi /Game/.../Walker_Elite_00_A_Head_MI ^
    set "Roughness Multiply" 0.5 ^
    set "Base Color" /Game/Characters/Heroes/Mom/Meshes/Elite/Elite_00/Textures/Mom_Elite_00_A_Head_BC_T ^
    set "Detail Colortint (Pores)" 1,0.2,0.2 ^
    -o mymod
```
(`^` continues a line in the Windows command prompt; `\` on Linux, `` ` `` in PowerShell.)
Values: a number (scalar), `r,g,b[,a]` (colour, linear 0-1), a `/Game/...` texture path or `none` (texture);
`parent <path>` re-parents. A parameter the material instance doesn't set yet is added (its parent must have it:
`b4bmod mi <parent>` shows the names). Static switches can't be changed (they select compiled shaders).
Edits read the asset from `-o <moddir>` if it is already there, so several commands build one mod.

## 5. Pack, install, test
```
b4bmod pack mymod -o walker_red.pak --title "Walker red jacket" --author you --version 1.0 --category survivors --zip
b4bmod install walker_red.pak
```
Start the game, type `/addons` in chat to see it loaded, and look at the survivor (character customization shows the
outfit). Details and sharing: addons.md.

## Limits
- Only textures that exist are replaced (same path). A texture under a new path works as a material reference only if
  it is inside the add-on (not tested yet).
- Weapon skins: `Skin_Sets/<set>/Textures/` hold each skin's own textures; the default look is `<Code>/Textures/`.
  FP (first person) and 3P (world model) materials are separate.
- Not supported: static switches, new master materials, virtual textures, cubemaps, texture arrays. Pixel formats:
  BC1/BC3/BC4/BC5/BC7/BGRA8/G8 (all hero and weapon textures seen use BC1, BC5 or BC7).
- Your add-on is only on your PC: others see the normal look.

Verified in game (Proton): repainted outfits (third person and first-person arms), 61 weapon textures, a texture
shrunk from 2048 to 1024, and a head material with changed values and a texture from another survivor.
Evidence: docs/investigations/texture-mods.md in the b4b-coop repository.
