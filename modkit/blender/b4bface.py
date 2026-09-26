"""Face rig for survivor models fitted by b4bfit.py (3P): the game animates faces with face bones (jaw, lips, lids,
brows, cheeks, ... under `face_master`), driven by each hero's additive face pose asset (lip-sync visemes,
expressions) and a blink animation; there are no morph targets. So a custom head talks and blinks when

  1. its face is skinned to those bones like the survivor's face is (weights copied from the template head through a
     warp that lays the model's face onto the template's: eyes onto eyes, mouth onto mouth), and
  2. the bones sit on the model's face (their bind positions are moved onto it; the importer writes them into the
     mesh's reference skeleton). The skeleton retargets face bones with "OrientAndScale", so the game's animations
     keep them where this mesh puts them and the additive poses rotate/shift them in place.

Landmarks on the model, best source first: its own face bones (Rigify, VRoid, Mixamo/CC eyes, a jaw bone), shape keys
(a mouth-open key gives the lip line, a blink key the lids), mesh islands shaped like eyeballs, the front profile of the
face along the middle (nose, lip crease, chin), else the template's own landmarks scaled onto the model's head.
Used by b4bfit.py (`character --mode 3p`, `--face auto|off`); how it works: docs/investigations/mesh-mods.md §12
(b4b-coop repository).
"""
import bmesh, math, os, re
from mathutils import Vector, Matrix
from mathutils.kdtree import KDTree

DEBUG = bool(os.environ.get("B4B_FACE_DEBUG"))

# model materials: which ones may take face weights
HAIR_RX = re.compile(r"hair|fur\b|ponytail|afro|bangs?\b|fringe|braid|\bbun\b|mane|wig|sideburn|beard|mustache|"
                     r"moustache", re.I)
OVERLAY_RX = re.compile(r"lash|brow(?!n)|eyeline|eye_?liner", re.I)
GEAR_RX = re.compile(r"hat\b|cap\b|helmet|glass|goggle|mask|hood|visor|headphone|earring|jewel|crown|horn", re.I)
EYE_RX = re.compile(r"eye(?!_?(line|lash|brow|shadow|liner|extra|highlight))|iris|cornea|pupil|sclera", re.I)
MOUTH_RX = re.compile(r"teeth|tooth|tongue|gum|mouth(?!.*skin)", re.I)
HIGHLIGHT_RX = re.compile(r"highlight|extra|spec", re.I)
CLOTH_RX = re.compile(r"jacket|coat|collar|shirt|tops?\b|outfit|cloth|scarf|vest\b|hoodie|sweater|dress|robe|cape|"
                      r"cloak|armou?r|uniform", re.I)

# shape keys: (role, regex on the key name)
SK_RULES = [("jaw", re.compile(r"jaw_?open|mouth_?open|open_?mouth|mth_a$|(^|[._ ])(a|aa|ah)$|v_aa$|viseme_?aa$", re.I)),
            ("blink", re.compile(r"blink|eyes?_?clos|eye_?close|close_?eyes?", re.I))]

# source bones: (landmark, regex, which end: head/tail)
SRC_BONE_RULES = [
    ("eye", re.compile(r"(^|[^a-z])(org-|def-|mch-)?eye(ball)?([^a-z]|$)|faceeye$|_eye$", re.I), "head"),
    ("jaw", re.compile(r"(^|[^a-z])(def-|org-)?jaw([^a-z]|$)", re.I), "head"),
    ("chin", re.compile(r"^(def-|org-)?chin$", re.I), "mid"),
    ("lip_t", re.compile(r"^(def-|org-)?lip\.t\.[lr]$", re.I), "head"),          # Rigify: lip.T.L runs middle -> corner
    ("lip_b", re.compile(r"^(def-|org-)?lip\.b\.[lr]$", re.I), "head"),
    ("lip_corner", re.compile(r"^(def-|org-)?lip\.t\.[lr]\.001$", re.I), "tail"),
]


def side_of(name):
    n = name.lower()
    if re.search(r"(^|[._\- ])(l|left)([._\- ]|$)|left|_l_|\.l$|_l$", n): return "l"
    if re.search(r"(^|[._\- ])(r|right)([._\- ]|$)|right|_r_|\.r$|_r$", n): return "r"
    return None


# ---- capture (before b4bfit changes the model) --------------------------------------------------------------------

def gltf_morph_names(path):
    """{glTF mesh name: [morph target names]} of a .glb/.gltf/.vrm (Blender's importer calls them target_N when the
    names sit on the primitive), with the VRM presets (a / aa, blink, blink_l / blinkLeft ...) as the names of the
    targets they drive."""
    import json, struct
    try:
        raw = open(path, "rb").read()
        if raw[:4] == b"glTF":
            n = struct.unpack("<I", raw[12:16])[0]
            j = json.loads(raw[20:20 + n])
        else:
            j = json.loads(raw)
    except (OSError, ValueError, struct.error):
        return {}
    out = {}
    meshes = j.get("meshes", [])
    for m in meshes:
        prims = m.get("primitives") or [{}]
        names = (m.get("extras") or {}).get("targetNames") or (prims[0].get("extras") or {}).get("targetNames") or []
        count = len(prims[0].get("targets", []))
        out[m.get("name", "")] = [names[i] if i < len(names) else f"target_{i}" for i in range(count)]
    ext = j.get("extensions", {})
    preset = {"a": "A", "aa": "A", "blink": "Blink", "blink_l": "Blink_L", "blinkleft": "Blink_L", "blink_r": "Blink_R",
              "blinkright": "Blink_R"}
    for g in ext.get("VRM", {}).get("blendShapeMaster", {}).get("blendShapeGroups", []):
        nm = preset.get((g.get("presetName") or "").lower())
        for b in g.get("binds", []) if nm else []:
            mi, ti = b.get("mesh", -1), b.get("index", -1)
            if 0 <= mi < len(meshes) and 0 <= ti < len(out[meshes[mi].get("name", "")]):
                out[meshes[mi].get("name", "")][ti] = nm
    nodes = j.get("nodes", [])
    for k, e in ext.get("VRMC_vrm", {}).get("expressions", {}).get("preset", {}).items():
        nm = preset.get(k.lower())
        for b in e.get("morphTargetBinds", []) if nm else []:
            mi = nodes[b["node"]].get("mesh", -1) if 0 <= b.get("node", -1) < len(nodes) else -1
            ti = b.get("index", -1)
            if 0 <= mi < len(meshes) and 0 <= ti < len(out[meshes[mi].get("name", "")]):
                out[meshes[mi].get("name", "")][ti] = nm
    return out


def capture_shape_keys(meshes, names=None):
    """Before shape keys are removed: per vertex, how far a mouth-open / blink key moves it, stored as float vertex
    attributes b4b_face_<role> (the fit keeps vertex order). names: gltf_morph_names() of the source file.
    Returns {role: [key names]}."""
    found = {}
    for m in meshes:
        sk = m.data.shape_keys
        if not sk or len(sk.key_blocks) < 2: continue
        basis = sk.reference_key
        best = {}
        tn = (names or {}).get(re.sub(r"\.\d{3}$", "", m.data.name)) or []
        for ki, kb in enumerate(sk.key_blocks):
            if kb == basis: continue
            kname = tn[ki - 1] if re.match(r"target_\d+$", kb.name) and 0 < ki <= len(tn) else kb.name
            role = next((r for r, rx in SK_RULES if rx.search(kname)), None)
            if role is None: continue
            dv = [kb.data[i].co - basis.data[i].co for i in range(len(kb.data))]
            # blink: signed (down = upper lid, up = lower lid; models are Z-up after import); jaw: how far, negative
            # where it lifts (a mouth-open key also raises the upper lip a little: that is not the jaw side)
            d = [x.length * (-1.0 if x.z < 0 else 1.0) for x in dv] if role == "blink" else \
                [x.length * (-1.0 if x.z > 0.3 * x.length else 1.0) for x in dv]
            tot = sum(abs(x) for x in d)
            if tot <= 1e-9: continue
            # blink: both-eye keys or one per side (summed); jaw: the key that moves the most
            if role == "blink" and side_of(kname):
                best.setdefault(("blink", side_of(kname)), (tot, kname, d, ki))
            elif tot > best.get((role, None), (0,))[0] or (role == "jaw" and re.fullmatch(r"a|aa", kname, re.I)):
                best[(role, None)] = (tot, kname, d, ki)
        for role in ("jaw", "blink"):
            parts = [best[k] for k in best if k[0] == role]
            if role == "blink" and (role, None) in best: parts = [best[(role, None)]]
            if not parts: continue
            vals = [sum(p[2][i] for p in parts) for i in range(len(m.data.vertices))]
            a = m.data.attributes.get(f"b4b_face_{role}") or m.data.attributes.new(f"b4b_face_{role}", "FLOAT", "POINT")
            a.data.foreach_set("value", vals)
            found.setdefault(role, []).extend(p[1] for p in parts)
            if role == "blink":
                # the whole move (world space) and where it starts: fit_blink() maps it through the fit and places the
                # eyelid pivots so the game's lid rotation reproduces it
                R3 = m.matrix_world.to_3x3()
                vec = [Vector() for _ in m.data.vertices]
                for p in parts:
                    kb = sk.key_blocks[p[3]]
                    for i in range(len(vec)): vec[i] += R3 @ (kb.data[i].co - basis.data[i].co)
                for nm, vals3 in (("b4b_face_blinkv", vec),
                                  ("b4b_face_src", [m.matrix_world @ basis.data[i].co for i in range(len(vec))])):
                    a = m.data.attributes.get(nm) or m.data.attributes.new(nm, "FLOAT_VECTOR", "POINT")
                    a.data.foreach_set("vector", [x for v in vals3 for x in v])
    return found


EYES_GIVEN = {}             # --face_eyes: {index: source point}, picked vertices carry b4b_face_eyepick = index + 1


