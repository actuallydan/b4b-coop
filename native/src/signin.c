// Auto sign-in Offline: get from the title screen to offline Fort Hope with no clicks.
//   Press "Sign in" on the title screen, then answer the Online/Offline popup with Offline, exactly like a click
//   (PopupUserWidget::Close("Offline") -> SignInTask_OnlineOfflinePopup).
//   Armed for players by a Steam "Join Game" / invite (presence.c -> signin_arm), for up to 10 minutes.
//   Dev builds also arm it from the ini (offline=1, unattended tests: launch/multi.sh); the `signin` command
//   (testing.c) runs one step by hand.
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
int signin_step(Out *o) {
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
        LOG("signin: StartSignIn on %p", (void *)s);
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
        LOG("signin: answering online/offline popup with Offline");
        ue_process_event(p, close, &args);
        return 1;
    }
    return 0;
}

static double signin_clock, signin_deadline = 600;

// A join target from Steam (presence.c): sign in Offline for a real player too, for the next 10 minutes.
void signin_arm(void) {
    if (signin_done) return;
    UClass *sc = ue_find_class("SignInScreen");
    UObject *w = ue_world();
    char pkg[256];
    if (w && ue_local_pc() && strstr(ue_world_package(w, pkg, sizeof pkg), "FortHope") && sc && !find_live(sc, NULL)) {
        signin_done = 1;   // already in the camp, past the title screen
        return;
    }
    if (!g_auto_offline) LOG("signin: auto sign-in (Offline) armed for a Steam join");
    g_auto_offline = 1;
    signin_deadline = signin_clock + 600;
}

// Title screen up (auto-host makes even the title's Fort Hope a listen server): nothing to advertise yet.
int signin_on_title(void) {
    static UClass *sc;
    if (!sc) sc = ue_find_class("SignInScreen");
    UObject *s = sc ? find_live(sc, NULL) : NULL;
    return s && SCREEN_STATE(s) != SIS_SignedIn;
}

int signin_pending(void) { return g_auto_offline && !signin_done; }

void signin_tick(float dt) {
    static double next;
    double clock = signin_clock += dt;
    if (!g_auto_offline || signin_done || clock < next) return;
    if (clock > signin_deadline) { signin_done = 1; LOG("signin: no sign-in after 10 min, auto sign-in off"); return; }
    next = clock + 2;
    if (!ue_world()) return;
    signin_step(NULL);
    if (signin_done) LOG("signin: signed in, auto sign-in off");
}
