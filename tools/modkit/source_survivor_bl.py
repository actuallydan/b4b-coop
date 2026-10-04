"""Blender side of tools/modkit/source_survivor.py: a Source engine character (.mdl + .vvd + .vtx, materials next to
it) imported with SourceIO, turned into a plain rigged glb the modkit's `b4bmod survivor` reads like any downloaded
model. Run headless with SourceIO on BLENDER_USER_SCRIPTS:

    blender -b --factory-startup --python source_survivor_bl.py -- <model.mdl> <out.glb> [--keep-flex]

What it changes on the way (SourceIO keeps the Source look; glTF and the modkit want plain PBR):
  - attachment empties and the eyeball helper empties are removed after use; only the highest-detail LOD is imported
    (SourceIO imports LOD0); materials no face uses (skin variants such as `*_it`) are dropped.
  - every material becomes a Principled BSDF with its $basetexture (alpha kept only for $alphatest/$translucent
    materials: Source keeps phong/envmap masks in the alpha of opaque textures, which would read as hair cards) and its
    $bumpmap (SourceIO already flipped green to OpenGL; its alpha, a phong mask, dropped).
  - `<who>_body` / `<who>_color` materials are renamed `<who>_clothes` (they are the clothes; "body" would read as
    skin to the slot placement).
  - eyes: Source projects the iris onto the eyeball in the shader (the mesh has no usable UVs). The projection
    (eyeball origin, forward/up, iris size from the .mdl) is baked into UVs plus a new iris texture laid out to them.
    Eye bones `eye_L` / `eye_R` (none in Source rigs) are added at the eyeball origins with the eyeballs on them.
  - face flexes renamed so the modkit's face rigger finds them: the largest jaw-drop flex (FACS AU27/AU26, "Z"
    variant first) -> `jaw_open`, the upper-eyelid closer (eyelid flex f01, the one that moves the lids down) ->
    `blink`; all other flexes removed (they would only be dropped by the fit).
"""
import bpy, addon_utils, os, re, sys
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
MDL, OUT = os.path.abspath(argv[0]), os.path.abspath(argv[1])
KEEP_FLEX = "--keep-flex" in argv
TEXDIR = os.path.splitext(OUT)[0] + "_textures"
os.makedirs(TEXDIR, exist_ok=True)


def log(m):
    print(f"source_survivor: {m}", flush=True)


bpy.ops.wm.read_factory_settings(use_empty=True)
if not addon_utils.enable("SourceIO", default_set=True, handle_error=None):
    sys.exit("source_survivor: SourceIO not found on BLENDER_USER_SCRIPTS")
d, n = os.path.split(MDL)
r = bpy.ops.sourceio.mdl(filepath=MDL, directory=d + "/", files=[{"name": n}], discover_resources=True,
                         write_qc=False, import_textures=True, bodygroup_grouping=False, import_physics=False,
                         use_bvlg=False)
if r != {"FINISHED"}:
    sys.exit(f"source_survivor: SourceIO import failed: {r}")
dg = bpy.context.evaluated_depsgraph_get()
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
log(f"imported {len(meshes)} meshes, {len(arm.data.bones)} bones")


def img_array(im):
    w, h = im.size
    a = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(a)
    return a.reshape(h, w, 4)


def save_png(name, arr, data=False):
    h, w, _ = arr.shape
    im = bpy.data.images.new(name, w, h, alpha=True, float_buffer=False, is_data=data)
    im.pixels.foreach_set(np.ascontiguousarray(arr, np.float32).ravel())
    p = os.path.join(TEXDIR, name + ".png")
    im.filepath_raw = p
    im.file_format = "PNG"
    im.save()
    im.filepath = p
    if data:
        im.colorspace_settings.name = "Non-Color"
    return im


def node_image(m, name):
    nd = m.node_tree.nodes.get(name) if m.node_tree else None
    return nd.image if nd is not None and getattr(nd, "image", None) is not None and nd.image.size[0] > 1 else None


