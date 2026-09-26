// Dev/test commands for unattended local testing (launch/multi.sh). Dev builds only: the whole file is compiled
// out of player builds (native/build.sh --release). Sign-in automation lives in signin.c.
// Commands: `signin` (one step by hand), `mission [raw] [map] [Easy|Normal|Hard|VeryHard]`, `ready [vote]`,
// `endmission [1|0]`, `burncard list|map|status|charge|[row] [table]`, `callp <Class> <Func> [args]`, `tp`, `takeover`,
// `face` (face bones of custom heads, make a hero speak).
#ifndef B4B_RELEASE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "ue.h"
#include "log.h"
#include "cmds.h"

typedef FName *(*FNameCtorFn)(FName *self, const wchar_t *name, int find_type);
#define ADDR_FNAME_CTOR VA(0x1424BC8E0ull)   // FName::FName(const TCHAR*, EFindName)

static FName make_name(const wchar_t *s) { FName n = {0}; ((FNameCtorFn)ADDR_FNAME_CTOR)(&n, s, 1 /*FNAME_Add*/); return n; }

// live instance of a class (skips the CDO and widget/BP archetypes)
static UObject *find_live(UClass *c, UObject *after) {
    int32_t n = ue_num_objects(), i = after ? U_INDEX(after) + 1 : 0;
    for (; i < n; i++) {
        UObject *o = ue_object_at(i);
        if (!o || (U_FLAGS(o) & 0x30 /*CDO|Archetype*/) || !ue_is_a(o, c)) continue;
        return o;
    }
    return NULL;
}

// ---- mission start without the war table ----
// The war table's start button (MatchmakingScreen::HandleStartMatchmaking) ends in Matchmaking::JoinRun
// (0x141AE9240). Matchmaking::Dev_JoinPool (BlueprintCallable) with a Coop, non-quickplay, non-private pool calls
// the same JoinRun with run id 0 (new run): offline that sets the local campaign run and travels with ?RunOwner=1,
// which our travel hook turns into a listen-server servertravel. (Calling JoinRun natively with the local owner id
// logged the identical "set local campaign run id 0" / NewRun sequence, so the reflected path is used.)
//   mission [map] [Easy|Normal|Hard|VeryHard]   new campaign run via Dev_JoinPool -> JoinRun
//   mission raw [map] [diff]                     bare servertravel of the war-table URL (no JoinRun)
static const char *DEFAULT_MAP = "/Game/Maps/Missions/Evansburgh/MAP_PERS_Evansburgh_B";
static const char *DIFFS[] = {"Easy", "Normal", "Hard", "VeryHard"};

static void fstring_set(FString *s, const char *utf8, wchar_t *storage, int cap) {
    int n = 0;
    for (; utf8[n] && n < cap - 1; n++) storage[n] = (wchar_t)(unsigned char)utf8[n];
    storage[n] = 0;
    s->data = storage; s->num = n + 1; s->max = cap;
}

static int32_t param_off(UFunction *f, const char *name) { FField *p = ue_find_prop(f, name); return p ? FP_OFFSET(p) : -1; }

static void cmd_mission(char *rest, Out *o) {
    char *args[3] = {0};
    int na = 0;
    for (char *t = rest ? strtok(rest, " ") : NULL; t && na < 3; t = strtok(NULL, " ")) args[na++] = t;
    int raw = na && !strcmp(args[0], "raw");
    int diff = 0; const char *map = DEFAULT_MAP;
    for (int k = raw; k < na; k++) {
        int found = 0;
        for (int i = 0; i < 4; i++) if (!_stricmp(args[k], DIFFS[i])) { diff = i; found = 1; }
        if (!found) map = args[k];
    }
    if (raw) {
        char cmd[700];
        snprintf(cmd, sizeof cmd, "servertravel %s?Difficulty=%s?PoolConfig=Coop"
                 "?game=/Game/Modes/HeroGameMode_BP.HeroGameMode_BP_C?RunOwner=1?listen", map, DIFFS[diff]);
        game_exec(cmd);
        out_printf(o, "%s\n", cmd);
        return;
    }
    UClass *mc = ue_find_class("Matchmaking");
    UObject *mm = mc ? find_live(mc, NULL) : NULL;
    UFunction *f = mm ? ue_find_function(U_CLASS(mm), "Dev_JoinPool") : NULL;
    static uint8_t p[256];
    static wchar_t wmap[512];
    memset(p, 0, sizeof p);
    int32_t om = f ? param_off(f, "Map") : -1, od = f ? param_off(f, "Difficulty") : -1;
    if (om < 0 || od < 0) { out_printf(o, "no Matchmaking instance / Dev_JoinPool\n"); return; }
    fstring_set((FString *)(p + om), map, wmap, 512);   // pool Coop, team A, not quickplay/private: all 0
    p[od] = (uint8_t)diff;
    LOG("testing: Dev_JoinPool map=%s difficulty=%s", map, DIFFS[diff]);
    ue_process_event(mm, f, p);
    out_printf(o, "mission: Dev_JoinPool(%s, %s, Coop) -> JoinRun (new campaign run)\n", map, DIFFS[diff]);
}

// ---- mission end without playing it ----
//   endmission [1|0]   host: MissionGameMode::OnMissionEnd(bSuccess, Context) — the BlueprintCallable the game uses
//                      when the party finishes (success) or wipes (failure); runs the normal reward code
static void cmd_endmission(char *rest, Out *o) {
    int ok = !(rest && rest[0] == '0');
    UObject *w = ue_world();
    UObject *gm = w ? ue_get_ptr(w, "AuthorityGameMode") : NULL;
    UClass *mc = ue_find_class("MissionGameMode");
    if (!gm || !mc || !ue_is_a(gm, mc)) { out_printf(o, "no MissionGameMode (not the host, or not in a mission)\n"); return; }
    UFunction *f = ue_find_function(U_CLASS(gm), "OnMissionEnd");
    int32_t ob = f ? param_off(f, "bSuccess") : -1, oc = f ? param_off(f, "Context") : -1;
    if (ob < 0 || oc < 0) { out_printf(o, "no OnMissionEnd(bSuccess, Context)\n"); return; }
    static uint8_t p[64];
    static wchar_t ctx[32];
    memset(p, 0, sizeof p);
    p[ob] = (uint8_t)ok;
    fstring_set((FString *)(p + oc), "b4bcoop", ctx, 32);
    LOG("testing: OnMissionEnd(%d)", ok);
    ue_process_event(gm, f, p);
    out_printf(o, "endmission: OnMissionEnd(%s)\n", ok ? "success" : "failure");
}

