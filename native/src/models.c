// Runtime model swaps between assets the game ships (issue #19, Tier 0). Findings: docs/investigations/model-swap.md.
//
// How a survivor's look is built: FCharacterCustomizationSet = 4 FDataTableRowHandles (Head, Torso, Legs, Outfit) +
// LastEquipSlot, each a row of the hero's CharacterCustomizationTable (CharacterDefinitionRow +0x88), whose
// FCharacterCustomizationRow holds the first- and third-person HeroMeshDefinition (soft SkeletalMesh + material slot
// overrides). The set lives in the replicated APlayerSlot::CurrentCustomizationSet (+0x318). Its OnRep (0x141A123C0,
// also run by the server) hands it to the slot's HeroCharacter (0x141BE5E20), which async-loads the meshes and puts
// them on its body/head/legs/first-person components. Clients push their own set with the server RPC
// AGobiPlayerState::ServerSelectCustomizationSet, whose implementation copies it into their slot WITHOUT validation
// (_Validate returns true) and runs the OnRep. Rows are looked up by (table, row) only, so a row of ANOTHER hero's
// table works: that is a cross-survivor outfit swap, applied by every machine's own game code from the replicated
// slot. Everyone sees it, even players without b4bcoop. Nothing is written to the profile (the profile side is a
// separate EquipCharacterCustomizationSet command).
//
// What we do:
//   - /model <name> (anyone, for yourself): send ServerSelectCustomizationSet(current set + the chosen row(s)) for our
//     own player state (host: runs locally). The wish is kept for the session and re-sent whenever our slot's set no
//     longer matches it (new map, respawned slot, the game re-sending our profile set).
//   - /model <player> <name> (host): write that slot's set directly + run its OnRep (replicates to everyone); kept
//     and re-applied the same way, dropped when a human takes over a bot's slot.
//   - /model reset: the game's own re-init from the profile (ClientInitCustomizationRowForSelectedCharacter), or for a
//     bot its hero's default skin.
//   - /models off|on (host): hook on ServerSelectCustomizationSet_Implementation refuses sets with another survivor's
//     rows; turning it off resets every such slot.
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

typedef FName *(*FNameCtorFn)(FName *self, const wchar_t *name, int find_type);
#define ADDR_FNAME_CTOR VA(0x1424BC8E0ull)

// AGobiPlayerState::ServerSelectCustomizationSet_Implementation (vtable +0x8c0)
#define ADDR_SELECTSET VA(0x141BD6DC0ull)
static const uint8_t SIG_SELECTSET[] = {0x40,0x53,0x48,0x83,0xec,0x20,0x48,0x8d,0x99,0x4c,0x05,0x00,0x00,0x4c,0x8b,0xda,
                                        0x48,0x8b,0xcb};

// FDataTableRowHandle in this build: {UDataTable*, FName RowName, FString (unreflected display name)} = 0x20
typedef struct { UObject *table; FName row; FString display; } RowHandle;
typedef struct { RowHandle slot[4]; uint8_t last, pad[7]; } CustSet;   // FCharacterCustomizationSet (0x88)
enum { SLOT_HEAD, SLOT_TORSO, SLOT_LEGS, SLOT_OUTFIT };
static const char *SLOTN[] = {"head", "torso", "legs", "outfit"};

#define DT_ROWSTRUCT(t)   (*(UObject **)((char *)(t) + 0x30))
#define DT_ROWMAP(t)      ((void *)((char *)(t) + 0x38))
// CharacterDefinitionRow
#define CD_SLUG(r)        (*(FName *)((char *)(r) + 0x08))
#define CD_CUSTTABLE(r)   (*(UObject **)((char *)(r) + 0x88))
#define CD_DEFSKIN(r)     ((RowHandle *)((char *)(r) + 0x90))
// CharacterCustomizationRow
#define CC_SLOT(r)        (*(uint8_t *)((char *)(r) + 0x48))
#define CC_FP(r)          ((char *)(r) + 0x50)    // HeroMeshDefinition
#define CC_3P(r)          ((char *)(r) + 0x118)
#define HMD_MESHPATH(d)   (*(FName *)((char *)(d) + 0xa0 + 0x10))  // SoftObjectPtr -> FSoftObjectPath.AssetPathName
#define HMD_MATS(d)       ((TArray *)((char *)(d) + 0x00))        // TMap<FName, TSoftObjectPtr<MaterialInterface>>

static int fname_eq(FName a, FName b) { return a.idx == b.idx && a.num == b.num; }
static FName make_name(const char *s) {
    wchar_t w[512]; int k = 0;
    for (; s[k] && k < 511; k++) w[k] = (wchar_t)(unsigned char)s[k];
    w[k] = 0;
    FName n = {0};
    ((FNameCtorFn)ADDR_FNAME_CTOR)(&n, w, 1);
    return n;
}
static void *sparse_at(const void *set, int i, int stride) {
    const TArray *a = set;
    if (!a->data || i < 0 || i >= a->num || i >= *(const int32_t *)((const char *)set + 0x28)) return NULL;
    const uint32_t *bits = *(uint32_t *const *)((const char *)set + 0x20);
    if (!bits) bits = (const uint32_t *)((const char *)set + 0x10);
    return (bits[i >> 5] >> (i & 31)) & 1 ? (char *)a->data + (size_t)i * stride : NULL;
}
static uint8_t *dt_row(UObject *table, FName row) {
    if (!table) return NULL;
    TArray *m = DT_ROWMAP(table);
    for (int i = 0; i < m->num; i++) {
        char *e = sparse_at(m, i, 0x18);
        if (e && fname_eq(*(FName *)e, row)) return *(uint8_t **)(e + 8);
    }
    return NULL;
}
static int is_rowstruct(UObject *t, const char *name) {
    char b[128];
    UObject *rs = t ? DT_ROWSTRUCT(t) : NULL;
    return rs && !strcmp(ue_obj_name(rs, b, sizeof b), name);
}
static int is_live(UObject *o) { return o && !(U_FLAGS(o) & 0x30); }
static UFunction *fn_of(UObject *o, const char *name) { return o ? ue_find_function(U_CLASS(o), name) : NULL; }
static int32_t parm_off(UFunction *f, const char *p) { FField *x = f ? ue_find_prop(f, p) : NULL; return x ? FP_OFFSET(x) : -1; }

