// Unattended local testing (launch/multi.sh): get from boot to offline Fort Hope with no clicks.
//   offline=1 in the agent config: press "Sign in" on the title screen, then answer the Online/Offline popup
//   with Offline, exactly like a click (PopupUserWidget::Close("Offline") -> SignInTask_OnlineOfflinePopup).
// Commands: `signin` (one step by hand), `mission [raw] [map] [Easy|Normal|Hard|VeryHard]`, `ready [vote]`,
// `endmission [1|0]`, `burncard list|<card row> [table]`, `callp <Class> <Func> [args]`.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ue.h"
#include "log.h"
#include "cmds.h"

extern int g_auto_offline;
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

static int call_bool(UObject *o, const char *fn_name) {
    UFunction *f = ue_find_function(U_CLASS(o), fn_name);
    if (!f) return -1;
    uint8_t p[16] = {0};
    ue_process_event(o, f, p);
    return p[0];
}

static int state_of(UObject *task) { return *(int32_t *)((char *)task + 0x30); }   // ESignInTaskState, 1 = Running

// Open popup whose OnPopupClosed delegate is bound to `task` (FScriptDelegate: weak {index, serial}, FName fn).
static UObject *popup_for(UObject *task) {
    static UClass *pc;
    if (!pc) pc = ue_find_class("PopupUserWidget");
    for (UObject *p = pc ? find_live(pc, NULL) : NULL; p; p = find_live(pc, p)) {
        int32_t off = ue_prop_offset(p, "OnPopupClosed");
        if (off < 0) continue;
        TArray *inv = (TArray *)((char *)p + off);
        for (int i = 0; i < inv->num; i++)
            if (*(int32_t *)((char *)inv->data + i * 16) == U_INDEX(task) && call_bool(p, "IsOpen") == 1) return p;
    }
    return NULL;
}

#define SCREEN_STATE(s) (*(uint8_t *)((char *)(s) + 0x568))   // ESignInScreenState (SetState 0x141D37600)
enum { SIS_NotSignedIn = 0, SIS_SignedIn = 7 };
static int signin_done;

// One step of the sign-in flow. Returns 1 when it acted.
static int signin_step(Out *o) {
    static UClass *task_c, *screen_c;
    static int seen_screen;
    if (!task_c) task_c = ue_find_class("SignInTask_OnlineOfflinePopup");
    if (!screen_c) screen_c = ue_find_class("SignInScreen");
    if (!task_c || !screen_c) { if (o) out_printf(o, "sign-in classes not loaded\n"); return 0; }
    UObject *s = find_live(screen_c, NULL);
    if (!s) {
        if (seen_screen) signin_done = 1;   // screen gone after we saw it: signed in
        if (o) out_printf(o, "no sign-in screen\n");
        return 0;
    }
    seen_screen = 1;
    int st = SCREEN_STATE(s);
    if (o) out_printf(o, "sign-in screen %p state=%d\n", (void *)s, st);
    if (st == SIS_SignedIn) { signin_done = 1; return 0; }
    if (st == SIS_NotSignedIn) {   // title screen waiting for "Sign in" (skipped by the game while EOS pre-login runs)
        UFunction *f = ue_find_function(U_CLASS(s), "StartSignIn");
        if (!f) return 0;
        LOG("testing: StartSignIn on %p", (void *)s);
        uint8_t none[16] = {0};
        ue_process_event(s, f, none);
        return 1;
    }
    // signing in: Online/Offline popup up -> answer Offline
    for (UObject *t = find_live(task_c, NULL); t; t = find_live(task_c, t)) {
        if (state_of(t) != 1) continue;
        UObject *p = popup_for(t);
        if (o) out_printf(o, "online/offline task running, popup=%p\n", (void *)p);
        if (!p) return 0;
        UFunction *close = ue_find_function(U_CLASS(p), "Close");
        struct { FName cmd; } args = { make_name(L"Offline") };
        LOG("testing: answering online/offline popup with Offline");
        ue_process_event(p, close, &args);
        return 1;
    }
    return 0;
}

