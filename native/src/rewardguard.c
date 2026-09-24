// Client: check every profile command the host sends before it touches this player's offline save.
// See docs/investigations/client-rewards.md §8.
//
// rewards.c (on the host) forwards remote players' rewards with the profile component's ClientExecute*Command RPCs.
// On the client their handlers run ExecuteCommand(cmd, bPersist) on the local controller, i.e. straight into the
// client's own PlayerProfileSettings. A malicious or buggy host could send anything through them (a million supply
// points, -500 of every burn card). So on a network client we hook the five RPC handlers and apply a command only if
// it looks like a real mission reward:
//   AdjustSupplyPoints / AdjustSkullTotemPoints   delta 1..1000, at most 2000 per map (mission)
//   AdjustConsumableQuantity -1                   only for a burn card this client itself played on this map (seen
//                                                 leaving through ServerPlayBurnCard), once per played card
//   AdjustConsumableQuantity +N                   duffel-bag reward: 1..5, at most 10 per map, and the product must
//                                                 be a duffel-bag product (below)
//   UnlockProduct                                 duffel-bag product only, at most 10 per map
//   SetSecureLeaderboardMetadata                  never (b4bcoop hosts don't send it)
// "Duffel-bag product": a row of the game's own products table (the profile component's ProductsTable, checked by
// object identity) whose ProductRow.DuffelBagTags is not empty, i.e. one the duffel-bag reward roll can pick.
// Rejected commands are logged ("rewardguard: REJECTED ...") and the player gets one chat line per map.
// The host's own rewards never pass through here (they are applied locally, not via these RPCs), and on a host or in
// a standalone game the handlers run unchanged.
//
// Bounds, from the game's own reward table (MissionDifficulties, DifficultyRow; `rewardguard difficulty`, see
// client-rewards.md §8): a mission's SP reward is CoreSp(map, difficulty) * (1 + bonuses). The largest CoreSp is 195
// (VeryHard), and every bonus at its maximum (objective 0.5, glyphs 0.15 each, group 0.1 per survivor, consecutive
// maps <= 0.3, party, quick play 0.25) stays under x4, so one reward is < 800 SP. Failure pays 0.2 of that. Limits:
// 1000 per command, 2000 per map (success after retried failures). Skull totem points (5 per carried totem, every
// survivor gets the team's total) use the same limits.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define MAX_PER_CMD        1000   // supply points / skull totem points per command
#define MAX_PER_MAP        2000   // ... per map (one mission's success or failure reward, plus retries)
#define CONS_GAIN_PER_CMD  5      // consumables (burn cards) gained from one duffel-bag reward
#define CONS_GAIN_PER_MAP  10
#define UNLOCKS_PER_MAP    10