// ---- burn cards (docs/investigations/burn-cards.md) ----
// burncard list            cards this instance's player can play now: GameplayCardManager::GetPlayerBurnCards(own
//                          PlayerState) — the list the start-saferoom card UI offers, read from this instance's profile
// burncard [row]           play one of them (default: the first) via GobiPlayerController::ServerPlayBurnCard, like the
//                          UI does. On a client that is a real server RPC to the host.
// burncard <row> <table>   play an explicit card handle (row + card DataTable name), bypassing the list
// burncard map             GameplayCardManager.CardNameToProductHandles: card row -> product row
// burncard status          per player slot: burn cards played this map, total, and (host) queued charges + charge key
// burncard charge          host: run the start-saferoom charge now: GameplayCardManager::OnSafeRoomStateChanged(
//                          InStartingRoom, NotInRoom), the handler that fires when the party leaves the start saferoom
// Why the first version never played anything: it built the handle with a guessed table (PlayerCards_MASTER_DT).
// The server looks the card up by row name, but CanBurnCardBePlayedThisMap then needs the row in the handle's own
// table, so a wrong table fails silently. The list now comes from the game (the handles the UI would pass).
#define ADDR_FMEMORY_FREE VA(0x140C823B0ull)  // FMemory::Free
static const uint8_t SIG_FREE[] = {0x48,0x85,0xc9,0x74,0x49,0x53,0x48,0x83,0xec,0x20,0x48,0x8b,0xd9};
static void game_free(void *p) {
    if (p && !memcmp((void *)ADDR_FMEMORY_FREE, SIG_FREE, sizeof SIG_FREE)) ((void (*)(void *))ADDR_FMEMORY_FREE)(p);
}

static UObject *find_named(const char *name) {
    char nm[160];
    for (int32_t i = 0, n = ue_num_objects(); i < n; i++) {
        UObject *x = ue_object_at(i);
        if (x && U_CLASS(x) && !(U_FLAGS(x) & 0x30) && !strcmp(ue_obj_name(x, nm, sizeof nm), name)) return x;
    }
    return NULL;
}

// The current world's instance (the first live one can be a leftover from the previous map)
static UObject *find_in_world(UClass *c) {
    UObject *w = ue_world(), *first = NULL;
    for (UObject *o = c ? find_live(c, NULL) : NULL; o; o = find_live(c, o)) {
        if (!first) first = o;
        for (UObject *p = U_OUTER(o); p; p = U_OUTER(p))
            if (p == w) return o;
    }
    return first;
}

static UObject *live_gcm(void) {
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    return gs ? ue_get_ptr(gs, "GameplayCardManager") : NULL;
}

static UObject *local_ps(void) {
    UObject *pc = ue_local_pc();
    return pc ? ue_get_ptr(pc, "PlayerState") : NULL;
}

// FDataTableRowHandle in this build: {UDataTable*, FName RowName, FString (unreflected display name)} = 0x20
typedef struct { UObject *table; FName row; FString display; } RowHandle;

// GetPlayerBurnCards(ps) into *out (game-allocated; release with free_handles). Returns -1 if unavailable.
static int player_burn_cards(UObject *gcm, UObject *ps, TArray *out) {
    UFunction *f = gcm ? ue_find_function(U_CLASS(gcm), "GetPlayerBurnCards") : NULL;
    int32_t op = f ? param_off(f, "GobiPlayerState") : -1, orv = f ? param_off(f, "ReturnValue") : -1;
    if (op < 0 || orv < 0 || UFN_PARMSSIZE(f) > 64) return -1;
    uint8_t p[64] = {0};
    *(UObject **)(p + op) = ps;
    ue_process_event(gcm, f, p);
    *out = *(TArray *)(p + orv);
    return out->num;
}

static void free_handles(TArray *a) {
    for (int i = 0; i < a->num; i++) game_free(((RowHandle *)a->data)[i].display.data);
    game_free(a->data);
    a->data = NULL; a->num = a->max = 0;
}

// bool GCM function with one parameter; -1 if it is missing
static int gcm_bool(UObject *gcm, const char *fname, const char *pname, const void *arg, size_t size) {
    UFunction *f = ue_find_function(U_CLASS(gcm), fname);
    int32_t op = f ? param_off(f, pname) : -1, orv = f ? param_off(f, "ReturnValue") : -1;
    if (op < 0 || orv < 0 || UFN_PARMSSIZE(f) > 64) return -1;
    uint8_t p[64] = {0};
    memcpy(p + op, arg, size);
    ue_process_event(gcm, f, p);
    return p[orv];
}

static void burn_status(UObject *gcm, Out *o) {
    int32_t off = ue_prop_offset(gcm, "PlayerActiveGameplayCardDataArray");
    if (off < 0) { out_printf(o, "no slot data\n"); return; }
    TArray *slots = (TArray *)((char *)gcm + off);
    int host = ue_is_listen_server(ue_world());
    char a[128], b[128];
    for (int i = 0; i < slots->num; i++) {
        char *s = (char *)slots->data + i * 0xD8;   // PlayerActiveGameplayCardData
        TArray *played = (TArray *)(s + 0x60);      // BurnCardsPlayedThisMap
        out_printf(o, "slot %d (index %d/%d, player id %d): played this map %d, ever %d", i, *(int32_t *)s, s[4],
                   *(int32_t *)(s + 8), played->num, *(int32_t *)(s + 0x70));
        for (int k = 0; k < played->num; k++) out_printf(o, " %s", ue_name(((RowHandle *)played->data)[k].row, a, sizeof a));
        // the card's effect: PlayBurnCard adds it to the slot's ActiveHeroCards (+0x10, FActiveGameplayCard 0x28 each)
        TArray *active = (TArray *)(s + 0x10);
        out_printf(o, "; active cards %d", active->num);
        for (int k = 0; k < active->num; k++) {
            ue_name(*(FName *)((char *)active->data + k * 0x28 + 8), a, sizeof a);
            if (!_strnicmp(a, "Burn_", 5)) out_printf(o, " [active %s]", a);
        }
        if (host) {   // unreflected, server only: queued for the saferoom-exit charge + the key it is charged under
            TArray *q = (TArray *)(s + 0x78);
            FString *key = (FString *)(s + 0x88);
            size_t n = 0;
            for (int k = 0; key->data && k < key->num && key->data[k] && n + 1 < sizeof b; k++) b[n++] = (char)key->data[k];
            b[n] = 0;
            out_printf(o, "; queued charge %d, key '%s'", q->num, b);
        }
        out_printf(o, "\n");
    }
}

