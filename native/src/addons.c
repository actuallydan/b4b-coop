// Add-ons (#20, epic #23), L4D style: drop-in paks, no in-game browser. docs/investigations/addons.md.
//   Folder: <game>\b4bcoop-addons\ (next to Back4Blood.exe; ini addons_dir=<windows path> moves it, addons=0 turns
//   add-ons off). Each add-on is one .pak written by tools/modkit/addon.py: cooked files plus an optional
//   b4bcoop-addoninfo.txt entry (title, author, version, category, description; `key=value` or L4D's
//   `addontitle "..."`). Any pak in our format loads; without addoninfo its file name is its title.
//   addonlist.txt in the folder: `<file>.pak=1|0`, load order top to bottom; later add-ons win conflicts. New add-ons
//   are appended switched on (sorted by name) and the file is rewritten. /addons on|off edits it (applies on restart).
//   Everything is read in DllMain (addons_scan, before the engine starts) so that paks.c installs no pak hook when
//   there is nothing to mount; the paks are mounted from the FPakPlatformFile::Initialize hook (addons_mount).
//   Our pak format only (tools/b4bpak.py): v9, magic 0x18772, plain index; the index SHA1 is verified here (a damaged
//   index would be a Fatal in the engine) and is the add-on's content id (#22: compare it across a session).
//   Conflicts: two enabled add-ons with the same file path; logged, listed by /addons, and one chat notice. "Mixed":
//   one package's files (.uasset/.uexp/.ubulk) end up from different add-ons, which can crash the game.
//   Each add-on is classified from its files (addonclass.c, #22): cosmetic or gameplay-affecting; the addoninfo's
//   own `content=` line is only compared. Add-ons stay client-side (each player sees their own); what a joiner runs is
//   summarized in the login options and checked against the host's addons_policy (addons_mp.c).
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "log.h"
#include "cmds.h"
#include "addonclass.h"

#define ADDON_ORDER 1000          // read order of the first add-on; retail paks are 4 (+100 per patch level)
#define PAK_MAGIC 0x18772u
#define PAK_FOOTER 222
#define ENTRY_HDR 53              // in-data FPakEntry of an uncompressed entry (B4B layout, tools/b4bpak.py)
#define INFO_NAME "b4bcoop-addoninfo.txt"
#define LIST_NAME "addonlist.txt"
#define MAX_CONFLICTS 64

typedef struct {
    wchar_t file[MAX_PATH];       // file name in the add-ons folder
    char name[MAX_PATH];          // same, UTF-8 (addonlist.txt key, chat)
    char title[96], author[64], version[32], category[48], desc[400], hash[41], claim[48];   // claim: addoninfo content=
    int nfiles, present, on, on_at_start, valid, mounted;   // mounted: 1 ok, -1 Mount failed, 0 not mounted
    char why[120];                // why it can't load
    uint32_t order;
    char **keys;                  // normalized file paths (conflict check, freed after the scan)
    AddonClass cls;               // cosmetic or gameplay, from the files (addonclass.c)
} Addon;
static Addon A[MAX_ADDONS];
static int nA, n_mount;
static int enabled_cfg = 1, list_dirty;
static char unavailable[80];
static wchar_t dirw[MAX_PATH];
static char dir8[MAX_PATH * 3];
typedef struct { int loser, winner, nfiles, mixed; char example[160], mixed_example[160]; } Conflict;
static Conflict C[MAX_CONFLICTS];
static int nC;
static CRITICAL_SECTION cs;       // addonlist.txt writes (chat thread) vs. nothing else after startup; cheap anyway

// ---- small helpers ----
static void w2u(const wchar_t *w, char *out, int n) { if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, n, NULL, NULL)) out[0] = 0; }
static void u2w(const char *u, wchar_t *out, int n) { if (!MultiByteToWideChar(CP_UTF8, 0, u, -1, out, n)) out[0] = 0; }
static char *trim(char *s) {
    while (*s == ' ' || *s == '\t') s++;
    char *e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n')) *--e = 0;
    return s;
}
static char *unquote(char *s) {
    s = trim(s);
    size_t l = strlen(s);
    if (l >= 2 && s[0] == '"' && s[l - 1] == '"') { s[l - 1] = 0; s++; }
    return s;
}