// ---- catalogue: every row of every hero's customization table (loaded with the hero definitions) ----
typedef struct {
    char name[48];       // friendly: third-person mesh name without 3P_/_SKM, lower case ("holly_elite_04")
    int hero;            // index into heroes[]
    UObject *table;      // customization table
    FName row;
    int slot;
    char mesh3p[160];
} Entry;
#define MAX_ENTRIES 768
static Entry cat[MAX_ENTRIES];
static int n_cat;
typedef struct { char slug[32]; UObject *deftable, *custtable; FName defrow; RowHandle defskin; int first, count; } Hero;
static Hero heroes[32];
static int n_heroes;
static UObject *cat_world;

static void friendly(const char *path, char *out, size_t n) {
    const char *b = strrchr(path, '.');
    b = b ? b + 1 : path;
    if (!_strnicmp(b, "3P_", 3)) b += 3;
    size_t k = 0;
    for (; *b && k + 1 < n; b++) out[k++] = (char)tolower((unsigned char)*b);
    out[k] = 0;
    if (k > 4 && !strcmp(out + k - 4, "_skm")) out[k - 4] = 0;
}

static void build_catalogue(void) {
    n_cat = n_heroes = 0;
    cat_world = ue_world();
    int32_t n = ue_num_objects();
    UClass *dtc = ue_find_class("DataTable");
    char b[64];
    for (int32_t i = 0; dtc && i < n; i++) {
        UObject *t = ue_object_at(i);
        if (!is_live(t) || !ue_is_a(t, dtc) || !is_rowstruct(t, "CharacterDefinitionRow")) continue;
        TArray *m = DT_ROWMAP(t);
        for (int k = 0; k < m->num && n_heroes < 32; k++) {
            char *e = sparse_at(m, k, 0x18);
            uint8_t *r = e ? *(uint8_t **)(e + 8) : NULL;
            if (!r) continue;
            Hero *h = &heroes[n_heroes];
            memset(h, 0, sizeof *h);
            ue_name(CD_SLUG(r), h->slug, sizeof h->slug);
            for (char *c = h->slug; *c; c++) *c = (char)tolower((unsigned char)*c);
            h->deftable = t; h->defrow = *(FName *)e; h->custtable = CD_CUSTTABLE(r); h->defskin = *CD_DEFSKIN(r);
            h->defskin.display.data = NULL; h->defskin.display.num = h->defskin.display.max = 0;
            h->first = n_cat;
            UObject *ct = h->custtable;
            if (ct && is_rowstruct(ct, "CharacterCustomizationRow")) {
                TArray *cm = DT_ROWMAP(ct);
                for (int j = 0; j < cm->num && n_cat < MAX_ENTRIES; j++) {
                    char *ce = sparse_at(cm, j, 0x18);
                    uint8_t *cr = ce ? *(uint8_t **)(ce + 8) : NULL;
                    if (!cr) continue;
                    Entry *en = &cat[n_cat++];
                    memset(en, 0, sizeof *en);
                    en->hero = n_heroes; en->table = ct; en->row = *(FName *)ce; en->slot = CC_SLOT(cr) & 3;
                    ue_name(HMD_MESHPATH(CC_3P(cr)), en->mesh3p, sizeof en->mesh3p);
                    if (strcmp(en->mesh3p, "None") && en->mesh3p[0]) friendly(en->mesh3p, en->name, sizeof en->name);
                    else snprintf(en->name, sizeof en->name, "%s_%s_%s", h->slug, SLOTN[en->slot], ue_name(en->row, b, 9));
                }
            }
            h->count = n_cat - h->first;
            n_heroes++;
        }
    }
    // duplicate friendly names (material-only variants share a mesh): suffix _2, _3 ...
    for (int i = 0; i < n_cat; i++) {
        int dup = 1;
        for (int j = 0; j < i; j++) if (!strcmp(cat[i].name, cat[j].name)) dup++;
        if (dup > 1) { size_t l = strlen(cat[i].name); snprintf(cat[i].name + (l > 40 ? 40 : l), 8, "_%d", dup); }
    }
    LOG("models: catalogue %d heroes, %d customization rows", n_heroes, n_cat);
}
static void need_catalogue(void) { if (!n_cat) build_catalogue(); }

static const Entry *entry_by_row(UObject *table, FName row) {
    for (int i = 0; i < n_cat; i++) if (cat[i].table == table && fname_eq(cat[i].row, row)) return &cat[i];
    return NULL;
}
static int hero_by_table(UObject *custtable) {
    for (int i = 0; i < n_heroes; i++) if (heroes[i].custtable == custtable) return i;
    return -1;
}
static int hero_by_slug(const char *s) {
    for (int i = 0; i < n_heroes; i++) if (!_stricmp(heroes[i].slug, s)) return i;
    return -1;
}

static void handle_str(const RowHandle *h, char *buf, size_t n) {
    char t[128], r[128];
    const Entry *e = entry_by_row(h->table, h->row);
    if (!h->table) { snprintf(buf, n, "-"); return; }
    if (e) snprintf(buf, n, "%s", e->name);
    else snprintf(buf, n, "%s/%s", ue_obj_name(h->table, t, sizeof t), ue_name(h->row, r, sizeof r));
}