static void cmd_burncard(char *rest, Out *o) {
    char *row = rest ? strtok(rest, " ") : NULL, *table = row ? strtok(NULL, " ") : NULL;
    UObject *gcm = live_gcm();
    if (!gcm) { out_printf(o, "no GameplayCardManager (not in a mission?)\n"); return; }
    char a[160], b[160], c[160];
    if (row && !strcmp(row, "map")) {
        // TMap<FName, FDataTableRowHandle>: sparse array of {FName key, handle (0x20), hash} (0x30)
        int32_t moff = ue_prop_offset(gcm, "CardNameToProductHandles");
        TArray *elems = moff >= 0 ? (TArray *)((char *)gcm + moff) : NULL;
        for (int i = 0; elems && i < elems->num && i < 400; i++) {
            char *e = (char *)elems->data + i * 0x30;
            UObject *dt = *(UObject **)(e + 8);
            out_printf(o, "  %s -> %s %s\n", ue_name(*(FName *)e, a, sizeof a), dt ? ue_obj_name(dt, b, sizeof b) : "null",
                       ue_name(*(FName *)(e + 0x10), c, sizeof c));
        }
        out_printf(o, "%d entries\n", elems ? elems->num : 0);
        return;
    }
    if (row && !strcmp(row, "status")) { burn_status(gcm, o); return; }
    if (row && !strcmp(row, "charge")) {
        UFunction *f = ue_find_function(U_CLASS(gcm), "OnSafeRoomStateChanged");
        int32_t oo = f ? param_off(f, "OldPartySafeRoomState") : -1, on = f ? param_off(f, "NewPartySafeRoomState") : -1;
        if (oo < 0 || on < 0 || !ue_is_listen_server(ue_world())) { out_printf(o, "host only (OnSafeRoomStateChanged)\n"); return; }
        uint8_t p[16] = {0};
        p[oo] = 1 /*InStartingRoom*/; p[on] = 0 /*NotInRoom*/;
        LOG("testing: GCM OnSafeRoomStateChanged(InStartingRoom, NotInRoom)");
        ue_process_event(gcm, f, p);
        out_printf(o, "charge: OnSafeRoomStateChanged(InStartingRoom -> NotInRoom)\n");
        return;
    }
    UObject *pc = ue_local_pc(), *ps = local_ps();
    UFunction *f = pc ? ue_find_function(U_CLASS(pc), "ServerPlayBurnCard") : NULL;
    int32_t off = f ? param_off(f, "ProductRowHandle") : -1;
    if (!ps || off < 0) { out_printf(o, "no local player / ServerPlayBurnCard\n"); return; }
    static uint8_t p[64];
    memset(p, 0, sizeof p);
    RowHandle *h = (RowHandle *)(p + off);
    if (table) {   // explicit handle
        static wchar_t wrow[128];
        int k = 0;
        for (; row[k] && k < 127; k++) wrow[k] = (wchar_t)row[k];
        wrow[k] = 0;
        if (!(h->table = find_named(table))) { out_printf(o, "no table %s\n", table); return; }
        h->row = make_name(wrow);
    } else {
        TArray cards;
        int n = player_burn_cards(gcm, ps, &cards);
        if (n < 0) { out_printf(o, "GetPlayerBurnCards unavailable\n"); return; }
        int pick = -1;
        for (int i = 0; i < n; i++) {
            RowHandle *x = &((RowHandle *)cards.data)[i];
            ue_name(x->row, a, sizeof a);
            if (!row || !strcmp(row, "list"))
                out_printf(o, "  %s %s\n", x->table ? ue_obj_name(x->table, b, sizeof b) : "null", a);
            if (pick < 0 && row && strcmp(row, "list") && !_stricmp(a, row)) pick = i;
        }
        if (!row && n > 0) pick = 0;
        if (pick >= 0) { h->table = ((RowHandle *)cards.data)[pick].table; h->row = ((RowHandle *)cards.data)[pick].row; }
        free_handles(&cards);
        if (row && !strcmp(row, "list")) { out_printf(o, "%d playable burn card(s)\n", n); return; }
        if (pick < 0) { out_printf(o, "%s is not among this player's %d playable burn cards (burncard list)\n", row ? row : "-", n); return; }
    }
    ue_obj_name(h->table, a, sizeof a);
    ue_name(h->row, b, sizeof b);
    // the server's own checks that do not depend on the profile, evaluated here on replicated data
    out_printf(o, "precheck: IsBurnCard %d, CanBurnCardBePlayedThisMap %d, HasPlayedBurnCardThisMap %d\n",
               gcm_bool(gcm, "IsBurnCard", "CardRowHandle", h, sizeof *h),
               gcm_bool(gcm, "CanBurnCardBePlayedThisMap", "GameplayCardRowHandle", h, sizeof *h),
               gcm_bool(gcm, "HasPlayedBurnCardThisMap", "GobiPlayerState", &ps, sizeof ps));
    LOG("testing: ServerPlayBurnCard %s (%s)", b, a);
    ue_process_event(pc, f, p);
    out_printf(o, "burncard: ServerPlayBurnCard(%s %s); check with `burncard status`\n", a, b);
}

// callp <Class> <Func> [args...]: call a function on the live instance in the current world, parameters in declaration
// order (bool/int/byte/enum/float/string, objects: pc | ps | gs | gcm | world | null); the return value is skipped.
// Bytes of the parms block are printed back. The first version took the first object of the class, which could be a
// component template or a leftover from the previous map.
static UObject *object_arg(const char *a) {
    UObject *w = ue_world();
    if (!strcmp(a, "pc")) return ue_local_pc();
    if (!strcmp(a, "ps")) return local_ps();
    if (!strcmp(a, "gs")) return w ? ue_get_ptr(w, "GameState") : NULL;
    if (!strcmp(a, "gcm")) return live_gcm();
    if (!strcmp(a, "world")) return w;
    return NULL;
}

static void cmd_callp(char *rest, Out *o) {
    char *cls = rest ? strtok(rest, " ") : NULL, *fn = cls ? strtok(NULL, " ") : NULL;
    if (!fn) { out_printf(o, "usage: callp <Class> <Func> [args...]\n"); return; }
    UObject *t = !strcmp(cls, "GameplayCardManager") ? live_gcm() : find_in_world(ue_find_class(cls));
    UFunction *f = t ? ue_find_function(U_CLASS(t), fn) : NULL;
    if (!f) { out_printf(o, "no %s\n", t ? "function" : "instance"); return; }
    static uint8_t p[1024];
    static wchar_t sbuf[4][256];
    int ns = 0;
    memset(p, 0, sizeof p);
    if (UFN_PARMSSIZE(f) > sizeof p) { out_printf(o, "parms too big\n"); return; }
    char tn[64];
    for (FField *prop = US_CHILDPROPS(f); prop; prop = FF_NEXT(prop)) {
        if (!(FP_FLAGS(prop) & 0x80) || (FP_FLAGS(prop) & 0x400)) continue;   // CPF_Parm, not CPF_ReturnParm
        char *a = strtok(NULL, " ");
        if (!a) break;
        ue_name(*(FName *)FF_CLASS(prop), tn, sizeof tn);
        uint8_t *d = p + FP_OFFSET(prop);
        if (!strcmp(tn, "FloatProperty")) *(float *)d = (float)atof(a);
        else if (!strcmp(tn, "StrProperty") && ns < 4) fstring_set((FString *)d, a, sbuf[ns++], 256);
        else if (!strcmp(tn, "ObjectProperty")) *(UObject **)d = object_arg(a);
        else if (FP_ELSIZE(prop) == 4) *(int32_t *)d = atoi(a);
        else if (FP_ELSIZE(prop) == 1) *d = (uint8_t)atoi(a);
        else { out_printf(o, "unsupported param type %s\n", tn); return; }
    }
    char nm[128];
    ue_process_event(t, f, p);
    out_printf(o, "called %s.%s on %s; parms:", cls, fn, ue_obj_name(t, nm, sizeof nm));
    for (int i = 0; i < UFN_PARMSSIZE(f) && i < 32; i++) out_printf(o, " %02x", p[i]);
    out_printf(o, "\n");
}

