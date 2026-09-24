// Slot guard (issue #7): a host crashed when one more human joined than it had survivor slots. RequestSlot fails
// for that player ("failed to claim slot", PC goes Spectating), yet ~1 s later the game mode restarts it, and
// AHeroGameMode::RestartPlayerAtPlayerStart dereferences the player's null slot after spawning the hero.
// Three layers, all host-side:
//   1. Login gate: AGameSession::ApproveLogin says "Server full." when every hero slot is held by a human
//      (bots don't count: a joining human takes a bot's slot over). The engine builds the error string itself: we
//      lower net.MaxPlayersOverride to 1 for the duration of that one call.
//   2. Fallback for races (two joiners for one slot, reserved slots): when a remote player fails to claim a slot,
//      close its connection on the next tick.
//   3. Crash guard: RestartPlayerAtPlayerStart does not spawn a hero for a player with no slot.
// docs/investigations/slot-guard.md.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define ADDR_APPROVELOGIN VA(0x143CC0440ull)  // FString* AGameSession::ApproveLogin(this, FString* ret, const FString* Options)
#define ADDR_MAXPL_LOAD   VA(0x143CC0643ull)  // in ApproveLogin: mov rax,[rip+x] -> int* net.MaxPlayersOverride
#define ADDR_FAILCLAIM    VA(0x141B9B680ull)  // void AGobiPlayerController::OnFailedToClaimSlot(this) (vtable +0xdf8)
#define ADDR_HERO_RESTART VA(0x1419FE7D0ull)  // void AHeroGameMode::RestartPlayerAtPlayerStart(this, AController*, AActor*)
                                              // calls AGameModeBase's (0x143CB3180) first; crash site at +0xa5
#define ADDR_WEAK_GET     VA(0x1426F7D20ull)  // UObject* FWeakObjectPtr::Get(const FWeakObjectPtr*)
#define PS_SLOT_WEAK      0x554               // AGobiPlayerState: weak ptr to the player's slot (read at the crash site)
#define TEAMSLOTS_SZ      0x20                // FTeamSlots: Team u8 +0, Slots TArray +8
#define ADDR_CONN_CLOSE   VA(0x143E02670ull)  // void UNetConnection::Close(this)

static const uint8_t SIG_APPROVE[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x48,0x89,0x7c,0x24,0x18,0x55,
                                      0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,0x48,0x8b,0xec,0x48,0x83,0xec,0x50,0x48,
                                      0x8b,0x01,0x4d,0x8b,0xe8,0x48,0x8b,0xda,0x48,0x8b,0xf9,0xff,0x90,0x50,0x01,0x00};
static const uint8_t SIG_MAXPL[] = {0x48,0x8b,0x05,0xc6,0x75,0xca,0x02,0x8b,0x30,0x85,0xf6,0x7f,0x0e};
static const uint8_t SIG_FAILCLAIM[] = {0x48,0x89,0x5c,0x24,0x10,0x48,0x89,0x74,0x24,0x18,0x55,0x57,0x41,0x57,0x48,0x8b,
                                        0xec,0x48,0x83,0xec,0x60,0x80,0x3d,0xd1,0x22,0xe2,0x04,0x00};
static const uint8_t SIG_RESTART[] = {0x40,0x53,0x48,0x83,0xec,0x30,0x48,0x8b,0xda,0xe8,0xa2,0x49,0x2b,0x02,0x48,0x85,
                                      0xdb,0x0f,0x84,0xb3,0x01,0x00,0x00,0x48,0x8b,0x9b,0xa8,0x02,0x00,0x00};
static const uint8_t SIG_WEAK[] = {0x44,0x8b,0x49,0x04,0x45,0x85,0xc9,0x74,0x55,0x8b,0x01,0x85,0xc0,0x78,0x4f};
static const uint8_t SIG_CLOSE[] = {0x48,0x8b,0xc4,0x53,0x41,0x56,0x48,0x83,0xec,0x68,0x45,0x33,0xf6,0x48,0x8b,0xd9,
                                    0x4c,0x39,0x71,0x60,0x0f,0x84,0x88,0x01,0x00,0x00,0x83,0xb9,0x3c,0x01,0x00,0x00};

typedef FString *(*ApproveFn)(UObject *sess, FString *ret, const FString *opts);
typedef void (*FailClaimFn)(UObject *pc);
typedef void (*RestartFn)(UObject *gm, UObject *ctrl, UObject *start);
typedef UObject *(*WeakGetFn)(const void *weak);
typedef void (*CloseFn)(UObject *conn);
static ApproveFn orig_approve;
static FailClaimFn orig_failclaim;
static RestartFn orig_restart;
static WeakGetFn weak_get;
static CloseFn close_conn;
static int *maxplayers_cvar;
static int gate = 1;
static int n_rejected, n_kicked, n_guarded;

