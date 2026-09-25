// Add-on content classification (#22): cosmetic vs. gameplay-affecting, from the files an add-on overrides.
// docs/investigations/addons.md §7. modkit/addon.py (classify) implements the same rules for the packer; keep
// both in sync. Pure C (no engine, no Windows API): runs in DllMain from addons_scan, and in the offline harness.
//
// A package (.uasset; B4B: legacy version -7, unversioned, split .uasset/.uexp) is judged by the classes of all its
// exports, read from the package summary, name map, import map and export map (the export's ClassIndex; export
// entries are 104 bytes in this build, tools/modkit/upkg.py). Cosmetic = every export's class is in the cosmetic
// table below; anything else (physics assets, data/curve tables, blueprints, skeletons, maps,
// configs, unknown classes and file types) is gameplay. The author's `content=` line in the addoninfo is never
// trusted: addons.c logs when it differs.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "addonclass.h"

#define PKG_TAG 0x9E2A83C1u
#define EXPORT_SIZE 104

typedef struct { const uint8_t *p; size_t n, o; int bad; } Cur;
static const uint8_t *take(Cur *c, size_t k) { if (c->bad || c->n - c->o < k) { c->bad = 1; return NULL; } const uint8_t *q = c->p + c->o; c->o += k; return q; }
static int32_t ri32(Cur *c) { const uint8_t *q = take(c, 4); int32_t v = 0; if (q) memcpy(&v, q, 4); return v; }
static void skip_fstr(Cur *c) {
    int32_t len = ri32(c);
    if (len > 0 && len < 65536) take(c, (size_t)len);
    else if (len < 0 && len > -65536) take(c, (size_t)-len * 2);
    else if (len) c->bad = 1;
}

typedef struct { const char *s; int len; } Name;   // ANSI names only (class and module names are ASCII)
typedef struct { int32_t cls_name, outer, obj_name; } Import;   // name-map indices (-1 = none)

static int name_is(const Name *nm, int nn, int32_t i, const char *want) {
    if (i < 0 || i >= nn || !nm[i].s) return 0;
    size_t l = strlen(want);
    return (size_t)nm[i].len == l && !memcmp(nm[i].s, want, l);
}
static int name_prefix(const Name *nm, int nn, int32_t i, const char *pre) {
    if (i < 0 || i >= nn || !nm[i].s) return 0;
    size_t l = strlen(pre);
    return (size_t)nm[i].len >= l && !memcmp(nm[i].s, pre, l);
}