// SHA1 (the pak footer's index hash)
typedef struct { uint32_t h[5]; uint64_t len; uint8_t buf[64]; size_t n; } Sha1;
#define ROL(x, k) (((x) << (k)) | ((x) >> (32 - (k))))
static void sha1_block(Sha1 *s, const uint8_t *p) {
    uint32_t w[80], a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3], e = s->h[4];
    for (int i = 0; i < 16; i++) w[i] = (uint32_t)p[4 * i] << 24 | p[4 * i + 1] << 16 | p[4 * i + 2] << 8 | p[4 * i + 3];
    for (int i = 16; i < 80; i++) w[i] = ROL(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; }
        else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
        else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
        else { f = b ^ c ^ d; k = 0xCA62C1D6; }
        uint32_t t = ROL(a, 5) + f + e + k + w[i];
        e = d; d = c; c = ROL(b, 30); b = a; a = t;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e;
}
static void sha1(const uint8_t *p, size_t n, uint8_t out[20]) {
    Sha1 s = {{0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0}, 0, {0}, 0};
    s.len = (uint64_t)n * 8;
    for (; n >= 64; n -= 64, p += 64) sha1_block(&s, p);
    uint8_t t[128] = {0};
    memcpy(t, p, n);
    t[n] = 0x80;
    size_t tl = n + 9 <= 64 ? 64 : 128;
    for (int i = 0; i < 8; i++) t[tl - 1 - i] = (uint8_t)(s.len >> (8 * i));
    for (size_t o = 0; o < tl; o += 64) sha1_block(&s, t + o);
    for (int i = 0; i < 20; i++) out[i] = (uint8_t)(s.h[i / 4] >> (24 - 8 * (i % 4)));
}

static int read_at(HANDLE h, uint64_t off, void *buf, DWORD n) {
    LARGE_INTEGER li; li.QuadPart = (LONGLONG)off;
    DWORD got;
    return SetFilePointerEx(h, li, NULL, FILE_BEGIN) && ReadFile(h, buf, n, &got, NULL) && got == n;
}

// ---- addoninfo ----
static void parse_info(Addon *a, char *text) {
    for (char *line = strtok(text, "\n"); line; line = strtok(NULL, "\n")) {
        char *l = trim(line), *v;
        if (!*l || *l == '#' || *l == ';' || *l == '{' || *l == '}' || !strncmp(l, "//", 2)) continue;
        if ((v = strchr(l, '='))) *v++ = 0;                 // key=value
        else {                                               // L4D KeyValues: addontitle "value"
            if (*l == '"') { char *q = strchr(l + 1, '"'); if (!q) continue; v = q + 1; *q = 0; l++; }
            else { v = l + strcspn(l, " \t"); if (*v) *v++ = 0; }
        }
        char *k = unquote(l);
        v = unquote(v);
        if (!_strnicmp(k, "addon", 5)) k += 5;
        char *dst = NULL; size_t n = 0;
        if (!_stricmp(k, "title")) dst = a->title, n = sizeof a->title;
        else if (!_stricmp(k, "author")) dst = a->author, n = sizeof a->author;
        else if (!_stricmp(k, "version")) dst = a->version, n = sizeof a->version;
        else if (!_stricmp(k, "category")) dst = a->category, n = sizeof a->category;
        else if (!_stricmp(k, "description")) dst = a->desc, n = sizeof a->desc;
        else if (!_stricmp(k, "content")) dst = a->claim, n = sizeof a->claim;
        if (dst) snprintf(dst, n, "%s", v);
    }
}

// ---- pak index (our format only) ----
typedef struct { const uint8_t *p; size_t n, o; int bad; } Rd;
static const uint8_t *rd(Rd *r, size_t k) { if (r->bad || r->n - r->o < k) { r->bad = 1; return NULL; } const uint8_t *q = r->p + r->o; r->o += k; return q; }
static uint32_t rd32(Rd *r) { const uint8_t *q = rd(r, 4); uint32_t v = 0; if (q) memcpy(&v, q, 4); return v; }
static uint64_t rd64(Rd *r) { const uint8_t *q = rd(r, 8); uint64_t v = 0; if (q) memcpy(&v, q, 8); return v; }
static void rdstr(Rd *r, char *out, size_t n) {   // FString -> UTF-8
    int32_t len = (int32_t)rd32(r);
    out[0] = 0;
    if (len > 0 && len < 4096) {
        const uint8_t *q = rd(r, (size_t)len);
        if (q) snprintf(out, n, "%.*s", len, (const char *)q);
    } else if (len < 0 && len > -4096) {
        const uint8_t *q = rd(r, (size_t)-len * 2);
        if (q) {
            wchar_t w[4096];
            memcpy(w, q, (size_t)-len * 2);
            w[-len - 1] = 0;
            w2u(w, out, (int)n);
        }
    } else if (len) r->bad = 1;
}

static void norm_key(char *s) {   // mount point + name -> "gobi/content/..." (lower case, '/')
    for (char *c = s; *c; c++) *c = (char)tolower((unsigned char)(*c == '\\' ? '/' : *c));
    char *p = s;
    while (!strncmp(p, "../", 3)) p += 3;
    if (p != s) memmove(s, p, strlen(p) + 1);
}