// Client RPC handlers on UGobiPlayerProfileComponent: void Impl(PPC* this, const FXxxCommand& cmd). Each logs
// "[CLIENT RPC] ..." and calls ExecuteCommand(this, cmd, true). PPC vtable 0x1454E66A8 +0x410/+0x420/.../+0x450.
enum { R_SP, R_STP, R_CONS, R_UNLOCK, R_SLB, NR };
static const struct { uint64_t va; const char *name; uint8_t sig[32]; } RPC[NR] = {
    {0x141BC5230, "AdjustSupplyPoints",
     {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x57,0x48,0x83,0xec,0x50,0x48,0x8b,0xf2,0x48,0x8b,0xd9,0xe8,0xc6,0x50,0x56,0x02,0x48,0x8b,0xf8,0x48,0x85,0xc0}},
    {0x141BC5330, "AdjustSkullTotemPoints",
     {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x57,0x48,0x83,0xec,0x50,0x48,0x8b,0xf2,0x48,0x8b,0xd9,0xe8,0xc6,0x4f,0x56,0x02,0x48,0x8b,0xf8,0x48,0x85,0xc0}},
    {0x141BC5430, "AdjustConsumableQuantity",
     {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x6c,0x24,0x10,0x57,0x48,0x83,0xec,0x60,0x48,0x8b,0xea,0x48,0x8b,0xf9,0xe8,0xc6,0x4e,0x56,0x02,0x48,0x8b,0xd8,0x48,0x85,0xc0}},
    {0x141BC5570, "UnlockProduct",
     {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x6c,0x24,0x10,0x57,0x48,0x83,0xec,0x60,0x48,0x8b,0xea,0x48,0x8b,0xf9,0xe8,0x86,0x4d,0x56,0x02,0x48,0x8b,0xd8,0x48,0x85,0xc0}},
    {0x141BC56B0, "SetSecureLeaderboardMetadata",
     {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x57,0x48,0x83,0xec,0x50,0x48,0x8b,0xf2,0x48,0x8b,0xd9,0xe8,0x46,0x4c,0x56,0x02,0x48,0x8b,0xf8,0x48,0x85,0xc0}},
};
// bool AActor::CallRemoteFunction(AActor*, UFunction*, void* Parms, FOutParmRec*, FFrame*) (UObject vtable +0x220):
// every RPC an actor sends to the other side, from C++ (ProcessEvent) and Blueprint alike. We watch it for the local
// player's ServerPlayBurnCard (the card UI calls it from Blueprint, so no C++ stub exists to hook).
#define ADDR_ACTOR_CRF VA(0x143A013B0ull)
static const uint8_t SIG_CRF[] = {0x40,0x55,0x56,0x41,0x54,0x41,0x55,0x41,0x56,0x48,0x83,0xec,0x40,0x48,0x8b,0x01,
                                  0x4d,0x8b,0xe1,0x4d,0x8b,0xe8,0x48,0x8b,0xea,0x4c,0x8b,0xf1,0x40,0x32,0xf6,0xff};

// Command layouts (SDK: /Script/Gobi.*Command; +0 is the C++ vtable of FPlayerProfileCommand)
#define CMD_DELTA(c)      (*(int32_t *)((char *)(c) + 0x08))   // AdjustSupplyPoints / AdjustSkullTotemPoints
#define CMD_PRODUCT(c)    ((RowHandle *)((char *)(c) + 0x08))  // AdjustConsumableQuantity / UnlockProduct
#define CMD_CONS_DELTA(c) (*(int32_t *)((char *)(c) + 0x28))
// FDataTableRowHandle in this build: {UDataTable*, FName RowName, FString (unreflected display name)} = 0x20
typedef struct { UObject *table; FName row; FString display; } RowHandle;
#define DT_ROWSTRUCT(t)   (*(UObject **)((char *)(t) + 0x30))   // UDataTable::RowStruct (reflected)
#define DT_ROWMAP(t)      ((void *)((char *)(t) + 0x38))        // UDataTable::RowMap, TMap<FName, uint8*> (0x50)
#define PRODUCT_DUFFEL_TAGS(row) ((TArray *)((char *)(row) + 0x70))  // ProductRow.DuffelBagTags.GameplayTags

typedef void (*RpcFn)(UObject *ppc, void *cmd);
typedef uint8_t (*CrfFn)(UObject *self, UFunction *fn, void *parms, void *out, void *stack);
static RpcFn orig_rpc[NR];
static CrfFn orig_crf;
static UFunction *fn_playburn;
static int32_t off_playburn = -1;

// ---- per-map state (reset when the world changes) ----
#define MAX_PLAYED 8
static UObject *cur_world;
static int sp_total, stp_total, cons_gain, n_unlocks, told;
static struct { FName card, product; int charged; } played[MAX_PLAYED];
static int n_played;
static int n_accepted, n_rejected;

static void sync_world(void) {
    UObject *w = ue_world();
    if (w == cur_world) return;
    cur_world = w;
    sp_total = stp_total = cons_gain = n_unlocks = told = n_played = 0;
}

static int fname_eq(FName a, FName b) { return a.idx == b.idx && a.num == b.num; }

static int is_net_client(void) {
    UObject *w = ue_world(), *nd = w ? ue_get_ptr(w, "NetDriver") : NULL;
    return nd && ue_get_ptr(nd, "ServerConnection");
}