// ---- rules ----
typedef struct { const char *module, *cls; unsigned kind; } Rule;   // cls ending in '*' = prefix; cls NULL = whole module
static const Rule COSMETIC[] = {
    {"/Script/Engine", "Texture2D", AK_TEXTURES}, {"/Script/Engine", "TextureCube", AK_TEXTURES},
    {"/Script/Engine", "Texture2DArray", AK_TEXTURES}, {"/Script/Engine", "VolumeTexture", AK_TEXTURES},
    {"/Script/Engine", "TextureRenderTarget2D", AK_TEXTURES}, {"/Script/Engine", "TextureRenderTargetCube", AK_TEXTURES},
    {"/Script/Engine", "TextureLightProfile", AK_TEXTURES},
    {"/Script/Engine", "Material", AK_MATERIALS}, {"/Script/Engine", "MaterialInstanceConstant", AK_MATERIALS},
    {"/Script/Engine", "MaterialFunction*", AK_MATERIALS}, {"/Script/Engine", "SubsurfaceProfile", AK_MATERIALS},
    {"/Script/Engine", "SkeletalMesh", AK_MESHES}, {"/Script/Engine", "SkeletalMeshSocket", AK_MESHES},
    {"/Script/Engine", "MorphTarget", AK_MESHES}, {"/Script/Engine", "SkeletalMeshLODSettings", AK_MESHES},
    {"/Script/Engine", "StaticMesh", AK_MESHES}, {"/Script/Engine", "StaticMeshSocket", AK_MESHES},
    // a static mesh's own collision travels with it; on a joiner the host's collision is authoritative anyway
    {"/Script/Engine", "BodySetup", AK_MESHES}, {"/Script/NavigationSystem", "NavCollision", AK_MESHES},
    {"/Script/Engine", "NavCollision", AK_MESHES},
    // animations (their notifies' gameplay effects run on the host)
    {"/Script/Engine", "AnimSequence", AK_ANIMATIONS}, {"/Script/Engine", "AnimMontage", AK_ANIMATIONS},
    {"/Script/Engine", "AnimComposite", AK_ANIMATIONS}, {"/Script/Engine", "BlendSpace*", AK_ANIMATIONS},
    {"/Script/Engine", "AimOffsetBlendSpace*", AK_ANIMATIONS}, {"/Script/Engine", "PoseAsset", AK_ANIMATIONS},
    {"/Script/ClothingSystemRuntimeCommon", NULL, AK_MESHES}, {"/Script/ClothingSystemRuntimeNv", NULL, AK_MESHES},   // cloth
    {"/Script/Engine", "SoundWave", AK_SOUNDS}, {"/Script/Engine", "SoundCue", AK_SOUNDS},
    {"/Script/Engine", "SoundClass", AK_SOUNDS}, {"/Script/Engine", "SoundMix", AK_SOUNDS},
    {"/Script/Engine", "SoundAttenuation", AK_SOUNDS}, {"/Script/Engine", "SoundConcurrency", AK_SOUNDS},
    {"/Script/Engine", "SoundSubmix", AK_SOUNDS}, {"/Script/Engine", "ReverbEffect", AK_SOUNDS},
    {"/Script/Engine", "SoundNode*", AK_SOUNDS}, {"/Script/AkAudio", NULL, AK_SOUNDS},
    {"/Script/UMG", NULL, AK_UI}, {"/Script/MovieScene", NULL, AK_UI}, {"/Script/MovieSceneTracks", NULL, AK_UI},
    {"/Script/Engine", "Font", AK_UI}, {"/Script/Engine", "FontFace", AK_UI}, {"/Script/Engine", "StringTable", AK_UI},
    {"/Script/Engine", "SlateBrushAsset", AK_UI}, {"/Script/SlateCore", "SlateWidgetStyleAsset", AK_UI},
    {"/Script/CoreUObject", "Function", AK_UI}, {"/Script/CoreUObject", "DelegateFunction", AK_UI},
    {"/Script/CoreUObject", "SparseDelegateFunction", AK_UI},
    {"/Script/Engine", "ParticleSystem", AK_EFFECTS}, {"/Script/Engine", "ParticleEmitter", AK_EFFECTS},
    {"/Script/Engine", "ParticleSpriteEmitter", AK_EFFECTS}, {"/Script/Engine", "ParticleLODLevel", AK_EFFECTS},
    {"/Script/Engine", "ParticleModule*", AK_EFFECTS}, {"/Script/Engine", "Distribution*", AK_EFFECTS},
    {"/Script/Engine", "InterpCurveEdSetup", AK_EFFECTS}, {"/Script/Niagara", NULL, AK_EFFECTS},
};
// Readable reasons for the usual gameplay classes (anything else: "<Class>")
static const struct { const char *cls, *why; } GAMEPLAY[] = {
    {"PhysicsAsset", "physics asset"}, {"SkeletalBodySetup", "physics asset"}, {"PhysicsConstraintTemplate", "physics asset"},
    {"PhysicalMaterial", "physical material"},
    {"DataTable", "data table"}, {"GuidDataTable", "data table"}, {"CompositeDataTable", "data table"},
    {"CurveTable", "curve table"}, {"CompositeCurveTable", "curve table"}, {"Curve*", "curve"},
    {"BlueprintGeneratedClass", "blueprint"}, {"AnimBlueprintGeneratedClass", "animation blueprint"},
    {"Skeleton", "skeleton"},
    {"World", "map"}, {"LevelSequence", "level sequence"},
};

static int cls_match(const Name *nm, int nn, int32_t i, const char *pat) {
    size_t l = strlen(pat);
    if (l && pat[l - 1] == '*') {
        char pre[64];
        snprintf(pre, sizeof pre, "%.*s", (int)(l - 1), pat);
        return name_prefix(nm, nn, i, pre);
    }
    return name_is(nm, nn, i, pat);
}

static void set_gameplay(AddonClass *c, const char *why, const char *path) {
    c->gameplay = 1;
    c->n_gameplay++;
    if (!c->reason[0]) snprintf(c->reason, sizeof c->reason, "%s: %s", why, path);
}

static const char *base_name(const char *path) { const char *s = strrchr(path, '/'); return s ? s + 1 : path; }

