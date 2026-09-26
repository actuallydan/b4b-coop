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
            # blink: signed (down = upper lid, up = lower lid; models are Z-up after import); jaw: how far
            d = [x.length * (-1.0 if x.z < 0 else 1.0) for x in dv] if role == "blink" else [x.length for x in dv]
            tot = sum(abs(x) for x in d)
            if tot <= 1e-9: continue
            # blink: both-eye keys or one per side (summed); jaw: the key that moves the most
            if role == "blink" and side_of(kname):
                best.setdefault(("blink", side_of(kname)), (tot, kname, d))
            elif tot > best.get((role, None), (0,))[0] or (role == "jaw" and re.fullmatch(r"a|aa", kname, re.I)):
                best[(role, None)] = (tot, kname, d)
        for role in ("jaw", "blink"):
            parts = [best[k] for k in best if k[0] == role]
            if role == "blink" and (role, None) in best: parts = [best[(role, None)]]
            if not parts: continue
            vals = [sum(p[2][i] for p in parts) for i in range(len(m.data.vertices))]
            a = m.data.attributes.get(f"b4b_face_{role}") or m.data.attributes.new(f"b4b_face_{role}", "FLOAT", "POINT")
            a.data.foreach_set("value", vals)
            found.setdefault(role, []).extend(p[1] for p in parts)
    return found


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


def vertex_materials(m):
    mats = [re.sub(r"\.\d{3}$", "", x.name) if x else "" for x in m.data.materials]
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
    for tm in tpl.meshes:
        W = weights_of(tm)
        mats = vertex_materials(tm)
        per_mat = {}
        for w, mt in zip(W, mats):
            c = per_mat.setdefault(mt, [0, 0]); c[1] += 1
            if any(b in face for b in w): c[0] += 1
        use = {mt for mt, (a, n) in per_mat.items() if n and a / n > 0.03 and not HAIR_RX.search(mt)}
        for v, w, mt in zip(tm.data.vertices, W, mats):
            if mt not in use: continue
            if not any(b in face or b == "head" for b in w): continue
            cls = "eye" if EYE_RX.search(mt) else "mouth" if MOUTH_RX.search(mt) else "skin"
            jw = sum(x for b, x in w.items() if b in jaw_chain)
            verts.append((F.loc(tm.matrix_world @ v.co), w, cls, jw))
    prof = profile_landmarks([p for p, w, c, j in verts if c == "skin"], lm, 1.0)
    lm["crease"] = prof["crease"] if "crease" in prof and lm["lip_lo"].z < prof["crease"].z < lm["lip_up"].z \
        else (lm["lip_up"] + lm["lip_lo"]) / 2
    return face, jaw_chain, lm, verts


# ---- model landmarks --------------------------------------------------------------------------------------------------

def model_head(meshes, face, F, tl):
    """Model vertices that may take face weights: [(mesh, index, face-frame pos, material, class)]."""
    out = []
    zmin = min(tl["chin"].z, tl["mouth_l"].z) - 0.06
    for m in meshes:
        W = weights_of(m)
        mats = vertex_materials(m)
        for i, (v, w, mt) in enumerate(zip(m.data.vertices, W, mats)):
            hw = sum(x for b, x in w.items() if b == "head" or b in face)
            if hw < 0.05: continue
            if (HAIR_RX.search(mt) and not OVERLAY_RX.search(mt)) or GEAR_RX.search(mt) or HIGHLIGHT_RX.search(mt):
                continue
            p = F.loc(m.matrix_world @ v.co)
            if p.z < zmin: continue
            cls = "eye" if EYE_RX.search(mt) else "mouth" if MOUTH_RX.search(mt) else \
                "overlay" if OVERLAY_RX.search(mt) else "skin"
            out.append((m, i, p, mt, cls))
    return out


def bbox(ps):
    lo = Vector([min(p[k] for p in ps) for k in range(3)]); hi = Vector([max(p[k] for p in ps) for k in range(3)])
    return lo, hi


def seed_warp(tl, tverts, head):
    """Template -> model, first guess: per-axis scale/offset from the two heads' skin bounding boxes (front half)."""
    ts = [p for p, w, c, j in tverts if c == "skin" and p.x > 0]
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
    return dict(sorted(prof.items()))


def profile_landmarks(pts, seed, scale, lips=None):
    """Nose tip, lip crease, upper/lower lip, chin from the middle profile, searched around the seeds (lips: the
    upper and lower lip already known: only nose and chin)."""
    zs = [seed["nose"].z + 0.03 * scale, seed["chin"].z - 0.03 * scale] if lips is None else \
        [lips[0].z + 0.06 * scale, lips[1].z - 0.045 * scale]
    prof = front_profile(pts, zs[1], zs[0], 0.004 * scale)
    if len(prof) < 10: return {}
    ks = list(prof)
    # smooth over 3 bins
    sm = {k: sum(prof[x] for x in ks[max(0, i - 1):i + 2]) / len(ks[max(0, i - 1):i + 2]) for i, k in enumerate(ks)}
    def near(z, r):
        return [k for k in ks if abs(k - z) <= r]
    out = {}
    if lips is not None:
        ku = min(ks, key=lambda x: abs(x - lips[0].z)); kl = min(ks, key=lambda x: abs(x - lips[1].z))
        return chin_nose(out, ks, sm, ku, kl, scale)
    lm = (seed["lip_up"].z + seed["lip_lo"].z) / 2
    # crease: the deepest dent (x below the maxima on both sides) near the seed's mouth line
    cand = near(lm, 0.015 * scale)
    best, bd = None, 0.0
    for k in cand:
        above = [sm[x] for x in ks if k < x <= k + 0.012 * scale]
        below = [sm[x] for x in ks if k - 0.012 * scale <= x < k]
        if not above or not below: continue
        d = min(max(above), max(below)) - sm[k]
        if d > bd: best, bd = k, d
    # a gap in the profile (open mouth / slit between the lips) also marks it
    if best is None or bd < 0.001 * scale:
        return {}
    out["crease"] = Vector((sm[best], 0.0, best))
    up = [x for x in ks if best < x <= best + 0.012 * scale]
    lo = [x for x in ks if best - 0.012 * scale <= x < best]
    ku = max(up, key=lambda x: sm[x]); kl = max(lo, key=lambda x: sm[x])
    out["lip_up"] = Vector((sm[ku], 0.0, ku)); out["lip_lo"] = Vector((sm[kl], 0.0, kl))
    return chin_nose(out, ks, sm, ku, kl, scale)