// tp volumes                    list FlashlightVolume actors in this world (index, location, bEnableFlashlight)
// tp <slot> <x> <y> <z>         host: teleport the hero of hero-team slot <slot> (K2_SetActorLocation, teleport)
// tp <slot> volume <n>          ... to the centre of FlashlightVolume <n> (unattended flashlight-volume tests)
static int actor_loc(UObject *a, float v[3]) {
    UObject *root = a ? ue_get_ptr(a, "RootComponent") : NULL;
    int32_t rl = root ? ue_prop_offset(root, "RelativeLocation") : -1;
    if (rl < 0) return -1;
    memcpy(v, (char *)root + rl, 12);
    return 0;
}

static UObject *nth_volume(int want, Out *o) {
    UClass *c = ue_find_class("FlashlightVolume"), *lc = ue_find_class("Level");
    int32_t n = ue_num_objects(), k = 0;
    char b[256];
    for (int32_t i = 0; c && lc && i < n; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & 0x10) || !ue_is_a(x, c)) continue;
        if (!U_OUTER(x) || !ue_is_a(U_OUTER(x), lc)) continue;   // placed actors (volumes are in streamed sublevels)
        if (o) {
            float v[3] = {0}; actor_loc(x, v);
            int32_t en = ue_prop_offset(x, "bEnableFlashlight");
            out_printf(o, "[%d] %s at=(%.0f,%.0f,%.0f) bEnableFlashlight=%d\n", k, ue_obj_name(x, b, sizeof b), v[0], v[1], v[2],
                       en >= 0 ? *((uint8_t *)x + en) : -1);
        }
        if (k++ == want) return x;
    }
    return NULL;
}

static void cmd_tp(char *rest, Out *o) {
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL, *c = b ? strtok(NULL, " ") : NULL;
    char *d = c ? strtok(NULL, " ") : NULL;
    if (!a || !strcmp(a, "volumes")) { nth_volume(-1, o); return; }
    float v[3];
    if (b && !strcmp(b, "volume") && c) {
        UObject *vol = nth_volume(atoi(c), NULL);
        if (!vol || actor_loc(vol, v)) { out_printf(o, "no volume %s\n", c); return; }
    } else if (b && c && d) { v[0] = (float)atof(b); v[1] = (float)atof(c); v[2] = (float)atof(d); }
    else { out_printf(o, "usage: tp volumes | tp <slot> <x> <y> <z> | tp <slot> volume <n>\n"); return; }
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    UObject *psm = gs ? ue_get_ptr(gs, "PlayerSlotManager") : NULL;
    TArray *teams = psm ? (TArray *)((char *)psm + ue_prop_offset(psm, "TeamSlots")) : NULL;
    TArray *slots = teams && teams->num ? (TArray *)((uint8_t *)teams->data + 8) : NULL;
    int si = atoi(a);
    UObject *slot = slots && si >= 0 && si < slots->num ? ((UObject **)slots->data)[si] : NULL;
    UObject *pawn = slot ? ue_get_ptr(slot, "AssignedPawn") : NULL;
    UFunction *f = pawn ? ue_find_function(U_CLASS(pawn), "K2_SetActorLocation") : NULL;
    if (!f) { out_printf(o, "no pawn in slot %d\n", si); return; }
    static uint8_t p[1024];
    memset(p, 0, sizeof p);
    int32_t ol = param_off(f, "NewLocation"), ot = param_off(f, "bTeleport");
    if (ol < 0 || UFN_PARMSSIZE(f) > sizeof p) { out_printf(o, "bad K2_SetActorLocation\n"); return; }
    memcpy(p + ol, v, 12);
    if (ot >= 0) p[ot] = 1;
    ue_process_event(pawn, f, p);
    LOG("testing: tp slot %d to (%.0f,%.0f,%.0f)", si, v[0], v[1], v[2]);
    out_printf(o, "tp: slot %d -> (%.0f,%.0f,%.0f)\n", si, v[0], v[1], v[2]);
}

// takeover <slot>: host: the human who owns hero slot <slot> but spectates its bot ("Press SPACE to take over")
// takes the bot over: GobiPlayerController::ServerTakeOverBot(AssignedPawn), run on the server for that player.
static void cmd_takeover(char *rest, Out *o) {
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    UObject *psm = gs ? ue_get_ptr(gs, "PlayerSlotManager") : NULL;
    TArray *teams = psm ? (TArray *)((char *)psm + ue_prop_offset(psm, "TeamSlots")) : NULL;
    TArray *slots = teams && teams->num ? (TArray *)((uint8_t *)teams->data + 8) : NULL;
    int si = rest ? atoi(rest) : -1;
    UObject *slot = slots && si >= 0 && si < slots->num ? ((UObject **)slots->data)[si] : NULL;
    UObject *ps = slot ? ue_get_ptr(slot, "OwningPlayer") : NULL, *pc = ps ? ue_get_ptr(ps, "Owner") : NULL;
    UObject *pawn = slot ? ue_get_ptr(slot, "AssignedPawn") : NULL;
    UFunction *f = pc ? ue_find_function(U_CLASS(pc), "ServerTakeOverBot") : NULL;
    int32_t op = f ? param_off(f, "TargetPawn") : -1;
    if (!pawn || op < 0) { out_printf(o, "slot %d: no owner PlayerController / pawn\n", si); return; }
    uint8_t p[32] = {0};
    *(UObject **)(p + op) = pawn;
    ue_process_event(pc, f, p);
    out_printf(o, "takeover: ServerTakeOverBot for slot %d\n", si);
}

// ---- rewards that Easy never gives (docs/investigations/client-rewards.md §8) ----
// stp <N>|off              host: AMissionGameMode::CountSkullTotemPoints returns N (the per-survivor total the game
//                          otherwise sums from skull totems carried by humans), so the next `endmission 1` runs the
//                          real RewardSurvivorsForSuccess STP award for every human. Hooked on first use.
// items [substr]           host: live ItemPickup actors in this world and their item rows (duffel bag = Duffel)
// giveitem <slot> <pickup> [entry]   host: that pickup's item row into hero slot <slot>'s inventory
// giveitem <slot> row <DataTable> <RowName>   host: an explicit item row (e.g. a weapon from Weapons_DT)
//                          (InventoryComponent::ServerAddItemsOfHandle, the server side of a pickup)
// duffelreward <slot> <ProductsRowGuid> [delta]      host: the per-player duffel-bag reward issuer (0x141BD7610)
//                          directly: consumable -> AdjustConsumableQuantity(delta), else UnlockProduct
#include "MinHook.h"
#define ADDR_COUNT_STP VA(0x141A08FB0ull)   // int32 AMissionGameMode::CountSkullTotemPoints(this)
static const uint8_t SIG_COUNT_STP[] = {0x40,0x53,0x48,0x81,0xec,0x00,0x01,0x00,0x00,0x48,0x8b,0x05,0x20,0xe8,0x9e,0x04,
                                        0x48,0x33,0xc4,0x48,0x89,0x84,0x24,0xd0,0x00,0x00,0x00,0x48,0x8b,0xd9,0xe8,0xbd};