// Returns 1 (and sets *kind) if (module, class) is cosmetic; else *why = a readable reason.
static int judge(const Name *nm, int nn, int32_t mod, int32_t cls, unsigned *kind, char *why, size_t wn) {
    for (size_t i = 0; i < sizeof COSMETIC / sizeof COSMETIC[0]; i++) {
        if (!name_is(nm, nn, mod, COSMETIC[i].module)) continue;
        if (!COSMETIC[i].cls || cls_match(nm, nn, cls, COSMETIC[i].cls)) { *kind = COSMETIC[i].kind; return 1; }
    }
    for (size_t i = 0; i < sizeof GAMEPLAY / sizeof GAMEPLAY[0]; i++)
        if (cls_match(nm, nn, cls, GAMEPLAY[i].cls)) { snprintf(why, wn, "%s", GAMEPLAY[i].why); return 0; }
    if (cls >= 0 && cls < nn && nm[cls].s) snprintf(why, wn, "%.*s", nm[cls].len > 60 ? 60 : nm[cls].len, nm[cls].s);
    else snprintf(why, wn, "unknown class");
    return 0;
}

// One cooked package header. Returns 1 if cosmetic (kinds |= what it is), 0 gameplay (why).
static int judge_package(const uint8_t *b, size_t n, unsigned *kinds, char *why, size_t wn) {
    Cur c = {b, n, 0, 0};
    uint32_t tag = (uint32_t)ri32(&c);
    int32_t legacy = ri32(&c);
    if (c.bad || tag != PKG_TAG || legacy != -7) { snprintf(why, wn, "unreadable package"); return 0; }
    ri32(&c); ri32(&c); ri32(&c);                        // LegacyUE3, UE4, licensee versions
    int32_t ncv = ri32(&c);
    if (ncv < 0 || ncv > 4096) { snprintf(why, wn, "unreadable package"); return 0; }
    take(&c, (size_t)ncv * 20);                          // custom versions
    ri32(&c);                                            // TotalHeaderSize
    skip_fstr(&c);                                       // FolderName
    uint32_t flags = (uint32_t)ri32(&c);
    int32_t nn = ri32(&c), no = ri32(&c);
    if (!(flags & 0x80000000u)) skip_fstr(&c);           // LocalizationId (not with PKG_FilterEditorOnly)
    ri32(&c); ri32(&c);                                  // GatherableTextData
    int32_t ec = ri32(&c), eo = ri32(&c), ic = ri32(&c), io = ri32(&c);
    if (c.bad || nn < 0 || nn > 200000 || ec < 0 || ec > 100000 || ic < 0 || ic > 100000 || no < 0 || eo < 0 || io < 0 ||
        (size_t)no > n || (size_t)eo > n || (size_t)io > n || (size_t)ec * EXPORT_SIZE > n - (size_t)eo ||
        (size_t)ic * 28 > n - (size_t)io) {
        snprintf(why, wn, "unreadable package"); return 0;
    }
    Name *nm = calloc((size_t)nn + 1, sizeof *nm);
    Import *im = calloc((size_t)ic + 1, sizeof *im);
    int ok = 0;
    if (!nm || !im) { snprintf(why, wn, "out of memory"); goto out; }
    c.o = (size_t)no;
    for (int32_t i = 0; i < nn && !c.bad; i++) {
        int32_t len = ri32(&c);
        if (len > 0 && len < 1024) { const uint8_t *q = take(&c, (size_t)len); if (q) { nm[i].s = (const char *)q; nm[i].len = len - 1; } }
        else if (len < 0 && len > -1024) take(&c, (size_t)-len * 2);   // UTF-16 name: never a class or module
        else if (len) c.bad = 1;
        take(&c, 4);                                                    // hashes
    }
    if (c.bad) { snprintf(why, wn, "unreadable package"); goto out; }
    c.o = (size_t)io;
    for (int32_t i = 0; i < ic; i++) {
        ri32(&c); ri32(&c);                              // ClassPackage
        im[i].cls_name = ri32(&c); ri32(&c);             // ClassName
        im[i].outer = ri32(&c);
        im[i].obj_name = ri32(&c); ri32(&c);             // ObjectName
    }
    int has_skel_import = 0, has_skel_mesh = 0;
    for (int32_t i = 0; i < ic; i++) if (name_is(nm, nn, im[i].cls_name, "Skeleton")) has_skel_import = 1;
    unsigned k = 0;
    for (int32_t e = 0; e < ec; e++) {
        int32_t ci;
        memcpy(&ci, b + eo + (size_t)e * EXPORT_SIZE, 4);
        unsigned kind = 0;
        if (ci < 0 && -(int64_t)ci <= ic) {
            const Import *ip = &im[-ci - 1];
            if (name_is(nm, nn, ip->cls_name, "WidgetBlueprintGeneratedClass")) { k |= AK_UI; continue; }   // a user widget
            if (!name_is(nm, nn, ip->cls_name, "Class")) {   // an instance of some other package's blueprint class
                int32_t on = ip->obj_name;
                int ok_name = on >= 0 && on < nn && nm[on].s;
                snprintf(why, wn, "instance of blueprint %.*s", ok_name ? (nm[on].len > 60 ? 60 : nm[on].len) : 1, ok_name ? nm[on].s : "?");
                goto out;
            }
            int32_t mod = ip->outer < 0 && -(int64_t)ip->outer <= ic ? im[-ip->outer - 1].obj_name : -1;
            if (!judge(nm, nn, mod, ip->obj_name, &kind, why, wn)) goto out;
            if (name_is(nm, nn, ip->obj_name, "SkeletalMesh")) has_skel_mesh = 1;
            k |= kind;
        } else if (ci > 0 && ci <= ec) {   // class defined in this package (a blueprint's default object)
            int32_t cc;
            memcpy(&cc, b + eo + (size_t)(ci - 1) * EXPORT_SIZE, 4);
            if (cc < 0 && -(int64_t)cc <= ic && name_is(nm, nn, im[-cc - 1].obj_name, "WidgetBlueprintGeneratedClass")) { k |= AK_UI; continue; }
            snprintf(why, wn, "blueprint");
            goto out;
        } else { snprintf(why, wn, "unreadable package"); goto out; }
    }
    if (has_skel_mesh && !has_skel_import) { snprintf(why, wn, "skeletal mesh without a game skeleton"); goto out; }
    *kinds |= k;
    ok = 1;
out:
    free(nm); free(im);
    return ok;
}

