// Opt-in, host-only cheats / sandbox (#14). Player reference: docs/commands-cheats.md.
//
// Rules:
//  - Every cheat is a chat command run by admin.c's dispatcher (`/god`, `/fly`, ...). Nothing works until the host types
//    `/cheats on`; `/cheats off` or the next map change that lands in Fort Hope (camp) or the menus turns them off.
//  - Host only: the machine must be the server (listen host or standalone). A client's `/cheat ...` never leaves its
//    machine (chat.c) and gets "host only".
//  - Transparent: enabling/disabling, and every cheat that touches another player or the whole session, is announced to
//    every player with admin.c's host notice (ClientTeamMessage, our own type; the same path as /say).
//  - No client save is ever written: cheats act on the live session, or on the HOST's own offline profile (`/supply`,
//    `/unlockall`: ExecuteCommand on the host's local profile component only, never forwarded). While cheats are on,
//    and for the rest of any map they were on during (cheats_tainted), rewards.c forwards no reward to remote players
//    and the host doesn't send them stat deltas or achievements (hook_client_progress), so a `/win` or a god-mode run
//    can't feed their saves. Their own burn-card charges (-1) still reach them.
//  - Host-side only: clients need no matching code (notices use admin.c's existing notice type), so no protocol bump.
//
// How (the shipping build compiled out Gobi's own cheat execs: GobiPlayerController.God/Heal/GiveUnlock/... are empty,
// APlayerController::AddCheats is `ret` so no controller ever gets a CheatManager, and ACharacter::ClientCheatFly/
// Ghost/Walk_Implementation are empty):
//  - god: HealthComponent.PushDamageDisabled + PushDeathDisabled (counters the game itself uses), popped exactly once.
//  - heal / revive: HealthComponent.Heal, LifeStateComponent.Revive; ammo: ClipAmmoComponent.bInfiniteReserveAmmo on
//    every weapon; copper: InventoryComponent.AdjustCurrency; card: GameplayCardManager.AddGameplayCardToSlotByRowHandle.
//  - fly/noclip/walk: CharacterMovement.SetMovementMode + AActor.SetActorEnableCollision on the server.
//  - size/freecam/slomo/freeze: the engine's own UCheatManager (ChangeSize, ToggleDebugCamera, Slomo, PlayersOnly are
//    intact), constructed for the host's PlayerController with StaticConstructObject_Internal and stored in
//    PlayerController.CheatManager.
//  - world: GameDirector.TriggerHordeOnDirector / ForcePacingPhaseOnDirector / GobiSpawnAIFromClass (statics),
//    LifeStateComponent.Kill on ridden, MissionGameMode.OnMissionEnd (win/lose, as dev `endmission`).
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>
#include <stdarg.h>
#include "ue.h"
#include "log.h"
#include "cmds.h"
#include "MinHook.h"

typedef FName *(*FNameCtorFn)(FName *self, const wchar_t *name, int find_type);
#define ADDR_FNAME_CTOR VA(0x1424BC8E0ull)
// UObject* StaticConstructObject_Internal(UClass*, UObject* Outer, FName, EObjectFlags, EInternalObjectFlags,
//   UObject* Template, bool bCopyTransientsFromClassDefaults, FObjectInstancingGraph*, bool bAssumeTemplateIsArchetype)
#define ADDR_SCO VA(0x1426E4840ull)
static const uint8_t SIG_SCO[] = {0x4c,0x89,0x44,0x24,0x18,0x53,0x55,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,
                                  0x57,0x48,0x81,0xec,0xa8,0x01,0x00,0x00};
typedef UObject *(*ScoFn)(UClass *, UObject *, FName, uint32_t, uint32_t, UObject *, uint8_t, void *, uint8_t);

// FDataTableRowHandle in this build: {UDataTable*, FName RowName, FString (unreflected display name)} = 0x20
typedef struct { UObject *table; FName row; FString display; } RowHandle;
#define DT_ROWMAP(t) ((TArray *)((char *)(t) + 0x38))   // UDataTable::RowMap, TMap<FName, uint8*>

// ---- small helpers ----
static void ascii_of(const FString *s, char *buf, size_t n) {
    size_t k = 0;
    for (int i = 0; s && s->data && i < s->num && s->data[i] && k + 1 < n; i++)
        buf[k++] = s->data[i] < 128 ? (char)s->data[i] : '?';
    buf[k] = 0;
}

static void fstring_set(FString *s, const char *utf8, wchar_t *storage, int cap) {
    int n = 0;
    for (; utf8[n] && n < cap - 1; n++) storage[n] = (wchar_t)(unsigned char)utf8[n];
    storage[n] = 0;
    s->data = storage; s->num = n + 1; s->max = cap;
}

static int alive(UObject *o, int32_t idx) { return o && idx >= 0 && ue_object_at(idx) == o; }
#define LIVE_FLAGS 0x30   // RF_ClassDefaultObject | RF_ArchetypeObject

// One reflected call: parameters by name into a zeroed block, then ProcessEvent.
typedef struct { UObject *obj; UFunction *fn; uint8_t p[1024]; } Call;
static int call_prep(Call *c, UObject *obj, const char *fname) {
    c->obj = obj;
    c->fn = obj && U_CLASS(obj) ? ue_find_function(U_CLASS(obj), fname) : NULL;
    if (!c->fn || UFN_PARMSSIZE(c->fn) > sizeof c->p) { if (obj) LOG("cheats: no usable %s", fname); return 0; }
    memset(c->p, 0, UFN_PARMSSIZE(c->fn));
    return 1;
}
static void *carg(Call *c, const char *name) { FField *f = ue_find_prop(c->fn, name); return f ? c->p + FP_OFFSET(f) : NULL; }
static void call_go(Call *c) { ue_process_event(c->obj, c->fn, c->p); }
#define SET(c, name, type, v) do { void *_a = carg(c, name); if (_a) *(type *)_a = (v); } while (0)

// no-argument call returning an object / float / bool
static UObject *call_obj(UObject *o, const char *fn) {
    Call c; if (!call_prep(&c, o, fn)) return NULL;
    call_go(&c);
    void *r = carg(&c, "ReturnValue");
    return r ? *(UObject **)r : NULL;
}
static float call_float(UObject *o, const char *fn) {
    Call c; if (!call_prep(&c, o, fn)) return 0;
    call_go(&c);
    void *r = carg(&c, "ReturnValue");
    return r ? *(float *)r : 0;
}
static int call_bool(UObject *o, const char *fn) {
    Call c; if (!call_prep(&c, o, fn)) return -1;
    call_go(&c);
    uint8_t *r = carg(&c, "ReturnValue");
    return r ? *r != 0 : -1;
}
static int call_void(UObject *o, const char *fn) { Call c; if (!call_prep(&c, o, fn)) return 0; call_go(&c); return 1; }

// native (/Script) classes never unload: look each one up once (ue_find_class scans every object)
static UClass *cls(const char *name) {
    static struct { const char *name; UClass *c; } cache[48];
    for (int i = 0; i < 48 && cache[i].name; i++) if (!strcmp(cache[i].name, name)) return cache[i].c;
    UClass *c = ue_find_class(name);
    for (int i = 0; i < 48 && c; i++) if (!cache[i].name) { cache[i].name = name; cache[i].c = c; break; }
    return c;
}
static UObject *class_cdo(const char *name) { UClass *c = cls(name); return c ? UC_CDO(c) : NULL; }

// ---- world ----
static UObject *game_state(void) { UObject *w = ue_world(); return w ? ue_get_ptr(w, "GameState") : NULL; }
static UObject *game_mode(void) { UObject *w = ue_world(); return w ? ue_get_ptr(w, "AuthorityGameMode") : NULL; }
static int in_mission(void) {
    UObject *gm = game_mode();
    UClass *mc = cls("MissionGameMode");
    return gm && mc && ue_is_a(gm, mc);
}
// o belongs to world w: its level (persistent or a streamed sublevel) is owned by w
static int in_world(UObject *o, UObject *w) {
    UClass *lc = cls("Level");
    for (UObject *p = U_OUTER(o); p; p = U_OUTER(p)) {
        if (p == w) return 1;
        if (lc && ue_is_a(p, lc)) return ue_get_ptr(p, "OwningWorld") == w;
    }
    return 0;
}
// next live object of class c in the current world (not a CDO / archetype)
static UObject *next_live(UClass *c, UObject *after) {
    UObject *w = ue_world();
    for (int32_t i = after ? U_INDEX(after) + 1 : 0, n = ue_num_objects(); c && w && i < n; i++) {
        UObject *o = ue_object_at(i);
        if (o && !(U_FLAGS(o) & LIVE_FLAGS) && ue_is_a(o, c) && in_world(o, w)) return o;
    }
    return NULL;
}

// ---- players ----
static int is_a(UObject *o, const char *name);
static UObject *freecam_pc;
static int32_t freecam_pci;
// the host's own controller (while the free camera is on, the local player belongs to the debug camera controller)
static UObject *host_pc(void) {
    UObject *pc = ue_local_pc();
    if (pc && is_a(pc, "DebugCameraController") && alive(freecam_pc, freecam_pci)) return freecam_pc;
    return pc;
}
static UObject *host_ps(void) { UObject *pc = host_pc(); return pc ? ue_get_ptr(pc, "PlayerState") : NULL; }
static int is_a(UObject *o, const char *name) { UClass *c = o ? cls(name) : NULL; return c && ue_is_a(o, c); }
static UObject *ps_controller(UObject *ps) { return ps ? ue_get_ptr(ps, "Owner") : NULL; }
static UObject *ps_pc(UObject *ps) { UObject *c = ps_controller(ps); return is_a(c, "PlayerController") ? c : NULL; }
static UObject *ps_pawn(UObject *ps) {
    UObject *c = ps_controller(ps), *p = c ? ue_get_ptr(c, "Pawn") : NULL;
    if (!p && ps) p = ue_get_ptr(ps, "PawnPrivate");
    return is_a(p, "HeroCharacter") ? p : NULL;
}
static UObject *health_of(UObject *pawn) { return pawn ? call_obj(pawn, "GetHealthComponent") : NULL; }
static UObject *life_of(UObject *pawn) { return pawn ? call_obj(pawn, "GetLifeStateComponent") : NULL; }
static UObject *inventory_of(UObject *pawn) { return pawn ? call_obj(pawn, "GetInventoryComponent") : NULL; }