// TSet/TMap storage (0x50): elements TArray +0, allocation bits TBitArray +0x10 (4 inline dwords, heap pointer +0x20,
// NumBits +0x28). NULL for a free slot.
static void *sparse_at(const void *set, int i, int stride) {
    const TArray *a = set;
    if (!a->data || i < 0 || i >= a->num || i >= *(const int32_t *)((const char *)set + 0x28)) return NULL;
    const uint32_t *bits = *(uint32_t *const *)((const char *)set + 0x20);
    if (!bits) bits = (const uint32_t *)((const char *)set + 0x10);
    return (bits[i >> 5] >> (i & 31)) & 1 ? (char *)a->data + (size_t)i * stride : NULL;
}

static uint8_t *dt_row(UObject *table, FName row) {
    TArray *m = DT_ROWMAP(table);
    for (int i = 0; i < m->num; i++) {
        char *e = sparse_at(m, i, 0x18);   // {FName key, uint8* row, hash links}
        if (e && fname_eq(*(FName *)e, row)) return *(uint8_t **)(e + 8);
    }
    return NULL;
}

static UObject *products_table(UObject *ppc) {
    UObject *t = ppc ? ue_get_ptr(ppc, "ProductsTable") : NULL;
    if (!t) { UObject *m = ue_find_first_of("GobiPlayerProfileManager"); t = m ? ue_get_ptr(m, "ProductsTable") : NULL; }
    return t;
}

// 1 if h names a duffel-bag product of the game's products table; else 0 and the reason.
static int duffel_product(UObject *ppc, const RowHandle *h, char *why, size_t n) {
    UObject *table = products_table(ppc);
    char b[128];
    if (!table) { snprintf(why, n, "products table not found"); return 0; }
    if (h->table != table) { snprintf(why, n, "not a row of the products table"); return 0; }
    UObject *rs = DT_ROWSTRUCT(table);
    if (!rs || strcmp(ue_obj_name(rs, b, sizeof b), "ProductRow")) { snprintf(why, n, "unexpected products table layout"); return 0; }
    uint8_t *row = dt_row(table, h->row);
    if (!row) { snprintf(why, n, "no such product"); return 0; }
    if (PRODUCT_DUFFEL_TAGS(row)->num <= 0) { snprintf(why, n, "not a duffel-bag reward"); return 0; }
    return 1;
}

// ---- burn cards this client played (ServerPlayBurnCard leaving this machine) ----
static UObject *live_gcm(void) {
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    return gs ? ue_get_ptr(gs, "GameplayCardManager") : NULL;
}

// card row -> product row via GameplayCardManager.CardNameToProductHandles (TMap<FName, FDataTableRowHandle>, 0x30
// per element: key +0, handle +8, row name +0x10); 0 if unknown
static int card_product(FName card, FName *product) {
    UObject *gcm = live_gcm();
    int32_t off = gcm ? ue_prop_offset(gcm, "CardNameToProductHandles") : -1;
    if (off < 0) return 0;
    void *m = (char *)gcm + off;
    for (int i = 0; i < ((TArray *)m)->num; i++) {
        char *e = sparse_at(m, i, 0x30);
        if (e && fname_eq(*(FName *)e, card)) { *product = *(FName *)(e + 0x10); return 1; }
    }
    return 0;
}

static void note_play(const RowHandle *card) {
    sync_world();
    char a[128], b[128];
    FName product = card->row;
    int mapped = card_product(card->row, &product);
    if (n_played < MAX_PLAYED) {
        played[n_played].card = card->row;
        played[n_played].product = product;
        played[n_played].charged = 0;
        n_played++;
    }
    LOG("rewardguard: you played burn card %s (product %s%s); the host may charge it once on this map",
        ue_name(card->row, a, sizeof a), ue_name(product, b, sizeof b), mapped ? "" : ", unmapped");
}

static uint8_t crf_detour(UObject *self, UFunction *fn, void *parms, void *out, void *stack) {
    if (fn && fn == fn_playburn && parms && off_playburn >= 0 && self == ue_local_pc())
        note_play((const RowHandle *)((char *)parms + off_playburn));
    return orig_crf(self, fn, parms, out, stack);
}