#define ADDR_DUFFEL_ISSUE VA(0x141BD7610ull)  // void (AGobiPlayerState*, const FDataTableRowHandle* product, int32 delta)
static const uint8_t SIG_DUFFEL_ISSUE[] = {0x48,0x89,0x5c,0x24,0x10,0x48,0x89,0x74,0x24,0x18,0x57,0x48,0x83,0xec,0x20,0x48,
                                           0x83,0xb9,0x70,0x07,0x00,0x00,0x00,0x41,0x8b,0xd8,0x48,0x8b,0xf2,0x48,0x8b,0xf9};
typedef int32_t (*CountStpFn)(UObject *gm);
static CountStpFn orig_count_stp;
static int stp_force = -1;
static int32_t count_stp_detour(UObject *gm) {
    int32_t n = orig_count_stp(gm);
    if (stp_force < 0) return n;
    LOG("testing: CountSkullTotemPoints %d -> %d (stp)", n, stp_force);
    return stp_force;
}

static void cmd_stp(char *rest, Out *o) {
    if (!rest || !*rest) { out_printf(o, "stp: %d (usage: stp <N>|off)\n", stp_force); return; }
    if (!orig_count_stp) {
        if (memcmp((void *)ADDR_COUNT_STP, SIG_COUNT_STP, sizeof SIG_COUNT_STP)) { out_printf(o, "stp: signature mismatch\n"); return; }
        if (MH_CreateHook((void *)ADDR_COUNT_STP, (void *)count_stp_detour, (void **)&orig_count_stp) != MH_OK ||
            MH_EnableHook((void *)ADDR_COUNT_STP) != MH_OK) { orig_count_stp = NULL; out_printf(o, "stp: hook failed\n"); return; }
    }
    stp_force = strcmp(rest, "off") ? atoi(rest) : -1;
    LOG("testing: stp %d", stp_force);
    out_printf(o, "stp: CountSkullTotemPoints -> %d\n", stp_force);
}

static UObject *slot_at(int si) {
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    UObject *psm = gs ? ue_get_ptr(gs, "PlayerSlotManager") : NULL;
    TArray *teams = psm ? (TArray *)((char *)psm + ue_prop_offset(psm, "TeamSlots")) : NULL;
    TArray *slots = teams && teams->num ? (TArray *)((uint8_t *)teams->data + 8) : NULL;
    return slots && si >= 0 && si < slots->num ? ((UObject **)slots->data)[si] : NULL;
}

static UObject *nth_pickup(int want, const char *filter, Out *o) {
    UClass *c = ue_find_class("ItemPickup");
    int k = 0;
    char a[160], b[160];
    for (UObject *p = c ? find_live(c, NULL) : NULL; p; p = find_live(c, p)) {   // any level (streamed sublevels too)
        int32_t off = ue_prop_offset(p, "ItemRowsAndQuantities");
        TArray *rows = off >= 0 ? (TArray *)((char *)p + off) : NULL;
        int match = !filter;
        for (int i = 0; rows && i < rows->num; i++)
            if (filter && strstr(ue_name(((RowHandle *)((char *)rows->data + i * 0x48))->row, a, sizeof a), filter)) match = 1;
        if (!match) continue;
        if (o) {
            out_printf(o, "[%d] %s", k, ue_obj_name(p, a, sizeof a));
            for (int i = 0; rows && i < rows->num; i++) {
                RowHandle *h = (RowHandle *)((char *)rows->data + i * 0x48);
                out_printf(o, " {%d: %s %s x%d}", i, h->table ? ue_obj_name(h->table, b, sizeof b) : "null",
                           ue_name(h->row, a, sizeof a), *(int32_t *)((char *)h + 0x20));
            }
            out_printf(o, "\n");
        }
        if (k++ == want) return p;
    }
    return NULL;
}

static void cmd_giveitem(char *rest, Out *o) {
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL, *c = b ? strtok(NULL, " ") : NULL;
    if (!b) { out_printf(o, "usage: giveitem <slot> <pickup#> [entry] | giveitem <slot> row <DataTable> <RowName>\n"); return; }
    UObject *slot = slot_at(atoi(a)), *pawn = slot ? ue_get_ptr(slot, "AssignedPawn") : NULL;
    RowHandle byrow = {0};
    if (!strcmp(b, "row")) {   // an explicit item row, e.g. giveitem 0 row Weapons_DT DF038C6A4ED79AB7FDCF9CAB8D742DC7
        char *rn = c ? strtok(NULL, " ") : NULL;
        static wchar_t wrow[128];
        int k = 0;
        for (; rn && rn[k] && k < 127; k++) wrow[k] = (wchar_t)rn[k];
        wrow[k] = 0;
        if (!c || !rn || !(byrow.table = find_named(c))) { out_printf(o, "no table %s / row\n", c ? c : "-"); return; }
        byrow.row = make_name(wrow);
    }
    UObject *pk = byrow.table ? NULL : nth_pickup(atoi(b), NULL, NULL);
    int32_t off = pk ? ue_prop_offset(pk, "ItemRowsAndQuantities") : -1;
    TArray *rows = off >= 0 ? (TArray *)((char *)pk + off) : NULL;
    int e = c && !byrow.table ? atoi(c) : 0;
    if (!pawn || (!byrow.table && (!rows || e < 0 || e >= rows->num))) { out_printf(o, "no pawn in slot %s / no pickup %s entry %d\n", a, b, e); return; }
    UFunction *gi = ue_find_function(U_CLASS(pawn), "GetInventoryComponent");
    uint8_t p0[16] = {0};
    if (gi) ue_process_event(pawn, gi, p0);
    UObject *inv = *(UObject **)p0;
    UFunction *f = inv ? ue_find_function(U_CLASS(inv), "ServerAddItemsOfHandle") : NULL;
    int32_t oh = f ? param_off(f, "ItemHandle") : -1, on = f ? param_off(f, "NumItems") : -1;
    if (oh < 0 || on < 0 || UFN_PARMSSIZE(f) > 64) { out_printf(o, "no inventory / ServerAddItemsOfHandle\n"); return; }
    uint8_t p[64] = {0};
    RowHandle *src = byrow.table ? &byrow : (RowHandle *)((char *)rows->data + e * 0x48), *h = (RowHandle *)(p + oh);
    h->table = src->table; h->row = src->row;   // display string left empty
    *(int32_t *)(p + on) = 1;
    char nm[160];
    ue_name(h->row, nm, sizeof nm);
    LOG("testing: giveitem %s to slot %s", nm, a);
    ue_process_event(inv, f, p);
    out_printf(o, "giveitem: ServerAddItemsOfHandle(%s, 1) on slot %s's inventory\n", nm, a);
}

