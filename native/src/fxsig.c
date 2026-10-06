// Particle significance guard (issue #46: client crash at the Titan Tunnels finale, Cinematic4).
//
// The game hands every active ParticleSystemComponent (PSC) to the world's SignificanceManager (tag "Particles"):
// UParticleSystemComponent's static OnSystemPreActivationChange delegate is bound to 0x1419C3EA0 (PSC, bool), which
// registers the component on activation (bit 0x80 of +0x4F1 marks it) and unregisters it on deactivation. The
// broadcast happens only from ActivateSystem (when the component wasn't active) and from DeactivateSystem /
// ResetParticles (only while bIsActive, +0xC2 bit 0, is set); OnUnregister resets the particles, so a destroyed or
// streamed-out component normally leaves the manager first. The manager keeps a raw pointer per object and runs the
// significance function (0x1419C3D90 -> 0x143E5D420, a render-time check: GetWorld -> Owner->GetWorld()) on
// all of them every frame, in a ParallelFor on the task-graph threads.
//
// A component whose bIsActive is cleared without that broadcast stays registered while inactive; its OnUnregister
// then skips the unregistration, GC frees it, and the next significance update calls through freed memory: a call to
// 0 from 0x143B2C0F2 (UActorComponent::GetWorld_Uncached, Owner->GetWorld()) on several TaskGraphThreadHP threads at
// once. That is the 0.9.2 client crash (Titan Tunnels finale: the Titan's death, then Cinematic4's level stream-out
// forces a GC). bIsActive is replicated, and UActorComponent::OnRep_IsActive (0x143B2EEE0, not overridden by PSCs)
// only toggles ticking. ActivateSystem returns early when the machine can't render (FApp::CanEverRender,
// 0x141233880), so a dedicated server never has an active particle and never sends a change; our listen host renders,
// so clients get bIsActive=0 for replicated particles they activated themselves (on every Titan Tunnels client:
// FallingRock_Chamber02_*.RockDustTell1 right after load). Retail code path, exposed by the listen server.
//
// Guard (both roles, local only, no protocol change): before ResetParticles runs (OnUnregister, DeactivateSystem, ...),
// a component still registered with the significance manager but no longer active is unregistered by the game's own
// function. Repro (dev, guard off on the client): Titan Tunnels, host `fxsig kill FallingRock_Chamber02_`, client
// `fxsig gc` -> the crash above; guard on -> "fxsig: inactive particle component left the significance manager".
// Dev: `fxsig` (count registered/active/orphaned PSCs, list orphans), `fxsig fix` (unregister orphans now),
// `fxsig list <path part>`, `fxsig guard on|off`, `fxsig kill <actor name part>` (K2_DestroyActor), `fxsig gc`; dev
// builds also log replicated bIsActive=0 arriving on a registered PSC. docs/investigations/particle-significance.md.
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define ADDR_RESET_PARTICLES VA(0x143E631D0ull)   // void UParticleSystemComponent::ResetParticles(this, bool bEmptyInstances)
#define ADDR_SIG_ACTIVATION  VA(0x1419C3EA0ull)   // static void (UParticleSystemComponent*, bool bActivating): significance (un)registration
static const uint8_t SIG_RESET[] = {0x48,0x89,0x5c,0x24,0x10,0x48,0x89,0x74,0x24,0x18,0x57,0x48,0x83,0xec,0x20,0x48,
                                    0x83,0xb9,0x30,0x07,0x00,0x00,0x00};
static const uint8_t SIG_ACT[]   = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x57,0x48,0x81,0xec,0xc0,0x00,
                                    0x00,0x00,0x0f,0xb6,0xf2,0x48,0x8b,0xd9};
#define PSC_ACTIVE(c)     (*((uint8_t *)(c) + 0xC2) & 1)      // UActorComponent::bIsActive
#define PSC_SIGNIFICANT(c) (*((uint8_t *)(c) + 0x4F1) & 0x80) // registered with the significance manager
#define PSC_TEMPLATE(c)   (*(UObject **)((char *)(c) + 0x4C8))
#define AC_OWNER(c)       (*(UObject **)((char *)(c) + 0xD8))