// ---- content class (addonclass.c): each .uasset's header is read from the pak ----
typedef struct { uint64_t off, size; uint32_t method; char path[200]; } Ent;
typedef struct { const char *key; size_t kl; int owner; } Slot;   // key[0..kl): a file path or a package stem
static uint64_t fnv(const char *s, size_t n) { uint64_t h = 1469598103934665603ull; for (size_t i = 0; i < n; i++) h = (h ^ (uint8_t)s[i]) * 1099511628211ull; return h; }
static Slot *slot_of(Slot *t, size_t cap, const char *k, size_t kl) {
    for (size_t i = fnv(k, kl) & (cap - 1);; i = (i + 1) & (cap - 1))
        if (!t[i].key || (t[i].kl == kl && !memcmp(t[i].key, k, kl))) return &t[i];
}
static size_t stem_len(const char *k);

static void classify(Addon *a, HANDLE h, const Ent *ents) {
    size_t cap = 16;
    while (cap < (size_t)a->nfiles * 2 + 16) cap <<= 1;
    Slot *assets = calloc(cap, sizeof *assets);   // stems that have a .uasset in this add-on
    addonclass_begin(&a->cls);
    if (!assets) { a->cls.gameplay = 1; snprintf(a->cls.reason, sizeof a->cls.reason, "out of memory"); return; }
    for (int i = 0; i < a->nfiles; i++) {
        size_t l = strlen(a->keys[i]);
        if (l > 7 && !strcmp(a->keys[i] + l - 7, ".uasset")) { Slot *s = slot_of(assets, cap, a->keys[i], l - 7); s->key = a->keys[i]; s->kl = l - 7; }
    }
    for (int i = 0; i < a->nfiles; i++) {
        const char *k = a->keys[i];
        size_t sl = stem_len(k), l = strlen(k);
        int has_uasset = slot_of(assets, cap, k, sl)->key != NULL;
        uint8_t *data = NULL;
        if (l > 7 && !strcmp(k + l - 7, ".uasset") && !ents[i].method && ents[i].size <= (32u << 20)) {
            data = malloc(ents[i].size ? ents[i].size : 1);
            if (data && !read_at(h, ents[i].off + ENTRY_HDR, data, (DWORD)ents[i].size)) { free(data); data = NULL; }
        }
        addonclass_file(&a->cls, k, ents[i].path, has_uasset, data, data ? ents[i].size : 0);
        free(data);
    }
    free(assets);
}

static int load_pak(Addon *a, const wchar_t *path) {
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) { snprintf(a->why, sizeof a->why, "can't open (error %lu)", GetLastError()); return 0; }
    LARGE_INTEGER sz; uint8_t ft[PAK_FOOTER]; uint8_t *idx = NULL; int ok = 0;
    if (!GetFileSizeEx(h, &sz) || sz.QuadPart < PAK_FOOTER || !read_at(h, sz.QuadPart - PAK_FOOTER, ft, PAK_FOOTER)) {
        snprintf(a->why, sizeof a->why, "not a pak file"); goto out;
    }
    uint32_t ver, magic; uint64_t isz, ioff;
    memcpy(&ver, ft, 4); memcpy(&magic, ft + 4, 4); memcpy(&isz, ft + 45, 8); memcpy(&ioff, ft + 53, 8);
    if (magic != PAK_MAGIC || ver != 9) { snprintf(a->why, sizeof a->why, "damaged, or not a Back 4 Blood add-on pak (magic %#x v%u)", magic, ver); goto out; }
    if (ft[24]) { snprintf(a->why, sizeof a->why, "encrypted pak index: not made with tools/modkit/addon.py"); goto out; }
    if (ft[61] || isz > (64u << 20) || ioff + isz > (uint64_t)sz.QuadPart - PAK_FOOTER) { snprintf(a->why, sizeof a->why, "damaged pak (bad index position)"); goto out; }
    idx = malloc(isz ? isz : 1);
    uint8_t hash[20];
    if (!idx || !read_at(h, ioff, idx, (DWORD)isz)) { snprintf(a->why, sizeof a->why, "damaged pak (index unreadable)"); goto out; }
    sha1(idx, isz, hash);
    if (memcmp(hash, ft + 25, 20)) { snprintf(a->why, sizeof a->why, "damaged pak (index checksum wrong): download it again"); goto out; }
    for (int i = 0; i < 20; i++) snprintf(a->hash + 2 * i, 3, "%02x", hash[i]);
    Rd r = {idx, isz, 0, 0};
    char mount[520], name[1040], key[1600];
    rdstr(&r, mount, sizeof mount);
    int32_t n = (int32_t)rd32(&r);
    if (r.bad || n < 0 || n > 1000000) { snprintf(a->why, sizeof a->why, "damaged pak (bad index)"); goto out; }
    a->keys = calloc((size_t)n + 1, sizeof *a->keys);
    Ent *ents = calloc((size_t)n + 1, sizeof *ents);
    if (!a->keys || !ents) { snprintf(a->why, sizeof a->why, "out of memory"); free(ents); goto out; }
    for (int32_t i = 0; i < n && !r.bad; i++) {
        rdstr(&r, name, sizeof name);
        uint64_t off = rd64(&r), size = rd64(&r); rd64(&r);
        uint32_t method = rd32(&r);
        rd(&r, 20);
        if (method) { uint32_t nb = rd32(&r); rd(&r, (size_t)nb * 16); }
        rd32(&r); rd(&r, 1);
        if (r.bad) break;
        if (off > ioff || size > ioff - off) { snprintf(a->why, sizeof a->why, "damaged pak (file %s out of range)", name); free(ents); goto out; }
        snprintf(key, sizeof key, "%s%s", mount, name);
        norm_key(key);
        if (!strcmp(key, INFO_NAME)) {
            if (!method && size < 65536) {
                char *t = calloc(1, size + 1);
                if (t && read_at(h, off + ENTRY_HDR, t, (DWORD)size)) parse_info(a, t);
                free(t);
            }
            continue;
        }
        ents[a->nfiles].off = off; ents[a->nfiles].size = size; ents[a->nfiles].method = method;
        snprintf(ents[a->nfiles].path, sizeof ents[0].path, "%s", name);
        a->keys[a->nfiles++] = _strdup(key);
    }
    if (r.bad) { snprintf(a->why, sizeof a->why, "damaged pak (bad index)"); free(ents); goto out; }
    classify(a, h, ents);
    free(ents);
    ok = 1;