static int actor_loc(UObject *a, float v[3]) {
    Call c;
    if (!a || !call_prep(&c, a, "K2_GetActorLocation")) return -1;
    call_go(&c);
    float *r = carg(&c, "ReturnValue");
    if (!r) return -1;
    memcpy(v, r, 12);
    return 0;
}
static int actor_fwd(UObject *a, float v[3]) {
    Call c;
    if (!a || !call_prep(&c, a, "GetActorForwardVector")) return -1;
    call_go(&c);
    float *r = carg(&c, "ReturnValue");
    if (!r) return -1;
    memcpy(v, r, 12);
    return 0;
}

// A player's name for replies/notices; an NPC ally in the player list (e.g. Emmett) by its hero class
static void who_name(UObject *ps, char *buf, size_t n) {
    admin_display_name(ps, buf, n);
    UObject *pawn = strcmp(buf, "?") ? NULL : ps_pawn(ps);
    if (!pawn) return;
    char c[128];
    ue_obj_name(U_CLASS(pawn), c, sizeof c);
    char *e = strstr(c, "_BP_C");
    if (e) *e = 0;
    snprintf(buf, n, "%s", c);
}

// Who a command is for: "" / "me" = the host's own hero, "all" = every hero (humans and bots), else a player from
// /players (name or #n).
typedef struct { UObject *ps[16]; int n, all, others; char label[64]; } Targets;
static int targets(const char *spec, Targets *t, Out *o) {
    memset(t, 0, sizeof *t);
    UObject *me = host_ps();
    if (!spec || !*spec || !_stricmp(spec, "me")) {
        if (!ps_pawn(me)) { out_printf(o, "you have no hero right now\n"); return 0; }
        t->ps[t->n++] = me;
        snprintf(t->label, sizeof t->label, "the host");
        return 1;
    }
    if (!_stricmp(spec, "all")) {
        UObject **pa; int n = admin_player_array(&pa);
        for (int i = 0; i < n && t->n < 16; i++) {
            if (!ps_pawn(pa[i])) continue;
            t->ps[t->n++] = pa[i];
            if (pa[i] != me) t->others = 1;
        }
        t->all = 1;
        snprintf(t->label, sizeof t->label, "everyone");
        if (!t->n) { out_printf(o, "no heroes right now\n"); return 0; }
        return 1;
    }
    UObject *ps = admin_find_player(spec, o);
    if (!ps) return 0;
    if (!ps_pawn(ps)) { char nm[64]; who_name(ps, nm, sizeof nm); out_printf(o, "%s has no hero right now\n", nm); return 0; }
    t->ps[t->n++] = ps;
    t->others = ps != me;
    if (ps == me) snprintf(t->label, sizeof t->label, "the host");
    else who_name(ps, t->label, sizeof t->label);
    return 1;
}

// ---- state ----
static int on;
static UObject *cur_world, *taint_world;
static char pending_notice[200];
static float pending_in;
static int clients_seen;   // remote players in the last mission: the camp notice waits for them to be back
static int ammo_inf, frozen, freecam;
static float slomo = 1.f;
typedef struct { UObject *ps; int32_t psi; UObject *hc; int32_t hci; } God;
static God gods[16];
static int n_gods;
typedef struct { UObject *c; int32_t i; } Ref;
static Ref ammo_set[256];   // weapons we made infinite (restored by /ammo off, /cheats off)
static int n_ammo;

int cheats_tainted(void) { return taint_world && taint_world == ue_world(); }

static void notice(const char *fmt, ...) {
    char msg[240];
    va_list ap; va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    admin_notice("%s", msg);
}

// ---- god ----
static void hc_push(UObject *hc, int push) {
    call_void(hc, push ? "PushDamageDisabled" : "PopDamageDisabled");
    call_void(hc, push ? "PushDeathDisabled" : "PopDeathDisabled");
}
static God *god_find(UObject *ps) {
    for (int i = 0; i < n_gods; i++) if (gods[i].ps == ps && alive(ps, gods[i].psi)) return &gods[i];
    return NULL;
}
// keep the counters on the hero this player controls now (a new hero after a rescue, a bot taken over, ...)
static void god_sync(God *g) {
    UObject *hc = health_of(ps_pawn(g->ps));
    if (hc == g->hc && alive(g->hc, g->hci)) return;
    if (alive(g->hc, g->hci)) hc_push(g->hc, 0);
    g->hc = hc; g->hci = hc ? U_INDEX(hc) : -1;
    if (hc) { hc_push(hc, 1); LOG("cheats: god on %p", (void *)hc); }
}
static void god_set(UObject *ps, int en) {
    God *g = god_find(ps);
    if (en && !g && n_gods < 16) {
        g = &gods[n_gods++];
        memset(g, 0, sizeof *g);
        g->ps = ps; g->psi = U_INDEX(ps); g->hci = -1;
        god_sync(g);
    } else if (!en && g) {
        if (alive(g->hc, g->hci)) hc_push(g->hc, 0);
        *g = gods[--n_gods];
    }
}
static void god_all_off(void) {
    for (int i = 0; i < n_gods; i++) if (alive(gods[i].hc, gods[i].hci)) hc_push(gods[i].hc, 0);
    n_gods = 0;
}

static void cmd_god(char *rest, Out *o) {
    char *who = rest ? strtok(rest, " ") : NULL, *mode = who ? strtok(NULL, " ") : NULL;
    if (who && (!_stricmp(who, "on") || !_stricmp(who, "off"))) { mode = who; who = NULL; }
    if (mode && _stricmp(mode, "on") && _stricmp(mode, "off")) { out_printf(o, "usage: /god [player|all] [on|off]\n"); return; }
    Targets t;
    if (!targets(who, &t, o)) return;
    int en = mode ? !_stricmp(mode, "on") : (t.all ? 1 : !god_find(t.ps[0]));
    for (int i = 0; i < t.n; i++) god_set(t.ps[i], en);
    out_printf(o, "god mode %s for %s\n", en ? "on" : "off", t.label);
    if (t.others) notice(en ? "host gave god mode to %s" : "host took god mode from %s", t.label);
}

// ---- heal / revive ----
// Heal the trauma first (InPermanentHealth: the lost part of the max health bar, capped by the game), then fill the
// health up to the restored max.
static int heal_pawn(UObject *pawn) {
    UObject *hc = health_of(pawn);
    Call c;
    if (!hc || !call_prep(&c, hc, "Heal")) return 0;
    SET(&c, "InPermanentHealth", float, 100000.f);
    SET(&c, "SourceActor", UObject *, pawn);
    SET(&c, "SourcePawn", UObject *, pawn);
    call_go(&c);
    float mx = call_float(hc, "GetCurrentMaxHealth"), now = call_float(hc, "GetHealth");
    if (mx > now && call_prep(&c, hc, "Heal")) {
        SET(&c, "Health", float, mx - now);
        SET(&c, "SourceActor", UObject *, pawn);
        SET(&c, "SourcePawn", UObject *, pawn);
        call_go(&c);
    }
    return 1;
}

static void cmd_heal(char *rest, Out *o) {
    Targets t;
    if (!targets(rest, &t, o)) return;
    int n = 0, down = 0;
    for (int i = 0; i < t.n; i++) {
        UObject *pawn = ps_pawn(t.ps[i]), *ls = life_of(pawn);
        if (ls && call_bool(ls, "IsAlive") != 1) { down++; continue; }
        n += heal_pawn(pawn);
    }
    out_printf(o, "healed %d hero(es)%s\n", n, down ? " (downed/dead ones need /revive)" : "");
    if (t.others && n) notice("host healed %s", t.label);
}

static int revive_pawn(UObject *pawn) {
    UObject *ls = life_of(pawn), *hc = health_of(pawn);
    if (!ls || call_bool(ls, "IsIncapped") != 1) return 0;
    Call c;
    if (!call_prep(&c, ls, "Revive")) return 0;
    uint8_t *info = carg(&c, "ReviveInfo");   // ReviveInfo {NewHealth, NewPermanentHealth, IncapStrikesToReturn, tag, loc}
    if (!info) return 0;
    float mx = hc ? call_float(hc, "GetCurrentMaxHealth") : 100.f;
    *(float *)info = mx > 0 ? mx * 0.5f : 50.f;        // NewHealth
    *(float *)(info + 4) = mx > 0 ? mx : 100.f;          // NewPermanentHealth: keep the max health it had (0 = the minimum)
    actor_loc(pawn, (float *)(info + 0x14));
    SET(&c, "Reviver", UObject *, pawn);
    call_go(&c);
    return 1;
}

static void cmd_revive(char *rest, Out *o) {
    Targets t;
    if (!targets(rest && *rest ? rest : "all", &t, o)) return;
    int n = 0, dead = 0;
    for (int i = 0; i < t.n; i++) {
        UObject *pawn = ps_pawn(t.ps[i]), *ls = life_of(pawn);
        if (revive_pawn(pawn)) n++;
        else if (ls && call_bool(ls, "IsDead") == 1) dead++;
    }
    out_printf(o, "revived %d downed hero(es)%s\n", n, dead ? "; dead heroes come back through the rescue closets as usual" : "");
    if (n && (t.others || t.all)) notice("host revived %s", t.label);
}