// ---- validation ----
static void reject(int kind, const char *detail, const char *why) {
    n_rejected++;
    LOG("rewardguard: REJECTED %s %s from the host: %s", RPC[kind].name, detail, why);
    if (!told++) chat_local("Ignored a reward from the host that failed a safety check (%s %s: %s). See the b4bcoop log.",
                            RPC[kind].name, detail, why);
}

// 1 = apply. Game thread (RPCs are dispatched from the net driver's tick).
static int check(int kind, UObject *ppc, void *cmd) {
    char d[160], why[96], a[128];
    sync_world();
    switch (kind) {
    case R_SP: case R_STP: {
        int v = CMD_DELTA(cmd), *total = kind == R_SP ? &sp_total : &stp_total;
        snprintf(d, sizeof d, "%+d", v);
        if (v < 1) { reject(kind, d, "not a positive reward"); return 0; }
        if (v > MAX_PER_CMD) { snprintf(why, sizeof why, "more than %d at once", MAX_PER_CMD); reject(kind, d, why); return 0; }
        if (*total + v > MAX_PER_MAP) {
            snprintf(why, sizeof why, "over %d on this map (already %d)", MAX_PER_MAP, *total);
            reject(kind, d, why);
            return 0;
        }
        *total += v;
        return 1;
    }
    case R_CONS: {
        RowHandle *h = CMD_PRODUCT(cmd);
        int v = CMD_CONS_DELTA(cmd);
        snprintf(d, sizeof d, "%s %+d", ue_name(h->row, a, sizeof a), v);
        if (v == -1) {   // a burn card charge: must be one we played on this map, charged once
            for (int i = 0; i < n_played; i++) {
                if (played[i].charged || (!fname_eq(played[i].product, h->row) && !fname_eq(played[i].card, h->row))) continue;
                played[i].charged = 1;
                return 1;
            }
            reject(kind, d, "you did not play this burn card on this map (or it was already charged)");
            return 0;
        }
        if (v < 1) { reject(kind, d, "only -1 for a burn card you played"); return 0; }
        if (v > CONS_GAIN_PER_CMD || cons_gain + v > CONS_GAIN_PER_MAP) {
            snprintf(why, sizeof why, "more than %d at once or %d per map", CONS_GAIN_PER_CMD, CONS_GAIN_PER_MAP);
            reject(kind, d, why);
            return 0;
        }
        if (!duffel_product(ppc, h, why, sizeof why)) { reject(kind, d, why); return 0; }
        cons_gain += v;
        return 1;
    }
    case R_UNLOCK: {
        RowHandle *h = CMD_PRODUCT(cmd);
        snprintf(d, sizeof d, "%s", ue_name(h->row, a, sizeof a));
        if (n_unlocks >= UNLOCKS_PER_MAP) {
            snprintf(why, sizeof why, "more than %d unlocks on this map", UNLOCKS_PER_MAP);
            reject(kind, d, why);
            return 0;
        }
        if (!duffel_product(ppc, h, why, sizeof why)) { reject(kind, d, why); return 0; }
        n_unlocks++;
        return 1;
    }
    default:
        reject(kind, "", "never sent by a b4bcoop host");
        return 0;
    }
}

static void rpc_common(int kind, UObject *ppc, void *cmd) {
    if (!cmd || !is_net_client()) { orig_rpc[kind](ppc, cmd); return; }   // host / standalone: not from a host
    if (!check(kind, ppc, cmd)) return;
    n_accepted++;
    LOG("rewardguard: accepted %s from the host", RPC[kind].name);
    orig_rpc[kind](ppc, cmd);
}
static void d_sp(UObject *p, void *c) { rpc_common(R_SP, p, c); }
static void d_stp(UObject *p, void *c) { rpc_common(R_STP, p, c); }
static void d_cons(UObject *p, void *c) { rpc_common(R_CONS, p, c); }
static void d_unlock(UObject *p, void *c) { rpc_common(R_UNLOCK, p, c); }
static void d_slb(UObject *p, void *c) { rpc_common(R_SLB, p, c); }
static void *const DETOUR[NR] = {(void *)d_sp, (void *)d_stp, (void *)d_cons, (void *)d_unlock, (void *)d_slb};