out:
    free(idx);
    CloseHandle(h);
    return ok;
}

static void free_keys(Addon *a) {
    if (!a->keys) return;
    for (int i = 0; i < a->nfiles; i++) free(a->keys[i]);
    free(a->keys);
    a->keys = NULL;
}

// "cosmetic (textures, materials)" / "gameplay (3 files)"
static const char *addon_class_str(const Addon *a, char *buf, size_t n) {
    char k[64];
    if (a->cls.gameplay) snprintf(buf, n, "gameplay (%d file%s)", a->cls.n_gameplay, a->cls.n_gameplay == 1 ? "" : "s");
    else snprintf(buf, n, "cosmetic%s%s%s", a->cls.kinds ? " (" : "", addonclass_kinds(a->cls.kinds, k, sizeof k), a->cls.kinds ? ")" : "");
    return buf;
}

// ---- conflicts: file path -> add-on that wins it (last in load order) ----
static Conflict *conflict(int loser, int winner) {
    for (int i = 0; i < nC; i++) if (C[i].loser == loser && C[i].winner == winner) return &C[i];
    if (nC == MAX_CONFLICTS) return NULL;
    Conflict *c = &C[nC++];
    memset(c, 0, sizeof *c);
    c->loser = loser; c->winner = winner;
    return c;
}
static size_t stem_len(const char *k) {   // "x/y.uexp" -> length of "x/y" for package files, else the whole path
    const char *dot = strrchr(k, '.'), *sl = strrchr(k, '/');
    if (!dot || (sl && dot < sl)) return strlen(k);
    if (!strcmp(dot, ".uasset") || !strcmp(dot, ".uexp") || !strcmp(dot, ".ubulk") || !strcmp(dot, ".uptnl") || !strcmp(dot, ".umap"))
        return (size_t)(dot - k);
    return strlen(k);
}