typedef void (*ResetFn)(UObject *psc, uint8_t empty);
typedef void (*SigActFn)(UObject *psc, uint8_t activating);
static ResetFn orig_reset;
static SigActFn sig_activation;
static volatile LONG guard_on = 1;
static unsigned n_fixed;

static void describe(UObject *psc, char *out, size_t len) {
    char a[128], w[160], t[128];
    UObject *ow = AC_OWNER(psc), *tp = PSC_TEMPLATE(psc);
    snprintf(out, len, "%s on %s (%s)", ue_obj_name(psc, a, sizeof a), ow ? ue_obj_name(ow, w, sizeof w) : "-",
             tp ? ue_obj_name(tp, t, sizeof t) : "no template");
}

static void unregister_orphan(UObject *psc, const char *why) {
    sig_activation(psc, 0);
    n_fixed++;
    if (n_fixed <= 5 || !(n_fixed & (n_fixed - 1))) {   // the first few, then 8, 16, 32, ...
        char d[400];
        describe(psc, d, sizeof d);
        LOG("fxsig: %s particle component left the significance manager (%u so far): %s%s", why, n_fixed, d,
            PSC_SIGNIFICANT(psc) ? " [still registered]" : "");
    }
}

static void reset_detour(UObject *psc, uint8_t empty) {
    if (guard_on && psc && PSC_SIGNIFICANT(psc) && !PSC_ACTIVE(psc)) unregister_orphan(psc, "inactive");
    orig_reset(psc, empty);
}

#ifndef B4B_RELEASE
// dev diagnostic: UActorComponent::OnRep_IsActive (PSC vtable +0x260, not overridden): only SetComponentTickEnabled
#define ADDR_ONREP_ISACTIVE VA(0x143B2EEE0ull)
static const uint8_t SIG_ONREP[] = {0x0f,0xb6,0x91,0xc2,0x00,0x00,0x00,0x48,0x8b,0x01,0x80,0xe2,0x01,0x48,0xff,0xa0};
typedef void (*OnRepFn)(UObject *comp);
static OnRepFn orig_onrep;
static unsigned n_rep_orphans;
static void onrep_detour(UObject *c) {
    static UClass *cls;
    if (!cls) cls = ue_find_class("ParticleSystemComponent");
    if (cls && ue_is_a(c, cls) && PSC_SIGNIFICANT(c) && !PSC_ACTIVE(c) && ++n_rep_orphans <= 20) {
        char d[400];
        describe(c, d, sizeof d);
        LOG("fxsig: replicated bIsActive=0 on a registered particle component (no deactivation broadcast): %s", d);
    }
    orig_onrep(c);
}
#endif

int fxsig_init(void) {
    if (memcmp((void *)ADDR_RESET_PARTICLES, SIG_RESET, sizeof SIG_RESET) ||
        memcmp((void *)ADDR_SIG_ACTIVATION, SIG_ACT, sizeof SIG_ACT)) { LOG("fxsig: signature mismatch, not hooked"); return -1; }
    sig_activation = (SigActFn)ADDR_SIG_ACTIVATION;
    if (MH_CreateHook((void *)ADDR_RESET_PARTICLES, (void *)reset_detour, (void **)&orig_reset) != MH_OK ||
        MH_EnableHook((void *)ADDR_RESET_PARTICLES) != MH_OK) { LOG("fxsig: hook failed"); orig_reset = NULL; return -1; }
#ifndef B4B_RELEASE
    if (!memcmp((void *)ADDR_ONREP_ISACTIVE, SIG_ONREP, sizeof SIG_ONREP) &&
        MH_CreateHook((void *)ADDR_ONREP_ISACTIVE, (void *)onrep_detour, (void **)&orig_onrep) == MH_OK)
        MH_EnableHook((void *)ADDR_ONREP_ISACTIVE);
#endif
    LOG("fxsig: particle significance guard on");
    return 0;
}