static void resolve_playburn(void) {
    UClass *c = ue_find_class("GobiPlayerController");
    UFunction *f = c ? ue_find_function(c, "ServerPlayBurnCard") : NULL;
    FField *p = f ? ue_find_prop(f, "ProductRowHandle") : NULL;
    if (!p) return;
    off_playburn = FP_OFFSET(p);
    fn_playburn = f;   // last: crf_detour reads off_playburn once this is set
}

void rewardguard_tick(float dt) {
    static float acc;
    if (fn_playburn || !orig_crf || (acc += dt) < 1.f) return;
    acc = 0;
    resolve_playburn();
    if (fn_playburn) LOG("rewardguard: watching ServerPlayBurnCard");
}

int rewardguard_init(void) {
    int ok = 1;
    for (int i = 0; i < NR; i++) {
        void *at = (void *)VA(RPC[i].va);
        if (memcmp(at, RPC[i].sig, sizeof RPC[i].sig)) { LOG("rewardguard: %s handler signature mismatch", RPC[i].name); ok = 0; continue; }
        if (MH_CreateHook(at, DETOUR[i], (void **)&orig_rpc[i]) != MH_OK || MH_EnableHook(at) != MH_OK) {
            LOG("rewardguard: %s hook failed", RPC[i].name);
            ok = 0;
        }
    }
    if (memcmp((void *)ADDR_ACTOR_CRF, SIG_CRF, sizeof SIG_CRF)) LOG("rewardguard: CallRemoteFunction signature mismatch");
    else if (MH_CreateHook((void *)ADDR_ACTOR_CRF, (void *)crf_detour, (void **)&orig_crf) != MH_OK ||
             MH_EnableHook((void *)ADDR_ACTOR_CRF) != MH_OK) { LOG("rewardguard: CallRemoteFunction hook failed"); orig_crf = NULL; }
    if (orig_crf) resolve_playburn();
    // Without the hooks, host-sent profile commands would go through unchecked; that is the pre-b4bcoop behavior
    // (nothing sends them) only if rewards.c is off on the host, so say it loudly.
    LOG("rewardguard: %s; burn-card plays %s", ok ? "profile RPCs from the host are validated" : "SOME HANDLERS UNHOOKED",
        fn_playburn ? "watched" : orig_crf ? "watched once the class is loaded" : "NOT watched (burn card charges will be rejected)");
    return ok ? 0 : -1;
}

#ifndef B4B_RELEASE
// ---- dev commands ----
//   rewardguard                          client-side state: counters, per-map totals, played burn cards
//   rewardtest sp|stp <delta>            host: send a synthesized ClientExecute*Command to every remote player
//   rewardtest cons <product row> <delta>
//   rewardtest unlock <product row>
//   rewardtest slb
typedef FName *(*FNameCtorFn)(FName *self, const wchar_t *name, int find_type);
#define ADDR_FNAME_CTOR VA(0x1424BC8E0ull)

static int remote_ppcs(UObject **out, int max) {
    static UClass *cls_pc, *cls_lp;
    if (!cls_pc) cls_pc = ue_find_class("PlayerController");
    if (!cls_lp) cls_lp = ue_find_class("LocalPlayer");
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    int32_t off = gs ? ue_prop_offset(gs, "PlayerArray") : -1;
    if (off < 0 || !cls_pc || !cls_lp) return 0;
    TArray *pa = (TArray *)((char *)gs + off);
    int n = 0;
    for (int i = 0; i < pa->num && n < max; i++) {
        UObject *pc = ue_get_ptr(((UObject **)pa->data)[i], "Owner");
        UObject *player = pc && ue_is_a(pc, cls_pc) ? ue_get_ptr(pc, "Player") : NULL;
        UObject *ppc = player && !ue_is_a(player, cls_lp) ? ue_get_ptr(pc, "GobiPlayerProfileComponent") : NULL;
        if (ppc) out[n++] = ppc;
    }
    return n;
}