// ---- players, slots, sets ----
static UObject *my_ps(void) { UObject *pc = ue_local_pc(); return pc ? ue_get_ptr(pc, "PlayerState") : NULL; }
static int is_client(void) {
    UObject *w = ue_world(), *nd = w ? ue_get_ptr(w, "NetDriver") : NULL;
    return nd && ue_get_ptr(nd, "ServerConnection");
}
static UObject *ps_slot(UObject *ps) {   // GobiPlayerState.OwnedPlayerSlot (weak)
    int32_t off = ps ? ue_prop_offset(ps, "OwnedPlayerSlot") : -1;
    return off >= 0 ? ue_weak_get((char *)ps + off) : NULL;
}
static CustSet *slot_set(UObject *slot) {
    int32_t off = slot ? ue_prop_offset(slot, "CurrentCustomizationSet") : -1;
    return off >= 0 ? (CustSet *)((char *)slot + off) : NULL;
}
static int slot_hero(UObject *slot) {   // index into heroes[] of the slot's current hero, -1 unknown
    int32_t off = slot ? ue_prop_offset(slot, "CurrentHeroRowHandle") : -1;
    if (off < 0) return -1;
    RowHandle *h = (RowHandle *)((char *)slot + off);
    for (int i = 0; i < n_heroes; i++) if (heroes[i].deftable == h->table && fname_eq(heroes[i].defrow, h->row)) return i;
    return -1;
}
static UObject *slot_pawn(UObject *slot) { return slot ? ue_get_ptr(slot, "AssignedPawn") : NULL; }
static UObject *slot_owner(UObject *slot) { return slot ? ue_get_ptr(slot, "OwningPlayer") : NULL; }

// A handle names a customization row of a hero other than `hero` (or of no hero table we know).
static int foreign(const RowHandle *h, int hero) {
    if (!h->table) return 0;
    int t = hero_by_table(h->table);
    return t < 0 || t != hero;
}
static int set_foreign(const CustSet *s, int hero) {
    for (int k = 0; k < 4; k++) if (foreign(&s->slot[k], hero)) return 1;
    return 0;
}
static int set_sane(const CustSet *s) {   // every non-empty handle is a row of a CharacterCustomizationRow table
    for (int k = 0; k < 4; k++)
        if (s->slot[k].table && (!is_rowstruct(s->slot[k].table, "CharacterCustomizationRow") || !dt_row(s->slot[k].table, s->slot[k].row)))
            return 0;
    return 1;
}

// ---- wishes ----
typedef struct {
    int on;
    char key[80];              // host: player key (steam:<id>/name:<n>) or "slot:<n>" for a bot's slot
    char label[48];            // what was asked for (entry or hero name)
    RowHandle pick[4]; uint8_t has[4];
    int32_t slot_idx;          // U_INDEX of the slot last seen (new map/slot: retry counters reset)
    int tries; float wait; int gave_up;
} Want;
static Want me;                // this machine's player
#define MAX_OTHERS 16
static Want others[MAX_OTHERS];   // host: forced on other players / bots
static int locked;             // host: /models off
static float tick_acc;
static int n_refused;

static void want_pick(Want *w, const Entry *e) {
    w->pick[e->slot].table = e->table; w->pick[e->slot].row = e->row; w->has[e->slot] = 1;
}
// A whole survivor: its default skin set (a handle to a customization row), else every outfit-slot row's first.
static int want_hero(Want *w, int hi) {
    const Hero *h = &heroes[hi];
    const Entry *e = h->defskin.table ? entry_by_row(h->defskin.table, h->defskin.row) : NULL;
    if (!e) for (int i = h->first; i < h->first + h->count; i++) if (cat[i].slot == SLOT_OUTFIT) { e = &cat[i]; break; }
    if (!e) return -1;
    want_pick(w, e);
    return 0;
}

// cur + picks. A head/torso/legs pick clears the outfit (an outfit overrides the pieces).
static void compose(const CustSet *cur, const Want *w, CustSet *out) {
    memset(out, 0, sizeof *out);
    for (int k = 0; k < 4; k++) { out->slot[k].table = cur->slot[k].table; out->slot[k].row = cur->slot[k].row; }
    out->last = cur->last;
    int pieces = 0;
    for (int k = 0; k < 4; k++) if (w->has[k]) { out->slot[k].table = w->pick[k].table; out->slot[k].row = w->pick[k].row; out->last = (uint8_t)k; pieces |= k != SLOT_OUTFIT; }
    if (pieces && !w->has[SLOT_OUTFIT]) { out->slot[SLOT_OUTFIT].table = NULL; out->slot[SLOT_OUTFIT].row.idx = out->slot[SLOT_OUTFIT].row.num = 0; }
}
static int matches(const CustSet *cur, const Want *w) {
    int pieces = 0;
    for (int k = 0; k < 4; k++) {
        if (!w->has[k]) continue;
        if (cur->slot[k].table != w->pick[k].table || !fname_eq(cur->slot[k].row, w->pick[k].row)) return 0;
        pieces |= k != SLOT_OUTFIT;
    }
    return !(pieces && !w->has[SLOT_OUTFIT] && cur->slot[SLOT_OUTFIT].table);
}