// ---- ammo ----
static void ammo_sync(void) {
    UClass *c = cls("ClipAmmoComponent");
    int32_t off = -1;
    for (UObject *a = next_live(c, NULL); a; a = next_live(c, a)) {
        if (off < 0) off = ue_prop_offset(a, "bInfiniteReserveAmmo");
        if (off < 0) return;
        uint8_t *f = (uint8_t *)a + off;
        if (*f || n_ammo >= 256) continue;   // already infinite (by design or by us), or no room to remember it
        *f = 1;
        ammo_set[n_ammo].c = a; ammo_set[n_ammo].i = U_INDEX(a); n_ammo++;
    }
}
static void ammo_restore(void) {
    UClass *c = cls("ClipAmmoComponent");
    int32_t off = -1;
    for (int i = 0; i < n_ammo; i++) {
        if (!alive(ammo_set[i].c, ammo_set[i].i)) continue;
        if (off < 0) off = ue_prop_offset(ammo_set[i].c, "bInfiniteReserveAmmo");
        if (off >= 0) *((uint8_t *)ammo_set[i].c + off) = 0;
    }
    n_ammo = 0;
    (void)c;
}
static void cmd_ammo(char *rest, Out *o) {
    if (!rest || (_stricmp(rest, "infinite") && _stricmp(rest, "on") && _stricmp(rest, "off"))) {
        out_printf(o, "usage: /ammo infinite|off (infinite reserve ammo for every hero; now %s)\n", ammo_inf ? "infinite" : "off");
        return;
    }
    int en = _stricmp(rest, "off") != 0;
    if (en == ammo_inf) { out_printf(o, "ammo is already %s\n", en ? "infinite" : "normal"); return; }
    ammo_inf = en;
    if (en) ammo_sync(); else ammo_restore();
    out_printf(o, "infinite reserve ammo %s\n", en ? "on" : "off");
    notice(en ? "host turned on infinite reserve ammo for everyone" : "host turned off infinite ammo");
}

// ---- copper ----
static void cmd_copper(char *rest, Out *o) {
    char *amt = rest ? strtok(rest, " ") : NULL, *who = amt ? strtok(NULL, " ") : NULL, *end = NULL;
    long v = amt ? strtol(amt, &end, 10) : 0;
    if (!amt || *end || !v || v > 100000 || v < -100000) { out_printf(o, "usage: /copper <+N|-N> [player|all] (default: all)\n"); return; }
    Targets t;
    if (!targets(who ? who : "all", &t, o)) return;
    static wchar_t cause[32];
    int n = 0;
    for (int i = 0; i < t.n; i++) {
        Call c;
        if (!call_prep(&c, inventory_of(ps_pawn(t.ps[i])), "AdjustCurrency")) continue;
        SET(&c, "CurrencyDelta", int32_t, (int32_t)v);
        void *cs = carg(&c, "Cause");
        if (cs) fstring_set(cs, "b4bcoop", cause, 32);
        call_go(&c);
        n++;
    }
    out_printf(o, "copper %+ld for %d hero(es)\n", v, n);
    if (n && (t.others || t.all)) {
        if (v > 0) notice("host gave %ld copper to %s", v, t.label);
        else notice("host took %ld copper from %s", -v, t.label);
    }
}

// ---- cards ----
static UObject *card_manager(void) { UObject *gs = game_state(); return gs ? ue_get_ptr(gs, "GameplayCardManager") : NULL; }
static void norm(const char *s, char *out, size_t n) {   // lowercase letters/digits only
    size_t k = 0;
    for (; *s && k + 1 < n; s++) if (isalnum((unsigned char)*s)) out[k++] = (char)tolower((unsigned char)*s);
    out[k] = 0;
}
#define ADDR_FMEMORY_FREE VA(0x140C823B0ull)
static const uint8_t SIG_FREE[] = {0x48,0x85,0xc9,0x74,0x49,0x53,0x48,0x83,0xec,0x20,0x48,0x8b,0xd9};
// UDataTable RowMap: TMap sparse array of {FName, uint8* row, hash} (0x18), allocation bits TBitArray at +0x10 (inline
// dwords) or its heap pointer at +0x20, NumBits at +0x28 (as rewardguard.c's sparse_at)
static uint8_t *dt_row(UObject *table, FName row) {
    TArray *m = table ? DT_ROWMAP(table) : NULL;
    if (!m || !m->data) return NULL;
    const uint32_t *bits = *(uint32_t *const *)((char *)m + 0x20);
    if (!bits) bits = (const uint32_t *)((char *)m + 0x10);
    int nbits = *(int32_t *)((char *)m + 0x28);
    for (int i = 0; i < m->num && i < nbits; i++) {
        if (!((bits[i >> 5] >> (i & 31)) & 1)) continue;   // free slot
        char *e = (char *)m->data + (size_t)i * 0x18;
        if (((FName *)e)->idx == row.idx && ((FName *)e)->num == row.num) return *(uint8_t **)(e + 8);
    }
    return NULL;
}
// a card's name as the game shows it: GameplayCardRow.Name (FText at +0x10) through KismetTextLibrary.Conv_TextToString
static void card_display(const RowHandle *h, char *buf, size_t n) {
    buf[0] = 0;
    uint8_t *row = dt_row(h->table, h->row);
    Call c;
    if (!row || !call_prep(&c, class_cdo("KismetTextLibrary"), "Conv_TextToString")) return;
    void *in = carg(&c, "InText");
    FString *out = carg(&c, "ReturnValue");
    if (!in || !out) return;
    memcpy(in, row + 0x10, 0x18);   // borrowed: the parms block is never destructed
    call_go(&c);
    ascii_of(out, buf, n);
    if (out->data && !memcmp((void *)ADDR_FMEMORY_FREE, SIG_FREE, sizeof SIG_FREE)) ((void (*)(void *))ADDR_FMEMORY_FREE)(out->data);
}

// AllCards (every gameplay card handle) -> the handle(s) whose row name or shown name matches
static int card_match(UObject *gcm, const char *want, RowHandle **hit, Out *list, int max_list) {
    int32_t off = ue_prop_offset(gcm, "AllCards");
    if (off < 0) return -1;
    TArray *all = (TArray *)((char *)gcm + off);
    char w[96], rn[128], dn[128], a[128], b[128];
    norm(want ? want : "", w, sizeof w);
    int exact = 0, sub = 0, shown = 0;
    RowHandle *first_sub = NULL;
    for (int i = 0; i < all->num; i++) {
        RowHandle *h = (RowHandle *)((char *)all->data + (size_t)i * sizeof(RowHandle));
        ue_name(h->row, rn, sizeof rn);
        card_display(h, dn, sizeof dn);
        norm(rn, a, sizeof a); norm(dn, b, sizeof b);
        if (w[0] && (!strcmp(a, w) || !strcmp(b, w))) { *hit = h; exact = 1; break; }
        if (!w[0] || strstr(a, w) || strstr(b, w)) {
            if (!first_sub) first_sub = h;
            sub++;
            if (list && shown++ < max_list) out_printf(list, "  %s  (%s)\n", dn[0] ? dn : "?", rn);
        }
    }
    if (exact) return 1;
    if (sub == 1) { *hit = first_sub; return 1; }
    return sub ? -sub : 0;
}

static UObject *slot_of(UObject *pawn) {
    UObject *gs = game_state(), *psm = gs ? ue_get_ptr(gs, "PlayerSlotManager") : NULL;
    int32_t off = psm ? ue_prop_offset(psm, "TeamSlots") : -1;
    TArray *teams = off >= 0 ? (TArray *)((char *)psm + off) : NULL;
    for (int t = 0; teams && t < teams->num; t++) {
        TArray *slots = (TArray *)((uint8_t *)teams->data + (size_t)t * 0x18 + 8);   // {team, TArray<PlayerSlot*>}
        for (int i = 0; i < slots->num; i++) {
            UObject *s = ((UObject **)slots->data)[i];
            if (s && ue_get_ptr(s, "AssignedPawn") == pawn) return s;
        }
    }
    return NULL;
}

static void cmd_card(char *rest, Out *o) {
    UObject *gcm = card_manager();
    if (!gcm || !in_mission()) { out_printf(o, "cards: only during a mission\n"); return; }
    char *a = rest ? strtok(rest, " ") : NULL;
    if (!a) { out_printf(o, "usage: /card <card name> [player]  |  /card list [filter]\n"); return; }
    RowHandle *h = NULL;
    if (!_stricmp(a, "list")) {
        char *f = strtok(NULL, "");
        size_t before = o->len;
        int r = card_match(gcm, f, &h, o, 40);
        if (r == 1 && h && o->len == before) { char dn[128], rn[128]; card_display(h, dn, sizeof dn); out_printf(o, "  %s  (%s)\n", dn, ue_name(h->row, rn, sizeof rn)); }
        else if (r < -40) out_printf(o, "... %d matches, narrow it down: /card list <filter>\n", -r);
        else if (!r) out_printf(o, "no card matches '%s'\n", f ? f : "");
        return;
    }
    // the card name may have spaces; a trailing word naming a player (or "all"/"me") is the target
    char name[128], *who = NULL, *more = strtok(NULL, "");
    snprintf(name, sizeof name, "%s%s%s", a, more ? " " : "", more ? more : "");
    char *sp = strrchr(name, ' ');
    if (sp) {
        UObject **pa; int n = admin_player_array(&pa), is_player = !_stricmp(sp + 1, "all") || !_stricmp(sp + 1, "me") || sp[1] == '#';
        char nm[64];
        for (int i = 0; i < n && !is_player; i++) { admin_display_name(pa[i], nm, sizeof nm); if (!_stricmp(nm, sp + 1)) is_player = 1; }
        if (is_player) { *sp = 0; who = sp + 1; }
    }
    int r = card_match(gcm, name, &h, NULL, 0);
    if (r != 1) {
        if (!r) out_printf(o, "no card matches '%s' (/card list [filter])\n", name);
        else { out_printf(o, "'%s' matches %d cards:\n", name, -r); card_match(gcm, name, &h, o, 10); }
        return;
    }
    Targets t;
    if (!targets(who, &t, o)) return;
    char dn[128], rn[128];
    card_display(h, dn, sizeof dn);
    ue_name(h->row, rn, sizeof rn);
    int n = 0;
    for (int i = 0; i < t.n; i++) {
        UObject *slot = slot_of(ps_pawn(t.ps[i]));
        Call c;
        if (!slot || !call_prep(&c, gcm, "AddGameplayCardToSlotByRowHandle")) continue;
        SET(&c, "PlayerSlot", UObject *, slot);
        RowHandle *ph = carg(&c, "GameplayCardRowHandle");
        if (!ph) continue;
        ph->table = h->table; ph->row = h->row;   // display string left empty (not needed for the lookup)
        LOG("cheats: card %s (%s) to slot %p", rn, dn, (void *)slot);
        call_go(&c);
        n++;
    }
    out_printf(o, "card %s added for %d hero(es)\n", dn[0] ? dn : rn, n);
    if (n && (t.others || t.all)) notice("host gave the card %s to %s", dn[0] ? dn : rn, t.label);
}