static void cmd_rewardtest(char *rest, Out *o) {
    static const char *const RPCNAME[NR] = {"ClientExecuteAdjustSupplyPointsCommand", "ClientExecuteAdjustSkullTotemPointsCommand",
        "ClientExecuteAdjustConsumableQuantityCommand", "ClientExecuteUnlockProductCommand",
        "ClientExecuteSetSecureLeaderboardMetadataCommand"};
    char *what = rest ? strtok(rest, " ") : NULL, *a1 = what ? strtok(NULL, " ") : NULL, *a2 = a1 ? strtok(NULL, " ") : NULL;
    int kind = !what ? -1 : !strcmp(what, "sp") ? R_SP : !strcmp(what, "stp") ? R_STP : !strcmp(what, "cons") ? R_CONS
             : !strcmp(what, "unlock") ? R_UNLOCK : !strcmp(what, "slb") ? R_SLB : -1;
    if (kind < 0 || ((kind == R_SP || kind == R_STP || kind == R_UNLOCK) && !a1) || (kind == R_CONS && !a2)) {
        out_printf(o, "usage: rewardtest sp|stp <delta> | cons <product row> <delta> | unlock <product row> | slb\n");
        return;
    }
    UObject *ppcs[8];
    int n = remote_ppcs(ppcs, 8), sent = 0;
    for (int i = 0; i < n; i++) {
        UFunction *f = ue_find_function(U_CLASS(ppcs[i]), RPCNAME[kind]);
        FField *p = f ? ue_find_prop(f, "Command") : NULL;
        static uint8_t parms[0x100];
        if (!p || UFN_PARMSSIZE(f) > sizeof parms) { out_printf(o, "no %s\n", RPCNAME[kind]); return; }
        memset(parms, 0, sizeof parms);   // vtable 0 is fine: an RPC serializes reflected fields only
        void *cmd = parms + FP_OFFSET(p);
        if (kind == R_SP || kind == R_STP) CMD_DELTA(cmd) = atoi(a1);
        if (kind == R_CONS || kind == R_UNLOCK) {
            static wchar_t w[128];
            int k = 0;
            for (; a1[k] && k < 127; k++) w[k] = (wchar_t)a1[k];
            w[k] = 0;
            CMD_PRODUCT(cmd)->table = products_table(ppcs[i]);
            ((FNameCtorFn)ADDR_FNAME_CTOR)(&CMD_PRODUCT(cmd)->row, w, 1);
            if (kind == R_CONS) CMD_CONS_DELTA(cmd) = atoi(a2);
        }
        LOG("rewardguard: test: sending %s (%s %s %s) to remote player %d", RPCNAME[kind], what, a1 ? a1 : "", a2 ? a2 : "", i);
        ue_process_event(ppcs[i], f, parms);
        sent++;
    }
    out_printf(o, "rewardtest: %s sent to %d remote player(s)\n", RPCNAME[kind], sent);
}