// Our own player: the game's server RPC (host: runs locally).
static int send_select(UObject *ps, const CustSet *set) {
    UFunction *f = fn_of(ps, "ServerSelectCustomizationSet");
    int32_t po = parm_off(f, "InCustomizationSet");
    if (!f || po < 0 || UFN_PARMSSIZE(f) > 256) return -1;
    uint8_t p[256] = {0};
    memcpy(p + po, set, sizeof *set);
    ue_process_event(ps, f, p);
    return 0;
}
// Host: any slot. Same as the RPC implementation: copy (table, row) x4 + last, then the slot's OnRep applies it here;
// clients get the replicated property and apply it in their OnRep.
static int host_write(UObject *slot, const CustSet *set) {
    CustSet *cur = slot_set(slot);
    UFunction *f = fn_of(slot, "OnRep_CurrentCustomizationSet");
    if (!cur || !f) return -1;
    for (int k = 0; k < 4; k++) { cur->slot[k].table = set->slot[k].table; cur->slot[k].row = set->slot[k].row; }
    cur->last = set->last;
    uint8_t p[16] = {0};
    ue_process_event(slot, f, p);
    return 0;
}
// The game's own re-init of a player's look from their profile: ClientInitCustomizationRowForSelectedCharacter is a
// client RPC (runs on that player's machine, even without b4bcoop), which answers with ServerSelectCustomizationSet.
static int reinit_from_profile(UObject *ps, UObject *slot) {
    UFunction *f = fn_of(ps, "ClientInitCustomizationRowForSelectedCharacter");
    int32_t po = parm_off(f, "CurrentHeroRowHandle"), ho = slot ? ue_prop_offset(slot, "CurrentHeroRowHandle") : -1;
    if (!f || po < 0 || ho < 0 || UFN_PARMSSIZE(f) > 64) return -1;
    uint8_t p[64] = {0};
    RowHandle *h = (RowHandle *)(p + po), *src = (RowHandle *)((char *)slot + ho);
    h->table = src->table; h->row = src->row;
    ue_process_event(ps, f, p);
    return 0;
}
static int default_set(UObject *slot, CustSet *out) {   // a bot's look: its hero's default skin
    int hi = slot_hero(slot);
    Want w = {0};
    CustSet *cur = slot_set(slot);
    if (hi < 0 || !cur || want_hero(&w, hi)) return -1;
    CustSet clean = {0};
    compose(&clean, &w, out);
    return 0;
}

static int is_bot_slot(UObject *slot) {
    static UClass *pcc;
    if (!pcc) pcc = ue_find_class("PlayerController");
    UObject *ps = slot_owner(slot), *owner = ps ? ue_get_ptr(ps, "Owner") : NULL;
    return !ps || !owner || !pcc || !ue_is_a(owner, pcc);
}

// Hero-team slots (PlayerSlotManager.TeamSlots[team 0].Slots), in slot order.
static int hero_slots(UObject ***out) {
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL, *psm = gs ? ue_get_ptr(gs, "PlayerSlotManager") : NULL;
    int32_t off = psm ? ue_prop_offset(psm, "TeamSlots") : -1;
    if (off < 0) return 0;
    TArray *teams = (TArray *)((char *)psm + off);
    for (int t = 0; t < teams->num; t++) {
        uint8_t *ts = (uint8_t *)teams->data + t * 0x20;
        if (ts[0] == 0) { TArray *s = (TArray *)(ts + 8); *out = (UObject **)s->data; return s->num; }
    }
    return 0;
}

// Slot a host wish targets now (NULL: not in this map / not yet).
static UObject *want_slot(const Want *w) {
    if (!strncmp(w->key, "slot:", 5)) {
        UObject **s; int n = hero_slots(&s), i = atoi(w->key + 5);
        return i >= 0 && i < n ? s[i] : NULL;
    }
    UObject *gs = ue_world() ? ue_get_ptr(ue_world(), "GameState") : NULL;
    int32_t off = gs ? ue_prop_offset(gs, "PlayerArray") : -1;
    if (off < 0) return NULL;
    TArray *pa = (TArray *)((char *)gs + off);
    char k[80];
    for (int i = 0; i < pa->num; i++) {
        UObject *ps = ((UObject **)pa->data)[i];
        if (!ps) continue;
        admin_ps_key(ps, k, sizeof k);
        if (!strcmp(k, w->key)) return ps_slot(ps);
    }
    return NULL;
}

// ---- tick: keep wishes applied ----
static void tick_me(float dt) {
    if (!me.on) return;
    UObject *ps = my_ps(), *slot = ps_slot(ps);
    CustSet *cur = slot_set(slot);
    if (!cur || !slot_pawn(slot)) return;   // loading, dead, spectating: the slot keeps the set, retry later
    if (U_INDEX(slot) != me.slot_idx) { me.slot_idx = U_INDEX(slot); me.tries = 0; me.gave_up = 0; me.wait = 0; }
    if (matches(cur, &me)) { me.tries = 0; return; }
    if (me.gave_up || (me.wait -= dt) > 0) return;
    if (me.tries >= 3) {
        me.gave_up = 1;
        chat_local("The host did not accept your model (the host may have turned models off: /models).");
        return;
    }
    CustSet s;
    compose(cur, &me, &s);
    if (send_select(ps, &s)) return;
    me.tries++; me.wait = 4;
    LOG("models: sent %s (try %d)", me.label, me.tries);
}

static void tick_others(float dt) {
    if (is_client()) return;
    for (int i = 0; i < MAX_OTHERS; i++) {
        Want *w = &others[i];
        if (!w->on) continue;
        UObject *slot = want_slot(w);
        CustSet *cur = slot_set(slot);
        if (!cur || !slot_pawn(slot)) continue;
        if (!strncmp(w->key, "slot:", 5) && !is_bot_slot(slot)) {   // a human took over this bot: their own choice now
            LOG("models: %s taken over by a player, dropping the forced model", w->key);
            w->on = 0;
            continue;
        }
        if (U_INDEX(slot) != w->slot_idx) { w->slot_idx = U_INDEX(slot); w->wait = 0; }
        if (matches(cur, w) || (w->wait -= dt) > 0) continue;
        CustSet s;
        compose(cur, w, &s);
        if (!host_write(slot, &s)) LOG("models: applied %s to %s", w->label, w->key);
        w->wait = 2;
    }
}

void models_tick(float dt) {
    if ((tick_acc += dt) < 0.5f) return;
    dt = tick_acc; tick_acc = 0;
    UObject *w = ue_world();
    if (!w || !ue_local_pc()) return;
    if (!me.on && !others[0].on) {   // cheap path: nothing wished (others[] is compacted on use)
        int any = 0;
        for (int i = 0; i < MAX_OTHERS; i++) any |= others[i].on;
        if (!any) return;
    }
    need_catalogue();
    tick_me(dt);
    tick_others(dt);
}