// ---- engine CheatManager ----
static UObject *cheat_manager(UObject *pc) {
    int32_t off = pc ? ue_prop_offset(pc, "CheatManager") : -1;
    if (off < 0) return NULL;
    UObject **slot = (UObject **)((char *)pc + off);
    if (*slot) return *slot;
    if (memcmp((void *)ADDR_SCO, SIG_SCO, sizeof SIG_SCO)) { LOG("cheats: StaticConstructObject signature mismatch"); return NULL; }
    UClass *cc = ue_get_ptr(pc, "CheatClass");
    UClass *base = cls("CheatManager");
    if (!cc || !base || !UC_CDO(cc) || !ue_is_a(UC_CDO(cc), base)) cc = base;
    if (!cc) return NULL;
    FName none = {0, 0};
    UObject *cm = ((ScoFn)ADDR_SCO)(cc, pc, none, 0, 0, NULL, 0, NULL, 0);
    if (cm) *slot = cm;   // referenced by the controller's UPROPERTY: lives as long as the controller
    char a[128], b[128];
    LOG("cheats: created %s for %s", cm ? ue_obj_name(cm, a, sizeof a) : "(null)", ue_obj_name(pc, b, sizeof b));
    return cm;
}
static int cm_call(UObject *pc, const char *fn, const float *arg) {
    UObject *cm = cheat_manager(pc);
    Call c;
    if (!cm || !call_prep(&c, cm, fn)) return 0;
    if (arg) { FField *p = US_CHILDPROPS(c.fn); if (p) *(float *)(c.p + FP_OFFSET(p)) = *arg; }
    call_go(&c);
    return 1;
}

// fly / noclip / walk [player|all], humans only. The engine's CheatManager Fly/Ghost/Walk end in ACharacter::ClientCheat*
// _Implementation, which the shipping build left empty, so we do what they would: movement mode Flying (or Falling,
// which lands into Walking) on the server's CharacterMovement, and actor collision off/on for noclip. The owning client
// follows the server's movement mode through the normal movement corrections.
static int set_move(UObject *pawn, int mode) {   // 0 walk, 1 fly, 2 noclip
    UObject *cmc = pawn ? ue_get_ptr(pawn, "CharacterMovement") : NULL;
    Call c;
    if (!cmc || !call_prep(&c, pawn, "SetActorEnableCollision")) return 0;
    SET(&c, "bNewActorEnableCollision", uint8_t, mode != 2);
    call_go(&c);
    if (!call_prep(&c, cmc, "SetMovementMode")) return 0;
    SET(&c, "NewMovementMode", uint8_t, mode ? 5 /*MOVE_Flying*/ : 3 /*MOVE_Falling*/);
    call_go(&c);
    return 1;
}

static void cmd_move(const char *verb, char *rest, Out *o) {
    int mode = !strcmp(verb, "fly") ? 1 : !strcmp(verb, "noclip") ? 2 : 0;
    Targets t;
    if (!targets(rest, &t, o)) return;
    if (mode == 2 && t.others) {   // collision is not replicated: their own game would still stop them at walls
        out_printf(o, "/noclip: only for your own hero (another player's game still collides); /fly works for them\n");
        return;
    }
    int n = 0;
    for (int i = 0; i < t.n; i++) if (ps_pc(t.ps[i]) && set_move(ps_pawn(t.ps[i]), mode)) n++;
    if (!n) { out_printf(o, "/%s: no player to apply it to (bots can't)\n", verb); return; }
    out_printf(o, "%s: %s%s\n", verb, t.label, mode ? " (/walk to land)" : "");
    if (t.others) notice(!mode ? "host put %s back on their feet (walk)" : "host gave %s %s", t.label, mode == 1 ? "fly mode" : "noclip");
}

// size <x>: the host's own hero only. CheatManager.ChangeSize scales the capsule and the mesh on the server; neither is
// replicated, so on another player's hero their own client would fight it (and nobody else would see it).
static float size_now = 1.f;
static void cmd_size(char *rest, Out *o) {
    char *end = NULL;
    float f = rest ? strtof(rest, &end) : 0;
    if (!rest || *end || f < 0.25f || f > 4.f) { out_printf(o, "usage: /size <0.25-4> (your own hero; 1 = normal)\n"); return; }
    if (!ps_pawn(host_ps()) || !cm_call(host_pc(), "ChangeSize", &f)) { out_printf(o, "/size: you have no hero right now\n"); return; }
    size_now = f;
    out_printf(o, "your size: %.2f (only you see it)\n", f);
}

// The engine's debug camera: CheatManager.ToggleDebugCamera on the host's controller spawns a DebugCameraController and
// hands it the local player; the same call on the debug controller's own CheatManager gives the player back.
static void cmd_freecam(Out *o) {
    UObject *lp = ue_local_pc();
    if (is_a(lp, "DebugCameraController")) {
        if (!cm_call(lp, "ToggleDebugCamera", NULL)) { out_printf(o, "/freecam: can't leave the free camera\n"); return; }
        freecam = 0;
        out_printf(o, "free camera off\n");
        return;
    }
    if (!is_a(lp, "GobiPlayerControllerBase")) { out_printf(o, "no player controller\n"); return; }
    if (!cm_call(lp, "ToggleDebugCamera", NULL)) { out_printf(o, "/freecam: not available\n"); return; }
    freecam = is_a(ue_local_pc(), "DebugCameraController");
    freecam_pc = lp; freecam_pci = U_INDEX(lp);
    out_printf(o, freecam ? "free camera on: only your view; press F8 to come back (chat doesn't work in it)\n"
                          : "/freecam: the camera didn't switch\n");
}

static void cmd_slomo(char *rest, Out *o) {
    char *end = NULL;
    float f = rest ? strtof(rest, &end) : 0;
    if (!rest || *end || f < 0.1f || f > 5.f) { out_printf(o, "usage: /slomo <0.1-5> (1 = normal; now %.2f)\n", slomo); return; }
    if (!cm_call(host_pc(), "Slomo", &f)) { out_printf(o, "/slomo: not available\n"); return; }
    slomo = f;
    out_printf(o, "game speed %.2f\n", f);
    notice("host set the game speed to %.2fx", f);
}

static void cmd_freeze(Out *o) {
    if (!cm_call(host_pc(), "PlayersOnly", NULL)) { out_printf(o, "/freeze: not available\n"); return; }
    frozen = !frozen;
    out_printf(o, frozen ? "AI frozen (/freeze again to resume)\n" : "AI resumed\n");
    notice(frozen ? "host froze the ridden and bots (/freeze)" : "host unfroze the world");
}

// ---- teleport ----
// a saferoom of this map: named Saferoom_BP_End / _Start; otherwise the end one = the one farthest from the team
static UObject *saferoom(int want_end, float at[3]) {
    UClass *c = cls("SafeRoom");
    float team[3] = {0}, v[3];
    int nt = 0;
    UObject **pa; int n = admin_player_array(&pa);
    for (int i = 0; i < n; i++) if (!actor_loc(ps_pawn(pa[i]), v)) { team[0] += v[0]; team[1] += v[1]; team[2] += v[2]; nt++; }
    if (nt) { team[0] /= nt; team[1] /= nt; team[2] /= nt; }
    UObject *best = NULL;
    float bd = want_end ? -1.f : 1e30f;
    char nm[128];
    for (UObject *s = next_live(c, NULL); s; s = next_live(c, s)) {   // the map's own naming first: Saferoom_BP_End/_Start
        ue_obj_name(s, nm, sizeof nm);
        if (strstr(nm, want_end ? "_End" : "_Start") && !actor_loc(s, at)) return s;
    }
    for (UObject *s = next_live(c, NULL); s; s = next_live(c, s)) {
        UObject *vol = ue_get_ptr(s, "DefaultSaferoomVolume");
        if (actor_loc(s, v)) continue;
        if (vol) { Call k; if (call_prep(&k, vol, "K2_GetComponentLocation")) { call_go(&k); float *r = carg(&k, "ReturnValue"); if (r) memcpy(v, r, 12); } }
        float d = (v[0] - team[0]) * (v[0] - team[0]) + (v[1] - team[1]) * (v[1] - team[1]) + (v[2] - team[2]) * (v[2] - team[2]);
        if (want_end ? d > bd : d < bd) { bd = d; best = s; memcpy(at, v, 12); }
    }
    return best;
}