static void cmd_duffelreward(char *rest, Out *o) {
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL, *c = b ? strtok(NULL, " ") : NULL;
    if (!b) { out_printf(o, "usage: duffelreward <slot> <ProductsRowGuid> [delta]\n"); return; }
    if (memcmp((void *)ADDR_DUFFEL_ISSUE, SIG_DUFFEL_ISSUE, sizeof SIG_DUFFEL_ISSUE)) { out_printf(o, "signature mismatch\n"); return; }
    UObject *slot = slot_at(atoi(a)), *ps = slot ? ue_get_ptr(slot, "OwningPlayer") : NULL;
    UObject *dt = find_named("Products_DT");
    if (!ps || !dt) { out_printf(o, "no owner in slot %s / no Products_DT\n", a); return; }
    static wchar_t wrow[64];
    int k = 0;
    for (; b[k] && k < 63; k++) wrow[k] = (wchar_t)b[k];
    wrow[k] = 0;
    static RowHandle h;
    memset(&h, 0, sizeof h);
    h.table = dt; h.row = make_name(wrow);
    LOG("testing: duffelreward slot %s product %s delta %d", a, b, c ? atoi(c) : 1);
    ((void (*)(UObject *, const RowHandle *, int32_t))ADDR_DUFFEL_ISSUE)(ps, &h, c ? atoi(c) : 1);
    out_printf(o, "duffelreward: issued %s (delta %d) to slot %s\n", b, c ? atoi(c) : 1, a);
}

// ---- fnprobe: count what a native function returns (dev investigations, #27) ----
// fnprobe <va> [label]   hook the function at static VA <va> with a pass-through detour (up to 8 integer/pointer
//                        arguments; float arguments in xmm registers are not supported), count calls and the low
//                        byte of the return value, and keep the last call's arguments and return
// fnprobe                the counters; `fnprobe reset` zeroes them; `fnprobe off` disables every probe (the same
//                        <va> again re-enables it)
#include <windows.h>
#include "MinHook.h"
typedef uint64_t (*Fn8)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
#define FNP_N 8
static struct { void *at; Fn8 orig; char label[24]; unsigned calls, ret1, ret0; uint64_t last_a[8], last_r; } fnp[FNP_N];
static int n_fnp;
static uint64_t fnp_run(int i, uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e, uint64_t f, uint64_t g, uint64_t h) {
    uint64_t r = fnp[i].orig(a, b, c, d, e, f, g, h);
    fnp[i].calls++;
    if (r & 0xff) fnp[i].ret1++; else fnp[i].ret0++;
    uint64_t v[8] = {a, b, c, d, e, f, g, h};
    memcpy(fnp[i].last_a, v, sizeof v);
    fnp[i].last_r = r;
    return r;
}
#define FNP_DET(i) static uint64_t fnp_det##i(uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e, uint64_t f, \
    uint64_t g, uint64_t h) { return fnp_run(i, a, b, c, d, e, f, g, h); }
FNP_DET(0) FNP_DET(1) FNP_DET(2) FNP_DET(3) FNP_DET(4) FNP_DET(5) FNP_DET(6) FNP_DET(7)
static void *const FNP_DETS[FNP_N] = {fnp_det0, fnp_det1, fnp_det2, fnp_det3, fnp_det4, fnp_det5, fnp_det6, fnp_det7};
static void cmd_fnprobe(char *rest, Out *o) {
    char *a = rest ? strtok(rest, " ") : NULL, *lab = a ? strtok(NULL, " ") : NULL;
    if (a && !strcmp(a, "reset")) { for (int i = 0; i < n_fnp; i++) fnp[i].calls = fnp[i].ret0 = fnp[i].ret1 = 0; }
    else if (a && !strcmp(a, "off")) { for (int i = 0; i < n_fnp; i++) MH_DisableHook(fnp[i].at); }
    else if (a) {
        void *at = (void *)(uintptr_t)VA(strtoull(a, NULL, 16));
        int k = n_fnp, again = 0;
        for (int i = 0; i < n_fnp; i++) if (fnp[i].at == at) { MH_EnableHook(at); again = 1; }   // after `fnprobe off`
        if (!again && n_fnp >= FNP_N) { out_printf(o, "fnprobe: all %d slots used\n", FNP_N); return; }
        if (again) goto show;
        memset(&fnp[k], 0, sizeof fnp[k]);
        fnp[k].at = at;
        snprintf(fnp[k].label, sizeof fnp[k].label, "%s", lab ? lab : a);
        MH_STATUS st = MH_CreateHook(at, FNP_DETS[k], (void **)&fnp[k].orig);
        if (st != MH_OK || MH_EnableHook(at) != MH_OK) { out_printf(o, "fnprobe: hook failed (%d)\n", st); return; }
        n_fnp++;
    }
show:
    for (int i = 0; i < n_fnp; i++)
        out_printf(o, "%-12s 0x%llx calls %u ret!=0 %u ret0 %u last r 0x%llx args %llx %llx %llx %llx %llx %llx\n", fnp[i].label,
                   (unsigned long long)((uintptr_t)fnp[i].at - g_base_delta), fnp[i].calls, fnp[i].ret1, fnp[i].ret0,
                   (unsigned long long)fnp[i].last_r, (unsigned long long)fnp[i].last_a[0], (unsigned long long)fnp[i].last_a[1],
                   (unsigned long long)fnp[i].last_a[2], (unsigned long long)fnp[i].last_a[3], (unsigned long long)fnp[i].last_a[4],
                   (unsigned long long)fnp[i].last_a[5]);
    out_printf(o, "%d probe(s)\n", n_fnp);
}

// ---- faces (model mods #23: custom heads on the survivors' face bones; docs/investigations/mesh-mods.md §12) ----
// face                          list heroes (index, mesh)
// face <hero#> [bone...]        each face bone's current rotation/offset from the mesh's reference pose (parent space)
//                               and its reference-pose position (the bind pose the mesh brought: moved face bones)
// face say <hero#> <Response> [test]   make the hero speak a response group (Ping_Affirmative, Ping_Ammo ...):
//                               DialogueComponent SayLine (or TestLine); the line drives lip-sync on every machine
// face comm [action] | face ping     the local player's comm wheel action (Thank=10) / a ping: the survivor speaks
// face look <hero#> [dist] [dz]  stand dist cm (45) in front of that hero's face and look at it (screenshots; host)
// face walk <hero#> <x> <y> <z>  host: a bot runs there (AIBlueprintHelperLibrary.SimpleMoveToLocation on its controller;
//                               its behaviour tree takes over again later): hair/cloth tests while moving
static UObject *face_hero(int idx) {
    static UClass *hc;
    if (!hc) hc = ue_find_class("HeroCharacter");
    UObject *w = ue_world();
    int32_t n = ue_num_objects(); int k = 0;
    for (int32_t i = 0; hc && w && i < n; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & 0x30) || !ue_is_a(x, hc)) continue;
        UObject *lvl = U_OUTER(x);
        if (!lvl || U_OUTER(lvl) != w) continue;
        if (k++ == idx) return x;
    }
    return NULL;
}