def capture_eye_points(meshes, spec, log=print):
    """--face_eyes "x,y,z;x,y,z": the eyes' positions on the model as Blender imports the file (metres, Z up; e.g.
    the 3D cursor snapped onto each pupil). The nearest vertices are tagged so the points follow the fit."""
    import numpy as np
    pts = []
    for part in spec.replace(" ", "").split(";"):
        try:
            x, y, z = (float(t) for t in part.split(","))
        except ValueError:
            raise SystemExit(f"--face-eyes {spec!r}: write two points as x,y,z;x,y,z (Blender coordinates, metres)")
        pts.append(Vector((x, y, z)))
    if len(pts) != 2: raise SystemExit(f"--face-eyes {spec!r}: give both eyes (x,y,z;x,y,z)")
    allv = [(m, i, m.matrix_world @ v.co) for m in meshes for i, v in enumerate(m.data.vertices)]
    if not allv: return
    P = np.array([tuple(q) for _, _, q in allv])
    for k, pt in enumerate(pts):
        d = np.linalg.norm(P - np.array(tuple(pt)), axis=1)
        near = np.argsort(d)[:40]
        if d[near[0]] > 0.05 * max(1e-6, np.ptp(P[:, 2])):
            log(f"face: --face-eyes point {k + 1} ({pt.x:.3f}, {pt.y:.3f}, {pt.z:.3f}) is far from the model's surface "
                f"({d[near[0]]:.3f}): check the coordinates (Blender, metres, as the file imports)")
        EYES_GIVEN[k] = pt
        for j in near:
            m, i, q = allv[j]
            for nm, typ in (("b4b_face_eyepick", "FLOAT"), ("b4b_face_eyesrc", "FLOAT_VECTOR")):
                if m.data.attributes.get(nm) is None: m.data.attributes.new(nm, typ, "POINT")
            m.data.attributes["b4b_face_eyepick"].data[i].value = k + 1
            m.data.attributes["b4b_face_eyesrc"].data[i].vector = q


def given_eyes(meshes, F):
    """The --face_eyes points after the fit (face frame): {side: point}."""
    out = {}
    for k, pt in EYES_GIVEN.items():
        src, dst = [], []
        for m in meshes:
            a = m.data.attributes.get("b4b_face_eyepick")
            if a is None: continue
            b = m.data.attributes["b4b_face_eyesrc"]
            for i in range(len(a.data)):
                if round(a.data[i].value) == k + 1:
                    src.append(Vector(b.data[i].vector)); dst.append(m.matrix_world @ m.data.vertices[i].co)
        if not src: continue
        # rotation + uniform scale + offset from the tagged vertices (Kabsch), the point mapped with it
        import numpy as np
        S = np.array([tuple(v) for v in src]); D_ = np.array([tuple(v) for v in dst])
        cs, cd = S.mean(0), D_.mean(0)
        U, sv, Vt = np.linalg.svd((S - cs).T @ (D_ - cd))
        R = (U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt).T
        k_ = np.linalg.norm(D_ - cd) / max(1e-9, np.linalg.norm(S - cs))
        q = F.loc(Vector(tuple(cd + k_ * R @ (np.array(tuple(pt)) - cs))))
        out["l" if q.y > 0 else "r"] = q
    return out


def capture_source_bones(arm, fit_of):
    """Fitted head/tail of every bone of the source armature, world space: its rest joint moved by the fit transform
    b4bfit gave it (fit_of: {bone: 4x4}); the evaluated pose isn't used (rig constraints distort unmapped bones)."""
    mw = arm.matrix_world
    out = {}
    for b in arm.data.bones:
        D = fit_of.get(b.name, Matrix.Identity(4))
        out[b.name] = (mw @ (D @ b.head_local), mw @ (D @ b.tail_local))
    return out


# ---- helpers ----------------------------------------------------------------------------------------------------------

def weights_of(m):
    names = {g.index: g.name for g in m.vertex_groups}
    return [{names[g.group]: g.weight for g in v.groups if g.weight > 0} for v in m.data.vertices]


def set_weights(m, W):
    m.vertex_groups.clear()
    groups = {}
    for vi, d in enumerate(W):
        for n, w in d.items():
            if w <= 1e-4: continue
            g = groups.get(n) or groups.setdefault(n, m.vertex_groups.new(name=n))
            g.add([vi], w, "REPLACE")


def material_label(x):
    """A material's name plus its images' file names: 'Material #34 hair.png' (game rips name materials generically;
    the texture names still say hair, eye, head ...)."""
    if x is None: return ""
    n = re.sub(r"\.\d{3}$", "", x.name)
    if x.node_tree:                  # colour images only (a "MaskMap" or normal map says nothing about the part)
        imgs = {os.path.basename(nd.image.filepath or nd.image.name) for nd in x.node_tree.nodes
                if nd.type == "TEX_IMAGE" and nd.image is not None and nd.image.colorspace_settings.name != "Non-Color"
                and not re.search(r"normal|_n\.|_nrm|mask|rough|metal|_ao|occlusion|_orm|spec|gloss|height|bump",
                                  nd.image.filepath or nd.image.name, re.I)}
        if imgs: n += " " + " ".join(sorted(imgs))
    return n


def vertex_materials(m, labels=False):
    mats = [(material_label(x) if labels else re.sub(r"\.\d{3}$", "", x.name)) if x else "" for x in m.data.materials]
    out = [""] * len(m.data.vertices)
    for p in m.data.polygons:
        n = mats[p.material_index] if p.material_index < len(mats) else ""
        for v in p.vertices: out[v] = n
    return out


def islands(m, keep):
    """Connected components among the vertices in `keep` (a set of indices)."""
    bm = bmesh.new(); bm.from_mesh(m.data); bm.verts.ensure_lookup_table()
    seen, out = set(), []
    for i in keep:
        if i in seen: continue
        st, comp = [bm.verts[i]], []
        seen.add(i)
        while st:
            x = st.pop(); comp.append(x.index)
            for e in x.link_edges:
                y = e.other_vert(x)
                if y.index not in seen and y.index in keep: seen.add(y.index); st.append(y)
        out.append(comp)
    bm.free()
    return out


class Frame:
    """Face frame of the template: origin at the head joint, axes forward / lateral (to the left) / up; metres."""
    def __init__(self, arm):
        bw = lambda n: arm.matrix_world @ arm.data.bones[n].head_local
        self.o = bw("head")
        lat = (bw("eye_l") - bw("eye_r")).normalized()
        up = Vector((0, 0, 1)); up = (up - lat * up.dot(lat)).normalized()
        self.R = Matrix((lat.cross(up).normalized(), lat, up))       # rows: fwd, lat, up

    def loc(self, p): return self.R @ (p - self.o)
    def world(self, q): return self.R.transposed() @ q + self.o


def solve(A, b):
    """Small dense solve (Gauss-Jordan with partial pivoting)."""
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12: continue
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


class Warp:
    """src -> dst from landmark pairs (face-frame coordinates): one uniform scale + an offset (least squares; bones far
    from the landmarks, like the jaw hinge, only get this), plus a Gaussian RBF correction that fades out away from
    the landmarks (sigma in metres)."""
    def __init__(self, src, dst, sigma):
        self.sigma = sigma
        n = len(src)
        ms = [sum(p[k] for p in src) / n for k in range(3)]
        md = [sum(q[k] for q in dst) / n for k in range(3)]
        cov = sum((p[k] - ms[k]) * (q[k] - md[k]) for p, q in zip(src, dst) for k in range(3))
        var = sum((p[k] - ms[k]) ** 2 for p in src for k in range(3))
        sc = min(1.7, max(0.6, cov / var)) if var > 1e-8 else 1.0
        self.s = [sc] * 3
        self.t = [md[k] - sc * ms[k] for k in range(3)]
        self.src = [Vector(p) for p in src]
        res = [Vector(q) - self.affine(Vector(p)) for p, q in zip(src, dst)]
        Phi = [[self.phi((a - b).length) + (1e-3 if i == j else 0.0) for j, b in enumerate(self.src)]
               for i, a in enumerate(self.src)]
        self.c = [Vector(v) for v in zip(*[solve(Phi, [r[k] for r in res]) for k in range(3)])]

    def phi(self, d): return math.exp(-(d * d) / (2 * self.sigma * self.sigma))
    def affine(self, p): return Vector([self.s[k] * p[k] + self.t[k] for k in range(3)])

    def __call__(self, p):
        p = Vector(p)
        out = self.affine(p)
        for a, c in zip(self.src, self.c):
            out += c * self.phi((p - a).length)
        return out


# ---- template ---------------------------------------------------------------------------------------------------------

def descendants(arm, name):
    out, st = [], list(arm.data.bones[name].children)
    while st:
        b = st.pop(); out.append(b.name); st.extend(b.children)
    return out


TEMPLATE_RATIO = [None]     # the template's crease position between nose tip and chin bottom (profile_landmarks)


def template_face(tpl, F):
    """Template head: face bones, landmarks (face frame) and the vertices that carry face weights, by class."""
    arm = tpl.arm
    face = set(descendants(arm, "face_master"))
    jaw_chain = {"jaw"} | set(descendants(arm, "jaw"))
    bl = lambda n: F.loc(arm.matrix_world @ arm.data.bones[n].head_local)
    lm = {"eye_l": bl("eye_l"), "eye_r": bl("eye_r"), "eye_in_l": bl("eye_inner_l"), "eye_out_l": bl("eye_outer_l"),
          "eye_in_r": bl("eye_inner_r"), "eye_out_r": bl("eye_outer_r"),
          "mouth_l": (bl("lip_corner_upper_l") + bl("lip_corner_lower_l")) / 2,
          "mouth_r": (bl("lip_corner_upper_r") + bl("lip_corner_lower_r")) / 2,
          "lip_up": bl("lip_upper"), "lip_lo": bl("lip_lower"), "chin": bl("chin"), "nose": bl("nose")}
    verts = []          # (face-frame pos, weights, class, jaw-chain weight)
    sec = []
    for tm in tpl.meshes:
        W = weights_of(tm)
        mats = vertex_materials(tm)
        per_mat = {}
        for w, mt in zip(W, mats):
            c = per_mat.setdefault(mt, [0, 0]); c[1] += 1
            if any(b in face for b in w): c[0] += 1
        use = {mt for mt, (a, n) in per_mat.items() if n and a / n > 0.03 and not HAIR_RX.search(mt)}
        skin_idx = set()
        for v, w, mt in zip(tm.data.vertices, W, mats):
            if mt not in use: continue
            if not any(b in face or b == "head" for b in w): continue
            cls = "eye" if EYE_RX.search(mt) else "mouth" if MOUTH_RX.search(mt) else "skin"
            jw = sum(x for b, x in w.items() if b in jaw_chain)
            verts.append((F.loc(tm.matrix_world @ v.co), w, cls, jw))
            if cls == "skin": skin_idx.add(v.index)
        sec += mid_section(tm, skin_idx, F)
    prof = profile_landmarks(sec or [p for p, w, c, j in verts if c == "skin"], lm, 1.0)
    lm["crease"] = prof["crease"] if "crease" in prof and lm["lip_lo"].z < prof["crease"].z < lm["lip_up"].z \
        else (lm["lip_up"] + lm["lip_lo"]) / 2
    TEMPLATE_RATIO[0] = prof.get("ratio")
    return face, jaw_chain, lm, verts