// K2_TeleportTo (it moves the pawn out of walls) at a spot around `to`: the n-th hero sent gets its own spot, and
// beside=1 skips the exact spot (a player stands there)
static int teleport(UObject *pawn, const float to[3], int first) {
    static const float off[][2] = {{0, 0}, {120, 0}, {-120, 0}, {0, 120}, {0, -120}, {120, 120}, {-120, -120}, {120, -120}, {-120, 120}};
    for (int j = 0; j < 9; j++) {
        int k = (first + j) % 9;
        Call c;
        if (!call_prep(&c, pawn, "K2_TeleportTo")) return 0;
        float *d = carg(&c, "DestLocation");
        if (!d) return 0;
        d[0] = to[0] + off[k][0]; d[1] = to[1] + off[k][1]; d[2] = to[2];
        void *rot = carg(&c, "DestRotation");
        Call r;
        if (rot && call_prep(&r, pawn, "K2_GetActorRotation")) { call_go(&r); void *rv = carg(&r, "ReturnValue"); if (rv) memcpy(rot, rv, 12); }
        call_go(&c);
        uint8_t *ok = carg(&c, "ReturnValue");
        if (ok && *ok) return 1;
    }
    return 0;
}

static void cmd_tp(char *rest, Out *o) {
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL;
    if (!a) { out_printf(o, "usage: /tp <player|saferoom|start>  |  /tp <player|all> <player|me|saferoom|start>\n"); return; }
    const char *who = b ? a : "me", *dest = b ? b : a;
    float to[3];
    char dlabel[64];
    int beside = 0;
    if (!_stricmp(dest, "saferoom") || !_stricmp(dest, "start")) {
        int end = !_stricmp(dest, "saferoom");
        if (!saferoom(end, to)) { out_printf(o, "no saferoom in this map\n"); return; }
        to[2] += 20.f;
        snprintf(dlabel, sizeof dlabel, end ? "the saferoom" : "the start saferoom");
    } else {
        Targets d;
        if (!targets(dest, &d, o) || d.all) { if (d.all) out_printf(o, "can't teleport to everyone\n"); return; }
        if (actor_loc(ps_pawn(d.ps[0]), to)) { out_printf(o, "no position for %s\n", dest); return; }
        snprintf(dlabel, sizeof dlabel, "%s", d.label);
        beside = 1;
    }
    Targets t;
    if (!targets(who, &t, o)) return;
    int n = 0;
    for (int i = 0; i < t.n; i++) {
        UObject *pawn = ps_pawn(t.ps[i]);
        float me[3];
        if (!actor_loc(pawn, me) && fabsf(me[0] - to[0]) < 1 && fabsf(me[1] - to[1]) < 1) continue;   // the destination itself
        n += teleport(pawn, to, beside + n);
    }
    out_printf(o, "teleported %d hero(es) to %s\n", n, dlabel);
    if (n && t.others) notice("host teleported %s to %s", t.label, dlabel);
}

// ---- world / director ----
static void cmd_horde(Out *o) {
    Call c;
    UObject *cdo = class_cdo("GameDirector");
    if (!in_mission() || !call_prep(&c, cdo, "TriggerHordeOnDirector")) { out_printf(o, "/horde: only during a mission\n"); return; }
    static wchar_t why[32];
    SET(&c, "WorldContextObject", UObject *, ue_world());
    void *r = carg(&c, "TriggerReason");
    if (r) fstring_set(r, "b4bcoop", why, 32);
    SET(&c, "bHordeAudioCue", uint8_t, 1);
    call_go(&c);
    out_printf(o, "horde triggered\n");
    notice("host triggered a horde");
}

static void cmd_director(char *rest, Out *o) {
    static const struct { const char *name; uint8_t v; const char *label; } P[] = {
        {"calm", 1, "calm"}, {"build", 2, "build-up"}, {"violent", 2, "build-up"}, {"peak", 4, "peak"},
        {"fade", 8, "peak fade"}, {"recover", 16, "recover"},
    };
    int i = 0;
    for (; rest && i < (int)(sizeof P / sizeof P[0]) && _stricmp(rest, P[i].name); i++) {}
    if (!rest || i == (int)(sizeof P / sizeof P[0])) { out_printf(o, "usage: /director calm|build|peak|fade|recover\n"); return; }
    Call c;
    if (!in_mission() || !call_prep(&c, class_cdo("GameDirector"), "ForcePacingPhaseOnDirector")) { out_printf(o, "/director: only during a mission\n"); return; }
    static wchar_t why[32];
    SET(&c, "WorldContextObject", UObject *, ue_world());
    SET(&c, "Phase", uint8_t, P[i].v);
    void *r = carg(&c, "Reason");
    if (r) fstring_set(r, "b4bcoop", why, 32);
    call_go(&c);
    Call g;   // what the director says now
    int now = -1;
    if (call_prep(&g, class_cdo("GameDirector"), "GetDirectorPacingPhase")) {
        SET(&g, "WorldContextObject", UObject *, ue_world());
        call_go(&g);
        uint8_t *rv = carg(&g, "ReturnValue");
        if (rv) now = *rv;
    }
    out_printf(o, "director pacing: %s (phase now %d)\n", P[i].label, now);
    notice("host forced the director's pacing to %s", P[i].label);
}

// Ridden: friendly name -> the Blueprint class (loaded on demand).
static const struct { const char *name, *path; } RIDDEN[] = {
#include "cheats_ridden.h"
};
#define N_RIDDEN ((int)(sizeof RIDDEN / sizeof RIDDEN[0]))

static UClass *load_class(const char *path) {
    // already loaded?
    const char *dot = strrchr(path, '.');
    UClass *c = dot ? ue_find_class(dot + 1) : NULL;
    if (c) return c;
    Call k;   // KismetSystemLibrary::LoadClassAsset_Blocking(TSoftClassPtr): {FWeakObjectPtr, int32 tag, FSoftObjectPath {FName, FString}}
    if (!call_prep(&k, class_cdo("KismetSystemLibrary"), "LoadClassAsset_Blocking")) return NULL;
    uint8_t *soft = carg(&k, "AssetClass");
    if (!soft) return NULL;
    *(int32_t *)soft = -1;
    static wchar_t w[256];
    int n = 0;
    for (; path[n] && n < 255; n++) w[n] = (wchar_t)path[n];
    w[n] = 0;
    ((FNameCtorFn)ADDR_FNAME_CTOR)((FName *)(soft + 0x10), w, 1);
    call_go(&k);
    UObject **r = carg(&k, "ReturnValue");
    LOG("cheats: loaded %s -> %p", path, r ? (void *)*r : NULL);
    return r ? *r : NULL;
}

static void cmd_spawn(char *rest, Out *o) {
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL;
    int i = 0;
    for (; a && i < N_RIDDEN && _stricmp(a, RIDDEN[i].name); i++) {}
    int cnt = b ? atoi(b) : 1;
    if (!a || i == N_RIDDEN || cnt < 1 || cnt > 10) {
        out_printf(o, "usage: /spawn <type> [1-10]; types:");
        for (int k = 0; k < N_RIDDEN; k++) if (k == 0 || strcmp(RIDDEN[k].name, RIDDEN[k - 1].name)) out_printf(o, " %s", RIDDEN[k].name);
        out_printf(o, "\n");
        return;
    }
    if (!in_mission()) { out_printf(o, "/spawn: only during a mission\n"); return; }
    UObject *pawn = ps_pawn(host_ps());
    float at[3], fwd[3];
    if (actor_loc(pawn, at) || actor_fwd(pawn, fwd)) { out_printf(o, "you have no hero right now\n"); return; }
    UClass *cls = NULL;
    for (int k = i; k < N_RIDDEN && !_stricmp(RIDDEN[k].name, a) && !cls; k++) cls = load_class(RIDDEN[k].path);
    if (!cls) { out_printf(o, "/spawn: couldn't load the %s class\n", a); return; }
    int n = 0;
    for (int k = 0; k < cnt; k++) {
        Call c;
        if (!call_prep(&c, class_cdo("GameDirector"), "GobiSpawnAIFromClass")) break;
        SET(&c, "WorldContextObject", UObject *, ue_world());
        SET(&c, "PawnClass", UObject *, cls);
        float *l = carg(&c, "Location");
        float d = 500.f + 120.f * k, side = (k % 2 ? 1 : -1) * 80.f * ((k + 1) / 2);
        if (l) { l[0] = at[0] + fwd[0] * d - fwd[1] * side; l[1] = at[1] + fwd[1] * d + fwd[0] * side; l[2] = at[2] + 50.f; }
        float *r = carg(&c, "Rotation");
        if (r) r[1] = atan2f(-fwd[1], -fwd[0]) * 57.29578f;   // yaw facing the host
        SET(&c, "bNoCollisionFail", uint8_t, 1);
        call_go(&c);
        UObject **rv = carg(&c, "ReturnValue");
        if (rv && *rv) n++;
    }
    out_printf(o, "spawned %d/%d %s\n", n, cnt, a);
    if (n) notice("host spawned %d %s", n, a);
}

// Every living ridden: GobiCharacters that aren't heroes (players, bots, and NPC allies like Emmett are
// HeroCharacters) and aren't controlled by a player. Common ridden and the mutations (Common_/Hag_/Snitcher_/
// Brute_AICharacterBP) are plain GobiCharacters; the specials are ZombieCharacters.
static void cmd_killall(Out *o) {
    UClass *gc = cls("GobiCharacter"), *hc = cls("HeroCharacter"), *pcc = cls("PlayerController");
    if (!gc) { out_printf(o, "/killall: no ridden here\n"); return; }
    int n = 0;
    for (UObject *z = next_live(gc, NULL); z; z = next_live(gc, z)) {
        if (hc && ue_is_a(z, hc)) continue;   // never heroes (players, bots, NPC allies)
        UObject *ctrl = ue_get_ptr(z, "Controller");
        if (ctrl && pcc && ue_is_a(ctrl, pcc)) continue;   // never a player-controlled character
        UObject *ls = life_of(z);
        if (!ls || call_bool(ls, "IsAlive") != 1) continue;
        if (call_void(ls, "Kill")) n++;
    }
    out_printf(o, "killed %d ridden\n", n);
    if (n) notice("host killed all ridden (%d)", n);
}