// ---- counting ----
static UObject *slot_manager(UObject *w) {
    UObject *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    return gs ? ue_get_ptr(gs, "PlayerSlotManager") : NULL;
}

static int is_pc(UObject *o) {
    static UClass *pc_cls;
    if (!pc_cls) pc_cls = ue_find_class("PlayerController");
    return o && pc_cls && ue_is_a(o, pc_cls);
}

static int is_remote_pc(UObject *o) { return is_pc(o) && o != ue_local_pc(); }

// Hero-team slot count (-1 if there is no slot manager).
static int hero_slots(UObject *w) {
    UObject *psm = slot_manager(w);
    int32_t off = psm ? ue_prop_offset(psm, "TeamSlots") : -1;
    if (off < 0) return -1;
    TArray *teams = (TArray *)((char *)psm + off);
    for (int t = 0; t < teams->num; t++) {
        uint8_t *ts = (uint8_t *)teams->data + t * TEAMSLOTS_SZ;
        if (ts[0] == 0 /*EGobiTeam::Hero*/) return ((TArray *)(ts + 8))->num;
    }
    return -1;
}

// Human players in the game (host included): player states owned by a PlayerController, not a bot controller.
static int humans(UObject *w) {
    UObject *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    int32_t off = gs ? ue_prop_offset(gs, "PlayerArray") : -1;
    if (off < 0) return 0;
    TArray *pa = (TArray *)((char *)gs + off);
    int n = 0;
    for (int i = 0; i < pa->num; i++) n += is_pc(ue_get_ptr(((UObject **)pa->data)[i], "Owner"));
    return n;
}

// ---- 1. login gate ----
static FString *approve_detour(UObject *sess, FString *ret, const FString *opts) {
    UObject *w = ue_world();
    int cap = gate && ue_is_listen_server(w) ? hero_slots(w) : -1, h = cap > 0 ? humans(w) : 0;
    int full = cap > 0 && h >= cap && maxplayers_cvar;
    int saved = maxplayers_cvar ? *maxplayers_cvar : 0;
    if (full) *maxplayers_cvar = 1;   // "NumPlayers >= 1": the host alone is enough, the engine says "Server full."
    FString *r = orig_approve(sess, ret, opts);
    if (full) *maxplayers_cvar = saved;
    int rejected = r && r->num > 1;
    if (full && rejected) n_rejected++;
    LOG("slotguard: login %s (humans=%d hero_slots=%d%s)", rejected ? "REJECTED" : "approved", h, cap,
        full ? ", no free slot" : "");
    return r;
}

// ---- 2. tell a player that could not get a slot to leave (next tick, outside the game's own call) ----
typedef struct { UObject *obj; int32_t idx; } Ref;
static Ref kicks[8];
static int alive(const Ref *r) { return r->obj && ue_object_at(r->idx) == r->obj; }
static void push(Ref *arr, UObject *o) {
    for (int i = 0; i < 8; i++) if (alive(&arr[i]) && arr[i].obj == o) return;
    for (int i = 0; i < 8; i++) if (!alive(&arr[i])) { arr[i].obj = o; arr[i].idx = U_INDEX(o); return; }
}

// Close the player's connection, like a host that quit: the client shows the game's "disconnected" popup and
// reloads its own camp. (The retail kick RPC, GobiPlayerState::ClientKickWithDisconnectError, is a no-op on a
// client that is not in a Gobi party, which is every client of this mod.)
static void kick_now(UObject *pc) {
    static UClass *nc_cls;
    if (!nc_cls) nc_cls = ue_find_class("NetConnection");
    UObject *conn = ue_get_ptr(pc, "Player");
    if (!close_conn || !conn || !nc_cls || !ue_is_a(conn, nc_cls)) { LOG("slotguard: cannot kick %p (no connection)", (void *)pc); return; }
    close_conn(conn);
    n_kicked++;
    LOG("slotguard: closed the connection of player %p", (void *)pc);
}

int slotguard_kick(UObject *pc) {   // admin.c /kick, /ban
    if (!is_remote_pc(pc)) return -1;
    kick_now(pc);
    return 0;
}

static void failclaim_detour(UObject *pc) {
    orig_failclaim(pc);   // retail: log "Failed To claim slot!", player goes Spectating
    if (is_remote_pc(pc)) { LOG("slotguard: remote player %p got no slot, kicking", (void *)pc); push(kicks, pc); }
}