// ---- host: lock ----
typedef void (*SelectSetFn)(UObject *ps, const CustSet *set);
static SelectSetFn orig_selectset;
static void selectset_detour(UObject *ps, const CustSet *set) {
    UObject *slot = ps_slot(ps);
    if (set && slot && ps != my_ps()) {
        need_catalogue();
        char n[64];
        admin_ps_name(ps, n, sizeof n);
        const char *why = NULL;
        if (!set_sane(set)) why = "not a customization row";
        else if (locked && set_foreign(set, slot_hero(slot))) why = "models are off";
        if (why) {
            n_refused++;
            LOG("models: refused a look from %s: %s", n, why);
            UObject *pc = ue_get_ptr(ps, "Owner");
            static ULONGLONG last_told; static UObject *last_pc;
            if (pc && (pc != last_pc || GetTickCount64() - last_told > 10000)) {
                admin_notice(pc, "The host turned model swaps off (/models).");
                last_pc = pc; last_told = GetTickCount64();
            }
            return;
        }
        char b[4][64];
        for (int k = 0; k < 4; k++) handle_str(&set->slot[k], b[k], sizeof b[k]);
        LOG("models: %s selects head=%s torso=%s legs=%s outfit=%s", n, b[0], b[1], b[2], b[3]);
    }
    orig_selectset(ps, set);
}

// Undo every look with another survivor's rows (host, /models off).
static int reset_foreign_all(void) {
    UObject **s; int n = hero_slots(&s), done = 0;
    for (int i = 0; i < n; i++) {
        CustSet *cur = slot_set(s[i]);
        if (!cur || !set_foreign(cur, slot_hero(s[i]))) continue;
        UObject *ps = slot_owner(s[i]);
        CustSet d;
        if (!is_bot_slot(s[i]) && ps) { if (!reinit_from_profile(ps, s[i])) done++; }
        else if (!default_set(s[i], &d) && !host_write(s[i], &d)) done++;
    }
    return done;
}

// ---- commands ----
// Resolve a name: an entry ("holly_elite_04"), or a survivor ("holly": its default skin).
static int resolve(const char *name, Want *w, char *label, size_t n) {
    need_catalogue();
    memset(w->pick, 0, sizeof w->pick); memset(w->has, 0, sizeof w->has);
    for (int i = 0; i < n_cat; i++)
        if (!_stricmp(cat[i].name, name)) { want_pick(w, &cat[i]); snprintf(label, n, "%s", cat[i].name); return 0; }
    int hi = hero_by_slug(name);
    if (hi >= 0 && !want_hero(w, hi)) { snprintf(label, n, "%s", heroes[hi].slug); return 0; }
    return -1;
}

static void list(const char *cat_arg, Out *o) {
    need_catalogue();
    if (!n_heroes) { out_printf(o, "no survivor data loaded yet\n"); return; }
    int hi = cat_arg && *cat_arg ? hero_by_slug(cat_arg) : -1;
    if (hi < 0 && cat_arg && *cat_arg && strcmp(cat_arg, "all")) {
        out_printf(o, "no survivor '%s'. /model list shows them\n", cat_arg);
        return;
    }
    if (hi < 0 && !(cat_arg && !strcmp(cat_arg, "all"))) {   // overview
        char line[200]; size_t k = 0;
        out_printf(o, "survivors (/model <name> = their look, /model list <name> = their outfits):\n");
        for (int i = 0; i < n_heroes; i++) {
            if (!heroes[i].count) continue;
            k += snprintf(line + k, sizeof line - k, "%s%s(%d)", k ? " " : "", heroes[i].slug, heroes[i].count);
            if (k > 70) { out_printf(o, "%s\n", line); k = 0; }
        }
        if (k) out_printf(o, "%s\n", line);
        return;
    }
    for (int h = 0; h < n_heroes; h++) {
        if (hi >= 0 && h != hi) continue;
        char line[200]; size_t k = 0;
        k = snprintf(line, sizeof line, "%s:", heroes[h].slug);
        for (int i = heroes[h].first; i < heroes[h].first + heroes[h].count; i++) {
            const char *nm = cat[i].name;
            if (!strncmp(nm, heroes[h].slug, strlen(heroes[h].slug)) && nm[strlen(heroes[h].slug)] == '_') nm += strlen(heroes[h].slug) + 1;
            if (k + strlen(nm) + 12 > 90) { out_printf(o, "%s\n", line); k = snprintf(line, sizeof line, " "); }
            k += snprintf(line + k, sizeof line - k, " %s%s", nm, cat[i].slot == SLOT_OUTFIT ? "" : cat[i].slot == SLOT_HEAD ? "(h)" : cat[i].slot == SLOT_TORSO ? "(t)" : "(l)");
        }
        out_printf(o, "%s\n", line);
    }
    if (hi >= 0) out_printf(o, "use it as /model %s_<name>\n", heroes[hi].slug);
}

static void status(Out *o) {
    UObject *slot = ps_slot(my_ps());
    CustSet *cur = slot_set(slot);
    need_catalogue();
    if (cur) {
        char b[4][64];
        for (int k = 0; k < 4; k++) handle_str(&cur->slot[k], b[k], sizeof b[k]);
        out_printf(o, "you: outfit=%s head=%s torso=%s legs=%s\n", b[3], b[0], b[1], b[2]);
    }
    out_printf(o, "your model: %s%s\n", me.on ? me.label : "(game's own)", me.on && me.gave_up ? " (refused by the host)" : "");
    if (!is_client()) {
        for (int i = 0; i < MAX_OTHERS; i++) if (others[i].on) out_printf(o, "forced: %s = %s\n", others[i].key, others[i].label);
        out_printf(o, "models are %s for players (/models on|off)\n", locked ? "OFF" : "on");
    }
}