# ---- model landmarks --------------------------------------------------------------------------------------------------

def model_head(meshes, face, F, tl, no_cloth=True):
    """Model vertices that may take face weights: [(mesh, index, face-frame pos, material, class)]. Hair, hats and
    other gear, and clothes (a collar or coat reaching up to the head) are left out, by material and texture names."""
    out = []
    zmin = min(tl["chin"].z, tl["mouth_l"].z) - 0.06
    for m in meshes:
        W = weights_of(m)
        mats = vertex_materials(m, labels=True)
        for i, (v, w, mt) in enumerate(zip(m.data.vertices, W, mats)):
            hw = sum(x for b, x in w.items() if b == "head" or b in face)
            if hw < 0.05: continue
            if (HAIR_RX.search(mt) and not OVERLAY_RX.search(mt)) or GEAR_RX.search(mt) or HIGHLIGHT_RX.search(mt):
                continue
            if no_cloth and CLOTH_RX.search(mt) and not EYE_RX.search(mt) and not MOUTH_RX.search(mt):
                continue
            p = F.loc(m.matrix_world @ v.co)
            if p.z < zmin: continue
            cls = "eye" if EYE_RX.search(mt) else "mouth" if MOUTH_RX.search(mt) else \
                "overlay" if OVERLAY_RX.search(mt) else "skin"
            out.append((m, i, p, mt, cls))
    if no_cloth and not any(c == "skin" for *_, c in out):   # one material named like clothes: it is the skin too
        return model_head(meshes, face, F, tl, no_cloth=False)
    return out


def mid_section(m, idx, F):
    """Where the mesh's edges among the vertices idx cross the face's middle plane (face frame): a dense profile even
    on low-poly heads, whose vertices seldom sit exactly on the middle."""
    P = {i: F.loc(m.matrix_world @ m.data.vertices[i].co) for i in idx}
    out = [p for p in P.values() if abs(p.y) < 1e-4]
    for e in m.data.edges:
        a, b = e.vertices
        if a in P and b in P:
            pa, pb = P[a], P[b]
            if (pa.y < 0) != (pb.y < 0) and abs(pa.y - pb.y) > 1e-9:
                t = pa.y / (pa.y - pb.y)
                out.append(pa + (pb - pa) * t)
    return out


def bbox(ps):
    lo = Vector([min(p[k] for p in ps) for k in range(3)]); hi = Vector([max(p[k] for p in ps) for k in range(3)])
    return lo, hi


def seed_warp(tl, tverts, head):
    """Template -> model, first guess: per-axis scale/offset from the two heads' skin bounding boxes (front half)."""
    # both cut at the same height as model_head (the template's neck can reach far lower, e.g. stretched onto a model's
    # long neck by its own proportions)
    zmin = min(tl["chin"].z, tl["mouth_l"].z) - 0.06
    ts = [p for p, w, c, j in tverts if c == "skin" and p.x > 0 and p.z >= zmin]
    ms = [p for (m, i, p, mt, c) in head if c == "skin" and p.x > 0]
    if len(ms) < 20:
        ms = [p for (m, i, p, mt, c) in head]
    tlo, thi = bbox(ts); mlo, mhi = bbox(ms)
    s = [max(0.5, min(2.0, (mhi[k] - mlo[k]) / max(1e-4, thi[k] - tlo[k]))) for k in range(3)]
    # lateral: symmetric about the middle; up: keep the head joint (the fit already put it on the template's)
    s[0] = s[2] = (s[1] + s[2]) / 2 if abs(s[0] - 1) > 0.5 else s[0]
    def f(p):
        q = Vector((mlo.x + (p.x - tlo.x) * s[0], p.y * s[1], mlo.z + (p.z - tlo.z) * s[2]))
        return q
    return f, s