def plain_material(name, base, normal, alpha):
    nm = bpy.data.materials.new(name)
    nm.use_nodes = True
    nt = nm.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.7
    if base is not None:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = base
        nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
        if alpha:
            nt.links.new(t.outputs["Alpha"], bsdf.inputs["Alpha"])
            nm.blend_method = "HASHED" if hasattr(nm, "blend_method") else None
    if normal is not None:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = normal
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(t.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return nm


def eye_uvs(o, mi, m, name):
    """Bake Source's iris projection: UV = 0.5 + (p - origin) . (right, up) / iris size, then squeezed into 0..1
    with a matching texture, so atlas packing can't bleed. Each face is projected from the eyeball it sits on (the
    nearest eyeball origin): some models put a few faces of one eye on the other eye's material."""
    iris = node_image(m, "$Iris")
    eyes = []
    for e in bpy.data.objects:
        key = e.name + "_iris_scale"
        if e.type == "EMPTY" and key in o:
            Mw = dg.objects[e.name].matrix_world if e.name in dg.objects else e.matrix_world
            eyes.append((Mw.translation.copy(), Mw.inverted(), float(o[key])))   # iris diameter, Source units
    if not eyes or iris is None:
        log(f"{name}: no eyeball data, left as is"); return None
    W = o.matrix_world
    me = o.data
    uv = me.uv_layers.active.data
    proj, front = {}, []
    for p in me.polygons:
        if p.material_index != mi:
            continue
        c = W @ p.center
        loc, Minv, size = min(eyes, key=lambda e: (e[0] - c).length)
        for li in p.loop_indices:
            local = Minv @ (W @ me.vertices[me.loops[li].vertex_index].co)   # eyeball space (X right, Y fwd, Z up)
            a, b = 0.5 + local.x / size, 0.5 + local.z / size
            proj[li] = (a, b)
            if local.y > 0:                     # the half that shows decides the squeeze; the back is clamped
                front.append(max(abs(a - 0.5), abs(b - 0.5)))
    if not proj:
        return None
    r = max(front) if front else 0.5
    s = min(1.0, 0.49 / r) if r > 0 else 1.0
    for li, (a, b) in proj.items():
        uv[li].uv = (min(0.995, max(0.005, 0.5 + (a - 0.5) * s)), min(0.995, max(0.005, 0.5 + (b - 0.5) * s)))
    size = eyes[0][2]
    # texture in the squeezed layout: pixel (x, y) -> iris UV 0.5 + (t - 0.5) / s, clamped (the iris image's rim is sclera)
    src = img_array(iris)
    h, w, _ = src.shape
    N = 256
    t = (np.arange(N) + 0.5) / N
    u = np.clip(0.5 + (t - 0.5) / s, 0, 1)
    xs = np.clip((u * w).astype(int), 0, w - 1)
    ys = np.clip((u * h).astype(int), 0, h - 1)
    out = src[ys][:, xs].copy()
    out[..., 3] = 1.0
    log(f"{name}: iris projected (size {size:.3f} units, squeeze {s:.2f})")
    return save_png(name + "_iris", out)


# ---- materials
new_mats = {}
done = {}       # SourceIO material -> plain one
for o in meshes:
    me = o.data
    used = {p.material_index for p in me.polygons}
    for mi, m in enumerate(me.materials):
        if m is None or mi not in used:
            continue
        if m in done:
            me.materials[mi] = done[m]; continue
        src_name = os.path.basename(m.name.replace("\\", "/"))      # SourceIO keeps the .mdl's path in some names
        m.name = src_name + "__sourceio"
        vmt = m.get("vmt_parameters", {})
        vmt = vmt.to_dict() if hasattr(vmt, "to_dict") else dict(vmt)
        alpha = any(vmt.get(k, "0") not in ("0", 0) for k in ("$alphatest", "$translucent"))
        if "$iris" in vmt:
            base = eye_uvs(o, mi, m, src_name)
            nm = plain_material(src_name, base, None, False)
        else:
            b = node_image(m, "$basetexture")
            nrm = node_image(m, "$bumpmap")
            base = normal = None
            if b is not None:
                a = img_array(b)
                if not alpha:
                    a[..., 3] = 1.0
                base = save_png(src_name + "_color", a)
            if nrm is not None and nrm != b:
                a = img_array(nrm); a[..., 3] = 1.0
                normal = save_png(src_name + "_normal", a, data=True)
            nm = plain_material(src_name, base, normal, alpha)
            log(f"{src_name}: base {b.name if b else None}, normal {nrm.name if nrm else None}, alpha {'kept' if alpha else 'dropped'}")
        # names the modkit's slot placement reads right: Source "<who>_body" / "<who>_color" textures are the outfit
        # (clothes, with the hands on them), not skin
        if not re.search(r"head|hair|eye|teeth|tongue|skin", src_name, re.I) and re.search(r"body|color", src_name, re.I):
            nm.name = re.sub(r"_?(body|colou?r)", "", src_name, flags=re.I) + "_clothes"
            log(f"{src_name}: named {nm.name}")
        new_mats[src_name] = nm
        done[m] = nm
        me.materials[mi] = nm
    # drop slots no face uses
    for mi in sorted(set(range(len(me.materials))) - used, reverse=True):
        o.active_material_index = mi
        me.materials.pop(index=mi)
for m in list(bpy.data.materials):
    if m not in new_mats.values():
        bpy.data.materials.remove(m)

# ---- flexes
for o in meshes:
    sk = o.data.shape_keys
    if not sk or KEEP_FLEX:
        continue
    kb = sk.key_blocks
    base = np.empty(len(o.data.vertices) * 3); kb[0].data.foreach_get("co", base); base = base.reshape(-1, 3)

    def move(k):
        a = np.empty(base.size); k.data.foreach_get("co", a); return a.reshape(-1, 3) - base

    jaw = next((kb[n] for n in ("AU27ZL", "AU27Z", "AU26ZL", "AU26Z", "AU27L", "AU27", "AU26L", "AU26") if n in kb), None)
    blink = None
    # the upper-lid closer: named so (f01-upperLidLowerer) or the first eyelid flex (f01 / frame01) when it moves down
    cands = [k for k in kb if re.search(r"upperlidlower", k.name, re.I)] + \
            [k for k in kb if re.match(r"(f|frame)0?1($|[^0-9])", k.name, re.I)]
    for k in cands:
        dz = move(k)[:, 2]
        if dz.min() < -1e-4 and -dz.min() > dz.max():
            blink = k; break
    keep = {kb[0].name}
    if jaw is not None:
        log(f"{o.name}: flex {jaw.name} -> jaw_open"); jaw.name = "jaw_open"; keep.add("jaw_open")
    if blink is not None:
        log(f"{o.name}: flex {blink.name} -> blink"); blink.name = "blink"; keep.add("blink")
    for k in list(kb):
        if k.name not in keep:
            o.shape_key_remove(k)

# ---- eye bones: Source turns the eyeballs in the shader, so the rig has none. The face rigger finds eyes most
# reliably from eye bones (a hair bun or hoop earrings throw off its guess from the head's bounds), and the eyeball
# vertices go whole to them, as the survivors' own eyes are on eyeball bones.
eye_objs = [e for e in bpy.data.objects if e.type == "EMPTY" and any(e.name + "_iris_scale" in o for o in meshes)]
if eye_objs:
    eye_info = []
    for e in eye_objs:
        Mw = dg.objects[e.name].matrix_world if e.name in dg.objects else e.matrix_world
        con = next((c for c in e.constraints if c.type == "CHILD_OF"), None)
        fwd = (Mw.to_3x3() @ Vector((0, 1, 0))).normalized()
        eye_info.append((Mw.translation.copy(), fwd, con.subtarget if con else None))
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    Ainv = arm.matrix_world.inverted()
    names = []
    for c, fwd, parent in eye_info:
        nm = "eye_L" if (Ainv @ c).x > 0 else "eye_R"           # the model looks down -Y: +X is its left
        b = arm.data.edit_bones.new(nm)
        b.head = Ainv @ c
        b.tail = Ainv @ (c + fwd * 0.02)
        if parent and parent in arm.data.edit_bones:
            b.parent = arm.data.edit_bones[parent]
        names.append((nm, c))
    bpy.ops.object.mode_set(mode="OBJECT")
    for o in meshes:
        me = o.data
        eye_mi = {i for i, m in enumerate(me.materials) if m is not None and re.search(r"eye", m.name, re.I)}
        vs = {v for p in me.polygons if p.material_index in eye_mi for v in p.vertices}
        if not vs:
            continue
        groups = {nm: (o.vertex_groups.get(nm) or o.vertex_groups.new(name=nm)) for nm, _ in names}
        W = o.matrix_world
        for v in vs:
            p = W @ me.vertices[v].co
            nm = min(names, key=lambda x: (x[1] - p).length)[0]
            for g in list(me.vertices[v].groups):
                o.vertex_groups[g.group].remove([v])
            groups[nm].add([v], 1.0, "REPLACE")
        log(f"{o.name}: eye bones {', '.join(n for n, _ in names)}, {len(vs)} eyeball vertices on them")

# ---- clean up: attachments and eyeball empties go; one armature + its meshes stay
for o in list(bpy.data.objects):
    if o.type not in ("MESH", "ARMATURE"):
        bpy.data.objects.remove(o, do_unlink=True)
for o in meshes:
    for k in list(o.keys()):
        del o[k]
    if o.animation_data:
        o.animation_data_clear()
    o.name = os.path.splitext(n)[0] + ("" if len(meshes) == 1 else f"_{meshes.index(o)}")

bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB", export_skins=True, export_morph=True,
                          export_animations=False, export_yup=True, export_image_format="AUTO",
                          export_extras=False)
log(f"wrote {OUT}")