def chin_nose(out, ks, sm, ku, kl, scale):
    """Chin: the front-most point below the lower lip; nose tip: the front-most above the upper lip."""
    ch = [x for x in ks if kl - 0.035 * scale <= x < kl - 0.005 * scale]
    if ch:
        kc = max(ch, key=lambda x: sm[x]); out["chin"] = Vector((sm[kc], 0.0, kc))
    no = [x for x in ks if ku + 0.008 * scale <= x <= ku + 0.05 * scale]
    if no:
        kn = max(no, key=lambda x: sm[x]); out["nose"] = Vector((sm[kn], 0.0, kn))
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
    if src_bones:
        for n, (h, t) in src_bones.items():
            if SRC_BONE_RULES[0][1].search(n) and not re.search(r"lid|lash|brow|target|socket|set$|master|handle", n, re.I):
                sd = side_of(n)
                if sd:
                    p = F.loc(h)
                    if (p - seed[f"eye_{sd}"]).length < 0.05 * scale and sd not in eyes: eyes[sd] = (p, None, f"bone {n}")
    t_r = (tl["eye_out_l"] - tl["eye_in_l"]).length / 2
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
                          0.5 * t_r * scale if sd in eyes else 0.045 * scale)
        if not near: continue
        ps = [p for g in near for p in g[2]]
        lo, hi = bbox(ps); c = (lo + hi) / 2; ext = hi - lo
        eyeball_verts.update((m, i) for m, comp, _ in near for i in comp)
        if sd in eyes:
            eyes[sd] = (eyes[sd][0], max(ext.y, ext.z) / 2, eyes[sd][2]); continue
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
    prof = profile_landmarks(skin, seed, scale, (lm["lip_up"], lm["lip_lo"]) if got_mouth else None)
    if not got_mouth and "crease" in prof:
        lm["lip_up"], lm["lip_lo"] = prof["lip_up"], prof["lip_lo"]
        src["lip_up"] = src["lip_lo"] = "face profile"
        # corners: the template's mouth width relative to the eyes, on the model's surface at the crease height
        ew_t = (tl["eye_l"] - tl["eye_r"]).length; ew_m = (lm["eye_l"] - lm["eye_r"]).length
        dz = (tl["mouth_l"].z - (tl["lip_up"].z + tl["lip_lo"].z) / 2)
        for sd in ("l", "r"):
            y = tl[f"mouth_{sd}"].y * ew_m / ew_t
            z = prof["crease"].z + dz * scale
            p = surface_near(kd, skin, y, z, prof["crease"].x, 0.01 * scale)
            lm[f"mouth_{sd}"] = p if p is not None else Vector((prof["crease"].x - (tl["lip_up"].x - tl[f"mouth_{sd}"].x), y, z))
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
    return lm, scale, {k for k, v in src.items() if not v.startswith("scaled")}


def mouth_from_shape_key(head, seed, scale):
    """Lip line from a mouth-open key: at the front of the face, the top of what it moves is the lower lip, the first
    vertex above that stays is the upper lip; the corners are the sides of the moved lip region."""
    pts = []
    for (m, i, p, mt, c) in head:
        a = m.data.attributes.get("b4b_face_jaw")
        if a is None or c not in ("skin", "overlay"): continue
        pts.append((p, a.data[i].value))
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
    rx = re.compile(r"jaw|chin|lip\.?b\b|lip\.b\.|lower_?lip|lip_?lower|teeth\.b|lower_?teeth|teeth_?lower|tongue", re.I)
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
        F_ = {b: x / share * fade for b, x in tw.items() if b in face}
        return F_

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
    for m in meshes: set_weights(m, Wm[m])
    # new bind positions: every face bone mapped onto the model's face
    moved = {}
    worst = 0.0
    for b in sorted(face):
        pt = F.loc(arm.matrix_world @ arm.data.bones[b].head_local)
        pm = to_mdl(pt)
        worst = max(worst, (pm - pt).length)
        moved[b] = F.world(pm)
    for k in ("eye_l", "mouth_l", "lip_up"):
        log(f"face: {k} template {fmt(tl[k])} -> model {fmt(ml[k])} cm (face frame: forward, left, up)")
    log(f"face: {stats['verts']} vertices skinned to face bones ({stats['jaw']} jaw/lower lip, {stats['lids']} eyelids, "
        f"{stats['eyes']} eyeballs); {len(moved)} face bones moved onto the model's face (up to {worst * 100:.1f} cm)")
    return moved


def fmt(v): return "(" + ", ".join(f"{x * 100:.1f}" for x in v) + ")"