def front_profile(pts, zlo, zhi, half_width, step=0.002):
    """Frontmost x per z bin among points near the middle: {bin z: max x}."""
    prof = {}
    for p in pts:
        if abs(p.y) > half_width or p.z < zlo or p.z > zhi: continue
        b = round(p.z / step) * step
        if p.x > prof.get(b, -1e9): prof[b] = p.x
    # sparse meshes leave bins with only the back of the head in them: those aren't dents, drop them
    if prof:
        xs = sorted(prof.values()); med = xs[len(xs) // 2]
        prof = {b: x for b, x in prof.items() if x > med - 0.03}
    return dict(sorted(prof.items()))


def profile_landmarks(pts, seed, scale, lips=None, ratio=None):
    """Nose tip, lip crease, upper/lower lip, chin from the middle profile, searched around the seeds (lips: the
    upper and lower lip already known: only nose and chin). The crease is the dent between the lips; the fold under the
    lower lip can be deeper, so among the dents the one where the survivor has its crease wins: `ratio` = (nose tip -
    crease) / (nose tip - bottom of the chin) of the template (out["ratio"] when it is None)."""
    zs = [seed["nose"].z + 0.03 * scale, seed["chin"].z - 0.05 * scale] if lips is None else \
        [lips[0].z + 0.06 * scale, lips[1].z - 0.045 * scale]
    prof = front_profile(pts, zs[1], zs[0], 0.004 * scale)
    # a coarse mesh leaves thin slices with no front vertex (the slice then shows the back of the head: a false
    # dent many cm deep): those take the value of a slice three times as wide. A gap between open lips stays a gap
    # in the wide slice too (the lips run sideways)
    wide = front_profile(pts, zs[1], zs[0], 0.012 * scale)
    for k, x in wide.items():
        if k not in prof or prof[k] < x - 0.025 * scale: prof[k] = x
    prof = dict(sorted(prof.items()))
    if len(prof) < 10: return {}
    ks = list(prof)
    # smooth over 3 bins
    sm = {k: sum(prof[x] for x in ks[max(0, i - 1):i + 2]) / len(ks[max(0, i - 1):i + 2]) for i, k in enumerate(ks)}
    if DEBUG:
        print("b4bface: profile (z cm: x cm):", " ".join(f"{k * 100:.1f}:{sm[k] * 100:.1f}" for k in ks),
              "seed lips", fmt(seed["lip_up"]), fmt(seed["lip_lo"]), "scale", round(scale, 2))
    out = {}
    if lips is not None:
        ku = min(ks, key=lambda x: abs(x - lips[0].z)); kl = min(ks, key=lambda x: abs(x - lips[1].z))
        return chin_nose(out, ks, sm, ku, kl, scale)
    lm = (seed["lip_up"].z + seed["lip_lo"].z) / 2
    if ratio is not None:
        # with a nose that stands out: walk down the profile from the dent under it (subnasale, often deeper than a
        # closed mouth's crease): upper lip (a bulge), crease (the next dent), lower lip (the next bulge)
        nz = [k for k in ks if lm < k <= lm + 0.045 * scale]
        if nz:
            kn = max(nz, key=lambda x: sm[x])
            under = [k for k in ks if kn - 0.025 * scale <= k < kn - 0.003 * scale]
            if under:
                ksub = min(under, key=lambda x: sm[x])
                walk = lip_walk([k for k in reversed(ks) if ksub - 0.045 * scale <= k < ksub], sm, 0.0003 * scale) \
                    if sm[kn] - sm[ksub] > 0.008 * scale else None
                if walk:
                    ku, kc, kl = walk
                    out["crease"] = Vector((sm[kc], 0.0, kc))
                    out["lip_up"] = Vector((sm[ku], 0.0, ku)); out["lip_lo"] = Vector((sm[kl], 0.0, kl))
                    return chin_nose(out, ks, sm, ku, kl, scale)
    # else: the dent where the survivor has its crease. Nose tip near its seed, bottom of the chin: the lowest point
    # still near the front of the chin
    kn = max([k for k in ks if abs(k - seed["nose"].z) <= 0.02 * scale] or ks, key=lambda x: sm[x])
    chin_front = max([sm[k] for k in ks if k < lm - 0.01 * scale] or [sm[kn]])
    low = [k for k in ks if k < lm and sm[k] > chin_front - 0.015 * scale]
    km = min(low) if low else None
    # dents: x below the maxima on both sides within 1.2 cm
    dents = []
    for k in ks:
        if not (km is not None and km < k < kn - 0.008 * scale) and abs(k - lm) > 0.015 * scale: continue
        above = [sm[x] for x in ks if k < x <= k + 0.012 * scale]
        below = [sm[x] for x in ks if k - 0.012 * scale <= x < k]
        if not above or not below: continue
        d = min(max(above), max(below)) - sm[k]
        if d >= 0.001 * scale: dents.append((k, d))
    if not dents: return {}
    if km is not None and kn - km > 0.03 * scale:
        if ratio is None:                         # the template: its crease is known (the seed)
            best = min(dents, key=lambda t: abs(t[0] - lm))[0]
            out["ratio"] = (kn - best) / (kn - km)
        else:                                     # the model: the dent where the survivor has its crease
            want = kn - ratio * (kn - km)
            deep = max(d for k, d in dents); unit = 0.1 * (kn - km)
            best = min(dents, key=lambda t: abs(t[0] - want) / unit - t[1] / deep)[0]
            if DEBUG: print(f"b4bface: crease: nose {kn * 100:.1f}, chin bottom {km * 100:.1f}, expected {want * 100:.1f}, "
                            f"dents {[(round(k * 100, 1), round(d * 100, 2)) for k, d in dents]}")
    else:
        best = max([t for t in dents if abs(t[0] - lm) <= 0.015 * scale] or dents, key=lambda t: t[1])[0]
    up = [x for x in ks if best < x <= best + 0.012 * scale]
    lo = [x for x in ks if best - 0.012 * scale <= x < best]
    ku = max(up, key=lambda x: sm[x]); kl = max(lo, key=lambda x: sm[x])
    # a slit between the lips can run deep into the head: the crease stays near the lips' front
    out["crease"] = Vector((max(sm[best], min(sm[ku], sm[kl]) - 0.006 * scale), 0.0, best))
    out["lip_up"] = Vector((sm[ku], 0.0, ku)); out["lip_lo"] = Vector((sm[kl], 0.0, kl))
    return chin_nose(out, ks, sm, ku, kl, scale)


def lip_walk(seq, sm, tol):
    """Down the profile (seq: bins from the subnasale down): the first bulge, the dent after it, the bulge after that
    (each change by more than tol). (upper lip, crease, lower lip) or None."""
    marks, mode, best = [], "max", None
    for k in seq:
        if best is None or (sm[k] > sm[best] if mode == "max" else sm[k] < sm[best]): best = k
        elif abs(sm[k] - sm[best]) > tol:
            marks.append(best)
            if len(marks) == 3: return tuple(marks)
            mode, best = ("min" if mode == "max" else "max"), k
    return None


def chin_nose(out, ks, sm, ku, kl, scale):
    """Chin: the front-most point below the lower lip; nose tip: the front-most above the upper lip."""
    ch = [x for x in ks if kl - 0.035 * scale <= x < kl - 0.005 * scale]
    if ch:
        kc = max(ch, key=lambda x: sm[x])
        # a full lower lip is the front-most thing right under the lip line: then the chin is the bulge below the
        # dent under the lip (mentolabial fold), else the chin landed on the lip (the jaw then ended at the lip and
        # the chin stayed behind when the mouth opened)
        dz = [x for x in ch if x >= kl - 0.02 * scale]
        if dz:
            kd = min(dz, key=lambda x: sm[x])
            below = [x for x in ch if x < kd]
            if below and kc >= kd:
                kb = max(below, key=lambda x: sm[x])
                if sm[kb] > sm[kd] + 0.0005 * scale: kc = kb
        out["chin"] = Vector((sm[kc], 0.0, kc))
    no = [x for x in ks if ku + 0.008 * scale <= x <= ku + 0.05 * scale]
    if no:
        kn = max(no, key=lambda x: sm[x]); out["nose"] = Vector((sm[kn], 0.0, kn))
    return out


def middle_surface(head, scale, half=0.02, n=6):
    """Points on the skin's faces near the face's middle (|lateral| < 2 cm): the profile of a coarse mesh (large
    faces between the vertices, common in game models) has no holes then."""
    by = {}
    for (m, i, p, mt, c) in head:
        if c in ("skin", "overlay"): by.setdefault(m, {})[i] = p
    out = []
    for m, d in by.items():
        for poly in m.data.polygons:
            vs = poly.vertices
            if any(v not in d for v in vs): continue
            ps = [d[v] for v in vs]
            if min(abs(q.y) for q in ps) > half * scale: continue
            for k in range(1, len(ps) - 1):             # fan triangles, points on a barycentric grid
                a, b, c_ = ps[0], ps[k], ps[k + 1]
                for u in range(n + 1):
                    for v in range(n + 1 - u):
                        w = n - u - v
                        out.append((a * u + b * v + c_ * w) / n)
    return out


def surface_near(pts_kd, pts, y, z, x_hint, r):
    """Frontmost point near (y, z) within r."""
    best = None
    for co, i, d in pts_kd.find_range(Vector((x_hint, y, z)), r):
        p = pts[i]
        if abs(p.y - y) < r * 0.5 and abs(p.z - z) < r * 0.5 and (best is None or p.x > best.x): best = p
    return best


def model_landmarks(tl, tverts, head, head_islands, eyeball_verts, src_bones, F, notes):
    """Landmarks on the model (face frame) for every template landmark; notes gets where each came from."""
    seed_f, s = seed_warp(tl, tverts, head)
    scale = (s[1] + s[2]) / 2
    seed = {k: seed_f(v) for k, v in tl.items()}
    lm = dict(seed)
    src = {k: "scaled from the template" for k in lm}
    skin = [p for (m, i, p, mt, c) in head if c in ("skin", "overlay")]
    kd = KDTree(len(skin))
    for i, p in enumerate(skin): kd.insert(p, i)
    kd.balance()
    # 1. eyes: source eye bones, else eyeball-like islands (eye materials first)
    eyes = {}
    if DEBUG: print(f"b4bface: seed scale {s}, seed eye_l {fmt(seed['eye_l'])} chin {fmt(seed['chin'])}")
    if src_bones:
        for n, (h, t) in src_bones.items():
            if SRC_BONE_RULES[0][1].search(n) and not re.search(r"lid|lash|brow|target|socket|set$|master|handle", n, re.I):
                sd = side_of(n)
                if sd:
                    p = F.loc(h)
                    if DEBUG: print(f"b4bface: eye bone {n} at {fmt(p)}")
                    if (p - seed[f"eye_{sd}"]).length < 0.05 * scale and sd not in eyes: eyes[sd] = (p, None, f"bone {n}")
    t_r = (tl["eye_out_l"] - tl["eye_in_l"]).length / 2
    for sd, q in given_eyes({m for (m, i, p, mt, c) in head}, F).items():
        # a point on the eye's surface: the eye turns about a centre behind it (the template's depth, scaled)
        eyes[sd] = (q - Vector((1.1 * t_r * scale, 0.0, 0.0)), t_r * scale, "--face-eyes")
    def eye_groups(sd, centre, reach):
        """Eye-material islands, or ball-shaped eye-sized islands, near `centre` on side sd."""
        out, mats = [], []
        for (m, comp, ps, kind) in head_islands:
            if len(ps) < 6 or kind == "mouth": continue
            eyemat = kind == "eye"
            lo, hi = bbox(ps); c = (lo + hi) / 2; ext = hi - lo
            ball = ext.x >= 0.6 * max(ext.y, ext.z) and min(ext) > 0.4 * max(ext)
            sized = 0.8 * t_r * scale < max(ext) < 5.0 * t_r * scale
            if DEBUG and (eyemat or ball and sized):
                print(f"b4bface: island {m.name} {len(ps)} eyemat={eyemat} ball={ball} sized={sized} c={fmt(c)} "
                      f"ext={fmt(ext)} dist={(c - centre).length * 100:.1f} reach={reach * 100:.1f}")
            if c.y * (1 if sd == "l" else -1) <= 0: continue
            if eyemat and max(ext) < 6.0 * t_r * scale and (c - centre).length < 2 * reach: mats.append((m, comp, ps))
            elif ball and sized and (c - centre).length < reach: out.append((m, comp, ps))
        return mats or out                           # eye materials (iris, eye white ...) win over ball shapes
    for sd in ("l", "r"):
        near = eye_groups(sd, eyes[sd][0] if sd in eyes else seed[f"eye_{sd}"],
                          (1.5 if sd in eyes and eyes[sd][2] == "--face-eyes" else 0.5) * t_r * scale if sd in eyes
                          else 0.045 * scale)
        if not near: continue
        ps = [p for g in near for p in g[2]]
        lo, hi = bbox(ps); c = (lo + hi) / 2; ext = hi - lo
        eyeball_verts.update((m, i) for m, comp, _ in near for i in comp)
        if sd in eyes:
            ctr = c if eyes[sd][2] == "--face-eyes" and ext.x >= 0.5 * max(ext.y, ext.z) else eyes[sd][0]
            eyes[sd] = (ctr, max(ext.y, ext.z) / 2, eyes[sd][2]); continue     # a given point: an eyeball's centre wins
        if ext.x < 0.5 * max(ext.y, ext.z):          # a patch (painted/anime eye), not a ball: centre behind it
            c = c - Vector((0.45 * max(ext.y, ext.z), 0, 0))
        eyes[sd] = (c, max(ext.y, ext.z) / 2, f"eye mesh ({len(ps)} vertices)")
    for sd, (p, rad, how) in eyes.items():
        d = p - seed[f"eye_{sd}"]
        lm[f"eye_{sd}"] = p
        k = (rad / ((tl[f"eye_out_{sd}"] - tl[f"eye_in_{sd}"]).length / 2 * 1.15)) if rad else scale
        k = min(2.0, max(0.6, k))
        for c in ("in", "out"):
            lm[f"eye_{c}_{sd}"] = p + (tl[f"eye_{c}_{sd}"] - tl[f"eye_{sd}"]) * k
            src[f"eye_{c}_{sd}"] = how
        src[f"eye_{sd}"] = how
    if len(eyes) == 1:                                   # mirror the one found
        sd, (p, rad, how) = next(iter(eyes.items()))
        o = "r" if sd == "l" else "l"
        for k in ("eye_{}", "eye_in_{}", "eye_out_{}"):
            q = lm[k.format(sd)]; lm[k.format(o)] = Vector((q.x, -q.y, q.z)); src[k.format(o)] = how + " (mirrored)"
    # the first guess scaled the template by the head's bounds; when those take in much of the neck, a beard or hair
    # the guessed eyes land far from the found ones, and the mouth search would look at the chin: search relative to
    # the eyes instead (the template's face scaled by the eye distance)
    if len(eyes) == 2:
        tm, mm = (tl["eye_l"] + tl["eye_r"]) / 2, (lm["eye_l"] + lm["eye_r"]) / 2
        sm_ = (seed["eye_l"] + seed["eye_r"]) / 2
        k = min(1.6, max(0.6, (lm["eye_l"] - lm["eye_r"]).length / max(1e-4, (tl["eye_l"] - tl["eye_r"]).length)))
        if abs(sm_.z - mm.z) > 0.02 * scale or abs(sm_.x - mm.x) > 0.03 * scale:
            for key in seed:
                if key.startswith("eye"): continue
                seed[key] = mm + (tl[key] - tm) * k
                if src[key].startswith("scaled"): lm[key] = seed[key]
            notes.setdefault("the eyes (the head's bounds take in neck or hair: the rest is searched from the eyes)",
                             []).append("seeds")
    # 2. mouth: source lip/jaw bones, else the mouth-open shape key, else the middle profile
    got_mouth = False
    if src_bones:
        pick = {}
        for key, rx, end in SRC_BONE_RULES[1:]:
            for n, (h, t) in src_bones.items():
                if rx.search(n):
                    pick.setdefault(key, []).append((n, F.loc(h if end == "head" else t if end == "tail" else (h + t) / 2)))
        for key, v in pick.items():                      # Rigify: the DEF-/ORG- layers, not the control bones
            if any(re.match(r"(def|org)-", n, re.I) for n, _ in v):
                pick[key] = [(n, p) for n, p in v if re.match(r"(def|org)-", n, re.I)]
        if "lip_t" in pick and "lip_b" in pick and "lip_corner" in pick:
            up = sum((p for n, p in pick["lip_t"]), Vector()) / len(pick["lip_t"])
            lo = sum((p for n, p in pick["lip_b"]), Vector()) / len(pick["lip_b"])
            lm["lip_up"], lm["lip_lo"] = up, lo
            for n, p in pick["lip_corner"]:
                sd = side_of(n)
                if sd: lm[f"mouth_{sd}"] = p
            for k in ("lip_up", "lip_lo", "mouth_l", "mouth_r"): src[k] = "lip bones (" + pick["lip_t"][0][0] + " ...)"
            got_mouth = True
        if "chin" in pick:
            lm["chin"] = pick["chin"][0][1]; src["chin"] = f"bone {pick['chin'][0][0]}"
    if not got_mouth:
        mk = mouth_from_shape_key(head, seed, scale)
        if mk:
            lm.update(mk); got_mouth = True
            for k in mk: src[k] = "mouth-open shape key"
    sec = []
    by_m = {}
    for (m, i, p, mt, c) in head:
        if c in ("skin", "overlay"): by_m.setdefault(m, set()).add(i)
    for m, idx in by_m.items(): sec += mid_section(m, idx, F)
    prof = profile_landmarks((sec or skin) + middle_surface(head, scale), seed, scale,
                             (lm["lip_up"], lm["lip_lo"]) if got_mouth else None, ratio=TEMPLATE_RATIO[0] or 0.4)
    if not got_mouth and "crease" in prof:
        lm["lip_up"], lm["lip_lo"] = prof["lip_up"], prof["lip_lo"]
        src["lip_up"] = src["lip_lo"] = "face profile"
        # corners: the template's mouth width relative to the eyes, on the model's surface at the crease height
        ew_t = (tl["eye_l"] - tl["eye_r"]).length; ew_m = (lm["eye_l"] - lm["eye_r"]).length
        dz = (tl["mouth_l"].z - (tl["lip_up"].z + tl["lip_lo"].z) / 2)
        for sd in ("l", "r"):
            y = tl[f"mouth_{sd}"].y * ew_m / ew_t
            z = prof["crease"].z + dz * scale
            # the crease itself can lie deep inside a mouth slit: the corners are searched behind the lips' front
            xh = (lm["lip_up"].x + lm["lip_lo"].x) / 2 - (tl["lip_up"].x - tl[f"mouth_{sd}"].x) * scale
            p = surface_near(kd, skin, y, z, xh, 0.012 * scale)
            lm[f"mouth_{sd}"] = p if p is not None else Vector((xh, y, z))
            src[f"mouth_{sd}"] = "face profile + eye distance"
        got_mouth = True
    if "crease" in prof and lm["lip_lo"].z < prof["crease"].z < lm["lip_up"].z and src["crease"] != "mouth-open shape key":
        lm["crease"] = prof["crease"]; src["crease"] = "face profile"
    elif src["crease"].startswith("scaled"):
        lm["crease"] = (lm["lip_up"] + lm["lip_lo"]) / 2; src["crease"] = "between the lips"
    if prof:
        for k in ("chin", "nose"):
            if k in prof and src[k].startswith("scaled"):
                lm[k] = prof[k]; src[k] = "face profile"
    for k, v in src.items(): notes.setdefault(v, []).append(k)
    if DEBUG:
        for k in lm: print(f"b4bface: landmark {k:10s} {fmt(lm[k])} seed {fmt(seed[k])} ({src[k]})")
    return lm, scale, {k for k, v in src.items() if not v.startswith("scaled")}


def mouth_from_shape_key(head, seed, scale):
    """Lip line from a mouth-open key: at the front of the face, the top of what it moves is the lower lip, the first
    vertex above that stays is the upper lip; the corners are the sides of the moved lip region."""
    pts = []
    for (m, i, p, mt, c) in head:
        a = m.data.attributes.get("b4b_face_jaw")
        if a is None or c not in ("skin", "overlay"): continue
        pts.append((p, abs(a.data[i].value)))
    if not pts: return None
    mx = max(v for p, v in pts)
    if mx <= 0: return None
    moved = [(p, v) for p, v in pts if v > 0.35 * mx]
    if len(moved) < 5: return None
    front = max(p.x for p, v in moved)
    mid = [(p, v) for p, v in pts if abs(p.y) < 0.004 * scale and p.x > front - 0.02 * scale]
    lo = [p for p, v in mid if v > 0.35 * mx]
    if not lo: return None
    lip_lo = max(lo, key=lambda p: p.z)
    above = [p for p, v in mid if v < 0.15 * mx and p.z > lip_lo.z and p.z < lip_lo.z + 0.03 * scale]
    if not above: return None
    lip_up = min(above, key=lambda p: p.z)
    crease = (lip_lo + lip_up) / 2
    band = [p for p, v in pts if v > 0.15 * mx and abs(p.z - crease.z) < 0.008 * scale and p.x > front - 0.03 * scale]
    if DEBUG:
        print(f"b4bface: jaw key: max {mx:.4f}, lower lip {fmt(lip_lo)}, upper lip {fmt(lip_up)}, band {len(band)}")
    if len(band) < 2: return None
    ml = max(band, key=lambda p: p.y); mr = min(band, key=lambda p: p.y)
    if ml.y <= 0 or mr.y >= 0: return None
    # the corners: where the lips part, the key's motion jumps from stay to move within a few mm (the slit); past the
    # corners the cheek moves too, smoothly (the moved region alone put the corners on the cheeks: 8.6 cm wide mouth,
    # the corner bones then pulled a slash across the cheek)
    near = [(p, v) for p, v in pts if abs(p.z - crease.z) < 0.012 * scale and p.x > front - 0.03 * scale]
    step = 0.002 * scale
    bins = {}
    for p, v in near: bins.setdefault(int(math.floor(p.y / step)), []).append((p, v))
    slit = {}
    for b, ps in bins.items():
        ps.sort(key=lambda t: t[0].z)
        best = 0.0
        for k in range(len(ps)):
            for j in range(k + 1, len(ps)):
                if ps[j][0].z - ps[k][0].z > 0.003 * scale: break
                best = max(best, abs(ps[j][1] - ps[k][1]))
        slit[b] = best
    thr = 0.4 * mx
    def edge(sign):
        b0 = int(math.floor(crease.y / step))
        last, b, miss = None, b0, 0
        while abs(b - b0) * step < 0.06 * scale:
            if slit.get(b, 0.0) > thr: last, miss = b, 0
            else:
                miss += 1
                if miss > 2 and last is not None: break
            b += sign
        return last
    bl, br = edge(1), edge(-1)
    if bl is not None and br is not None and bl * step > 0.008 * scale and br * step < -0.008 * scale:
        pick = lambda b: min(bins[b], key=lambda t: abs(t[0].z - crease.z))[0]
        cl, cr = pick(bl), pick(br)
        if cl.y < ml.y or cr.y > mr.y:
            if DEBUG: print(f"b4bface: mouth corners by the slit {fmt(cl)} {fmt(cr)} (moved region {fmt(ml)} {fmt(mr)})")
            ml, mr = (cl if cl.y < ml.y else ml), (cr if cr.y > mr.y else mr)
    return {"mouth_l": ml, "mouth_r": mr, "lip_up": lip_up, "lip_lo": lip_lo, "crease": crease}


class MouthLine:
    """The lip line (crease) as a curve through the mouth corners and the middle of the crease (face frame): which side
    of the mouth a point is on (+1 upper lip and above, -1 lower lip / jaw, 0 away from the mouth)."""
    def __init__(self, lm, scale=1.0):
        self.mid = lm.get("crease", (lm["lip_up"] + lm["lip_lo"]) / 2)
        self.cy = max(1e-4, (lm["mouth_l"].y - lm["mouth_r"].y) / 2)
        self.cz = (lm["mouth_l"].z + lm["mouth_r"].z) / 2
        self.scale = scale

    def z(self, y):
        u = min(1.0, abs(y) / self.cy)
        return self.mid.z + (self.cz - self.mid.z) * u * u

    def __call__(self, p):
        if abs(p.y) > self.cy * 1.35 or p.x < self.mid.x - 0.035 * self.scale: return 0
        return 1 if p.z > self.z(p.y) else -1


def capture_jaw_weights(meshes):
    """Before the source rig's weights are renamed: per vertex, the weight on its jaw-side face bones (jaw, chin, lower
    lip, lower teeth, tongue), as vertex attribute b4b_face_jawsrc (which lip a vertex belongs to)."""
    # (Auto-Rig Pro: c_lips_bot*, c_teeth_bot*, tong_* under c_jawbone.x)
    rx = re.compile(r"jaw|chin|lip\.?b\b|lip\.b\.|lower_?lip|lip_?lower|lips?_?bot|teeth\.b|lower_?teeth|teeth_?lower|"
                    r"teeth_?bot|tongue|(^|[_.])tong([_.\d]|$)", re.I)
    n = 0
    for m in meshes:
        gi = {g.index for g in m.vertex_groups if rx.search(g.name)}
        if not gi: continue
        vals = [min(1.0, sum(g.weight for g in v.groups if g.group in gi)) for v in m.data.vertices]
        a = m.data.attributes.get("b4b_face_jawsrc") or m.data.attributes.new("b4b_face_jawsrc", "FLOAT", "POINT")
        a.data.foreach_set("value", vals)
        n += 1
    return n


# ---- the rig --------------------------------------------------------------------------------------------------------

def rig_face(tpl, meshes, src_bones=None, log=print):
    """Skin the model's face (meshes already bound to the template skeleton by vertex group names) to the template's
    face bones and return {bone: world position (m)} for the face bones' new bind positions."""
    arm = tpl.arm
    if "face_master" not in arm.data.bones or "eye_l" not in arm.data.bones:
        log("face: the template has no face bones; face not rigged"); return {}
    F = Frame(arm)
    face, jaw_chain, tl, tverts = template_face(tpl, F)
    head = model_head(meshes, face, F, tl)
    if len(head) < 50:
        log("face: no head found on the model (no vertices weighted to the head); face not rigged"); return {}
    notes = {}
    head_islands = []
    by = {}
    for (m, i, p, mt, c) in head: by.setdefault(m, {})[i] = (p, c)
    for m, d in by.items():
        for comp in islands(m, set(d)):
            cl = {d[i][1] for i in comp}
            head_islands.append((m, comp, [d[i][0] for i in comp], "eye" if "eye" in cl else
                                 "mouth" if "mouth" in cl else "other"))
    eyeball_verts = set()
    ml, scale, found = model_landmarks(tl, tverts, head, head_islands, eyeball_verts, src_bones, F, notes)
    # the warp: landmarks found on the model (guesses scaled from the template would only bend it)
    keys = [k for k in tl if k in ml and k in found] or [k for k in tl if k in ml]
    ew = (tl["eye_l"] - tl["eye_r"]).length
    to_tpl = Warp([ml[k] for k in keys], [tl[k] for k in keys], sigma=0.35 * ew)
    to_mdl = Warp([tl[k] for k in keys], [ml[k] for k in keys], sigma=0.35 * ew * scale)
    for how, ks in notes.items(): log(f"face: {', '.join(ks)} from {how}")
    no_eyes = not ({"eye_l", "eye_r"} & found)
    if no_eyes:
        log("face: WARNING: no eyes found (no eye bones, eye mesh/material or eyeball-shaped parts): the face won't "
            "blink (eyelids left on the head). Give their positions with --face-eyes x,y,z;x,y,z (Blender "
            "coordinates of each pupil, metres, as the file imports) to rig them")
    side_t = MouthLine(tl)                     # upper/lower lip: template vertices by the template's lip line,
    side_m = MouthLine(ml, scale)              # model vertices by the model's own (not through the warp)
    def tside(p, j):                           # template: its own jaw weights say which lip
        s_ = side_t(p)
        return 0 if s_ == 0 else (-1 if j >= 0.5 else 1)
    # template KD trees per class (skin/overlay share one) and lip side
    trees = {}
    for cls in ("skin", "eye", "mouth"):
        for sd in (1, -1, 0):
            idx = [i for i, (p, w, c, j) in enumerate(tverts) if c == cls and (sd == 0 or tside(p, j) != -sd)]
            if not idx: continue
            kd = KDTree(len(idx))
            for k, i in enumerate(idx): kd.insert(tverts[i][0], k)
            kd.balance()
            trees[(cls, sd)] = (kd, idx)
    # eye meshes of the model go whole to the eyeball bones
    eye_side = {}
    for (m, i, p, mt, c) in head:
        if c == "eye" or (m, i) in eyeball_verts: eye_side[(m, i)] = "l" if p.y > 0 else "r"
    Wm = {m: weights_of(m) for m in meshes}
    jaw_attr = {m: m.data.attributes.get("b4b_face_jaw") or m.data.attributes.get("b4b_face_jawsrc") for m in meshes}
    jmax = max([a.data[i].value for m, a in jaw_attr.items() if a for i in range(len(a.data))] or [0.0])
    stats = {"verts": 0, "jaw": 0, "lids": 0, "eyes": 0}
    blink_attr = {m: m.data.attributes.get("b4b_face_blink") for m in meshes}
    bmax = max([abs(a.data[i].value) for m, a in blink_attr.items() if a for i in range(len(a.data))] or [0.0])
    def transfer(m, i, p, c):
        """Face weights of the template at the model vertex's place (through the warp)."""
        q = to_tpl(p)
        cls = "mouth" if c == "mouth" else "skin"
        sd = side_m(p)
        a = jaw_attr[m]
        v = a.data[i].value / jmax if a is not None and jmax > 0 else None
        near = abs(p.z - side_m.z(p.y)) < 0.006 * scale
        if sd != 0 and cls == "skin":
            if v is not None and a.name == "b4b_face_jaw":      # a mouth-open shape key: what it moves is jaw
                sd = -1 if v > 0.25 else 1 if v < 0.05 else sd
            else:
                # at the crease the lips curl inwards: facing down = upper lip, facing up = lower lip
                nz = (F.R @ (m.matrix_world.to_3x3() @ m.data.vertices[i].normal)).normalized().z
                if near and abs(nz) > 0.3: sd = 1 if nz < 0 else -1
                elif v is not None: sd = -1 if v > 0.6 else 1 if v < 0.05 else sd     # the rig's jaw weights
        F_ = lookup(q, cls, sd)
        u = abs(p.y) / side_m.cy
        if sd != 0 and u > CORNER_BLEND[0]:
            # at the mouth corners upper and lower lip meet: blend into the template's corner weights (both lips)
            # instead of a hard split, which left spiky triangles at the corners of an opening mouth
            t = min(1.0, (u - CORNER_BLEND[0]) / (CORNER_BLEND[1] - CORNER_BLEND[0]))
            G_ = lookup(q, cls, 0)
            F_ = {b: F_.get(b, 0.0) * (1 - t) + G_.get(b, 0.0) * t for b in set(F_) | set(G_)}
        return F_

    def lookup(q, cls, sd):
        kd, idx = trees.get((cls, sd)) or trees.get((cls, 0)) or trees[("skin", 0)]
        hits = kd.find_n(q, 4)
        if not hits: return {}
        d0 = hits[0][2]
        fade = 1.0 if d0 < 0.008 else max(0.0, 1.0 - (d0 - 0.008) / 0.012)
        if fade <= 0: return {}
        acc, tot = {}, 0.0
        for co, k, d in hits:
            f = 1.0 / max(d, 1e-4); tot += f
            for b, x in tverts[idx[k]][1].items(): acc[b] = acc.get(b, 0.0) + x * f
        tw = {b: x / tot for b, x in acc.items()}
        share = sum(x for b, x in tw.items() if b in face or b == "head")
        if share <= 1e-6: return {}
        return {b: x / share * fade for b, x in tw.items() if b in face}

    for (m, i, p, mt, c) in head:
        w = Wm[m][i]
        # face bones the source rig mapped (e.g. a jaw) go back to the head first: the template's split replaces them
        hw = sum(x for b, x in w.items() if b == "head" or b in face)
        if hw <= 0: continue
        rest = {b: x for b, x in w.items() if b != "head" and b not in face}
        if (m, i) in eye_side:
            F_ = {f"eyeball_{eye_side[(m, i)]}": 1.0}; stats["eyes"] += 1
        else:
            F_ = transfer(m, i, p, c)
            if no_eyes: F_ = {b: x for b, x in F_.items() if not b.startswith(("eyelid", "eyeball"))}
            ba = blink_attr[m]
            if ba is not None and bmax > 0:
                v = ba.data[i].value / bmax
                if abs(v) > 0.05:                  # the model's own blink key: what it moves is the lid
                    sd = "l" if p.y > 0 else "r"
                    F_ = {b: x for b, x in F_.items() if not b.startswith("eyelid")}
                    lid = min(1.0, abs(v) * 1.2)
                    k = min(1.0, max(0.0, 1.0 - lid) / max(1e-6, sum(F_.values()))) if F_ else 1.0
                    F_ = {b: x * k for b, x in F_.items()}
                    F_[f"eyelid_{'upper' if v < 0 else 'lower'}_{sd}"] = lid
            if not F_: continue
            if any(b in jaw_chain for b in F_): stats["jaw"] += 1
            if any(b.startswith("eyelid") for b in F_): stats["lids"] += 1
        fsum = sum(F_.values())
        nw = dict(rest)
        if fsum < 1.0: nw["head"] = hw * (1.0 - fsum)
        for b, x in F_.items(): nw[b] = nw.get(b, 0.0) + hw * x
        Wm[m][i] = nw
        stats["verts"] += 1
    smooth_corners(head, Wm, side_m, scale, face, jaw_chain)
    sj = smooth_jaw_edges(head, Wm, side_m, scale, face, jaw_chain)
    if sj: log(f"face: jaw: {sj} vertices around hard jaw-weight edges (chin, jaw line) blended")
    welded = weld_seams(head, Wm, meshes, F)
    if welded: log(f"face: {welded} vertices where the head's meshes meet share their weights (no seams opening)")
    # new bind positions: every face bone mapped onto the model's face (face frame)
    tpos = {b: F.loc(arm.matrix_world @ arm.data.bones[b].head_local) for b in face}
    mpos = {b: to_mdl(tpos[b]) for b in face}
    for sd, (P, how) in fit_blink(head, Wm, F, scale, face).items():
        if f"eyelid_upper_{sd}" in mpos:
            mpos[f"eyelid_upper_{sd}"] = P; log(f"face: blink ({sd}): {how}")
    k_mouth = (ml["mouth_l"] - ml["mouth_r"]).length / max(1e-4, (tl["mouth_l"] - tl["mouth_r"]).length)
    lipfix = fit_lip_bones(arm, tverts, tpos, head, Wm, mpos, k_mouth, jaw_chain)
    for m in meshes: set_weights(m, Wm[m])
    if MOUTH_INTERIOR[0] != "off" and {"lip_up", "lip_lo"} <= found:
        note = mouth_interior(head, ml, side_m, scale, F, force=MOUTH_INTERIOR[0] == "on")
        if note: log(f"face: mouth: {note}")
    moved = {}
    worst = 0.0
    for b in sorted(face):
        worst = max(worst, (mpos[b] - tpos[b]).length)
        moved[b] = F.world(mpos[b])
    for k in ("eye_l", "mouth_l", "lip_up"):
        log(f"face: {k} template {fmt(tl[k])} -> model {fmt(ml[k])} cm (face frame: forward, left, up)")
    log(f"face: {stats['verts']} vertices skinned to face bones ({stats['jaw']} jaw/lower lip, {stats['lids']} eyelids, "
        f"{stats['eyes']} eyeballs); {len(moved)} face bones moved onto the model's face (up to {worst * 100:.1f} cm)")
    if lipfix: log(f"face: lips: {lipfix}")
    return moved


CORNER_BLEND = (0.8, 1.1)  # share of the mouth's half width where the lips' split fades into the shared corner weights
MOUTH_INTERIOR = ["auto"]   # auto: only when the model has none; on; off (b4bfit --mouth)


def base_image(mat):
    """The image a material's base colour comes from (followed upstream through mix/multiply nodes), or None."""
    if mat is None or not mat.use_nodes: return None
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    st = [l.from_node for l in bsdf.inputs["Base Color"].links] if bsdf else []
    seen = set()
    while st:
        n = st.pop()
        if n in seen: continue
        seen.add(n)
        if n.type == "TEX_IMAGE" and n.image is not None: return n.image
        st.extend(l.from_node for i in n.inputs for l in i.links)
    imgs = [n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None]
    return next((i for i in imgs if not re.search(r"norm|_n\b|rough|metal|_ao|occl|spec|emis|mask|shade", i.name, re.I)),
                None)


def mouth_interior(head, ml, line, scale, F, force=False):
    """A mouth without an inside shows a hole when the jaw opens. When the model has no mouth interior (no teeth /
    tongue / mouth material, nothing behind the lips facing into the mouth), add a dark mouth cavity: a closed bag
    behind the lips, faces turned inwards (so it can't show from outside even where it pokes through a thin face),
    upper half on the head, lower half on the jaw, textured with the darkest texel of the lip's own texture."""
    import numpy as np
    mid, cy = line.mid, line.cy
    C_in = Vector((mid.x - 0.02 * scale, 0.0, mid.z))
    inner, lip = 0, None
    for (m, i, p, mt, c) in head:
        if abs(p.y) > 1.3 * cy or abs(p.z - mid.z) > 0.025 * scale: continue
        if c == "mouth" and not force: return ""
        if p.x < mid.x - 0.004 * scale:
            n = F.R @ (m.matrix_world.to_3x3() @ m.data.vertices[i].normal)
            if n.dot(C_in - p) > 0: inner += 1
        elif c == "skin" and (lip is None or (p - mid).length < (lip[2] - mid).length): lip = (m, i, p)
    if inner >= 12 and not force: return ""
    if lip is None: return "no lip vertex found: no mouth interior added"
    m, li, _ = lip
    # the lip's material and the darkest texel its UVs reach
    poly = next(pl for pl in m.data.polygons if li in pl.vertices)
    mat_i = poly.material_index
    mat = m.data.materials[mat_i] if mat_i < len(m.data.materials) else None
    uvl = m.data.uv_layers.active
    uv_dark, how = None, "skin coloured"
    if uvl is not None:
        uvs = [uvl.data[k].uv[:] for pl in m.data.polygons if pl.material_index == mat_i for k in pl.loop_indices]
        img = base_image(mat)
        if img is not None and img.size[0] and uvs:
            w, h = img.size
            px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, img.channels)
            U = np.array(uvs, dtype=np.float32)
            xs = np.clip((U[:, 0] % 1.0) * w, 0, w - 1).astype(int); ys = np.clip((U[:, 1] % 1.0) * h, 0, h - 1).astype(int)
            lum = px[ys, xs, :3] @ np.array([0.3, 0.59, 0.11], np.float32)
            k = int(np.argmin(lum)); uv_dark = uvs[k]; how = f"darkest texel of {img.name} ({lum[k]:.2f})"
        elif uvs:
            uv_dark = uvs[0]
    # the bag: a narrow opening just behind the lips (the lips' own depth), widening inside like a mouth, closed at
    # the back; rings in the face frame
    a_rim, a_max = 0.9 * cy, 1.1 * cy
    b_rim, b_max = 0.005 * scale, 0.016 * scale
    x0, D = mid.x - 0.006 * scale, 0.04 * scale
    NU, NT = 16, 8
    def g(t):             # 0 at the opening .. 1 at the widest (t = 0.4) .. 0 at the back
        return math.sin(math.pi / 2 * t / 0.4) if t <= 0.4 else math.sqrt(max(0.0, 1 - ((t - 0.4) / 0.6) ** 2))
    rings = []
    for k in range(NT + 1):
        t = k / NT
        if k == NT:
            rings.append([Vector((x0 - D, 0.0, mid.z))]); continue
        gt = g(t)
        if t > 0.4: aw, bh = a_max * gt, b_max * gt
        else: aw, bh = a_rim + (a_max - a_rim) * gt, b_rim + (b_max - b_rim) * gt
        rings.append([Vector((x0 - D * t, aw * math.cos(2 * math.pi * j / NU), mid.z + bh * math.sin(2 * math.pi * j / NU)))
                      for j in range(NU)])
    Minv = m.matrix_world.inverted()
    bm = bmesh.new(); bm.from_mesh(m.data)
    dl = bm.verts.layers.deform.verify()
    uvlay = bm.loops.layers.uv.active
    gi = {g.name: g.index for g in m.vertex_groups}
    for g in ("head", "jaw"):
        if g not in gi: gi[g] = m.vertex_groups.new(name=g).index
    V = []
    for ring in rings:
        vr = []
        for q in ring:
            v = bm.verts.new(Minv @ F.world(q))
            dz = (q.z - mid.z) / (0.004 * scale)
            jaw = min(1.0, max(0.0, 0.5 - 0.5 * dz))          # below the lip line: jaw; blend over 8 mm
            if jaw > 0: v[dl][gi["jaw"]] = jaw
            if jaw < 1: v[dl][gi["head"]] = 1.0 - jaw
            vr.append(v)
        V.append(vr)
    faces = []
    for k in range(NT - 1):
        for j in range(NU):
            a, b, c, d = V[k][j], V[k][(j + 1) % NU], V[k + 1][(j + 1) % NU], V[k + 1][j]
            faces.append(bm.faces.new((a, d, c, b)))      # wound to face into the bag
    for j in range(NU):
        faces.append(bm.faces.new((V[NT - 1][j], V[NT][0], V[NT - 1][(j + 1) % NU])))
    for f in faces:
        f.material_index = mat_i; f.smooth = True
        if uvlay is not None and uv_dark is not None:
            for l in f.loops: l[uvlay].uv = uv_dark
    bm.normal_update()
    # faces must look into the bag (towards its axis): flip any that don't
    for f in faces:
        cen = F.loc(m.matrix_world @ f.calc_center_median())
        n = F.R @ (m.matrix_world.to_3x3() @ f.normal)
        if n.dot(Vector((max(cen.x, x0 - 0.4 * D), 0.0, mid.z)) - cen) < 0: f.normal_flip()
    bm.to_mesh(m.data); bm.free()
    return f"no mouth interior on the model: added a mouth cavity ({len(faces)} faces on {m.name}, {how})"