static void find_conflicts(void) {
    size_t total = 0, cap = 16;
    for (int i = 0; i < nA; i++) if (A[i].valid && A[i].on_at_start) total += A[i].nfiles;
    while (cap < total * 2 + 16) cap <<= 1;
    Slot *files = calloc(cap, sizeof *files), *stems = calloc(cap, sizeof *stems);
    if (!files || !stems) { free(files); free(stems); return; }
    for (int i = 0; i < nA; i++) {
        if (!A[i].valid || !A[i].on_at_start) continue;
        for (int j = 0; j < A[i].nfiles; j++) {
            const char *k = A[i].keys[j];
            Slot *s = slot_of(files, cap, k, strlen(k));
            if (s->key && s->owner != i) {
                Conflict *c = conflict(s->owner, i);
                if (c && !c->nfiles++) snprintf(c->example, sizeof c->example, "%s", k);
            }
            s->key = k; s->kl = strlen(k); s->owner = i;
        }
    }
    for (size_t i = 0; i < cap; i++) {   // one package from two add-ons (e.g. .uasset from A, .uexp from B)
        if (!files[i].key) continue;
        size_t sl = stem_len(files[i].key);
        Slot *s = slot_of(stems, cap, files[i].key, sl);
        if (!s->key) { s->key = files[i].key; s->kl = sl; s->owner = files[i].owner; continue; }
        if (s->owner == files[i].owner) continue;
        int lo = s->owner < files[i].owner ? s->owner : files[i].owner, hi = s->owner ^ files[i].owner ^ lo;
        Conflict *c = conflict(lo, hi);
        if (c && !c->mixed++) snprintf(c->mixed_example, sizeof c->mixed_example, "%.*s", (int)sl, files[i].key);
    }
    // stems[].key point into files[].key strings; both tables go, the keys themselves are freed with the add-ons
    free(files); free(stems);
}

// ---- addonlist.txt ----
static void list_path(wchar_t *out, size_t n) { swprintf(out, n, L"%ls\\" LIST_NAME, dirw); }

static int write_list(void) {
    wchar_t p[MAX_PATH + 32], tmp[MAX_PATH + 40];
    list_path(p, MAX_PATH + 32);
    swprintf(tmp, MAX_PATH + 40, L"%ls.tmp", p);
    FILE *f = _wfopen(tmp, L"wb");
    if (!f) { LOG("addons: can't write %ls", p); return 0; }
    fputs("# b4bcoop add-ons: one line per add-on (.pak file in this folder), =1 on, =0 off.\r\n"
          "# Load order is top to bottom: when two add-ons change the same file, the one further down wins.\r\n"
          "# New add-ons are added at the bottom, switched on. Changes apply the next time the game starts.\r\n", f);
    for (int i = 0; i < nA; i++) fprintf(f, "%s=%d\r\n", A[i].name, A[i].on);
    int ok = !fclose(f) && MoveFileExW(tmp, p, MOVEFILE_REPLACE_EXISTING);
    if (!ok) LOG("addons: can't replace %ls (error %lu)", p, GetLastError());
    return ok;
}

static Addon *add(const char *name8) {
    if (nA == MAX_ADDONS) return NULL;
    Addon *a = &A[nA++];
    memset(a, 0, sizeof *a);
    snprintf(a->name, sizeof a->name, "%s", name8);
    u2w(name8, a->file, MAX_PATH);
    a->on = 1;
    return a;
}
static Addon *by_name(const char *name8) {
    for (int i = 0; i < nA; i++) if (!_stricmp(A[i].name, name8)) return &A[i];
    return NULL;
}
static int cmp_name(const void *x, const void *y) { return _stricmp(*(const char **)x, *(const char **)y); }

static void read_list(void) {
    wchar_t p[MAX_PATH + 32];
    list_path(p, MAX_PATH + 32);
    FILE *f = _wfopen(p, L"rb");
    char line[700];
    while (f && fgets(line, sizeof line, f)) {
        char *l = trim(line), *v;
        if (!*l || *l == '#' || *l == ';' || !(v = strchr(l, '='))) continue;
        *v++ = 0;
        l = unquote(l); v = unquote(v);
        if (!*l || by_name(l)) continue;
        Addon *a = add(l);
        if (a) a->on = !(!strcmp(v, "0") || !_stricmp(v, "off") || !_stricmp(v, "false"));
    }
    if (f) fclose(f);
    else list_dirty = 1;
}

static void config(void) {
    FILE *f = fopen(cmds_config_path(), "r");
    char line[700], custom[MAX_PATH * 3] = "";
    while (f && fgets(line, sizeof line, f)) {
        char *l = trim(line), *v;
        if (*l == '#' || *l == ';' || !(v = strchr(l, '='))) continue;
        *v++ = 0;
        v = trim(v);
        if (!strcmp(l, "addons")) enabled_cfg = atoi(v) != 0;
        else if (!strcmp(l, "addons_dir") && *v) snprintf(custom, sizeof custom, "%s", v);
        else if (!strcmp(l, "addons_policy") && addons_policy_set(v)) LOG("addons: bad addons_policy=%s (any|cosmetic|none|match), keeping %s", v, addons_policy_name());
    }
    if (f) fclose(f);
    wchar_t want[MAX_PATH * 2];
    if (custom[0]) u2w(custom, want, MAX_PATH * 2);
    else {   // <game>\b4bcoop-addons: the DLL is in <game>\Gobi\Binaries\Win64
        extern char g_module_dir[];
        swprintf(want, MAX_PATH * 2, L"%hs..\\..\\..\\b4bcoop-addons", g_module_dir);
    }
    if (!GetFullPathNameW(want, MAX_PATH, dirw, NULL)) swprintf(dirw, MAX_PATH, L"%ls", want);
    size_t l = wcslen(dirw);
    while (l > 3 && (dirw[l - 1] == L'\\' || dirw[l - 1] == L'/')) dirw[--l] = 0;
    w2u(dirw, dir8, sizeof dir8);
}