static UObject *face_component(UObject *actor, const char *cls) {
    UClass *c = ue_find_class(cls);
    int32_t n = ue_num_objects();
    for (int32_t i = 0; c && i < n; i++) {
        UObject *x = ue_object_at(i);
        if (x && !(U_FLAGS(x) & 0x30) && U_OUTER(x) == actor && ue_is_a(x, c)) return x;
    }
    return NULL;
}

static FName face_name(const char *s) {
    wchar_t w[128]; int k = 0;
    for (; s[k] && k < 127; k++) w[k] = (wchar_t)(unsigned char)s[k];
    w[k] = 0;
    return make_name(w);
}

static void cmd_face(char *rest, Out *o) {
    static const char *DEF[] = {"jaw", "lip_lower", "lip_upper", "lip_corner_upper_l", "eyelid_upper_l", "eyelid_lower_l",
                                "eyeball_l", "eyebrow_l", NULL};
    char *a = rest ? strtok(rest, " ") : NULL;
    char nm[160], b[300];
    if (!a) {
        for (int i = 0; ; i++) {
            UObject *h = face_hero(i);
            if (!h) break;
            UObject *m = ue_get_ptr(h, "Mesh"), *sk = m ? ue_get_ptr(m, "SkeletalMesh") : NULL;
            out_printf(o, "#%d %s mesh=%s\n", i, ue_obj_name(h, nm, sizeof nm), sk ? ue_full_path(sk, b, sizeof b) : "-");
        }
        return;
    }
    if (!strcmp(a, "say")) {
        char *hs = strtok(NULL, " "), *resp = strtok(NULL, " "), *mode = strtok(NULL, " ");
        UObject *h = hs ? face_hero(atoi(hs)) : NULL, *dc = h ? face_component(h, "DialogueComponent") : NULL;
        UFunction *f = dc ? ue_find_function(U_CLASS(dc), mode && !strcmp(mode, "test") ? "TestLine" : "SayLine") : NULL;
        if (!resp || !f) { out_printf(o, "usage: face say <hero#> <Response> [test] (%s)\n", h ? "no DialogueComponent" : "no hero"); return; }
        static uint8_t p[128];
        memset(p, 0, sizeof p);
        int32_t po = param_off(f, "Params");
        uint8_t *sp = p + (po < 0 ? 0 : po);            // SpokenLineParams (0x24)
        *(FName *)sp = face_name(resp);
        sp[0x0C] = 1;                                    // bShowSubtitles
        *(float *)(sp + 0x18) = 2000.0f;                 // AttenuationRadius
        sp[0x20] = 1;                                    // bShouldReplicate
        ue_process_event(dc, f, p);
        out_printf(o, "face: %s.%s(%s) on %s\n", ue_obj_name(dc, nm, sizeof nm), mode ? "TestLine" : "SayLine", resp,
                   ue_obj_name(h, b, sizeof b));
        return;
    }
    if (!strcmp(a, "comm") || !strcmp(a, "ping")) {   // face comm <action 1-10> | face ping: the local player's comm
        char *as = strtok(NULL, " ");                    // wheel (Approve=2, Thank=10 ...) or ping (the survivor says it)
        UObject *pc = ue_local_pc(), *pw = pc ? ue_get_ptr(pc, "PlayerWaypoints") : NULL, *me = pc ? ue_get_ptr(pc, "Pawn") : NULL;
        if (!pw || !me) { out_printf(o, "no PlayerWaypoints / pawn\n"); return; }
        static uint8_t p[256];
        memset(p, 0, sizeof p);
        UFunction *f;
        if (!strcmp(a, "ping")) {
            f = ue_find_function(U_CLASS(pw), "SpawnPing");
            ue_process_event(pw, f, p);
            out_printf(o, "face: SpawnPing -> %d\n", p[param_off(f, "ReturnValue")]);
            return;
        }
        f = ue_find_function(U_CLASS(pw), "ServerSpawnCommWheelPing");
        UFunction *gl = ue_find_function(U_CLASS(me), "K2_GetActorLocation");
        static uint8_t q[64]; memset(q, 0, sizeof q); ue_process_event(me, gl, q);
        *(UObject **)(p + param_off(f, "OwnerController")) = pc;
        float *t = (float *)(p + param_off(f, "Transform"));
        t[3] = 1.f; memcpy(t + 4, q + param_off(gl, "ReturnValue"), 12); t[8] = t[9] = t[10] = 1.f;
        p[param_off(f, "Action")] = (uint8_t)(as ? atoi(as) : 10);
        ue_process_event(pw, f, p);
        out_printf(o, "face: ServerSpawnCommWheelPing(action %d)\n", as ? atoi(as) : 10);
        return;
    }
    if (!strcmp(a, "walk")) {
        char *hs = strtok(NULL, " "), *xs = strtok(NULL, " "), *ys = strtok(NULL, " "), *zs = strtok(NULL, " ");
        UObject *h = hs ? face_hero(atoi(hs)) : NULL, *ctl = h ? ue_get_ptr(h, "Controller") : NULL;
        UClass *lc = ue_find_class("AIBlueprintHelperLibrary");
        UObject *cdo = lc ? UC_CDO(lc) : NULL;
        UFunction *f = cdo ? ue_find_function(lc, "SimpleMoveToLocation") : NULL;
        if (!zs || !ctl || !f) { out_printf(o, "usage: face walk <hero#> <x> <y> <z> (%s)\n", !h ? "no hero" : !ctl ? "no controller" : "no function"); return; }
        static uint8_t p[64];
        memset(p, 0, sizeof p);
        *(UObject **)(p + param_off(f, "Controller")) = ctl;
        float g[3] = {(float)atof(xs), (float)atof(ys), (float)atof(zs)};
        memcpy(p + param_off(f, "Goal"), g, 12);
        ue_process_event(cdo, f, p);
        out_printf(o, "face walk: %s -> (%.0f %.0f %.0f)\n", ue_obj_name(ctl, nm, sizeof nm), g[0], g[1], g[2]);
        return;
    }
    if (!strcmp(a, "look")) {        // face look <hero#> [dist] [dz]: stand in front of that hero's face, looking at it
        char *hs = strtok(NULL, " "), *ds = strtok(NULL, " "), *zs = strtok(NULL, " ");
        UObject *h = hs ? face_hero(atoi(hs)) : NULL, *m = h ? ue_get_ptr(h, "Mesh") : NULL;
        UObject *pc = ue_local_pc(), *me = pc ? ue_get_ptr(pc, "Pawn") : NULL;
        if (!m || !me || me == h) { out_printf(o, "usage: face look <hero#> [dist] [dz] (not your own hero)\n"); return; }
        static uint8_t p[512];
        float hd[3], fw[3], el[3];
        UFunction *f = ue_find_function(U_CLASS(m), "GetSocketLocation");
        memset(p, 0, sizeof p); *(FName *)(p + param_off(f, "InSocketName")) = face_name("head");
        ue_process_event(m, f, p); memcpy(hd, p + param_off(f, "ReturnValue"), 12);
        f = ue_find_function(U_CLASS(h), "GetActorForwardVector");
        memset(p, 0, sizeof p); ue_process_event(h, f, p); memcpy(fw, p + param_off(f, "ReturnValue"), 12);
        float d = ds ? (float)atof(ds) : 45.f, dz = zs ? (float)atof(zs) : 3.f;
        hd[2] += dz;
        // eyes stand `d` in front of the face; the pawn's origin is BaseEyeHeight below its eyes
        f = ue_find_function(U_CLASS(me), "GetActorEyesViewPoint");
        float at[3];
        memset(p, 0, sizeof p); ue_process_event(me, f, p); memcpy(el, p + param_off(f, "OutLocation"), 12);
        UFunction *gl = ue_find_function(U_CLASS(me), "K2_GetActorLocation");
        memset(p, 0, sizeof p); ue_process_event(me, gl, p); memcpy(at, p + param_off(gl, "ReturnValue"), 12);
        float eye_h = el[2] - at[2];
        float np[3] = {hd[0] + fw[0] * d, hd[1] + fw[1] * d, hd[2] - eye_h};
        f = ue_find_function(U_CLASS(me), "K2_SetActorLocation");
        memset(p, 0, sizeof p); memcpy(p + param_off(f, "NewLocation"), np, 12); p[param_off(f, "bTeleport")] = 1;
        ue_process_event(me, f, p);
        float v[3] = {hd[0] - np[0], hd[1] - np[1], hd[2] - (np[2] + eye_h)}, n = sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
        float r[3] = {n > 0 ? asinf(v[2] / n) * 57.29578f : 0.f, atan2f(v[1], v[0]) * 57.29578f, 0.f};
        f = ue_find_function(U_CLASS(pc), "SetControlRotation");
        memset(p, 0, sizeof p); memcpy(p + param_off(f, "NewRotation"), r, 12); ue_process_event(pc, f, p);
        out_printf(o, "face look: %.0f cm in front of %s's head (%.0f %.0f %.0f), pitch %.1f yaw %.1f\n", d,
                   ue_obj_name(h, nm, sizeof nm), hd[0], hd[1], hd[2], r[0], r[1]);
        return;
    }
    UObject *h = face_hero(atoi(a)), *m = h ? ue_get_ptr(h, "Mesh") : NULL;
    if (!m) { out_printf(o, "no hero %s\n", a); return; }
    UFunction *fd = ue_find_function(U_CLASS(m), "GetDeltaTransformFromRefPose");
    UFunction *fi = ue_find_function(U_CLASS(m), "GetBoneIndex");
    UFunction *fr = ue_find_function(U_CLASS(m), "GetRefPosePosition");
    if (!fd || !fi || !fr) { out_printf(o, "mesh functions missing\n"); return; }
    const char *list[32]; int nl = 0;
    for (char *t = strtok(NULL, " "); t && nl < 31; t = strtok(NULL, " ")) list[nl++] = t;
    if (!nl) for (; DEF[nl]; nl++) list[nl] = DEF[nl];
    UObject *sk = ue_get_ptr(m, "SkeletalMesh");
    out_printf(o, "%s mesh=%s\n", ue_obj_name(h, nm, sizeof nm), sk ? ue_obj_name(sk, b, sizeof b) : "-");
    for (int k = 0; k < nl; k++) {
        static uint8_t p[256];
        memset(p, 0, sizeof p);
        FName bn = face_name(list[k]);
        *(FName *)(p + param_off(fi, "BoneName")) = bn;
        ue_process_event(m, fi, p);
        int32_t bi = *(int32_t *)(p + param_off(fi, "ReturnValue"));
        if (bi < 0) { out_printf(o, "  %-20s (no bone)\n", list[k]); continue; }
        memset(p, 0, sizeof p);
        *(int32_t *)(p + param_off(fr, "BoneIndex")) = bi;
        ue_process_event(m, fr, p);
        float *ref = (float *)(p + param_off(fr, "ReturnValue"));
        float rx = ref[0], ry = ref[1], rz = ref[2];
        memset(p, 0, sizeof p);
        *(FName *)(p + param_off(fd, "BoneName")) = bn;
        ue_process_event(m, fd, p);
        float *t = (float *)(p + param_off(fd, "ReturnValue"));    // FTransform: quat xyzw, translation (+16)
        float w = t[3] < 0 ? -t[3] : t[3];
        if (w > 1.0f) w = 1.0f;
        double ang = 2.0 * acos(w) * 57.29578;
        double tl = sqrt((double)t[4] * t[4] + (double)t[5] * t[5] + (double)t[6] * t[6]);
        out_printf(o, "  %-20s rot=%6.2fdeg off=%5.2fcm  q=(%.3f %.3f %.3f %.3f)  ref=(%.2f %.2f %.2f)\n", list[k], ang, tl,
                   t[0], t[1], t[2], t[3], rx, ry, rz);
    }
}