def weld_seams(head, Wm, meshes, F):
    """A head split into meshes (VRoid: the face ends at the jaw line, the neck is the body mesh) tears open where they
    meet when the jaw turns, unless both sides move alike: vertices on an open edge that sit on another mesh's vertex
    (within 1 mm) get the same weights (the average), inside the head region and with the neighbouring mesh."""
    heads = {}
    for (m, i, p, mt, c) in head: heads.setdefault(m, set()).add(i)
    allv = [(m, i, m.matrix_world @ v.co) for m in meshes for i, v in enumerate(m.data.vertices)]
    kd = KDTree(len(allv))
    for k, (m, i, q) in enumerate(allv): kd.insert(q, k)
    kd.balance()
    n = 0
    for m, idx in heads.items():
        cnt = {}
        for pl in m.data.polygons:
            for e in pl.edge_keys: cnt[e] = cnt.get(e, 0) + 1
        seam = {v for (a, b), k in cnt.items() if k == 1 for v in (a, b) if v in idx}
        for i in seam:
            q = m.matrix_world @ m.data.vertices[i].co
            grp = [(mm, ii) for co, k, d in kd.find_range(q, 0.001) for mm, ii, _ in [allv[k]] if mm is not m]
            if not grp: continue
            grp.append((m, i))
            avg = {}
            for mm, ii in grp:
                for bn, x in Wm[mm][ii].items(): avg[bn] = avg.get(bn, 0.0) + x / len(grp)
            for mm, ii in grp: Wm[mm][ii] = dict(avg)
            n += 1
    return n