static int is_ascii(const char *s) { for (; *s; s++) if ((unsigned char)*s >= 0x80) return 0; return 1; }

// DllMain (paks_early_init): read everything, decide what to mount. Returns the number of add-ons to mount.
int addons_scan(void) {
    InitializeCriticalSection(&cs);
    config();
    LOG("addons: policy for joiners: %s", addons_policy_name());
    if (!enabled_cfg) { LOG("addons: off (addons=0)"); return 0; }
    DWORD at = GetFileAttributesW(dirw);
    if (at == INVALID_FILE_ATTRIBUTES || !(at & FILE_ATTRIBUTE_DIRECTORY)) { LOG("addons: no add-ons folder (%s)", dir8); return 0; }
    read_list();
    // *.pak in the folder: listed ones keep their place, new ones are appended sorted by name, switched on
    char *fresh[MAX_ADDONS]; int nfresh = 0;
    wchar_t pat[MAX_PATH + 8];
    swprintf(pat, MAX_PATH + 8, L"%ls\\*.pak", dirw);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            char n8[MAX_PATH * 3];
            w2u(fd.cFileName, n8, sizeof n8);
            Addon *a = by_name(n8);
            if (a) { a->present = 1; continue; }
            if (nfresh < MAX_ADDONS) fresh[nfresh++] = _strdup(n8);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    qsort(fresh, nfresh, sizeof *fresh, cmp_name);
    for (int i = 0; i < nfresh; i++) {
        Addon *a = add(fresh[i]);
        if (a) { a->present = 1; list_dirty = 1; LOG("addons: new add-on %s, switched on", fresh[i]); }
        else LOG("addons: more than %d add-ons, %s ignored", MAX_ADDONS, fresh[i]);
        free(fresh[i]);
    }
    if (list_dirty) write_list();
    // read each add-on present; order = position in the list
    int pos = 0;
    for (int i = 0; i < nA; i++) {
        Addon *a = &A[i];
        a->on_at_start = a->on;
        if (!a->present) continue;
        a->order = ADDON_ORDER + pos++;
        wchar_t full[MAX_PATH * 2];
        swprintf(full, MAX_PATH * 2, L"%ls\\%ls", dirw, a->file);
        if (!is_ascii(a->name) || !is_ascii(dir8)) snprintf(a->why, sizeof a->why, "rename it (and its folder path) to plain letters, digits, - and _");
        else a->valid = load_pak(a, full);
        if (!a->title[0]) snprintf(a->title, sizeof a->title, "%.*s", (int)(strlen(a->name) - 4), a->name);
        if (a->valid && a->on) n_mount++;
        LOG("addons: %d. %s \"%s\" v%s by %s [%s]: %s, %d file(s), id %s%s%s", pos, a->name, a->title, a->version[0] ? a->version : "-",
            a->author[0] ? a->author : "-", a->category, a->on ? "on" : "off", a->nfiles, a->hash[0] ? a->hash : "-",
            a->valid ? "" : ", NOT LOADED: ", a->why);
        if (a->valid) {
            char kinds[80];
            LOG("addons:    content: %s%s%s", addon_class_str(a, kinds, sizeof kinds), a->cls.gameplay ? ": " : "", a->cls.gameplay ? a->cls.reason : "");
            int claim_g = !_strnicmp(a->claim, "gameplay", 8), claim_c = !_strnicmp(a->claim, "cosmetic", 8);
            if ((claim_g || claim_c) && claim_g != a->cls.gameplay)
                LOG("addons:    its addoninfo says content=%s; the files say %s (the files decide)", a->claim, a->cls.gameplay ? "gameplay" : "cosmetic");
        }
    }
    find_conflicts();
    for (int i = 0; i < nC; i++) {
        Conflict *c = &C[i];
        if (c->nfiles)
            LOG("addons: conflict: %s and %s both change %d file(s) (e.g. %s): %s wins (later in " LIST_NAME ")",
                A[c->loser].name, A[c->winner].name, c->nfiles, c->example, A[c->winner].name);
        if (c->mixed)
            LOG("addons: conflict: %s and %s each replace part of the same asset (%d file(s), e.g. %s): the game may crash; switch one off",
                A[c->loser].name, A[c->winner].name, c->mixed, c->mixed_example);
    }
    for (int i = 0; i < nA; i++) free_keys(&A[i]);
    LOG("addons: %s: %d add-on(s), %d to mount, %d conflict(s)", dir8, pos, n_mount, nC);
    return n_mount;
}