int rewardguard_cmd(const char *verb, char *rest, Out *o) {
    if (!strcmp(verb, "rewardtest")) { cmd_rewardtest(rest, o); return 1; }
    if (strcmp(verb, "rewardguard")) return 0;
    char a[128], b[128];
    sync_world();
    out_printf(o, "rewardguard: hooks %s, burn plays %s | accepted %d rejected %d | this map: SP %d STP %d consumables +%d "
               "unlocks %d (limits %d/cmd %d/map, +%d/cmd +%d/map, %d unlocks)\n",
               orig_rpc[R_SP] && orig_rpc[R_CONS] ? "on" : "OFF", fn_playburn ? "watched" : "NOT watched", n_accepted,
               n_rejected, sp_total, stp_total, cons_gain, n_unlocks, MAX_PER_CMD, MAX_PER_MAP, CONS_GAIN_PER_CMD,
               CONS_GAIN_PER_MAP, UNLOCKS_PER_MAP);
    for (int i = 0; i < n_played; i++)
        out_printf(o, "  played %s (product %s)%s\n", ue_name(played[i].card, a, sizeof a), ue_name(played[i].product, b, sizeof b),
                   played[i].charged ? " charged" : "");
    if (rest && !strncmp(rest, "product ", 8)) {   // rewardguard product <row>: is it a duffel-bag product?
        static wchar_t w[128];
        int k = 0;
        for (; rest[8 + k] && k < 127; k++) w[k] = (wchar_t)rest[8 + k];
        w[k] = 0;
        RowHandle h = {0};
        UObject *ppc = ue_local_pc() ? ue_get_ptr(ue_local_pc(), "GobiPlayerProfileComponent") : NULL;
        h.table = products_table(ppc);
        ((FNameCtorFn)ADDR_FNAME_CTOR)(&h.row, w, 1);
        char why[96] = "ok";
        int ok = duffel_product(ppc, &h, why, sizeof why);
        out_printf(o, "product %s: %s (%s)\n", rest + 8, ok ? "duffel-bag reward" : "rejected", why);
    }
    if (rest && !strcmp(rest, "difficulty")) {   // rewardguard difficulty: SP reward inputs of every DifficultyRow table
        UClass *dtc = ue_find_class("DataTable");
        for (int32_t i = 0, n = ue_num_objects(); dtc && i < n; i++) {
            UObject *t = ue_object_at(i);
            if (!t || (U_FLAGS(t) & 0x30) || !ue_is_a(t, dtc) || !DT_ROWSTRUCT(t)) continue;
            if (strcmp(ue_obj_name(DT_ROWSTRUCT(t), b, sizeof b), "DifficultyRow")) continue;
            out_printf(o, "%s:\n", ue_obj_name(t, a, sizeof a));
            TArray *m = DT_ROWMAP(t);
            for (int k = 0; k < m->num; k++) {
                char *e = sparse_at(m, k, 0x18);
                uint8_t *row = e ? *(uint8_t **)(e + 8) : NULL;
                if (!row) continue;
                TArray *core = (TArray *)(row + 0x60);   // SupplyPointRewardEntry {MapRow 0x20, float CoreSp} 0x28
                float mx = 0, sum = 0;
                for (int j = 0; j < core->num; j++) { float v = *(float *)((char *)core->data + j * 0x28 + 0x20); sum += v; if (v > mx) mx = v; }
                float *f = (float *)(row + 0x70);
                out_printf(o, "  %s: CoreSp maps=%d max=%.0f sum=%.0f | secondary %.2f glyph %.2f group %.2f consec %.2f+%.2f<=%.2f "
                           "party %.2f quickplay %.2f | fail base %d scale %.2f\n", ue_name(*(FName *)e, a, sizeof a), core->num,
                           mx, sum, f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], *(int32_t *)(row + 0x90), *(float *)(row + 0x94));
            }
        }
    }
    if (rest && !strcmp(rest, "products")) {   // rewardguard products: count products, and duffel-bag ones
        UObject *ppc = ue_local_pc() ? ue_get_ptr(ue_local_pc(), "GobiPlayerProfileComponent") : NULL;
        UObject *t = products_table(ppc);
        if (!t) { out_printf(o, "no products table\n"); return 1; }
        TArray *m = DT_ROWMAP(t);
        int rows = 0, duffel = 0, shown = 0;
        for (int i = 0; i < m->num; i++) {
            char *e = sparse_at(m, i, 0x18);
            if (!e) continue;
            rows++;
            uint8_t *row = *(uint8_t **)(e + 8);
            if (row && PRODUCT_DUFFEL_TAGS(row)->num > 0) {
                duffel++;
                if (shown++ < 40) out_printf(o, "  duffel: %s type=%d\n", ue_name(*(FName *)e, a, sizeof a), row[0x91]);
            }
        }
        out_printf(o, "%s (%s): %d rows, %d duffel-bag products\n", ue_obj_name(t, a, sizeof a),
                   DT_ROWSTRUCT(t) ? ue_obj_name(DT_ROWSTRUCT(t), b, sizeof b) : "?", rows, duffel);
    }
    return 1;
}
#endif  // !B4B_RELEASE