def smooth_corners(head, Wm, line, scale, face, jaw_chain, iters=6):
    """Around the mouth corners, even out how much of each vertex follows the jaw (neighbours averaged a few times):
    single vertices on the wrong side there stretch into spikes when the mouth opens."""
    region = {}
    for (m, i, p, mt, c) in head:
        u = abs(p.y) / line.cy
        if 0.5 < u < 1.5 and abs(p.z - line.z(p.y)) < 0.015 * scale and p.x > line.mid.x - 0.05 * scale:
            region.setdefault(m, set()).add(i)
    n = 0
    for m, idx in region.items():
        W = Wm[m]
        def jf(i):
            w = W[i]; hw = sum(x for b, x in w.items() if b == "head" or b in face)
            return sum(x for b, x in w.items() if b in jaw_chain) / hw if hw > 0 else 0.0
        nb = {i: [] for i in idx}
        for e in m.data.edges:
            a, b = e.vertices
            if a in nb: nb[a].append(b)
            if b in nb: nb[b].append(a)
        val = {i: jf(i) for i in set(idx) | {j for l in nb.values() for j in l}}
        for _ in range(iters):
            val.update({i: (val[i] + sum(val[j] for j in nb[i])) / (1 + len(nb[i])) for i in idx if nb[i]})
        for i in idx:
            w = W[i]; hw = sum(x for b, x in w.items() if b == "head" or b in face)
            if hw <= 0: continue
            old = jf(i); new = val[i]
            if abs(new - old) < 0.02: continue
            ja = {b: x for b, x in w.items() if b in jaw_chain}
            ot = {b: x for b, x in w.items() if (b == "head" or b in face) and b not in jaw_chain}
            nw = {b: x for b, x in w.items() if b != "head" and b not in face}
            sj, so = sum(ja.values()), sum(ot.values())
            for b, x in (ja.items() if sj > 0 else [("jaw", 1.0)]): nw[b] = x / (sj or 1.0) * hw * new
            for b, x in (ot.items() if so > 0 else [("head", 1.0)]): nw[b] = nw.get(b, 0.0) + x / (so or 1.0) * hw * (1 - new)
            W[i] = nw; n += 1
    return n