static void reset_me(Out *o) {
    UObject *ps = my_ps(), *slot = ps_slot(ps);
    me.on = 0;
    if (!slot) { out_printf(o, "model reset (applies when you have a survivor)\n"); return; }
    if (reinit_from_profile(ps, slot)) { out_printf(o, "reset failed\n"); return; }
    out_printf(o, "back to your own look\n");
}

static void notify_all(const char *text) {
    UObject *gs = ue_world() ? ue_get_ptr(ue_world(), "GameState") : NULL, *me_pc = ue_local_pc();
    int32_t off = gs ? ue_prop_offset(gs, "PlayerArray") : -1;
    if (off < 0) return;
    TArray *pa = (TArray *)((char *)gs + off);
    for (int i = 0; i < pa->num; i++) {
        UObject *ps = ((UObject **)pa->data)[i], *pc = ps ? ue_get_ptr(ps, "Owner") : NULL;
        if (pc && pc != me_pc && !is_bot_slot(ps_slot(ps))) admin_notice(pc, text);
    }
}

// /model <player> <name|reset> (host)
static void model_other(const char *who, const char *what, Out *o) {
    if (is_client()) { out_printf(o, "/model <player> <name>: host only\n"); return; }
    UObject *ps = admin_find_player(who, o);
    if (!ps) return;
    UObject *slot = ps_slot(ps);
    if (!slot) { out_printf(o, "that player has no survivor yet\n"); return; }
    char key[80], nm[64];
    admin_ps_name(ps, nm, sizeof nm);
    if (is_bot_slot(slot)) {
        UObject **s; int n = hero_slots(&s), idx = -1;
        for (int i = 0; i < n; i++) if (s[i] == slot) idx = i;
        snprintf(key, sizeof key, "slot:%d", idx);
    } else admin_ps_key(ps, key, sizeof key);
    Want *w = NULL, *free_w = NULL;
    for (int i = 0; i < MAX_OTHERS; i++) {
        if (others[i].on && !strcmp(others[i].key, key)) w = &others[i];
        if (!others[i].on && !free_w) free_w = &others[i];
    }
    if (ps == my_ps()) { out_printf(o, "that's you: /model <name>\n"); return; }
    if (!_stricmp(what, "reset")) {
        if (w) w->on = 0;
        CustSet d;
        if (!is_bot_slot(slot)) reinit_from_profile(ps, slot);
        else if (!default_set(slot, &d)) host_write(slot, &d);
        out_printf(o, "%s: back to their own look\n", nm);
        return;
    }
    if (locked) { out_printf(o, "models are off (/models on)\n"); return; }
    Want nw = {0};
    char label[48];
    if (resolve(what, &nw, label, sizeof label)) { out_printf(o, "no model '%s' (/model list)\n", what); return; }
    if (!w) w = free_w;
    if (!w) { out_printf(o, "too many forced models\n"); return; }
    *w = nw;
    w->on = 1;
    snprintf(w->key, sizeof w->key, "%s", key);
    snprintf(w->label, sizeof w->label, "%s", label);
    char line[160];
    snprintf(line, sizeof line, "[host] %s now looks like %s", nm, label);
    notify_all(line);
    out_printf(o, "%s now looks like %s\n", nm, label);
    LOG("models: host forces %s on %s (%s)", label, nm, key);
}

// Chat /model and /models, dev CLI `model` / `models` (same code).
void models_slash(const char *verb, char *rest, Out *o) {
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL;
    if (!strcmp(verb, "models")) {
        if (!a) { status(o); return; }
        if (is_client()) { out_printf(o, "/models on|off: host only\n"); return; }
        if (!_stricmp(a, "off")) {
            locked = 1;
            for (int i = 0; i < MAX_OTHERS; i++) others[i].on = 0;
            int n = reset_foreign_all();
            if (me.on) reset_me(o);
            notify_all("[host] model swaps are off");
            out_printf(o, "model swaps off for everyone (%d look(s) reset)\n", n);
            LOG("models: locked, %d reset", n);
        } else if (!_stricmp(a, "on")) {
            locked = 0;
            notify_all("[host] model swaps are on (/model list)");
            out_printf(o, "model swaps on\n");
            LOG("models: unlocked");
        } else out_printf(o, "usage: /models [on|off]\n");
        return;
    }
    if (!a || !_stricmp(a, "status")) { status(o); return; }
    if (!_stricmp(a, "list")) { list(b, o); return; }
    if (!_stricmp(a, "reset") && !b) { reset_me(o); return; }
    if (b) { model_other(a, b, o); return; }
    if (locked && !is_client()) { out_printf(o, "models are off (/models on)\n"); return; }
    Want nw = {0};
    char label[48];
    if (resolve(a, &nw, label, sizeof label)) { out_printf(o, "no model '%s' (/model list)\n", a); return; }
    me = nw;
    me.on = 1;
    snprintf(me.label, sizeof me.label, "%s", label);
    out_printf(o, "you now look like %s (/model reset to undo)\n", label);
    LOG("models: /model %s", label);
    tick_acc = 1;   // apply on the next tick
}

#ifndef B4B_RELEASE
// ---- dev: exploration (docs/investigations/model-swap.md) ----
static UObject *nth_hero(int idx) {
    static UClass *hc;
    if (!hc) hc = ue_find_class("HeroCharacter");
    UObject *w = ue_world();
    int32_t n = ue_num_objects(); int k = 0;
    for (int32_t i = 0; hc && w && i < n; i++) {
        UObject *o = ue_object_at(i);
        if (!is_live(o) || !ue_is_a(o, hc)) continue;
        UObject *lvl = U_OUTER(o);
        if (!lvl || U_OUTER(lvl) != w) continue;
        if (k++ == idx) return o;
    }
    return NULL;
}