void addonclass_begin(AddonClass *c) { memset(c, 0, sizeof *c); }

static int ends(const char *s, const char *suf) { size_t a = strlen(s), b = strlen(suf); return a >= b && !strcmp(s + a - b, suf); }

// key: normalized path (lower case), path: as in the pak (for the reason text), has_uasset: the add-on also has this
// package's .uasset, data/n: the .uasset's bytes (only for .uasset files).
void addonclass_file(AddonClass *c, const char *key, const char *path, int has_uasset, const uint8_t *data, size_t n) {
    char why[96];
    if (ends(key, ".uasset")) {
        if (!data) { set_gameplay(c, "unreadable package", base_name(path)); return; }
        if (!judge_package(data, n, &c->kinds, why, sizeof why)) set_gameplay(c, why, base_name(path));
        return;
    }
    if (ends(key, ".uexp")) { if (!has_uasset) set_gameplay(c, "part of a package without its .uasset", base_name(path)); return; }
    if (ends(key, ".ubulk") || ends(key, ".uptnl")) return;   // bulk data (texture mips, mesh LODs, audio): cosmetic
    if (ends(key, ".umap")) { set_gameplay(c, "map", base_name(path)); return; }
    if (ends(key, ".locres") || ends(key, ".ufont")) { c->kinds |= AK_UI; return; }
    if (ends(key, ".bnk") || ends(key, ".wem")) { c->kinds |= AK_SOUNDS; return; }
    if (ends(key, ".ushaderbytecode") || ends(key, ".ushadercode")) { c->kinds |= AK_MATERIALS; return; }
    if (ends(key, ".ini")) { set_gameplay(c, "config", base_name(path)); return; }
    set_gameplay(c, "file type", base_name(path));
}

const char *addonclass_kinds(unsigned kinds, char *buf, size_t n) {
    static const char *K[] = {"textures", "materials", "meshes", "sounds", "ui", "effects", "animations"};
    size_t o = 0;
    buf[0] = 0;
    for (int i = 0; i < 7; i++)
        if (kinds & (1u << i)) o += (size_t)snprintf(buf + o, o < n ? n - o : 0, "%s%s", o ? ", " : "", K[i]);
    return buf;
}