int testing_cmd(const char *verb, char *rest, Out *o) {
    if (!strcmp(verb, "face")) { cmd_face(rest, o); return 1; }
    if (!strcmp(verb, "fnprobe")) { cmd_fnprobe(rest, o); return 1; }
    if (!strcmp(verb, "stp")) { cmd_stp(rest, o); return 1; }
    if (!strcmp(verb, "items")) { nth_pickup(-1, rest && *rest ? rest : NULL, o); return 1; }
    if (!strcmp(verb, "giveitem")) { cmd_giveitem(rest, o); return 1; }
    if (!strcmp(verb, "duffelreward")) { cmd_duffelreward(rest, o); return 1; }
    if (!strcmp(verb, "tp")) { cmd_tp(rest, o); return 1; }
    if (!strcmp(verb, "takeover")) { cmd_takeover(rest, o); return 1; }
    (void)rest;
    if (!strcmp(verb, "signin")) { signin_step(o); return 1; }
    if (!strcmp(verb, "mission")) { cmd_mission(rest, o); return 1; }
    if (!strcmp(verb, "ready")) { admin_ready(rest, o); return 1; }
    if (!strcmp(verb, "burncard")) { cmd_burncard(rest, o); return 1; }
    if (!strcmp(verb, "callp")) { cmd_callp(rest, o); return 1; }
    if (!strcmp(verb, "endmission")) { cmd_endmission(rest, o); return 1; }
    return 0;
}
#endif  // !B4B_RELEASE