static void cmd_endmission(int ok, Out *o) {
    UObject *gm = game_mode();
    Call c;
    if (!in_mission() || !call_prep(&c, gm, "OnMissionEnd")) { out_printf(o, "/%s: only during a mission\n", ok ? "win" : "lose"); return; }
    static wchar_t ctx[32];
    SET(&c, "bSuccess", uint8_t, (uint8_t)ok);
    void *cx = carg(&c, "Context");
    if (cx) fstring_set(cx, "b4bcoop cheat", ctx, 32);
    notice(ok ? "host ended the mission: success (no rewards are sent to other players' saves)"
              : "host ended the mission: failure");
    call_go(&c);
    out_printf(o, "mission ended (%s)\n", ok ? "success" : "failure");
}

// ---- host's own progression ----
static UObject *own_profile(Out *o) {
    UObject *pc = host_pc(), *player = pc ? ue_get_ptr(pc, "Player") : NULL;
    if (!player || !is_a(player, "LocalPlayer") || admin_is_client()) { out_printf(o, "no local profile\n"); return NULL; }
    UObject *ppc = ue_get_ptr(pc, "GobiPlayerProfileComponent");
    if (!ppc) out_printf(o, "no profile component (sign in first)\n");
    return ppc;
}

// UGobiPlayerProfileComponent::ExecuteCommand(const FPlayerProfileCommand&, bool bPersist) on a local controller applies
// the command to that player's offline data; rewards.c owns the hook and runs it for us (rewards_execute_local: local
// components only, never forwarded). The client RPC handlers can't be used: they return early on the server. Commands
// are built with their own C++ vtables (from the RPC thunks' default construction), checked through GetType first.
#define VT_ADJUST_SP   VA(0x1454E8880ull)   // FAdjustSupplyPointsCommand, GetType() == 5
#define VT_UNLOCK      VA(0x1454E88F8ull)   // FUnlockProductCommand, GetType() == 6
static int command_ok(uintptr_t vt, uint8_t type) {
    const uint8_t *get_type = *(const uint8_t *const *)(vt + 8);   // slot 1: mov al, <type>; ret
    return get_type && get_type[0] == 0xb0 && get_type[1] == type && get_type[2] == 0xc3;
}

static void cmd_supply(char *rest, Out *o) {
    char *end = NULL;
    long v = rest ? strtol(rest, &end, 10) : 0;
    if (!rest || *end || v < 1 || v > 100000) { out_printf(o, "usage: /supply <+N> (1-100000; your own supply points)\n"); return; }
    UObject *ppc = own_profile(o);
    if (!ppc) return;
    if (!command_ok(VT_ADJUST_SP, 5)) { out_printf(o, "/supply: not available in this game build\n"); return; }
    struct { uintptr_t vt; int32_t delta, pad; } cmd = {VT_ADJUST_SP, (int32_t)v, 0};   // FAdjustSupplyPointsCommand
    LOG("cheats: host's own profile: AdjustSupplyPoints %+ld", v);
    if (rewards_execute_local(ppc, &cmd)) { out_printf(o, "/supply: not available\n"); return; }
    out_printf(o, "+%ld supply points in YOUR save. This is permanent (saved in a few seconds).\n", v);
}

// Every product in the supply lines (StaticCaravans: tutorial line + every chain), unlockable (not a consumable like a
// burn card), not DLC, that this profile hasn't unlocked yet.
static void cmd_unlockall(char *rest, Out *o) {
    UObject *ppc = own_profile(o);
    if (!ppc) return;
    UClass *scc = cls("StaticCaravans");
    UObject *sc = NULL;
    for (int32_t i = 0, n = ue_num_objects(); scc && i < n && !sc; i++) {
        UObject *x = ue_object_at(i);
        if (x && !(U_FLAGS(x) & 0x10) && ue_is_a(x, scc)) sc = x;
    }
    if (!sc) { out_printf(o, "/unlockall: use it in Fort Hope (the supply lines are not loaded here)\n"); return; }
    int32_t ot = ue_prop_offset(sc, "TutorialCaravan"), oc = ue_prop_offset(sc, "CaravanChains");
    if (ot < 0 || oc < 0) return;
    RowHandle *list[1024];
    int n = 0;
    TArray *items = (TArray *)((char *)sc + ot + 0x20);   // StaticCaravan {Merchant handle, TArray<handle> Items}
    for (int k = 0; k < items->num && n < 1024; k++) list[n++] = (RowHandle *)items->data + k;
    TArray *chains = (TArray *)((char *)sc + oc);
    for (int ci = 0; ci < chains->num; ci++) {
        TArray *cars = (TArray *)((char *)chains->data + ci * 0x10);
        for (int k = 0; k < cars->num; k++) {
            TArray *it = (TArray *)((char *)cars->data + k * 0x30 + 0x20);
            for (int j = 0; j < it->num && n < 1024; j++) list[n++] = (RowHandle *)it->data + j;
        }
    }
    int dry = rest && !_stricmp(rest, "check");
    if (!dry && !command_ok(VT_UNLOCK, 6)) { out_printf(o, "/unlockall: not available in this game build\n"); return; }
    int done = 0, owned = 0, skipped = 0;
    UObject *pc = host_pc();
    for (int k = 0; k < n; k++) {
        RowHandle *h = list[k];
        uint8_t *row = dt_row(h->table, h->row);   // ProductRow
        if (!row || row[0x91] != 0 /*Unlockable*/ || row[0xa9] != 0 /*no DLC*/) { skipped++; continue; }
        Call q;   // CaravanManager::IsCaravanItemCompletedForPlayer(PC, CaravanItem{handle, Purchased})
        if (call_prep(&q, class_cdo("CaravanManager"), "IsCaravanItemCompletedForPlayer")) {
            SET(&q, "PlayerController", UObject *, pc);
            RowHandle *ci = carg(&q, "CaravanItem");
            if (ci) { ci->table = h->table; ci->row = h->row; }
            call_go(&q);
            uint8_t *rv = carg(&q, "ReturnValue");
            if (rv && *rv) { owned++; continue; }
        }
        if (dry) { done++; continue; }
        struct { uintptr_t vt; RowHandle product; FString reason; } cmd;   // FUnlockProductCommand (0x38)
        memset(&cmd, 0, sizeof cmd);
        cmd.vt = VT_UNLOCK;
        cmd.product.table = h->table; cmd.product.row = h->row;
        if (rewards_execute_local(ppc, &cmd)) { out_printf(o, "/unlockall: not available\n"); break; }
        done++;
    }
    LOG("cheats: host's own profile: unlockall %d new, %d already owned, %d skipped (consumable/DLC) of %d", done, owned, skipped, n);
    if (dry) { out_printf(o, "/unlockall would unlock %d item(s) (%d already yours, %d consumables/DLC skipped)\n", done, owned, skipped); return; }
    out_printf(o, "unlocked %d supply-line item(s) in YOUR save (%d were already yours; burn cards and DLC items skipped). "
                  "This is permanent.\n", done, owned);
}

// ---- on/off ----
static void all_effects_off(int world_alive) {
    if (world_alive) {
        god_all_off();
        if (ammo_inf) ammo_restore();
        if (frozen) cm_call(host_pc(), "PlayersOnly", NULL);
        if (slomo != 1.f) { float one = 1.f; cm_call(host_pc(), "Slomo", &one); }
        if (size_now != 1.f && ps_pawn(host_ps())) { float one = 1.f; cm_call(host_pc(), "ChangeSize", &one); }
        if (is_a(ue_local_pc(), "DebugCameraController")) cm_call(ue_local_pc(), "ToggleDebugCamera", NULL);
    }
    n_gods = 0; n_ammo = 0;
    ammo_inf = frozen = freecam = 0;
    slomo = size_now = 1.f;
}

// Besides rewards.c's forwarded rewards, the host's game itself sends each remote player a few things that end up in
// their save or account: stat deltas (PlayerStatsComponent.ClientApplyStatDeltas: missions completed, kills, ...) and
// manual achievements. While cheats_tainted() those RPCs are not sent. They are component RPCs, so they all leave
// through UActorComponent::CallRemoteFunction, hooked the first time cheats are turned on (never, if nobody cheats).
#define ADDR_COMP_CRF VA(0x143B2C830ull)   // bool UActorComponent::CallRemoteFunction(this, UFunction*, void* Parms,
                                            //   FOutParmRec*, FFrame*): the component's net driver sends it
static const uint8_t SIG_COMP_CRF[] = {0x4c,0x89,0x44,0x24,0x18,0x53,0x55,0x57,0x41,0x55,0x41,0x56,0x41,0x57,0x48,0x83,
                                       0xec,0x48,0x4c,0x8b,0xb1,0xd8,0x00,0x00,0x00};
typedef uint8_t (*CompCrfFn)(UObject *self, UFunction *fn, void *parms, void *out, void *stack);
static CompCrfFn orig_comp_crf;
static UFunction *blocked_fn[4];
static int n_blocked_sent;
static uint8_t comp_crf_detour(UObject *self, UFunction *fn, void *parms, void *out, void *stack) {
    if (fn && cheats_tainted())
        for (int i = 0; i < 4; i++) {
            if (!blocked_fn[i] || blocked_fn[i] != fn) continue;
            char nm[96];
            if (n_blocked_sent++ < 20) LOG("cheats: not sending %s to a remote player (cheats were on this map)", ue_obj_name(fn, nm, sizeof nm));
            return 1;   // "sent"
        }
    return orig_comp_crf(self, fn, parms, out, stack);
}
static void hook_client_progress(void) {
    static int tried;
    if (tried) return;
    tried = 1;
    UClass *st = cls("PlayerStatsComponent"), *ac = cls("AchievementTrackerComponent");
    blocked_fn[0] = st ? ue_find_function(st, "ClientApplyStatDeltas") : NULL;
    blocked_fn[1] = st ? ue_find_function(st, "ClientForceReconcileLegendaryMapStatsWithProfile") : NULL;
    blocked_fn[2] = ac ? ue_find_function(ac, "ClientUnlockManualAchievement") : NULL;
    if (memcmp((void *)ADDR_COMP_CRF, SIG_COMP_CRF, sizeof SIG_COMP_CRF)) { LOG("cheats: component RPC signature mismatch"); return; }
    if (MH_CreateHook((void *)ADDR_COMP_CRF, (void *)comp_crf_detour, (void **)&orig_comp_crf) != MH_OK ||
        MH_EnableHook((void *)ADDR_COMP_CRF) != MH_OK) { LOG("cheats: component RPC hook failed"); orig_comp_crf = NULL; return; }
    LOG("cheats: holding back stats/achievements for remote players while cheats taint the map (%p %p %p)",
        (void *)blocked_fn[0], (void *)blocked_fn[1], (void *)blocked_fn[2]);
}