#ifndef B4B_RELEASE
// ---- dev command: fxsig [fix | list <path part> | guard on|off | kill <actor name part> | gc] ----
int fxsig_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "fxsig")) return 0;
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL;
    if (a && !strcmp(a, "guard") && b) {
        InterlockedExchange(&guard_on, !strcmp(b, "on"));
        out_printf(o, "fxsig: guard %s\n", guard_on ? "on" : "off");
        return 1;
    }
    if (a && !strcmp(a, "gc")) {   // UKismetSystemLibrary::CollectGarbage: full purge at the end of this frame
        UClass *k = ue_find_class("KismetSystemLibrary");
        UFunction *f = k ? ue_find_function(k, "CollectGarbage") : NULL;
        if (!f) { out_printf(o, "fxsig: no CollectGarbage\n"); return 1; }
        uint8_t p[16] = {0};
        ue_process_event(UC_CDO(k), f, p);
        out_printf(o, "fxsig: garbage collection requested\n");
        return 1;
    }
    if (a && !strcmp(a, "kill") && b) {   // K2_DestroyActor on every actor whose name contains <b> (repro helper)
        UClass *ac = ue_find_class("Actor");
        int k = 0;
        char nm[160];
        for (int32_t i = 0, m = ue_num_objects(); i < m; i++) {
            UObject *x = ue_object_at(i);
            if (!x || (U_FLAGS(x) & 0x10) || !ue_is_a(x, ac) || !strstr(ue_obj_name(x, nm, sizeof nm), b)) continue;
            UFunction *f = ue_find_function(U_CLASS(x), "K2_DestroyActor");
            if (!f || (U_FLAGS(x) & 0x30)) continue;   // not CDOs / archetypes
            uint8_t p[16] = {0};
            ue_process_event(x, f, p);
            out_printf(o, "  destroyed %s\n", nm);
            k++;
        }
        out_printf(o, "fxsig: destroyed %d actor(s)\n", k);
        return 1;
    }
    int fix = a && !strcmp(a, "fix");
    const char *sub = a && !strcmp(a, "list") ? b : NULL;   // fxsig list <path part>: every matching PSC with its flags
    int listed = 0;
    static UClass *cls;
    if (!cls) cls = ue_find_class("ParticleSystemComponent");
    if (!cls) { out_printf(o, "fxsig: no ParticleSystemComponent class\n"); return 1; }
    int n = 0, reg = 0, act = 0, orphans = 0, fixed = 0;
    for (int32_t i = 0, m = ue_num_objects(); i < m; i++) {
        UObject *c = ue_object_at(i);
        if (!c || (U_FLAGS(c) & 0x10) || !ue_is_a(c, cls)) continue;   // RF_ClassDefaultObject
        n++;
        if (PSC_SIGNIFICANT(c)) reg++;
        if (PSC_ACTIVE(c)) act++;
        if (sub && listed < 60) {
            char fp[512];
            if (strstr(ue_full_path(c, fp, sizeof fp), sub)) {
                out_printf(o, "  %p c0=%02x c2=%02x 4f1=%02x %s\n", (void *)c, *((uint8_t *)c + 0xC0), *((uint8_t *)c + 0xC2),
                           *((uint8_t *)c + 0x4F1), fp);
                listed++;
            }
        }
        if (PSC_SIGNIFICANT(c) && !PSC_ACTIVE(c)) {
            orphans++;
            char d[400];
            describe(c, d, sizeof d);
            if (orphans <= 40) out_printf(o, "  orphan %p flags %02x %02x: %s\n", (void *)c, *((uint8_t *)c + 0xC0), *((uint8_t *)c + 0xC2), d);
            if (fix) { sig_activation(c, 0); fixed += !PSC_SIGNIFICANT(c); }
        }
    }
    out_printf(o, "fxsig: guard %s (hooked %d), %d PSCs, %d registered, %d active, %d registered but inactive%s; guard unregistered %u\n",
               guard_on ? "on" : "off", orig_reset != NULL, n, reg, act, orphans, fix ? "" : " (fxsig fix: unregister them)", n_fixed);
    if (fix) out_printf(o, "fxsig: unregistered %d\n", fixed);
    return 1;
}
#endif