static void dump_comps(UObject *actor, Out *o) {
    static UClass *smc, *mc;
    if (!smc) smc = ue_find_class("SkinnedMeshComponent");
    if (!mc) mc = ue_find_class("MeshComponent");
    int32_t n = ue_num_objects();
    char a[128], b[300], c[200], d[200], e[200];
    for (int32_t i = 0; mc && i < n; i++) {
        UObject *x = ue_object_at(i);
        if (!is_live(x) || U_OUTER(x) != actor || !ue_is_a(x, mc)) continue;
        out_printf(o, "    %s [%s]", ue_obj_name(x, a, sizeof a), ue_obj_name(U_CLASS(x), c, sizeof c));
        if (smc && ue_is_a(x, smc)) {
            UObject *m = ue_get_ptr(x, "SkeletalMesh");
            UObject *sk = m ? ue_get_ptr(m, "Skeleton") : NULL, *pa = m ? ue_get_ptr(m, "PhysicsAsset") : NULL;
            UObject *ac = ue_get_ptr(x, "AnimClass");
            int32_t mo = ue_prop_offset(x, "MasterPoseComponent");
            UObject *mp = mo >= 0 ? ue_weak_get((char *)x + mo) : NULL;
            out_printf(o, " mesh=%s skel=%s phys=%s anim=%s master=%s", m ? ue_full_path(m, b, sizeof b) : "-",
                       sk ? ue_obj_name(sk, d, sizeof d) : "-", pa ? ue_obj_name(pa, e, sizeof e) : "-",
                       ac ? ue_obj_name(ac, c, sizeof c) : "-", mp ? ue_obj_name(mp, a, sizeof a) : "-");
        }
        int32_t om = ue_prop_offset(x, "OverrideMaterials");
        if (om >= 0) out_printf(o, " overrides=%d", ((TArray *)((char *)x + om))->num);
        out_printf(o, "\n");
    }
}

static void cmd_dump(Out *o) {
    need_catalogue();
    char a[128], b[300];
    for (int i = 0; ; i++) {
        UObject *h = nth_hero(i);
        if (!h) break;
        int32_t ro = ue_prop_offset(h, "Role"), hd = ue_prop_offset(h, "HeroDefinitionRowHandle");
        UObject *slot = ue_get_ptr(h, "OccupiedPlayerSlot");
        out_printf(o, "#%d %s role=%d hero=", i, ue_obj_name(h, a, sizeof a), ro >= 0 ? *((uint8_t *)h + ro) : -1);
        if (hd >= 0) { RowHandle *r = (RowHandle *)((char *)h + hd); out_printf(o, "%s", r->table ? ue_name(r->row, b, sizeof b) : "-"); }
        out_printf(o, " slot=%s herocust=%d\n", slot ? ue_obj_name(slot, a, sizeof a) : "-", slot_hero(slot));
        CustSet *s = slot_set(slot);
        if (s) {
            for (int k = 0; k < 4; k++) { handle_str(&s->slot[k], b, sizeof b); out_printf(o, "    set.%s %s\n", SLOTN[k], b); }
            out_printf(o, "    set.last %d\n", s->last);
        }
        dump_comps(h, o);
    }
}

static void cmd_rows(const char *filter, Out *o) {
    build_catalogue();
    char b[64], c[160];
    for (int i = 0; i < n_heroes; i++) {
        handle_str(&heroes[i].defskin, c, sizeof c);
        out_printf(o, "hero %s custtable=%s rows=%d defskin=%s\n", heroes[i].slug,
                   heroes[i].custtable ? ue_obj_name(heroes[i].custtable, b, sizeof b) : "-", heroes[i].count, c);
    }
    for (int i = 0; i < n_cat; i++) {
        const Entry *e = &cat[i];
        if (filter && *filter && !strstr(e->name, filter) && strcmp(heroes[e->hero].slug, filter)) continue;
        uint8_t *r = dt_row(e->table, e->row);
        char fp[160];
        ue_name(HMD_MESHPATH(CC_FP(r)), fp, sizeof fp);
        out_printf(o, "%-28s %-8s %-6s row=%s mats=%d/%d 3p=%s fp=%s\n", e->name, heroes[e->hero].slug, SLOTN[e->slot],
                   ue_name(e->row, b, sizeof b), HMD_MATS(CC_3P(r))->num, HMD_MATS(CC_FP(r))->num, e->mesh3p, fp);
    }
}

static UObject *load_asset(const char *path) {
    UClass *ksl = ue_find_class("KismetSystemLibrary");
    UObject *cdo = ksl ? UC_CDO(ksl) : NULL;
    UFunction *f = fn_of(cdo, "LoadAsset_Blocking");
    int32_t pa = parm_off(f, "Asset"), rv = parm_off(f, "ReturnValue");
    if (!f || pa < 0 || rv < 0 || UFN_PARMSSIZE(f) > 128) return NULL;
    uint8_t p[128] = {0};
    *(int32_t *)(p + pa) = -1;   // FWeakObjectPtr: none
    *(FName *)(p + pa + 0x10) = make_name(path);
    ue_process_event(cdo, f, p);
    return *(UObject **)(p + rv);
}

static UObject *comp_named(UObject *actor, const char *name) {
    int32_t n = ue_num_objects(); char a[128];
    for (int32_t i = 0; i < n; i++) {
        UObject *x = ue_object_at(i);
        if (is_live(x) && U_OUTER(x) == actor && !_stricmp(ue_obj_name(x, a, sizeof a), name)) return x;
    }
    return NULL;
}