// ---- 3. crash guard ----
static void restart_detour(UObject *gm, UObject *ctrl, UObject *start) {
    static UClass *gps;
    if (!gps) gps = ue_find_class("GobiPlayerState");
    UObject *ps = ctrl ? ue_get_ptr(ctrl, "PlayerState") : NULL;
    int gobi = ps && gps && ue_is_a(ps, gps);
    UObject *slot = gobi ? weak_get((char *)ps + PS_SLOT_WEAK) : NULL;
    if (gobi && !slot) {
        n_guarded++;
        LOG("slotguard: RestartPlayerAtPlayerStart for %p, which has no slot: not spawning a hero", (void *)ctrl);
        if (is_remote_pc(ctrl)) push(kicks, ctrl);
        return;
    }
    orig_restart(gm, ctrl, start);
    LOG("slotguard: restart %p slot %p -> %p", (void *)ctrl, (void *)slot,
        gobi ? (void *)weak_get((char *)ps + PS_SLOT_WEAK) : NULL);
}

void slotguard_tick(float dt) {
    (void)dt;
    for (int i = 0; i < 8; i++) {
        if (alive(&kicks[i])) kick_now(kicks[i].obj);
        kicks[i].obj = NULL;
    }
}

#ifndef B4B_RELEASE
// ---- command (dev builds): slotguard [gate 0|1 | kick <PlayerArray index>] ----
int slotguard_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "slotguard")) return 0;
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL;
    if (a && b && !strcmp(a, "gate")) gate = atoi(b);
    else if (a && b && !strcmp(a, "kick")) {   // testing: send the kick to PlayerArray[i]
        UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
        TArray *pa = gs ? (TArray *)((char *)gs + ue_prop_offset(gs, "PlayerArray")) : NULL;
        int i = atoi(b);
        UObject *ps = pa && i >= 0 && i < pa->num ? ((UObject **)pa->data)[i] : NULL;
        UObject *pc = ps ? ue_get_ptr(ps, "Owner") : NULL;
        if (is_remote_pc(pc)) kick_now(pc); else out_printf(o, "player %d is not a remote human\n", i);
    }
    UObject *w = ue_world();
    out_printf(o, "slotguard: gate=%d hooks=%d | hero_slots=%d humans=%d listen=%d | rejected=%d kicked=%d guarded=%d\n",
               gate, orig_approve && orig_failclaim && orig_restart, hero_slots(w), humans(w),
               ue_is_listen_server(w), n_rejected, n_kicked, n_guarded);
    return 1;
}
#endif  // !B4B_RELEASE

static int hook(uintptr_t at, const uint8_t *sig, size_t n, void *detour, void **orig, const char *what) {
    if (memcmp((void *)at, sig, n)) { LOG("slotguard: %s signature mismatch", what); return -1; }
    if (MH_CreateHook((void *)at, detour, orig) != MH_OK || MH_EnableHook((void *)at) != MH_OK) {
        LOG("slotguard: %s hook failed", what); *orig = NULL; return -1;
    }
    return 0;
}

int slotguard_init(void) {
    if (!memcmp((void *)ADDR_MAXPL_LOAD, SIG_MAXPL, sizeof SIG_MAXPL)) {
        uint8_t *ins = (uint8_t *)ADDR_MAXPL_LOAD;
        maxplayers_cvar = *(int **)(ins + 7 + *(int32_t *)(ins + 3));
        hook(ADDR_APPROVELOGIN, SIG_APPROVE, sizeof SIG_APPROVE, (void *)approve_detour, (void **)&orig_approve, "ApproveLogin");
    } else LOG("slotguard: MaxPlayersOverride signature mismatch");
    if (!memcmp((void *)ADDR_WEAK_GET, SIG_WEAK, sizeof SIG_WEAK)) {
        weak_get = (WeakGetFn)ADDR_WEAK_GET;
        hook(ADDR_HERO_RESTART, SIG_RESTART, sizeof SIG_RESTART, (void *)restart_detour, (void **)&orig_restart, "RestartPlayerAtPlayerStart");
    } else LOG("slotguard: FWeakObjectPtr::Get signature mismatch");
    if (!memcmp((void *)ADDR_CONN_CLOSE, SIG_CLOSE, sizeof SIG_CLOSE)) close_conn = (CloseFn)ADDR_CONN_CLOSE;
    else LOG("slotguard: UNetConnection::Close signature mismatch");
    hook(ADDR_FAILCLAIM, SIG_FAILCLAIM, sizeof SIG_FAILCLAIM, (void *)failclaim_detour, (void **)&orig_failclaim, "OnFailedToClaimSlot");
    LOG("slotguard: login gate %s, kick fallback %s, crash guard %s", orig_approve ? "on" : "OFF",
        orig_failclaim ? "on" : "OFF", orig_restart ? "on" : "OFF");
    return 0;
}