def smooth_jaw_edges(head, Wm, line, scale, face, jaw_chain):
    """Where the jaw's share jumps between neighbouring vertices away from the lips (under the chin, along the jaw
    line: the template's weights end where its surface and the model's part, or the model's own rig ends there), the
    opening mouth tears the chin off with a hard edge. Around every such edge (within JAW_BLEND_CM) the jaw share is
    evened out over the mesh (neighbours averaged; the lip line and everything outside stay as they are)."""
    import numpy as np
    hv = {}
    for (m, i, p, mt, c) in head: hv.setdefault(m, {})[i] = p
    total = 0
    for m, P in hv.items():
        W = Wm[m]
        def jf(i):
            w = W[i]; hw = sum(x for b, x in w.items() if b == "head" or b in face)
            return sum(x for b, x in w.items() if b in jaw_chain) / hw if hw > 0 else 0.0
        def lip_band(p):
            return abs(p.y) < line.cy * 1.25 and abs(p.z - line.z(p.y)) < 0.007 * scale and \
                p.x > line.mid.x - 0.035 * scale
        free = {i for i, p in P.items() if not lip_band(p)}
        J = {}
        nb = {}
        jumps = set()
        lens = []
        for e in m.data.edges:
            a, b = e.vertices
            if a not in P or b not in P: continue
            nb.setdefault(a, []).append(b); nb.setdefault(b, []).append(a)
            ja = J[a] if a in J else J.setdefault(a, jf(a))
            jb = J[b] if b in J else J.setdefault(b, jf(b))
            if abs(ja - jb) > 0.3 and a in free and b in free:
                jumps.update((a, b)); lens.append((P[a] - P[b]).length)
        if not jumps: continue
        r = JAW_BLEND_CM * 0.01 * scale
        kd = KDTree(len(jumps))
        for k, i in enumerate(jumps): kd.insert(P[i], k)
        kd.balance()
        region = [i for i in free if i in nb and kd.find(P[i])[2] < r]
        el = float(np.median(lens)) if lens else r / 3
        iters = int(min(80, max(4, (r / max(el, 1e-4)) ** 2)))
        val = dict(J)
        for _ in range(iters):
            val.update({i: (val[i] + sum(val[j] for j in nb[i])) / (1 + len(nb[i])) for i in region})
        for i in region:
            w = W[i]; hw = sum(x for b, x in w.items() if b == "head" or b in face)
            if hw <= 0: continue
            old, new = J[i], val[i]
            if abs(new - old) < 0.02: continue
            ja = {b: x for b, x in w.items() if b in jaw_chain}
            ot = {b: x for b, x in w.items() if (b == "head" or b in face) and b not in jaw_chain}
            nw = {b: x for b, x in w.items() if b != "head" and b not in face}
            sj, so = sum(ja.values()), sum(ot.values())
            for b, x in (ja.items() if sj > 0 else [("jaw", 1.0)]): nw[b] = x / (sj or 1.0) * hw * new
            for b, x in (ot.items() if so > 0 else [("head", 1.0)]): nw[b] = nw.get(b, 0.0) + x / (so or 1.0) * hw * (1 - new)
            W[i] = nw; total += 1
    return total