// mdl dump | rows [filter] | setslot <hero#> <entry> | rpc <entry> | reinit | mesh <hero#> <comp> <path> | load <path>
//     | skm [filter] | reg [filter] [class]
int models_cmd(const char *verb, char *rest, Out *o) {
    if (!strcmp(verb, "model") || !strcmp(verb, "models")) { models_slash(verb, rest, o); return 1; }
    if (strcmp(verb, "mdl")) return 0;
    char *sub = rest ? strtok(rest, " ") : NULL, *a1 = sub ? strtok(NULL, " ") : NULL, *a2 = a1 ? strtok(NULL, " ") : NULL;
    char *a3 = a2 ? strtok(NULL, "") : NULL;
    if (!sub || !strcmp(sub, "dump")) { cmd_dump(o); return 1; }
    if (!strcmp(sub, "rows")) { cmd_rows(a1, o); return 1; }
    if (!strcmp(sub, "setslot") && a2) {
        UObject *h = nth_hero(atoi(a1)), *slot = h ? ue_get_ptr(h, "OccupiedPlayerSlot") : NULL;
        Want w = {0}; char label[48];
        CustSet *cur = slot_set(slot);
        if (!cur || resolve(a2, &w, label, sizeof label)) { out_printf(o, "no hero/slot/entry\n"); return 1; }
        CustSet s; compose(cur, &w, &s);
        out_printf(o, "host_write %s: %d\n", label, host_write(slot, &s));
        return 1;
    }
    if (!strcmp(sub, "rpc") && a1) {
        UObject *ps = my_ps(), *slot = ps_slot(ps);
        Want w = {0}; char label[48];
        CustSet *cur = slot_set(slot);
        if (!cur || resolve(a1, &w, label, sizeof label)) { out_printf(o, "no slot/entry\n"); return 1; }
        CustSet s; compose(cur, &w, &s);
        out_printf(o, "ServerSelectCustomizationSet %s: %d\n", label, send_select(ps, &s));
        return 1;
    }
    if (!strcmp(sub, "reinit")) {
        UObject *ps = my_ps();
        out_printf(o, "reinit: %d\n", reinit_from_profile(ps, ps_slot(ps)));
        return 1;
    }
    if (!strcmp(sub, "mesh") && a3) {
        UObject *h = nth_hero(atoi(a1)), *c = h ? comp_named(h, a2) : NULL;
        if (!c) { out_printf(o, "no hero/component\n"); return 1; }
        UObject *m = load_asset(a3);
        if (!m) { out_printf(o, "load failed: %s\n", a3); return 1; }
        UFunction *f = fn_of(c, "SetSkeletalMesh");
        int32_t pm = parm_off(f, "NewMesh"), pr = parm_off(f, "bReinitPose");
        uint8_t p[32] = {0};
        *(UObject **)(p + pm) = m;
        if (pr >= 0) p[pr] = 1;
        ue_process_event(c, f, p);
        out_printf(o, "SetSkeletalMesh done\n");
        return 1;
    }
    if (!strcmp(sub, "load") && a1) {
        char b[300];
        UObject *m = load_asset(a1), *sk = m ? ue_get_ptr(m, "Skeleton") : NULL;
        out_printf(o, "%s skel=%s\n", m ? ue_full_path(m, b, sizeof b) : "load failed", sk ? ue_obj_name(sk, b, sizeof b) : "-");
        return 1;
    }
    if (!strcmp(sub, "skm")) {   // loaded skeletal meshes (filter a1) with skeleton
        UClass *c = ue_find_class("SkeletalMesh");
        int32_t n = ue_num_objects(); char b[300], d[128];
        for (int32_t i = 0; c && i < n; i++) {
            UObject *x = ue_object_at(i);
            if (!is_live(x) || !ue_is_a(x, c)) continue;
            ue_full_path(x, b, sizeof b);
            if (a1 && !strstr(b, a1)) continue;
            UObject *sk = ue_get_ptr(x, "Skeleton");
            out_printf(o, "%s skel=%s\n", b, sk ? ue_full_path(sk, d, sizeof d) : "-");
        }
        return 1;
    }
    if (!strcmp(sub, "reg")) {   // asset registry: every asset of a class (default SkeletalMesh), not loaded
        UObject *ar = ue_find_first_of("AssetRegistryImpl");
        UClass *ic = ue_find_class("AssetRegistry");
        UFunction *f = ic ? ue_find_function(ic, "GetAssetsByClass") : NULL;
        int32_t pc = parm_off(f, "ClassName"), pa = parm_off(f, "OutAssetData"), ps = parm_off(f, "bSearchSubClasses");
        if (!ar || !f || pc < 0 || pa < 0 || UFN_PARMSSIZE(f) > 64) { out_printf(o, "no asset registry (%p %p)\n", (void *)ar, (void *)f); return 1; }
        uint8_t p[64] = {0};
        *(FName *)(p + pc) = make_name(a2 ? a2 : "SkeletalMesh");
        if (ps >= 0) p[ps] = 1;
        ue_process_event(ar, f, p);
        TArray *arr = (TArray *)(p + pa);
        char b[300]; int shown = 0;
        for (int i = 0; i < arr->num; i++) {
            ue_name(*(FName *)((char *)arr->data + i * 0x50), b, sizeof b);
            if (a1 && strcmp(a1, "*") && !strstr(b, a1)) continue;
            if (shown++ < 4000) out_printf(o, "%s\n", b);
        }
        out_printf(o, "%d assets, %d shown\n", arr->num, shown);
        return 1;
    }
    out_printf(o, "usage: mdl dump|rows [filter]|setslot <hero#> <entry>|rpc <entry>|reinit|mesh <hero#> <comp> <path>|load <path>|skm [filter]|reg [filter] [class]\n");
    return 1;
}
#endif  // !B4B_RELEASE

int models_init(void) {
    if (memcmp((void *)ADDR_SELECTSET, SIG_SELECTSET, sizeof SIG_SELECTSET)) { LOG("models: SelectCustomizationSet signature mismatch, no host lock"); return -1; }
    if (MH_CreateHook((void *)ADDR_SELECTSET, (void *)selectset_detour, (void **)&orig_selectset) != MH_OK ||
        MH_EnableHook((void *)ADDR_SELECTSET) != MH_OK) { LOG("models: hook failed, no host lock"); orig_selectset = NULL; return -1; }
    LOG("models: on");
    return 0;
}