// Add-ons mounted in this game, in load order (addons_mp.c: login summary, host policy)
int addons_active(AddonRef *out, int max) {
    int k = 0;
    for (int i = 0; i < nA && k < max; i++) {
        const Addon *a = &A[i];
        if (a->mounted <= 0) continue;
        out[k].title = a->title; out[k].file = a->name; out[k].hash = a->hash;
        out[k].gameplay = a->cls.gameplay; out[k].kinds = a->cls.kinds; out[k].reason = a->cls.reason;
        k++;
    }
    return k;
}

void addons_unavailable(const char *why) { snprintf(unavailable, sizeof unavailable, "%s", why); }

// FPakPlatformFile::Initialize hook, right after the retail paks: mount in load order (later = higher read order).
void addons_mount(void) {
    for (int i = 0; i < nA; i++) {
        Addon *a = &A[i];
        if (!a->present || !a->valid || !a->on_at_start) continue;
        wchar_t full[MAX_PATH * 2];
        swprintf(full, MAX_PATH * 2, L"%ls\\%ls", dirw, a->file);
        a->mounted = paks_mount_unsigned(full, a->order) ? 1 : -1;
        LOG("addons: %s -> %s (read order %u)", a->name, a->mounted > 0 ? "mounted" : "MOUNT FAILED", a->order);
    }
}

// init_thread: one chat notice for the player (shown once they are in a map).
void addons_init(void) {
    int bad = 0, failed = 0;
    for (int i = 0; i < nA; i++) {
        if (A[i].present && A[i].on_at_start && !A[i].valid) bad++;
        if (A[i].mounted < 0) failed++;
    }
    char msg[300] = "";
    const Conflict *mix = NULL;
    for (int i = 0; i < nC && !mix; i++) if (C[i].mixed) mix = &C[i];
    if (unavailable[0] && n_mount) snprintf(msg, sizeof msg, "Add-ons are off: %s. /addons", unavailable);
    else if (mix)   // the one that can crash the game first
        snprintf(msg, sizeof msg, "Add-ons \"%s\" and \"%s\" mix parts of one asset (may crash): switch one off. /addons",
                 A[mix->loser].title, A[mix->winner].title);
    else if (nC == 1)
        snprintf(msg, sizeof msg, "Add-on conflict: \"%s\" overrides \"%s\". /addons", A[C[0].winner].title, A[C[0].loser].title);
    else if (nC) snprintf(msg, sizeof msg, "%d add-on conflicts. /addons for details", nC);
    else if (bad || failed) snprintf(msg, sizeof msg, "%d add-on(s) could not be loaded. /addons for details", bad + failed);
    if (msg[0]) { LOG("addons: notice: %s", msg); chat_local_later(msg); }
}

// ---- chat /addons ----
static const char *state_of(const Addon *a) {
    if (!a->present) return "missing";
    if (a->on != a->on_at_start) return a->on ? "on after restart" : "off after restart";
    if (!a->on) return "off";
    if (!a->valid) return "on, NOT LOADED";
    if (a->mounted > 0) return "on";
    if (a->mounted < 0) return "on, MOUNT FAILED";
    return unavailable[0] ? "on, not loaded" : "on";
}

static Addon *pick(const char *arg, int *num, Out *o) {   // "#", "3" or a file name (with or without .pak)
    while (arg && (*arg == ' ' || *arg == '#')) arg++;
    if (!arg || !*arg) { out_printf(o, "which add-on? /addons lists them with numbers\n"); return NULL; }
    // a number, the file name (with or without .pak) or the title; else a part of one of them matching one add-on
    int k = 0, nsub = 0, ksub = 0;
    Addon *sub = NULL;
    char want[MAX_PATH];
    snprintf(want, sizeof want, "%s", arg);
    for (char *c = want; *c; c++) *c = (char)tolower((unsigned char)*c);
    for (int i = 0; i < nA; i++) {
        if (!A[i].present) continue;
        k++;
        char base[MAX_PATH], hay[MAX_PATH + 100];
        snprintf(base, sizeof base, "%.*s", (int)(strlen(A[i].name) - 4), A[i].name);
        if (atoi(arg) == k || !_stricmp(arg, A[i].name) || !_stricmp(arg, base) || !_stricmp(arg, A[i].title)) { *num = k; return &A[i]; }
        snprintf(hay, sizeof hay, "%s|%s", A[i].name, A[i].title);
        for (char *c = hay; *c; c++) *c = (char)tolower((unsigned char)*c);
        if (strstr(hay, want)) { nsub++; sub = &A[i]; ksub = k; }
    }
    if (nsub == 1) { *num = ksub; return sub; }
    out_printf(o, nsub ? "\"%s\" matches %d add-ons: use the number (/addons)\n" : "no add-on \"%s\" (/addons)\n", arg, nsub);
    return NULL;
}