void testing_tick(float dt) {
    static double clock, next;
    clock += dt;
    if (!g_auto_offline || signin_done || clock < next) return;
    if (clock > 600) { signin_done = 1; LOG("testing: no sign-in after 10 min, auto sign-in off"); return; }
    next = clock + 2;
    if (!ue_world()) return;
    signin_step(NULL);
    if (signin_done) LOG("testing: signed in, auto sign-in off");
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

// ready [vote]: host readies every player so nobody has to click: the loadout screen's Ready
// (GobiPlayerState::ServerRequestPlayerReady(true)) or, with "vote", the post-round screen's
// (ServerSetReadyForPostRoundVote). Server RPCs called on the server run locally, so this works for remote players too.
static void cmd_ready(char *rest, Out *o) {
    int vote = rest && !strncmp(rest, "vote", 4);
    const char *fname = vote ? "ServerSetReadyForPostRoundVote" : "ServerRequestPlayerReady";
    UObject *w = ue_world();
    UObject *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    UClass *psc = ue_find_class("GobiPlayerState");
    int32_t off = gs ? ue_prop_offset(gs, "PlayerArray") : -1;
    if (!psc || off < 0) { out_printf(o, "no gamestate\n"); return; }
    TArray *pa = (TArray *)((char *)gs + off);
    int n = 0;
    for (int i = 0; i < pa->num; i++) {
        UObject *ps = ((UObject **)pa->data)[i];
        UFunction *f = ps && ue_is_a(ps, psc) ? ue_find_function(U_CLASS(ps), fname) : NULL;
        if (!f) continue;
        uint8_t p[16] = {0};
        int32_t ob = vote ? -1 : param_off(f, "bReady");
        if (ob >= 0) p[ob] = 1;
        ue_process_event(ps, f, p);
        n++;
    }
    LOG("testing: %s on %d player(s)", fname, n);
    out_printf(o, "%s: %d player(s)\n", fname, n);
}

// burncard list | burncard <card row> [<card table>]: this instance's player plays a burn card, like the start
// saferoom's card UI (GobiPlayerController::ServerPlayBurnCard). The handle is the gameplay CARD row (the server looks
// its name up in GameplayCardManager.CardNameToProductHandles and charges that product when the party leaves the
// saferoom). `list` prints that map: card row -> product row.
static UObject *find_named(const char *name) {
    char nm[160];
    for (int32_t i = 0, n = ue_num_objects(); i < n; i++) {
        UObject *x = ue_object_at(i);
        if (x && U_CLASS(x) && !(U_FLAGS(x) & 0x30) && !strcmp(ue_obj_name(x, nm, sizeof nm), name)) return x;
    }
    return NULL;
}

static void cmd_burncard(char *rest, Out *o) {
    char *row = rest ? strtok(rest, " ") : NULL, *table = row ? strtok(NULL, " ") : NULL;
    if (!row) { out_printf(o, "usage: burncard list | burncard <card row> [card table]\n"); return; }
    UObject *gcm = ue_find_first_of("GameplayCardManager");
    int32_t moff = gcm ? ue_prop_offset(gcm, "CardNameToProductHandles") : -1;
    if (moff < 0) { out_printf(o, "no GameplayCardManager\n"); return; }
    // TMap<FName, FDataTableRowHandle>: sparse array of {FName key, handle {UDataTable*, FName, FString}, hash} (0x30)
    TArray *elems = (TArray *)((char *)gcm + moff);
    char a[160], b[160], c[160];
    if (!strcmp(row, "list")) {
        for (int i = 0; i < elems->num && i < 200; i++) {
            char *e = (char *)elems->data + i * 0x30;
            UObject *dt = *(UObject **)(e + 8);
            out_printf(o, "  %s -> %s %s\n", ue_name(*(FName *)e, a, sizeof a), dt ? ue_obj_name(dt, b, sizeof b) : "null",
                       ue_name(*(FName *)(e + 0x10), c, sizeof c));
        }
        out_printf(o, "%d entries\n", elems->num);
        return;
    }
    UObject *pc = ue_local_pc(), *dt = find_named(table ? table : "PlayerCards_MASTER_DT");
    UFunction *f = pc ? ue_find_function(U_CLASS(pc), "ServerPlayBurnCard") : NULL;
    int32_t off = f ? param_off(f, "ProductRowHandle") : -1;
    if (!dt || off < 0) { out_printf(o, "no card table (%p) or ServerPlayBurnCard\n", (void *)dt); return; }
    static uint8_t p[64];
    static wchar_t wrow[128];
    memset(p, 0, sizeof p);
    int k = 0;
    for (; row[k] && k < 127; k++) wrow[k] = (wchar_t)row[k];
    wrow[k] = 0;
    *(UObject **)(p + off) = dt;
    *(FName *)(p + off + 8) = make_name(wrow);
    LOG("testing: ServerPlayBurnCard %s (%s)", row, ue_obj_name(dt, a, sizeof a));
    ue_process_event(pc, f, p);
    out_printf(o, "burncard: ServerPlayBurnCard(%s %s)\n", a, row);
}

// callp <Class> <Func> [args...]: call a function on the first live instance, parameters in declaration order
// (bool/int/byte/enum/float/string; the return value is skipped). Bytes of the parms block are printed back.
static void cmd_callp(char *rest, Out *o) {
    char *cls = rest ? strtok(rest, " ") : NULL, *fn = cls ? strtok(NULL, " ") : NULL;
    if (!fn) { out_printf(o, "usage: callp <Class> <Func> [args...]\n"); return; }
    UObject *t = ue_find_first_of(cls);
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
        else if (FP_ELSIZE(prop) == 4) *(int32_t *)d = atoi(a);
        else if (FP_ELSIZE(prop) == 1) *d = (uint8_t)atoi(a);
        else { out_printf(o, "unsupported param type %s\n", tn); return; }
    }
    ue_process_event(t, f, p);
    out_printf(o, "called %s.%s; parms:", cls, fn);
    for (int i = 0; i < UFN_PARMSSIZE(f) && i < 32; i++) out_printf(o, " %02x", p[i]);
    out_printf(o, "\n");
}

int testing_cmd(const char *verb, char *rest, Out *o) {
    (void)rest;
    if (!strcmp(verb, "signin")) { signin_step(o); return 1; }
    if (!strcmp(verb, "mission")) { cmd_mission(rest, o); return 1; }
    if (!strcmp(verb, "ready")) { cmd_ready(rest, o); return 1; }
    if (!strcmp(verb, "burncard")) { cmd_burncard(rest, o); return 1; }
    if (!strcmp(verb, "callp")) { cmd_callp(rest, o); return 1; }
    if (!strcmp(verb, "endmission")) { cmd_endmission(rest, o); return 1; }
    return 0;
}