static void set_on(int en, Out *o) {
    if (en == on) { out_printf(o, "cheats are already %s\n", on ? "on" : "off"); return; }
    if (!en) all_effects_off(1);
    if (en) hook_client_progress();
    on = en;
    if (en) taint_world = ue_world();
    LOG("cheats: %s by the host", en ? "ENABLED" : "disabled");
    out_printf(o, en ? "cheats on. /cheats help lists them. Rewards for this map won't be sent to the other players' saves.\n"
                     : "cheats off\n");
    notice(en ? "host enabled cheats (this map's rewards aren't sent to other players' saves)" : "host disabled cheats");
}

static const char *const VERBS[] = {"cheats", "god", "heal", "revive", "ammo", "copper", "card", "fly", "noclip", "walk",
    "tp", "freecam", "size", "horde", "director", "spawn", "killall", "freeze", "slomo", "win", "lose", "unlockall", "supply"};

static void help(Out *o) {
    out_printf(o, "cheats (host only, after /cheats on; [p] = player name, #n, me or all):\n"
                  "/god [p] [on|off]  /heal [p]  /revive [p]  /ammo infinite|off  /copper +N [p]  /card <name>|list [p]\n"
                  "/fly [p]  /noclip  /walk [p]  /tp [p] <p|saferoom|start>  /freecam  /size <x>\n"
                  "/horde  /director calm|build|peak  /spawn <type> [n]  /killall  /freeze  /slomo <x>  /win  /lose\n"
                  "YOUR save, permanent: /supply +N  /unlockall\n"
                  "state: cheats %s", on ? "ON" : "off");
    if (on) out_printf(o, ", god %d, ammo %s, speed %.2f%s%s", n_gods, ammo_inf ? "infinite" : "normal", slomo, frozen ? ", frozen" : "", freecam ? ", freecam" : "");
    out_printf(o, "\n");
}

int cheats_slash(const char *verb, char *rest, Out *o) {
    int i = 0;
    for (; i < (int)(sizeof VERBS / sizeof VERBS[0]) && strcmp(VERBS[i], verb); i++) {}
    if (i == (int)(sizeof VERBS / sizeof VERBS[0])) return 0;
    if (admin_is_client()) { out_printf(o, "/%s: host only (you are a client)\n", verb); return 1; }
    while (rest && *rest == ' ') rest++;
    if (rest && !*rest) rest = NULL;
    if (rest) { char *e = rest + strlen(rest); while (e > rest && e[-1] == ' ') *--e = 0; }
    LOG("cheats: /%s %s", verb, rest ? rest : "");
    if (!strcmp(verb, "cheats")) {
        if (!rest || !_stricmp(rest, "help") || !_stricmp(rest, "status")) help(o);
        else if (!_stricmp(rest, "on")) set_on(1, o);
        else if (!_stricmp(rest, "off")) set_on(0, o);
        else out_printf(o, "usage: /cheats on|off|help\n");
        return 1;
    }
    if (!on) { out_printf(o, "/%s: cheats are off (the host types /cheats on first)\n", verb); return 1; }
    if (!ue_world() || !game_state() || !host_pc()) { out_printf(o, "/%s: not now (loading)\n", verb); return 1; }
    if (!strcmp(verb, "god")) cmd_god(rest, o);
    else if (!strcmp(verb, "heal")) cmd_heal(rest, o);
    else if (!strcmp(verb, "revive")) cmd_revive(rest, o);
    else if (!strcmp(verb, "ammo")) cmd_ammo(rest, o);
    else if (!strcmp(verb, "copper")) cmd_copper(rest, o);
    else if (!strcmp(verb, "card")) cmd_card(rest, o);
    else if (!strcmp(verb, "fly") || !strcmp(verb, "noclip") || !strcmp(verb, "walk")) cmd_move(verb, rest, o);
    else if (!strcmp(verb, "tp")) cmd_tp(rest, o);
    else if (!strcmp(verb, "freecam")) cmd_freecam(o);
    else if (!strcmp(verb, "size")) cmd_size(rest, o);
    else if (!strcmp(verb, "horde")) cmd_horde(o);
    else if (!strcmp(verb, "director")) cmd_director(rest, o);
    else if (!strcmp(verb, "spawn")) cmd_spawn(rest, o);
    else if (!strcmp(verb, "killall")) cmd_killall(o);
    else if (!strcmp(verb, "freeze")) cmd_freeze(o);
    else if (!strcmp(verb, "slomo")) cmd_slomo(rest, o);
    else if (!strcmp(verb, "win") || !strcmp(verb, "lose")) cmd_endmission(!strcmp(verb, "win"), o);
    else if (!strcmp(verb, "supply")) cmd_supply(rest, o);
    else if (!strcmp(verb, "unlockall")) cmd_unlockall(rest, o);
    return 1;
}

// Map change: per-map effects end with the map (their actors are gone); back in camp or the menus turns cheats off.
// Decided once the new map has its game mode (a map change passes through worlds without one): a mission (the next
// chapter, a restart) keeps cheats on; Fort Hope, the menus, anything else turns them off.
static int world_check;
static void on_world_change(void) {
    all_effects_off(0);
    if (!on) return;
    taint_world = ue_world();   // still on: this map's rewards stay with the host
    world_check = 1;
}

static int own_window_focused(void) {
    DWORD pid = 0;
    HWND w = GetForegroundWindow();
    if (w) GetWindowThreadProcessId(w, &pid);
    return w && pid == GetCurrentProcessId();
}

void cheats_tick(float dt) {
    UObject *w = ue_world();
    if (w != cur_world) { cur_world = w; on_world_change(); }
    if (on && !world_check && w) { int n = ue_num_clients(w); if (n > clients_seen) clients_seen = n; }
    if (world_check && on && w && game_mode()) {
        world_check = 0;
        char pkg[256] = "";
        ue_world_package(w, pkg, sizeof pkg);
        if (!in_mission() || strstr(pkg, "FortHope")) {
            on = 0;
            taint_world = NULL;
            LOG("cheats: off (map change out of the mission)");
            snprintf(pending_notice, sizeof pending_notice, "cheats turned off (back in camp)");
            pending_in = 60.f;
        }
    }
    // after a map change the clients reconnect and load a little later: tell everyone once every player has a hero
    // again (their chat box exists by then), 5 s after that, or after a minute at most
    if (pending_notice[0] && game_state() && host_pc()) {
        pending_in -= dt;
        static float grace = -1;
        if (grace < 0) {
            UObject **pa; int n = admin_player_array(&pa), ready = 0;
            for (int i = 0; i < n; i++) if (ps_pc(pa[i]) && ps_pawn(pa[i])) ready++;
            if (ready > clients_seen) grace = 5.f;
        } else grace -= dt;
        if (pending_in <= 0 || (grace != -1 && grace <= 0)) {
            notice("%s", pending_notice);
            pending_notice[0] = 0;
            clients_seen = 0;
            grace = -1;
        }
    }
    // F8 leaves the free camera (the debug camera's own controller doesn't take chat): the debug controller's own input
    // (PlayerController.WasInputKeyJustPressed), or the key itself while our window has the focus
    if (freecam) {
        UObject *dcc = ue_local_pc();
        int f8 = (GetAsyncKeyState(VK_F8) & 1) && own_window_focused();
        Call k;
        if (!f8 && is_a(dcc, "DebugCameraController") && call_prep(&k, dcc, "WasInputKeyJustPressed")) {
            static FName f8name;
            if (!f8name.idx) ((FNameCtorFn)ADDR_FNAME_CTOR)(&f8name, L"F8", 1);
            FName *key = carg(&k, "Key");   // FKey {FName KeyName, TSharedPtr<FKeyDetails>}: looked up by name
            if (key) { *key = f8name; call_go(&k); uint8_t *r = carg(&k, "ReturnValue"); f8 = r && *r; }
        }
        if (f8) { static Out tmp; out_reset(&tmp); cmd_freecam(&tmp); LOG("cheats: F8: %s", tmp.buf); }
    }
    static float acc;
    if (!on || (acc += dt) < 0.5f) return;
    acc = 0;
    for (int i = 0; i < n_gods; i++) {
        if (!alive(gods[i].ps, gods[i].psi)) { gods[i] = gods[--n_gods]; i--; continue; }
        god_sync(&gods[i]);
    }
    if (ammo_inf) ammo_sync();
}