void addons_slash(const char *verb, char *rest, Out *o) {
    (void)verb;
    char *sub = rest ? strtok(rest, " ") : NULL, *arg = sub ? strtok(NULL, "") : NULL;
    if (sub) for (char *c = sub; *c; c++) *c = (char)tolower((unsigned char)*c);
    int present = 0;
    for (int i = 0; i < nA; i++) present += A[i].present;
    if (!sub || !strcmp(sub, "list")) {
        if (!enabled_cfg) { out_printf(o, "add-ons are off (addons=0 in b4bcoop.ini)\n"); return; }
        if (!present) { out_printf(o, "no add-ons. Put add-on .pak files in %s and restart\n", dir8); return; }
        if (unavailable[0]) out_printf(o, "add-ons are off: %s\n", unavailable);
        out_printf(o, "%d add-on(s), load order (a later one wins):\n", present);
        int k = 0;
        for (int i = 0; i < nA; i++) {
            Addon *a = &A[i];
            if (!a->present) continue;
            out_printf(o, "%d. %s%s%s [%s%s] %s\n", ++k, a->title, a->version[0] ? " " : "", a->version, state_of(a),
                       !a->valid ? "" : a->cls.gameplay ? ", gameplay" : ", cosmetic", a->name);
        }
        for (int i = 0; i < nC; i++) {
            if (C[i].nfiles) out_printf(o, "conflict: %s overrides %s (%d file(s))\n", A[C[i].winner].name, A[C[i].loser].name, C[i].nfiles);
            if (C[i].mixed) out_printf(o, "conflict: %s and %s mix parts of one asset: may crash, switch one off\n", A[C[i].loser].name, A[C[i].winner].name);
        }
        out_printf(o, "/addons on|off <#>  /addons info <#>  /addons players  /addons policy\n");
        return;
    }
    int num = 0;
    if (!strcmp(sub, "info")) {
        Addon *a = pick(arg, &num, o);
        if (!a) return;
        out_printf(o, "%d. %s%s%s (%s)\n", num, a->title, a->version[0] ? " " : "", a->version, a->name);
        if (a->author[0] || a->category[0])
            out_printf(o, "%s%s%s%s\n", a->author[0] ? "by " : "", a->author, a->author[0] && a->category[0] ? ", " : "", a->category);
        if (a->desc[0]) out_printf(o, "%s\n", a->desc);
        out_printf(o, "[%s] %d file(s)%s%s\n", state_of(a), a->nfiles, a->valid ? "" : ": ", a->valid ? "" : a->why);
        if (a->valid) {
            char k[80];
            out_printf(o, "content: %s%s%s\nid %.8s\n", addon_class_str(a, k, sizeof k), a->cls.gameplay ? ", e.g. " : "",
                       a->cls.gameplay ? a->cls.reason : "", a->hash);
        }
        return;
    }
    if (!strcmp(sub, "on") || !strcmp(sub, "off") || !strcmp(sub, "enable") || !strcmp(sub, "disable")) {
        int on = !strcmp(sub, "on") || !strcmp(sub, "enable");
        Addon *a = pick(arg, &num, o);
        if (!a) return;
        EnterCriticalSection(&cs);
        int was = a->on;
        a->on = on;
        int ok = was == on || write_list();
        if (!ok) a->on = was;
        LeaveCriticalSection(&cs);
        LOG("addons: /addons %s %s -> %s", sub, a->name, ok ? "saved" : "write FAILED");
        if (!ok) out_printf(o, "could not write %s\\" LIST_NAME "\n", dir8);
        else if (on == a->on_at_start) out_printf(o, "%s: %s (as now)\n", a->title, on ? "on" : "off");
        else out_printf(o, "%s: %s, applies after restart\n", a->title, on ? "on" : "off");
        if (ok && on && !a->valid) out_printf(o, "note: it can't load: %s\n", a->why);
        return;
    }
    if (addons_mp_slash(sub, arg, o)) return;
    out_printf(o, "usage: /addons [list]  /addons on|off <#>  /addons info <#>  /addons players  /addons policy [any|cosmetic|none|match]\n");
}