JAW_BLEND_CM = 1.5      # smooth_jaw_edges: how far around a hard jaw-weight edge the share is evened out


BLINK_DEG = 28.0        # the game's blink turns the upper lids about this far (live, every hero; mesh-mods.md §12)


def fit_blink(head, Wm, F, scale, face):
    """The model's own blink shape key (captured as vectors before the fit) closes the eyes; the game closes them by
    turning eyelid_upper_<side> about BLINK_DEG. Big (anime, painted) eyes need a longer lever than the eye's centre
    gives: the pivot goes where one BLINK_DEG turn moves the lid edge onto its keyed place (behind the eye, level with
    the lid), and each lid vertex takes the share of that turn its key moves it. Returns {side: (pivot, note)}."""
    import numpy as np
    per_mesh = {}
    for (m, i, p, mt, c) in head:
        if m.data.attributes.get("b4b_face_blinkv") is not None: per_mesh.setdefault(m, []).append((i, p))
    lids = {"l": [], "r": []}
    for m, items in per_mesh.items():
        av, asrc = m.data.attributes["b4b_face_blinkv"], m.data.attributes["b4b_face_src"]
        src = np.array([tuple(asrc.data[i].vector) for i, p in items])
        dst = np.array([tuple(m.matrix_world @ m.data.vertices[i].co) for i, p in items])
        if len(items) < 8: continue
        # the fit moved the head as a whole (rotation, scale, offset): least-squares affine map source -> fitted
        X = np.hstack([src, np.ones((len(src), 1))])
        A = np.linalg.lstsq(X, dst, rcond=None)[0][:3].T
        A3 = Matrix(A.tolist())
        for i, p in items:
            d = F.R @ (A3 @ Vector(av.data[i].vector))
            if d.length > 1e-6: lids["l" if p.y > 0 else "r"].append((m, i, p, d))
    out = {}
    a = math.radians(BLINK_DEG); c_, s_ = math.cos(a), math.sin(a)
    def turn(u):          # (M - I) u in the (forward, up) plane; M turns the front of the lid down
        return Vector(((c_ - 1) * u.x + s_ * u.z, 0.0, -s_ * u.x + (c_ - 1) * u.z))
    det = (c_ - 1) ** 2 + s_ * s_
    def unturn(d):        # (M - I)^-1 d
        return Vector((((c_ - 1) * d.x - s_ * d.z) / det, 0.0, (s_ * d.x + (c_ - 1) * d.z) / det))
    for sd, L in lids.items():
        up = [x for x in L if x[3].z < 0]
        if len(up) < 6: continue
        mx = max(x[3].length for x in up)
        # the key also lifts the lower lid, which the game's blink leaves where it is: the upper lid goes that much
        # further (and a little extra: blinks peak near BLINK_DEG, not always at it)
        lo_mx = max([x[3].length for x in L if x[3].z > 0] or [0.0])
        more = 1.08 + min(0.35, lo_mx / mx)
        up = [(m, i, p, d * more) for m, i, p, d in up]
        mx *= more
        up = [x for x in up if x[3].length > 0.05 * mx]
        edge = [x for x in up if x[3].length > 0.7 * mx]
        wsum, P = 0.0, Vector()
        for m, i, p, d in edge:
            q = p - unturn(Vector((d.x, 0.0, d.z)))
            P += q * d.length; wsum += d.length
        P /= wsum
        P.y = sum(x[2].y for x in edge) / len(edge)
        front = sum(x[2].x for x in edge) / len(edge)
        if not (P.x < front - 0.003 * scale and (Vector((front, P.y, P.z)) - P).length < 0.15 * scale):
            continue
        n_full = 0
        for m, i, p, d in up:
            r = turn(Vector((p.x - P.x, 0.0, p.z - P.z))).length
            w = min(1.0, d.length / max(r, 1e-6))
            if w > 0.97: n_full += 1
            wd = Wm[m][i]
            hw = sum(x for b, x in wd.items() if b == "head" or b in face)
            if hw <= 0: continue
            nw = {b: x for b, x in wd.items() if b != "head" and b not in face}
            others = {b: x for b, x in wd.items() if (b == "head" or b in face) and not b.startswith("eyelid")}
            so = sum(others.values())
            for b, x in (others.items() if so > 0 else [("head", 1.0)]):
                nw[b] = x / (so if so > 0 else 1.0) * hw * (1.0 - w)
            nw[f"eyelid_upper_{sd}"] = hw * w
            Wm[m][i] = nw
        out[sd] = (P, f"{len(up)} lid vertices from the blink shape key, pivot {(Vector((front, P.y, P.z)) - P).length * 100:.1f} cm "
                      f"behind the lid for a {BLINK_DEG:.0f} deg turn ({n_full} close fully)")
    return out


LIP_BONES = ("lip_lower", "lip_lower_l", "lip_lower_r", "lip_upper", "lip_upper_l", "lip_upper_r",
             "lip_corner_lower_l", "lip_corner_lower_r", "lip_corner_upper_l", "lip_corner_upper_r")


def fit_lip_bones(arm, tverts, tpos, head, Wm, mpos, k, jaw_chain):
    """Lips curl like the survivor's (lip_lower turns up to 26 deg in speech, 46 in MBP): each lip bone's weights stay
    within the reach the survivor's own lip bone has (its 85th percentile / farthest weighted vertex, scaled by mouth
    width), fading out beyond (the rest goes to the jaw for the lower lip, the head for the upper lip; a curl that
    reached the chin made the lower lip pout and dented the chin), then the bone sits in the middle of what it moves
    as on the survivor (the bone turns the lip in place instead of swinging it around a pivot above it)."""
    out = []
    for b in LIP_BONES:
        if b not in tpos or b not in mpos: continue
        # the survivor's reach and centre for this bone
        ds, cen, wt = [], Vector(), 0.0
        for p, w, c, j in tverts:
            x = w.get(b, 0.0)
            if x > 0.01:
                cen += (p - tpos[b]) * x; wt += x
                if x > 0.1: ds.append((p - tpos[b]).length)
        if not ds or wt <= 0: continue
        ds.sort()
        r0 = ds[int(0.85 * (len(ds) - 1))] * k
        r1 = max(r0 * 1.2, ds[-1] * k * 1.1)
        off_t = cen / wt * k
        parent = arm.data.bones[b].parent.name if arm.data.bones[b].parent else "head"
        to = parent if parent in jaw_chain else "head"
        cut, mw, mc = 0.0, 0.0, Vector()
        for (m, i, p, mt, c) in head:
            w = Wm[m][i]
            x = w.get(b, 0.0)
            if x <= 0: continue
            d = (p - mpos[b]).length
            f = 1.0 if d <= r0 else 0.0 if d >= r1 else 0.5 + 0.5 * math.cos(math.pi * (d - r0) / (r1 - r0))
            if f < 1.0:
                w[b] = x * f
                w[to] = w.get(to, 0.0) + x * (1.0 - f)
                cut += x * (1.0 - f)
            mw += w[b]; mc += p * w[b]
        if mw <= 0: continue
        want = mc / mw - off_t
        dv = want - mpos[b]
        lim = 0.008 * k
        if dv.length > lim: dv = dv * (lim / dv.length)
        mpos[b] = mpos[b] + dv
        if cut > 0.5 or dv.length > 0.001:
            out.append(f"{b} {cut:.0f} weight moved to {to}, bone {dv.length * 100:.1f} cm")
    return "; ".join(out)


def fmt(v): return "(" + ", ".join(f"{x * 100:.1f}" for x in v) + ")"