#ifndef B4B_RELEASE
// Dev CLI: `cheat <command line>` = the host typing /<command line> (e.g. `cheat god all`); `cheatprobe ...` finds
// class paths, card names and saferooms for testing.
int cheats_cmd(const char *verb, char *rest, Out *o) {
    if (!strcmp(verb, "cheat")) {
        if (!rest || !*rest) { help(o); return 1; }
        char *v = strtok(rest, " "), *r = strtok(NULL, "");
        if (v[0] == '/') v++;
        if (!cheats_slash(v, r, o)) out_printf(o, "not a cheat command: %s\n", v);
        return 1;
    }
    if (strcmp(verb, "cheatprobe")) return 0;
    char *what = rest ? strtok(rest, " ") : NULL, *arg = what ? strtok(NULL, " ") : NULL;
    char a[256], b[256], a2[256];
    if (what && !strcmp(what, "classes") && arg) {   // loaded subclasses of <arg> with their paths
        UClass *base = ue_find_class(arg), *cc = ue_find_class("Class");
        int n = 0;
        for (int32_t i = 0, m = ue_num_objects(); base && i < m; i++) {
            UObject *x = ue_object_at(i);
            if (!x || !U_CLASS(x) || !ue_is_a(x, cc)) continue;
            for (UStruct *s = x; s; s = US_SUPER(s)) if (s == base) { out_printf(o, "%s\n", ue_full_path(x, a, sizeof a)); n++; break; }
        }
        out_printf(o, "%d class(es)\n", n);
    } else if (what && !strcmp(what, "live") && arg) {   // live instances of a class in this world
        UClass *c = ue_find_class(arg);
        int n = 0;
        for (UObject *x = next_live(c, NULL); x && n < 60; x = next_live(c, x), n++) {
            float v[3] = {0};
            actor_loc(x, v);
            out_printf(o, "%p %s (%s) at %.0f %.0f %.0f\n", (void *)x, ue_obj_name(x, a, sizeof a), ue_obj_name(U_CLASS(x), b, sizeof b), v[0], v[1], v[2]);
        }
    } else if (what && !strcmp(what, "names") && arg) {   // every FName containing <arg> (soft paths of unloaded assets too)
        const uint8_t *pool = (const uint8_t *)ADDR_NAMEPOOL;
        uint32_t cur_block = *(const uint32_t *)(pool + 0), cursor = *(const uint32_t *)(pool + 4);   // this build: +0 block, +4 cursor
        char **blocks = (char **)(pool + 0x10);
        int hits = 0;
        for (uint32_t bi = 0; bi <= cur_block && hits < 200; bi++) {
            const uint8_t *blk = (const uint8_t *)blocks[bi];
            uint32_t end = bi == cur_block ? cursor : 0x20000;
            for (uint32_t off = 0; blk && off + 2 <= end && hits < 200;) {
                uint16_t hdr = *(const uint16_t *)(blk + off);
                int wide = hdr & 1, len = hdr >> 6;
                if (!len) break;
                if (!wide && len < (int)sizeof a) {
                    memcpy(a, blk + off + 2, len); a[len] = 0;
                    if (strstr(a, arg)) { out_printf(o, "%s\n", a); hits++; }
                }
                off += (2 + len * (wide ? 2 : 1) + 1) & ~1u;
            }
        }
        out_printf(o, "%d name(s)\n", hits);
    } else if (what && !strcmp(what, "load") && arg) {   // try loading a class by path
        UClass *c = load_class(arg);
        out_printf(o, "%s -> %p\n", arg, (void *)c);
    } else if (what && !strcmp(what, "hp") && arg) {   // hp <#n> [value]: HealthComponent.SetHealth (0 = a lethal hit)
        UObject *ps = admin_find_player(arg, o), *ls = life_of(ps_pawn(ps)), *hc = health_of(ps_pawn(ps));
        Call c;
        if (!ls || !hc || !call_prep(&c, hc, "SetHealth")) { out_printf(o, "no hero\n"); return 1; }
        char *hv = strtok(NULL, " ");
        SET(&c, "Health", float, hv ? (float)atof(hv) : 0.f);
        call_go(&c);
        out_printf(o, "hp: health %.0f alive %d incapped %d dead %d\n", call_float(hc, "GetHealth"), call_bool(ls, "IsAlive"),
                   call_bool(ls, "IsIncapped"), call_bool(ls, "IsDead"));
    } else if (what && !strcmp(what, "key") && arg) {   // key <vk>: post a key press to this game's window (dev)
        HWND w = NULL;
        while ((w = FindWindowExW(NULL, w, L"UnrealWindow", NULL))) {
            DWORD pid = 0;
            GetWindowThreadProcessId(w, &pid);
            if (pid == GetCurrentProcessId() && IsWindowVisible(w)) break;
        }
        int vk = (int)strtol(arg, NULL, 0);
        UINT sc = MapVirtualKeyW(vk, 0);
        if (w) { PostMessageW(w, WM_KEYDOWN, vk, 1 | (sc << 16)); PostMessageW(w, WM_KEYUP, vk, 1 | (sc << 16) | 0xC0000000u); }
        out_printf(o, "key 0x%02x -> window %p\n", vk, (void *)w);
    } else if (what && !strcmp(what, "damage") && arg) {   // damage <#n> [amount] [damage type class path]
        char *amt = strtok(NULL, " "), *dt_path = strtok(NULL, " ");
        UObject *ps = admin_find_player(arg, o), *pawn = ps_pawn(ps), *hc = health_of(pawn);
        Call c;
        if (!hc || !call_prep(&c, hc, "Damage")) { out_printf(o, "no hero/health\n"); return 1; }
        SET(&c, "InDamage", float, amt ? (float)atof(amt) : 30.f);
        SET(&c, "DamageTypeClass", UObject *, dt_path ? load_class(dt_path) : cls("GobiDamageType"));
        SET(&c, "Instigator", UObject *, host_pc());
        SET(&c, "SourcePawn", UObject *, ps_pawn(host_ps()));
        SET(&c, "SourceActor", UObject *, ps_pawn(host_ps()));
        float before = call_float(hc, "GetHealth");
        call_go(&c);
        out_printf(o, "damage: health %.0f -> %.0f (damage disabled %d)\n", before, call_float(hc, "GetHealth"),
                   call_bool(hc, "IsDamageDisabled"));
    } else if (what && !strcmp(what, "ammo")) {   // the host hero's weapons: clip / reserve / infinite flag
        UClass *c = cls("ClipAmmoComponent");
        UObject *me = ps_pawn(host_ps());
        for (UObject *a = next_live(c, NULL); a; a = next_live(c, a)) {
            UObject *owner = *(UObject **)((char *)a + 0xD8);   // UActorComponent::OwnerPrivate (unreflected)
            UObject *oo = owner ? ue_get_ptr(owner, "Owner") : NULL;
            if (me && owner != me && oo != me) continue;
            Call k; int clip = -1, res = -1;
            if (call_prep(&k, a, "GetCurrentClipAmount")) { call_go(&k); int32_t *r = carg(&k, "ReturnValue"); if (r) clip = *r; }
            if (call_prep(&k, a, "GetCurrentReserveAmount")) { call_go(&k); int32_t *r = carg(&k, "ReturnValue"); if (r) res = *r; }
            int32_t f = ue_prop_offset(a, "bInfiniteReserveAmmo");
            out_printf(o, "%s of %s: clip %d reserve %d infinite %d\n", ue_obj_name(a, b, sizeof b), owner ? ue_obj_name(owner, a2, sizeof a2) : "-",
                       clip, res, f >= 0 ? *((uint8_t *)a + f) : -1);
        }
    } else if (what && !strcmp(what, "cm")) {   // the host PC's CheatManager
        UObject *pc = host_pc();
        out_printf(o, "pc %p cheatmanager %p class %p\n", (void *)pc, (void *)(pc ? ue_get_ptr(pc, "CheatManager") : NULL),
                   (void *)(pc ? ue_get_ptr(pc, "CheatClass") : NULL));
    } else if (what && !strcmp(what, "state")) {
        out_printf(o, "on=%d tainted=%d gods=%d ammo=%d(%d) frozen=%d slomo=%.2f freecam=%d\n", on, cheats_tainted(), n_gods,
                   ammo_inf, n_ammo, frozen, slomo, freecam);
        for (int i = 0; i < n_gods; i++) {
            char nm[64]; who_name(gods[i].ps, nm, sizeof nm);
            UObject *hc = gods[i].hc;
            out_printf(o, "  god %s hc=%p alive=%d damage_disabled=%d\n", nm, (void *)hc, alive(hc, gods[i].hci),
                       alive(hc, gods[i].hci) ? call_bool(hc, "IsDamageDisabled") : -1);
        }
        UObject **pa; int n = admin_player_array(&pa);
        for (int i = 0; i < n; i++) {
            UObject *pawn = ps_pawn(pa[i]), *hc = health_of(pawn), *inv = inventory_of(pawn);
            char nm[64]; who_name(pa[i], nm, sizeof nm);
            float v[3] = {0};
            actor_loc(pawn, v);
            Call c; int cur = -1;
            if (inv && call_prep(&c, inv, "GetCurrency")) { call_go(&c); int32_t *r = carg(&c, "ReturnValue"); if (r) cur = *r; }
            UObject *cmc = pawn ? ue_get_ptr(pawn, "CharacterMovement") : NULL, *ls = life_of(pawn);
            int32_t mm = cmc ? ue_prop_offset(cmc, "MovementMode") : -1, col = pawn ? ue_prop_offset(pawn, "bActorEnableCollision") : -1;
            out_printf(o, "  #%d %s pawn=%p health=%.0f/%.0f copper=%d alive=%d incap=%d move=%d collision=%d at %.0f %.0f %.0f\n", i, nm,
                       (void *)pawn, hc ? call_float(hc, "GetHealth") : -1, hc ? call_float(hc, "GetCurrentMaxHealth") : -1, cur,
                       ls ? call_bool(ls, "IsAlive") : -1, ls ? call_bool(ls, "IsIncapped") : -1, mm >= 0 ? *((uint8_t *)cmc + mm) : -1,
                       col >= 0 ? *((uint8_t *)pawn + col) : -1, v[0], v[1], v[2]);
        }
    } else out_printf(o, "usage: cheatprobe classes <Base> | live <Class> | cm | state\n");
    return 1;
}
#endif
