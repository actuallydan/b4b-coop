// /thirdperson: over-the-shoulder view of your own hero, first person while aiming (#25). A personal command: every
// player (host or client) may use it, no cheats needed. docs/investigations/third-person.md.
//
// The hero's PlayerViewComponent owns both camera setups (Third/FirstPersonViewConfig: camera, spring arm and mesh
// tags). Which one is active comes from its requested-view byte (+0x200: 1 first person, 2 third person, 3 orbit),
// written only by OnOwnerTagChange (0x141C26C40: from the owner's gameplay tags vs ThirdPersonTags /
// ThirdPersonOrbitTags) and applied by UpdateView(this, bool bForce) (0x141C27250), which swaps camera, meshes and
// broadcasts OnViewChanged (weapons, flashlight, audio, anim follow). We write the byte and call UpdateView ourselves:
// no gameplay tag is added (tags carry gameplay meaning), nothing is replicated (every machine picks the view of its
// own hero locally; others always see the 3P body), so it only changes the local player's own camera. Local only on
// either side: no protocol change.
//
// Lifetime: on until /thirdperson again (or the game quits); it follows the local player to every new hero (next
// chapter, camp, another host's session, a bot take-over). Not saved; b4bcoop.ini `thirdperson=1` starts with it on.
// `thirdperson_key=N` (default; same values as flashlight_key, `off` = no key) toggles it.
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>
#include "ue.h"
#include "log.h"
#include "cmds.h"
#include "MinHook.h"
#include "overlay.h"

#define ADDR_PVC_UPDATE VA(0x141C27250ull)
static const uint8_t SIG_PVC_UPDATE[] = {0x48,0x8b,0xc4,0x57,0x41,0x54,0x48,0x83,0xec,0x68,0x48,0x83,0xb9,0xf8,0x01,0x00,
                                         0x00,0x00,0x44,0x0f,0xb6,0xe2};
typedef void (*PvcUpdateFn)(UObject *pvc, uint8_t force);
#define PVC_WANT(p)    (*((uint8_t *)(p) + 0x200))   // requested view (1 FP, 2 TP, 3 orbit)
// +0x201 = view UpdateView applied last, +0x215 = IsThirdPerson() (docs/investigations/third-person.md)
#define COMP_OWNER(c)  (*(UObject **)((char *)(c) + 0xD8))   // UActorComponent::OwnerPrivate (unreflected)
#define LIVE_FLAGS 0x30                                      // RF_ClassDefaultObject | RF_ArchetypeObject

typedef struct { UObject *c; int32_t i; } Ref;
static int tp_on;                  // /thirdperson on (local hero)
static uint8_t tp_written;         // the view byte we last wrote on this hero (0 = none)
static UObject *tp_pawn, *tp_pvc;  // cached for the current local hero
static int32_t tp_pawni = -1, tp_pvci = -1;
static Ref tp_ads[16];             // the hero's ADSComponents (one per weapon), refreshed every second
static int n_tp_ads;
static float tp_ads_age;
static int tp_key = 'N';           // ini thirdperson_key (VK), 0 = none

// Camera tuning (accessibility): the hero's ThirdPersonSpringArm (SpringArmComponent: TargetArmLength +0x230,
// SocketOffset +0x234, the game's own: 300 / 0,0,0) and ThirdPersonCamera (CameraComponent.FieldOfView +0x230).
// Applied every tick while /thirdperson is on (cheap float writes, so a new hero or a reset by the game is covered),
// the game's values are put back when it goes off. Shots come from the hero's eyes, not the camera (measured, see
// third-person.md): side/height move the camera off the line of fire; the aim correction below turns the eyes back
// onto the crosshair point (without it, hits land that far beside the crosshair).
#define TP_DIST_DEF 180.f
#define TP_SIDE_DEF 40.f     // over the right shoulder by default (aim correction puts hits under the crosshair)
static float tp_dist = TP_DIST_DEF, tp_side = TP_SIDE_DEF, tp_height, tp_fov;   // tp_fov 0 = the game's
static UObject *tp_arm, *tp_cam;                                     // on tp_pawn
static int32_t tp_armi = -1, tp_cami = -1;
static float tp_arm_orig[4] = {-1}, tp_fov_orig = -1;               // length, socket xyz; FOV (-1 = not saved)
#define ARM_LEN(a)    ((float *)((char *)(a) + 0x230))
#define ARM_SOCKET(a) ((float *)((char *)(a) + 0x234))
#define CAM_FOV(c)    ((float *)((char *)(c) + 0x230))

typedef struct { UObject *obj; UFunction *fn; uint8_t p[1024]; } TpCall;
static void *tc_prep(TpCall *c, UObject *obj, const char *fname) {
    c->obj = obj;
    c->fn = obj ? ue_find_function(U_CLASS(obj), fname) : NULL;
    if (!c->fn || UFN_PARMSSIZE(c->fn) > sizeof c->p) return NULL;
    memset(c->p, 0, sizeof c->p);
    return c;
}
static void *tc_arg(TpCall *c, const char *name) { FField *f = ue_find_prop(c->fn, name); return f ? c->p + FP_OFFSET(f) : NULL; }
static void rot_dir(const float r[3], float d[3]) {   // FRotator (pitch, yaw, roll) degrees -> unit vector
    float p = r[0] * 3.14159265f / 180.f, y = r[1] * 3.14159265f / 180.f;
    d[0] = cosf(p) * cosf(y); d[1] = cosf(p) * sinf(y); d[2] = sinf(p);
}
// KismetSystemLibrary::LineTraceSingle along d (unit) for len units on trace channel chan (ETraceTypeQuery: 0 =
// Visibility), ignoring the pawn: the first blocking point, 0 = none. The function and parameter offsets are looked up
// once (the aim correction runs it every frame).
static int32_t trace_hit_actor = -1;   // object index of the last trace_ch hit's actor
static int trace_ch(UObject *pawn, const float s[3], const float d[3], float len, int chan, float hit[3]) {
    static UObject *cdo; static UFunction *fn; static int32_t o_ctx, o_st, o_en, o_ch, o_self, o_hit, psize;
    if (!fn) {
        UClass *k = ue_find_class("KismetSystemLibrary");
        TpCall c;
        if (!k || !tc_prep(&c, UC_CDO(k), "LineTraceSingle")) return 0;
        uint8_t *b0 = c.p, *pc = tc_arg(&c, "WorldContextObject"), *ps = tc_arg(&c, "Start"), *pe = tc_arg(&c, "End"),
                *pch = tc_arg(&c, "TraceChannel"), *pi = tc_arg(&c, "bIgnoreSelf"), *ph = tc_arg(&c, "OutHit");
        if (!pc || !ps || !pe || !pch || !pi || !ph) return 0;
        o_ctx = (int32_t)(pc - b0); o_st = (int32_t)(ps - b0); o_en = (int32_t)(pe - b0); o_ch = (int32_t)(pch - b0);
        o_self = (int32_t)(pi - b0); o_hit = (int32_t)(ph - b0); psize = UFN_PARMSSIZE(c.fn);
        cdo = c.obj; fn = c.fn;
    }
    uint8_t p[1024];
    memset(p, 0, psize);
    *(UObject **)(p + o_ctx) = pawn;
    float *st = (float *)(p + o_st), *en = (float *)(p + o_en);
    for (int i = 0; i < 3; i++) { st[i] = s[i]; en[i] = s[i] + d[i] * len; }
    p[o_ch] = (uint8_t)chan;
    p[o_self] = 1;
    ue_process_event(cdo, fn, p);
    uint8_t *h = p + o_hit;
    if (!(h[0] & 1)) return 0;
    memcpy(hit, h + 0x1c, 12);   // HitResult.ImpactPoint
    trace_hit_actor = *(int32_t *)(h + 0x68);   // HitResult.Actor (weak pointer: object index first)
    return 1;
}
static int trace(UObject *pawn, const float s[3], const float d[3], float hit[3]) { return trace_ch(pawn, s, d, 50000.f, 0, hit); }
static float dist3(const float a[3], const float b[3]) {
    float x = a[0] - b[0], y = a[1] - b[1], z = a[2] - b[2];
    return sqrtf(x * x + y * y + z * z);
}
static int alive(UObject *o, int32_t idx) { return o && idx >= 0 && ue_object_at(idx) == o; }
static UClass *cls(const char *name) {   // /Script classes never unload
    static UClass *pvc, *ads, *hero;
    UClass **slot = !strcmp(name, "PlayerViewComponent") ? &pvc : !strcmp(name, "ADSComponent") ? &ads : &hero;
    if (!*slot) *slot = ue_find_class(name);
    return *slot;
}

static UObject *local_hero(void) {
    UObject *pc = ue_local_pc(), *p = pc ? ue_get_ptr(pc, "Pawn") : NULL;
    UClass *hc = cls("HeroCharacter");
    return p && hc && ue_is_a(p, hc) ? p : NULL;
}
// the hero's PlayerViewComponent; a new hero (map change, take-over) starts over (scanned once per hero)
static UObject *view_comp(UObject *pawn) {
    if (!pawn) return NULL;
    if (pawn == tp_pawn && alive(tp_pawn, tp_pawni) && (!tp_pvc || alive(tp_pvc, tp_pvci))) return tp_pvc;
    UClass *c = cls("PlayerViewComponent");
    UClass *sa = ue_find_class("SpringArmComponent"), *cc = ue_find_class("CameraComponent");
    tp_pawn = pawn; tp_pawni = U_INDEX(pawn); tp_pvc = NULL; tp_pvci = -1; tp_ads_age = 99.f; tp_written = 0; n_tp_ads = 0;
    tp_arm = tp_cam = NULL; tp_armi = tp_cami = -1; tp_arm_orig[0] = -1; tp_fov_orig = -1;
    char nm[64];
    for (int32_t i = 0, n = ue_num_objects(); c && i < n; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & LIVE_FLAGS)) continue;   // class first: +0xD8 is only the owner on components
        if (!tp_pvc && ue_is_a(x, c)) { if (COMP_OWNER(x) == pawn) { tp_pvc = x; tp_pvci = i; } }
        else if (!tp_arm && sa && ue_is_a(x, sa)) {
            if (COMP_OWNER(x) == pawn && !strcmp(ue_obj_name(x, nm, sizeof nm), "ThirdPersonSpringArm")) { tp_arm = x; tp_armi = i; }
        } else if (!tp_cam && cc && ue_is_a(x, cc)) {
            if (COMP_OWNER(x) == pawn && !strcmp(ue_obj_name(x, nm, sizeof nm), "ThirdPersonCamera")) { tp_cam = x; tp_cami = i; }
        }
    }
    return tp_pvc;
}
static void ads_refresh(UObject *pawn) {
    UClass *c = cls("ADSComponent");
    n_tp_ads = 0;
    for (int32_t i = 0, n = ue_num_objects(); c && pawn && i < n && n_tp_ads < 16; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & LIVE_FLAGS) || !ue_is_a(x, c)) continue;
        UObject *w = COMP_OWNER(x);
        if (w && (w == pawn || ue_get_ptr(w, "Owner") == pawn)) { tp_ads[n_tp_ads].c = x; tp_ads[n_tp_ads++].i = i; }
    }
}
static int hero_ads(void) {   // any of the hero's weapons held in ADS (ADSComponent.bIsHoldingADS)
    static int32_t off = -2;
    for (int i = 0; i < n_tp_ads; i++) {
        if (!alive(tp_ads[i].c, tp_ads[i].i)) continue;
        if (off == -2) off = ue_prop_offset(tp_ads[i].c, "bIsHoldingADS");
        if (off >= 0 && *((uint8_t *)tp_ads[i].c + off)) return 1;
    }
    return 0;
}
static int view_set(UObject *pvc, uint8_t want) {
    static int sig = -1;
    if (!pvc) return 0;
    if (sig < 0 && !(sig = !memcmp((void *)ADDR_PVC_UPDATE, SIG_PVC_UPDATE, sizeof SIG_PVC_UPDATE)))
        LOG("thirdperson: UpdateView signature mismatch");
    if (!sig) return 0;
    PVC_WANT(pvc) = want;
    ((PvcUpdateFn)ADDR_PVC_UPDATE)(pvc, 1);
    return 1;
}
// camera tuning on the current hero (apply = 1) or the game's own values back (apply = 0)
static void tune(int apply) {
    if (tp_arm && alive(tp_arm, tp_armi)) {
        float *len = ARM_LEN(tp_arm), *so = ARM_SOCKET(tp_arm);
        if (apply) {
            if (tp_arm_orig[0] < 0) { tp_arm_orig[0] = *len; memcpy(tp_arm_orig + 1, so, 12); }
            *len = tp_dist; so[1] = tp_side; so[2] = tp_height;
        } else if (tp_arm_orig[0] >= 0) { *len = tp_arm_orig[0]; memcpy(so, tp_arm_orig + 1, 12); tp_arm_orig[0] = -1; }
    }
    if (tp_cam && alive(tp_cam, tp_cami)) {
        float *fov = CAM_FOV(tp_cam);
        if (apply && tp_fov > 0) { if (tp_fov_orig < 0) tp_fov_orig = *fov; *fov = tp_fov; }
        else if (tp_fov_orig > 0) { *fov = tp_fov_orig; tp_fov_orig = -1; }
    }
}

// ---- Aim correction (#25) ----
// Shots come from the hero's eyes along GetActorEyesViewPoint (measured with bullet-hole decals), while an
// over-the-shoulder camera looks along a parallel ray `side`/`height` units away, so hits used to land that far from
// the crosshair. Fix: find the point under the crosshair (a trace along the camera ray, starting beside the eyes so
// nothing between the camera and the hero counts) and turn the local hero's eye rotation towards it: the shot still
// starts at the eyes, it just aims at what the crosshair covers. Only the local hero, only on the game thread, only
// in our third person (not while aiming: first person, the camera is the eyes). third-person.md "Aim correction".
#define VT_EYES    (0x5F0 / 8)   // AActor::GetActorEyesViewPoint(this, FVector *, FRotator *) (exec thunk 0x1441ABEC0)
#define VT_BASEAIM (0x6E0 / 8)   // APawn::GetBaseAimRotation(this, FRotator *ret) -> ret (exec thunk 0x1443A82D0)
typedef void (*EyesFn)(UObject *self, float *loc, float *rot);
typedef float *(*BaseAimFn)(UObject *self, float *ret);
static EyesFn orig_eyes;
static BaseAimFn orig_baseaim;
static void *eyes_at, *baseaim_at;
static int aim_hooked;          // 1 hooked, -1 failed
static DWORD tp_tid;            // the game thread
static UObject *aim_pawn;       // the local hero this frame (the detours act only on it)
static int aim_fix = 1;         // ini thirdperson_aimfix
static int aim_use = 1;         // which call gets corrected: 1 GetActorEyesViewPoint, 2 GetBaseAimRotation (dev)
static int aim_chan;            // trace channel for the crosshair point (ETraceTypeQuery, 0 = Visibility)
static int aim_ok;              // this frame: third person with an offset camera, aim_local valid
static float aim_local[3];      // camera relative to the eyes in the view frame (forward, right, up), last frame
static unsigned aim_frame, aim_cache_frame;
static float aim_cache_key[6], aim_cache_rot[3];
static int aim_n;               // corrections applied (dev status)
static float aim_last_p[3], aim_last_deg;   // last crosshair point and correction angle (dev status)
#ifndef B4B_RELEASE
static float aim_test[2];       // dev `thirdperson aimtest`: extra yaw on eyes / base aim, to find the fire path
static float probe_left;        // dev `thirdperson callers`: record who asks for the local hero's view point
static int probe_all;           // ... `callers <s> all`: other heroes too (slot +2: e.g. a client's hero on the host)
static struct { void *ra; int slot, n; } probe_ra[48];
static int n_probe_ra;
static void probe_note(int slot, void *ra) {
    if (probe_left <= 0) return;
    for (int i = 0; i < n_probe_ra; i++) if (probe_ra[i].ra == ra && probe_ra[i].slot == slot) { probe_ra[i].n++; return; }
    if (n_probe_ra < 48) { probe_ra[n_probe_ra].ra = ra; probe_ra[n_probe_ra].slot = slot; probe_ra[n_probe_ra++].n = 1; }
}
#endif

static void basis(const float r[3], float f[3], float rt[3], float up[3]) {   // FRotator (roll ignored) -> axes
    float p = r[0] * 3.14159265f / 180.f, y = r[1] * 3.14159265f / 180.f, sp = sinf(p), cp = cosf(p), sy = sinf(y), cy = cosf(y);
    f[0] = cp * cy; f[1] = cp * sy; f[2] = sp;
    rt[0] = -sy; rt[1] = cy; rt[2] = 0;
    up[0] = -sp * cy; up[1] = -sp * sy; up[2] = cp;
}
static float dot3(const float a[3], const float b[3]) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

// eye rotation `rot` at eye location `loc` -> towards the point under the crosshair (in place)
static void aim_correct(const float loc[3], float rot[3]) {
    if (!aim_ok) return;
    if (aim_cache_frame == aim_frame && !memcmp(aim_cache_key, loc, 12) && !memcmp(aim_cache_key + 3, rot, 12)) {
        memcpy(rot, aim_cache_rot, 12);
        return;
    }
    float f[3], rt[3], up[3], s[3], hit[3], d[3];
    basis(rot, f, rt, up);
    for (int i = 0; i < 3; i++) s[i] = loc[i] + rt[i] * aim_local[1] + up[i] * aim_local[2];   // camera ray, beside the eyes
    memcpy(aim_cache_key, loc, 12); memcpy(aim_cache_key + 3, rot, 12);
    aim_cache_frame = aim_frame;
    if (!trace_ch(aim_pawn, s, f, 50000.f, aim_chan, hit)) for (int i = 0; i < 3; i++) hit[i] = s[i] + f[i] * 50000.f;
    for (int i = 0; i < 3; i++) d[i] = hit[i] - loc[i];
    float n = sqrtf(dot3(d, d));
    if (n < 1.f || dot3(d, f) < 0.5f * n) { memcpy(aim_cache_rot, rot, 12); return; }   // behind / sideways: leave it
    for (int i = 0; i < 3; i++) d[i] /= n;
    float np = asinf(d[2] > 1 ? 1 : d[2] < -1 ? -1 : d[2]) * 180.f / 3.14159265f, ny = atan2f(d[1], d[0]) * 180.f / 3.14159265f;
    float c = dot3(d, f);
    aim_last_deg = acosf(c > 1 ? 1 : c) * 180.f / 3.14159265f;
    memcpy(aim_last_p, hit, 12);
    rot[0] = np; rot[1] = ny;
    memcpy(aim_cache_rot, rot, 12);
    aim_n++;
}
static int aim_here(UObject *self) { return self == aim_pawn && self && GetCurrentThreadId() == tp_tid; }
static void eyes_detour(UObject *self, float *loc, float *rot) {
    orig_eyes(self, loc, rot);
#ifndef B4B_RELEASE
    if (probe_all && self != aim_pawn && GetCurrentThreadId() == tp_tid && ue_is_a(self, cls("HeroCharacter")))
        probe_note(2, __builtin_return_address(0));
#endif
    if (!aim_here(self)) return;
#ifndef B4B_RELEASE
    probe_note(0, __builtin_return_address(0));
    rot[1] += aim_test[0];
#endif
    if (aim_use & 1) aim_correct(loc, rot);
}
static float *baseaim_detour(UObject *self, float *ret) {
    float *r = orig_baseaim(self, ret);
#ifndef B4B_RELEASE
    if (probe_all && self != aim_pawn && GetCurrentThreadId() == tp_tid && ue_is_a(self, cls("HeroCharacter")))
        probe_note(3, __builtin_return_address(0));
#endif
    if (!aim_here(self)) return r;
#ifndef B4B_RELEASE
    probe_note(1, __builtin_return_address(0));
    r[1] += aim_test[1];
#endif
    if ((aim_use & 2) && aim_ok) {
        float loc[3], tmp[3];
        (orig_eyes ? orig_eyes : (EyesFn)U_VTBL(self)[VT_EYES])(self, loc, tmp);
        aim_correct(loc, r);
    }
    return r;
}
// hooks on the hero class's own GetActorEyesViewPoint / GetBaseAimRotation (vtable entries of the live hero: every
// hero blueprint shares the native class's vtable), installed the first time third person is used
static void aim_hook(UObject *pawn) {
    if (aim_hooked || !pawn) return;
    void **vt = U_VTBL(pawn);
    eyes_at = vt[VT_EYES]; baseaim_at = vt[VT_BASEAIM];
    int ok = eyes_at && baseaim_at &&
             MH_CreateHook(eyes_at, (void *)eyes_detour, (void **)&orig_eyes) == MH_OK && MH_EnableHook(eyes_at) == MH_OK &&
             MH_CreateHook(baseaim_at, (void *)baseaim_detour, (void **)&orig_baseaim) == MH_OK &&
             MH_EnableHook(baseaim_at) == MH_OK;
    aim_hooked = ok ? 1 : -1;
    LOG("thirdperson: aim hooks %s (eyes 0x%llx, base aim 0x%llx)", ok ? "installed" : "FAILED",
        (unsigned long long)((uintptr_t)eyes_at - g_base_delta), (unsigned long long)((uintptr_t)baseaim_at - g_base_delta));
}
// every tick: where the camera sits relative to the eyes (the camera manager's POV of the last frame; the detours
// rebuild the camera from it with the current rotation, so turning or moving this frame doesn't lag)
static void aim_update(UObject *pawn, UObject *pvc) {
    static int32_t o_cache = -2;
    aim_ok = 0;
    aim_frame++;
    tp_tid = GetCurrentThreadId();
    aim_pawn = pawn;
    if (!pawn || !pvc || !aim_fix || !tp_on || !*((uint8_t *)pvc + 0x215)) return;   // +0x215: IsThirdPerson()
    aim_hook(pawn);
    if (aim_hooked != 1) return;
    UObject *pc = ue_local_pc(), *pcm = pc ? ue_get_ptr(pc, "PlayerCameraManager") : NULL;
    if (!pcm) return;
    if (o_cache == -2) o_cache = ue_prop_offset(pcm, "CameraCachePrivate");
    if (o_cache < 0) return;
    float *cam = (float *)((char *)pcm + o_cache + 0x10), *crot = cam + 3;   // CameraCacheEntry.POV Location, Rotation
    float el[3], er[3], f[3], rt[3], up[3], d[3];
    orig_eyes(pawn, el, er);
    basis(crot, f, rt, up);
    for (int i = 0; i < 3; i++) d[i] = cam[i] - el[i];
    aim_local[0] = dot3(d, f); aim_local[1] = dot3(d, rt); aim_local[2] = dot3(d, up);
    float lat = sqrtf(aim_local[1] * aim_local[1] + aim_local[2] * aim_local[2]);
    aim_ok = lat > 0.5f && lat < 400.f && aim_local[0] < 0;   // offset camera behind the eyes (not centred, not 1P)
}

// ---- Item pickups in third person (#27) ----
// Weapons, items and other ItemPickups become usable (prompt, tooltip, F) only while the hero's ItemObserverComponent
// "observes" them: ItemPickupUsableComponent's CanUse (vtable +0x410, strict call) needs the observer's entry in the
// pickup's ItemObservableComponent.ObservableStates. The observer switches itself off in third person: its refresh
// (0x1418F3600, run on OnViewChanged and OnUIScreenOpened) sets ObserverComponent.bEnabled (+0x108) = no blocking UI
// screen && ... && !PlayerViewComponent.IsThirdPerson (its weak pointer +0x138, byte +0x215), because the game's own
// third-person moments (healing, grabbed) are not meant for looting. In our third person we run that refresh with the
// view byte reading first person, so every other condition (open screens etc.) still decides. Local only: the host
// never sees it (clients observe and pick up through their own game; the pickup request is the retail RPC).
#define ADDR_OBS_REFRESH VA(0x1418F3600ull)
static const uint8_t SIG_OBS_REFRESH[] = {0x48,0x89,0x74,0x24,0x20,0x57,0x48,0x83,0xec,0x20,0x48,0x8d,0xb9,0x40,0x01,0x00,
                                          0x00,0x48,0x8b,0xf1,0x48,0x8b,0xcf,0xe8};
typedef void (*ObsRefreshFn)(UObject *obs);
static ObsRefreshFn orig_obs_refresh;
static int obs_hooked;   // 1 hooked, -1 failed / signature mismatch
static int item_fix = 1; // dev `thirdperson itemfix 0|1` (both parts of the #27 fix)
static unsigned obs_n;   // refreshes run as first person (dev status)
static void obs_refresh_detour(UObject *obs) {
    int32_t wi = *(int32_t *)((char *)obs + 0x138);   // ItemObserverComponent -> PlayerViewComponent (weak: index first)
    UObject *pvc = wi > 0 && wi < ue_num_objects() ? ue_object_at(wi) : NULL;
    uint8_t *is3p = pvc ? (uint8_t *)pvc + 0x215 : NULL;
    if (!item_fix || !tp_on || !pvc || pvc != tp_pvc || !alive(tp_pvc, tp_pvci) || PVC_WANT(pvc) != 2 || !*is3p ||
        GetCurrentThreadId() != tp_tid) {
        orig_obs_refresh(obs);
        return;
    }
    *is3p = 0;
    orig_obs_refresh(obs);
    *is3p = 1;
    obs_n++;
}
static void obs_hook(void) {
    if (obs_hooked) return;
    int ok = !memcmp((void *)ADDR_OBS_REFRESH, SIG_OBS_REFRESH, sizeof SIG_OBS_REFRESH) &&
             MH_CreateHook((void *)ADDR_OBS_REFRESH, (void *)obs_refresh_detour, (void **)&orig_obs_refresh) == MH_OK &&
             MH_EnableHook((void *)ADDR_OBS_REFRESH) == MH_OK;
    obs_hooked = ok ? 1 : -1;
    LOG("thirdperson: item observer hook %s", ok ? "installed" : "FAILED (signature mismatch?)");
}
static UObject *hero_observer(UObject *pawn) {   // HeroCharacter.ItemObserverComponent
    static int32_t off = -2;
    if (!pawn) return NULL;
    if (off == -2) off = ue_prop_offset(pawn, "ItemObserverComponent");
    return off >= 0 ? *(UObject **)((char *)pawn + off) : NULL;
}
#ifndef B4B_RELEASE
// run the local hero's observer refresh now (dev `thirdperson itemfix`)
static void obs_kick(void) {
    UObject *obs = hero_observer(local_hero());
    if (obs && obs_hooked == 1 && GetCurrentThreadId() == tp_tid) obs_refresh_detour(obs);
}
#endif
// Where the observer looks from: the observation system gathers every observer's view once per frame (0x140ECC480,
// game thread) into records of 0x68 bytes in its sparse array at +0x40: +0x18 the ObserverComponent, +0x20 location,
// +0x2c rotation, +0x38 direction. For our hero it takes ObserverComponent.ViewComponent (the FirstPersonCamera):
// the eyes, looking along the control rotation, i.e. parallel to our offset camera's crosshair ray. An item's rule is
// a ray-sphere test (radius 30, 0-300 units), so an item under the crosshair at arm's length is missed by the side
// offset (40 by default). After the gather, in our offset 3P, our record gets the eyes' view point as the aim
// correction turns it: from the eyes toward the point under the crosshair. (Clearing ViewComponent would make the
// gather use the eyes too, but the observer's own tick needs it to pick the tooltip to show.)
#define ADDR_OBS_GATHER VA(0x140ECC480ull)
static const uint8_t SIG_OBS_GATHER[] = {0x40,0x55,0x53,0x41,0x55,0x48,0x8d,0x6c,0x24,0xb9,0x48,0x81,0xec,0xf0,0x00,0x00,
                                         0x00,0x45,0x33,0xed,0xc7,0x44,0x24,0x24};
typedef void (*ObsGatherFn)(void *sys);
static ObsGatherFn orig_obs_gather;
static int gather_hooked;
static UObject *obs_aim;    // this frame: the local observer whose view gets the corrected eyes (NULL = none)
static unsigned gather_n;   // records patched (dev status)
static void obs_gather_detour(void *sys) {
    orig_obs_gather(sys);
    UObject *obs = obs_aim, *pawn = aim_pawn;
    if (!obs || !pawn || !orig_eyes || GetCurrentThreadId() != tp_tid) return;
    TArray *d = (TArray *)((char *)sys + 0x40);
    for (int32_t i = 0; d->data && i < d->num; i++) {
        uint8_t *r = (uint8_t *)d->data + (size_t)i * 0x68;
        if (*(UObject **)(r + 0x18) != obs) continue;
        float loc[3], rot[3], dir[3];
        orig_eyes(pawn, loc, rot);
        aim_correct(loc, rot);
        rot_dir(rot, dir);
        memcpy(r + 0x20, loc, 12); memcpy(r + 0x2c, rot, 12); memcpy(r + 0x38, dir, 12);
        gather_n++;
    }
}
static void obs_view_hook(void) {
    if (gather_hooked) return;
    int ok = !memcmp((void *)ADDR_OBS_GATHER, SIG_OBS_GATHER, sizeof SIG_OBS_GATHER) &&
             MH_CreateHook((void *)ADDR_OBS_GATHER, (void *)obs_gather_detour, (void **)&orig_obs_gather) == MH_OK &&
             MH_EnableHook((void *)ADDR_OBS_GATHER) == MH_OK;
    gather_hooked = ok ? 1 : -1;
    LOG("thirdperson: observation view hook %s", ok ? "installed" : "FAILED (signature mismatch?)");
}
// every tick: which observer (if any) looks along the corrected eyes this frame
static void obs_view_sync(void) {
    UObject *pawn = aim_pawn;
    obs_aim = NULL;
    if (!item_fix || !aim_ok || !pawn || pawn != tp_pawn || !alive(tp_pvc, tp_pvci) || PVC_WANT(tp_pvc) != 2) return;
    obs_view_hook();
    if (gather_hooked == 1) obs_aim = hero_observer(pawn);
}

// every tick while on: third person, first person while aiming. A view the game itself asked for (a tag-driven 2 we
// didn't write: healing, pounced, grabbed, ...; orbit 3) is left alone; when the game drops back to 1 we write 2 again.
static void tp_sync(float dt) {
    UObject *pawn = local_hero(), *pvc = view_comp(pawn);
    tp_tid = GetCurrentThreadId();
    obs_hook();   // before our first view switch, so its OnViewChanged refresh already sees it
    aim_update(pvc ? pawn : NULL, pvc);
    if (!pvc) return;
    if ((tp_ads_age += dt) > 1.f) { tp_ads_age = 0; ads_refresh(pawn); }
    tune(1);
    uint8_t cur = PVC_WANT(pvc), want = hero_ads() ? 1 : 2;
    if (cur == want || cur == 3 || (cur == 2 && tp_written != 2)) return;
    if (view_set(pvc, want)) tp_written = want;
}

#ifndef B4B_RELEASE
static void watch_tick(float dt);
#endif
void thirdperson_tick(float dt) {
#ifndef B4B_RELEASE
    watch_tick(dt);
#endif
    static int was_down;
    if (tp_key) {
        int down = cmds_hotkey_down(tp_key);
        if (down && !was_down) {
            static Out tmp;
            out_reset(&tmp);
            cmd_thirdperson(NULL, &tmp);
            LOG("thirdperson: hotkey: %.*s", (int)strcspn(tmp.buf, "\n"), tmp.buf);
        }
        was_down = down;
    }
    if (tp_on) tp_sync(dt);
    obs_view_sync();
    if (!tp_on) {
        aim_ok = 0;
#ifndef B4B_RELEASE
        if (probe_left > 0 || aim_test[0] || aim_test[1]) { aim_update(local_hero(), NULL); }
        if (probe_left > 0 && (probe_left -= dt) <= 0) LOG("thirdperson: callers: recording done (%d)", n_probe_ra);
#endif
    }
#ifndef B4B_RELEASE
    if (tp_on && probe_left > 0 && (probe_left -= dt) <= 0) LOG("thirdperson: callers: recording done (%d)", n_probe_ra);
#endif
}

static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

// b4bcoop.ini: thirdperson=1 (start in third person), thirdperson_key=N (toggle key; off = none), the camera settings,
// thirdperson_aimfix=0 (no aim correction). All of them also change live (cmds_ini_poll, val NULL = removed: the
// default): thirdperson=0/1 switches the view like /thirdperson off/on, the camera settings apply at once.
static int tp_started;   // thirdperson_init done: later calls are live changes
int thirdperson_live(const char *key, const char *v) {
    if (!strcmp(key, "thirdperson")) {
        int on = v && atoi(v) != 0;
        if (!tp_started) tp_on = on;
        else if (on != tp_on) { static Out tmp; out_reset(&tmp); cmd_thirdperson(on ? "on" : "off", &tmp); }
        return 1;
    }
    if (!strcmp(key, "thirdperson_key")) tp_key = v ? cmds_parse_key(v) : 'N';
    else if (!strcmp(key, "thirdperson_distance")) tp_dist = v ? clampf((float)atof(v), 50, 600) : TP_DIST_DEF;
    else if (!strcmp(key, "thirdperson_side")) tp_side = v ? clampf((float)atof(v), -150, 150) : TP_SIDE_DEF;
    else if (!strcmp(key, "thirdperson_height")) tp_height = v ? clampf((float)atof(v), -100, 150) : 0;
    else if (!strcmp(key, "thirdperson_fov")) tp_fov = v && atof(v) > 0 ? clampf((float)atof(v), 60, 130) : 0;
    else if (!strcmp(key, "thirdperson_aimfix")) { aim_fix = v ? atoi(v) != 0 : 1; return 1; }
    else return 0;
    if (tp_started) {   // camera settings: at once, like the chat command
        if (tp_fov <= 0) tune(0);
        if (tp_on) tune(1);
    }
    return 1;
}
static void ini_pair(const char *k, const char *v, void *ctx) { (void)ctx; thirdperson_live(k, v); }
int thirdperson_hotkey(void) { return tp_key; }

// ~ overlay tab (overlay.h): the view is an action (like /thirdperson, not saved); camera settings and keys are
// b4bcoop.ini settings, applied live through thirdperson_live and saved when an edit ends.
static void tp_slider(const char *label, const char *key, float cur, float lo, float hi) {
    float v = cur;
    ov_width(14);
    if (ov_slider(label, &v, lo, hi, "%.0f")) ov_setting_f(key, v, 0);
    if (ov_edit_done()) ov_setting_f(key, v, 1);
}
static void tp_panel(void) {
    int on = tp_on;
    if (ov_checkbox("Third person##view", &on)) ov_run(on ? "thirdperson on" : "thirdperson off");
    ov_tooltip("Your own over-the-shoulder camera (like /thirdperson). Aiming with right mouse is first person.");
    const char *iv = cmds_ini_value("thirdperson");
    int start = iv && atoi(iv) != 0;
    if (ov_checkbox("Start the game in third person##start", &start)) {
        int bad = cmds_ini_set("thirdperson", start ? "1" : NULL);
        overlay_note(bad ? "could not write b4bcoop.ini" : start ? "b4bcoop.ini: thirdperson=1" : "b4bcoop.ini: thirdperson back to the default (off)");
    }
    int k = tp_key;
    if (ov_key("Toggle key##thirdperson_key", &k)) ov_setting_key("thirdperson_key", k);
    ov_heading("Camera");
    tp_slider("Distance##distance", "thirdperson_distance", tp_dist, 50, 600);
    tp_slider("Side (- left, + right)##side", "thirdperson_side", tp_side, -150, 150);
    tp_slider("Height##height", "thirdperson_height", tp_height, -100, 150);
    tp_slider("FOV (0 = the game's)##fov", "thirdperson_fov", tp_fov, 0, 130);
    if (ov_button("Swap shoulder")) ov_setting_f("thirdperson_side", -tp_side, 1);
    ov_same_line();
    if (ov_button("Reset camera")) {
        static const char *K[] = {"thirdperson_distance", "thirdperson_side", "thirdperson_height", "thirdperson_fov"};
        for (int i = 0; i < 4; i++) ov_setting(K[i], NULL, 1);
    }
    int fix = aim_fix;
    if (ov_checkbox("Aim correction##aimfix", &fix)) ov_setting("thirdperson_aimfix", fix ? "1" : "0", 1);
    ov_tooltip("Turns your hero's aim onto the crosshair point, so shots land under it with a side/height offset.");
    ov_text_dim("Ctrl+click a slider to type a value. Camera changes apply at once and are saved to b4bcoop.ini.");
}

int thirdperson_init(void) {
    cmds_ini_each(ini_pair, NULL);
    tp_started = 1;
    overlay_add_panel("Camera", 30, tp_panel);
    LOG("thirdperson: start %s, key=0x%02x, distance %.0f side %.0f height %.0f fov %.0f aimfix %d", tp_on ? "on" : "off",
        tp_key, tp_dist, tp_side, tp_height, tp_fov, aim_fix);
    return 0;
}

static void tune_status(Out *o) {
    char fov[16] = "game's";
    if (tp_fov > 0) snprintf(fov, sizeof fov, "%.0f", tp_fov);
    out_printf(o, "third person %s: distance %.0f, side %.0f (%s), height %.0f, fov %s\n", tp_on ? "on" : "off", tp_dist,
               tp_side, tp_side > 0 ? "right shoulder" : tp_side < 0 ? "left shoulder" : "centred", tp_height, fov);
}
// /thirdperson distance|side|height|fov <value>, side left|right|swap, reset
static int tune_cmd(const char *arg, const char *val, Out *o) {
    float v = val && *val ? (float)atof(val) : 0;
    int has = val && *val && (isdigit((unsigned char)val[0]) || val[0] == '-' || val[0] == '.');
    if (!_stricmp(arg, "distance") && has) tp_dist = clampf(v, 50, 600);
    else if (!_stricmp(arg, "height") && has) tp_height = clampf(v, -100, 150);
    else if (!_stricmp(arg, "fov") && has) tp_fov = v > 0 ? clampf(v, 60, 130) : 0;
    else if (!_stricmp(arg, "side") && has) tp_side = clampf(v, -150, 150);
    else if (!_stricmp(arg, "side") && val && !_stricmp(val, "swap")) tp_side = -tp_side;
    else if (!_stricmp(arg, "side") && val && (!_stricmp(val, "left") || !_stricmp(val, "right"))) {
        float m = tp_side < 0 ? -tp_side : tp_side;
        tp_side = (m ? m : 40) * (!_stricmp(val, "left") ? -1 : 1);
    } else if (!_stricmp(arg, "reset")) { tp_dist = TP_DIST_DEF; tp_side = TP_SIDE_DEF; tp_height = tp_fov = 0; }
    else if (!_stricmp(arg, "distance") || !_stricmp(arg, "side") || !_stricmp(arg, "height") || !_stricmp(arg, "fov")) {
        out_printf(o, "usage: /thirdperson distance <50-600> | side <-150..150|left|right|swap> | height <-100..150> | "
                      "fov <60-130|0> | reset\n");
        return 1;
    } else return 0;
    if (tp_fov <= 0) tune(0);   // FOV back to the game's at once (the rest is re-applied below)
    if (tp_on) tune(1);
    tune_status(o);
    if ((tp_side || tp_height) && !aim_fix)
        out_printf(o, "note: shots come from your hero's eyes, so they land %.0f cm off the crosshair point; aiming "
                      "(right mouse) is exact\n", sqrtf(tp_side * tp_side + tp_height * tp_height));
    return 1;
}

// /thirdperson [on|off|status] (chat, everyone), and the camera settings above
void cmd_thirdperson(const char *arg_in, Out *o) {
    char arg[16] = "", val[16] = "";
    if (arg_in) sscanf(arg_in, " %15s %15s", arg, val);
    if (*arg && tune_cmd(arg, val, o)) return;
    int en = !*arg ? !tp_on : !_stricmp(arg, "on") ? 1 : !_stricmp(arg, "off") ? 0 : !_stricmp(arg, "status") ? 2 : -1;
    if (en < 0) { out_printf(o, "usage: /thirdperson [on|off|status|distance|side|height|fov|reset]\n"); return; }
    if (en == 2) { tune_status(o); return; }
    UObject *pvc = view_comp(local_hero());
    if (!en) {
        tune(0);
        if (pvc && tp_written == 2 && PVC_WANT(pvc) == 2) view_set(pvc, 1);
        tp_on = 0;
        tp_written = 0;
        LOG("thirdperson: off");
        out_printf(o, "first person\n");
        return;
    }
    tp_on = 1;
    LOG("thirdperson: on (hero %s)", pvc ? "now" : "none yet");
    tp_sync(0);
    out_printf(o, pvc ? "third person: only your view; aiming switches to first person. /thirdperson again to go back\n"
                      : "third person: on from your next hero. /thirdperson again to turn it off\n");
}

#ifndef B4B_RELEASE
// `thirdperson aim [pitch yaw]`: camera POV vs the hero's eyes (GetActorEyesViewPoint) and where each ray hits (#25
// crosshair accuracy). `thirdperson arm [i sx sy sz]`: the hero's spring arms; sets arm i's SocketOffset.
static void tp_aim(char *a1, Out *o) {
    UObject *pc = ue_local_pc(), *pawn = local_hero(), *pcm = pc ? ue_get_ptr(pc, "PlayerCameraManager") : NULL;
    if (!pawn || !pcm) { out_printf(o, "no hero / camera manager\n"); return; }
    TpCall c;
    char *a2 = a1 ? strtok(NULL, " ") : NULL;
    if (a1 && a2 && tc_prep(&c, pc, "SetControlRotation")) {
        float *r = tc_arg(&c, "NewRotation");
        r[0] = (float)atof(a1); r[1] = (float)atof(a2); r[2] = 0;
        ue_process_event(c.obj, c.fn, c.p);
        out_printf(o, "control rotation set (camera follows next frame; run `thirdperson aim` again)\n");
        return;
    }
    float cl[3] = {0}, cr[3] = {0}, el[3] = {0}, er[3] = {0}, cd[3], ed[3], ch[3], eh[3];
    if (tc_prep(&c, pcm, "GetCameraLocation")) { ue_process_event(c.obj, c.fn, c.p); memcpy(cl, tc_arg(&c, "ReturnValue"), 12); }
    if (tc_prep(&c, pcm, "GetCameraRotation")) { ue_process_event(c.obj, c.fn, c.p); memcpy(cr, tc_arg(&c, "ReturnValue"), 12); }
    if (tc_prep(&c, pawn, "GetActorEyesViewPoint")) {
        ue_process_event(c.obj, c.fn, c.p);
        memcpy(el, tc_arg(&c, "OutLocation"), 12); memcpy(er, tc_arg(&c, "OutRotation"), 12);
    }
    rot_dir(cr, cd); rot_dir(er, ed);
    float v[3] = {el[0] - cl[0], el[1] - cl[1], el[2] - cl[2]}, t = v[0] * cd[0] + v[1] * cd[1] + v[2] * cd[2];
    float perp[3] = {v[0] - t * cd[0], v[1] - t * cd[1], v[2] - t * cd[2]};
    out_printf(o, "camera (%.1f %.1f %.1f) rot (%.2f %.2f)\neyes   (%.1f %.1f %.1f) rot (%.2f %.2f)\n"
                  "eyes are %.1f along the camera ray, %.1f off it (dx %.1f dy %.1f dz %.1f)\n",
               cl[0], cl[1], cl[2], cr[0], cr[1], el[0], el[1], el[2], er[0], er[1], t, sqrtf(perp[0] * perp[0] +
               perp[1] * perp[1] + perp[2] * perp[2]), perp[0], perp[1], perp[2]);
    int hc = trace(pawn, cl, cd, ch), he = trace(pawn, el, ed, eh);
    if (hc) out_printf(o, "crosshair ray hits (%.1f %.1f %.1f) at %.0f from the camera\n", ch[0], ch[1], ch[2], dist3(ch, cl));
    if (he) out_printf(o, "eyes ray hits      (%.1f %.1f %.1f) at %.0f from the eyes\n", eh[0], eh[1], eh[2], dist3(eh, el));
    if (hc && he) {   // the eye-ray hit as seen from the camera: angle off the crosshair
        float w[3] = {eh[0] - cl[0], eh[1] - cl[1], eh[2] - cl[2]}, n = dist3(eh, cl);
        float dot = n > 0 ? (w[0] * cd[0] + w[1] * cd[1] + w[2] * cd[2]) / n : 1;
        out_printf(o, "hit points %.1f apart; eyes-ray hit is %.2f deg off the crosshair\n", dist3(ch, eh),
                   acosf(dot > 1 ? 1 : dot) * 180.f / 3.14159265f);
    }
}
static void tp_arms(char *a1, Out *o) {
    UObject *pawn = local_hero();
    UClass *sc = ue_find_class("SpringArmComponent");
    if (!pawn || !sc) { out_printf(o, "no hero\n"); return; }
    int want = a1 ? atoi(a1) : -1, k = 0;
    char *sx = a1 ? strtok(NULL, " ") : NULL, *sy = sx ? strtok(NULL, " ") : NULL, *sz = sy ? strtok(NULL, " ") : NULL;
    char nm[128];
    for (int32_t i = 0, n = ue_num_objects(); i < n; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & LIVE_FLAGS) || !ue_is_a(x, sc) || COMP_OWNER(x) != pawn) continue;
        float *so = (float *)((char *)x + 0x234), *to = (float *)((char *)x + 0x240);
        if (k == want && sz) { so[0] = (float)atof(sx); so[1] = (float)atof(sy); so[2] = (float)atof(sz); }
        out_printf(o, "[%d] %s len %.1f socket (%.1f %.1f %.1f) target (%.1f %.1f %.1f) flags %02x\n", k,
                   ue_obj_name(x, nm, sizeof nm), *(float *)((char *)x + 0x230), so[0], so[1], so[2], to[0], to[1], to[2],
                   *((uint8_t *)x + 0x254));
        k++;
    }
}

// `thirdperson decals [class]`: DecalComponents (or <class> scene components) that are new or moved since the last
// call, with RelativeLocation (spawned impact decals are unattached: world location) - where a shot really landed
static void tp_decals(char *a1, Out *o) {
    static struct { UObject *c; float l[3]; } seen[4096];
    static int n_seen;
    UClass *dc = ue_find_class(a1 ? a1 : "DecalComponent");
    int32_t rl = -1;
    int shown = 0, total = 0, n2 = 0;
    static struct { UObject *c; float l[3]; } now[4096];
    for (int32_t i = 0, n = ue_num_objects(); dc && i < n && n2 < 4096; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & LIVE_FLAGS) || !ue_is_a(x, dc)) continue;
        if (rl < 0) rl = ue_prop_offset(x, "RelativeLocation");
        if (rl < 0) break;
        float *l = (float *)((char *)x + rl);
        now[n2].c = x; memcpy(now[n2].l, l, 12); n2++; total++;
        int k = 0;
        while (k < n_seen && seen[k].c != x) k++;
        if (k < n_seen && !memcmp(seen[k].l, l, 12)) continue;
        if (shown++ < 40) out_printf(o, "%s %p at (%.1f %.1f %.1f)\n", k < n_seen ? "moved" : "new", (void *)x, l[0], l[1], l[2]);
    }
    memcpy(seen, now, sizeof now[0] * n2); n_seen = n2;
    out_printf(o, "%d new/moved of %d\n", shown, total);
}

// `thirdperson watch [s]`: for s seconds (default 3) log every change of the hero's selected item
// (InventoryComponent.SelectedItemActor) and of the DecalComponent count, with the time since the command: how long
// a weapon switch takes until the first shot lands (press the slot key and hold fire right after), 1P vs 3P
static float watch_left, watch_t;
static void watch_tick(float dt) {
    if (watch_left <= 0) return;
    watch_left -= dt; watch_t += dt;
    static UObject *last_sel; static int last_n = -1;
    UObject *pawn = local_hero();
    UClass *ic = ue_find_class("InventoryComponent"), *dc = ue_find_class("DecalComponent");
    UObject *sel = NULL;
    int n = 0;
    for (int32_t i = 0, m = ue_num_objects(); i < m; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & LIVE_FLAGS)) continue;
        if (dc && ue_is_a(x, dc)) n++;
        else if (!sel && pawn && ic && ue_is_a(x, ic) && COMP_OWNER(x) == pawn) sel = ue_get_ptr(x, "SelectedItemActor");
    }
    char nm[128];
    if (watch_t <= dt) { last_sel = sel; last_n = n; LOG("tpwatch: start sel %s decals %d", sel ? ue_obj_name(sel, nm, sizeof nm) : "-", n); }
    if (sel != last_sel) LOG("tpwatch: %.3f s selected %s", watch_t, sel ? ue_obj_name(sel, nm, sizeof nm) : "-");
    if (n != last_n) LOG("tpwatch: %.3f s decals %d", watch_t, n);
    last_sel = sel; last_n = n;
    if (watch_left <= 0) LOG("tpwatch: end");
}

// `thirdperson targets [range]`: characters (not heroes) near the local hero: location, health (aim tests: did the
// host apply a client's hit?)
static void tp_targets(char *a1, Out *o) {
    UObject *pawn = local_hero();
    UClass *gc = ue_find_class("GobiCharacter"), *hc = cls("HeroCharacter");
    float range = a1 ? (float)atof(a1) : 3000.f, me[3] = {0};
    TpCall c;
    if (pawn && tc_prep(&c, pawn, "K2_GetActorLocation")) { ue_process_event(c.obj, c.fn, c.p); memcpy(me, tc_arg(&c, "ReturnValue"), 12); }
    char nm[128];
    int shown = 0;
    for (int32_t i = 0, n = ue_num_objects(); gc && i < n && shown < 30; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & LIVE_FLAGS) || !ue_is_a(x, gc) || (hc && ue_is_a(x, hc))) continue;
        float l[3] = {0}, hp = -1;
        if (!tc_prep(&c, x, "K2_GetActorLocation")) continue;
        ue_process_event(c.obj, c.fn, c.p); memcpy(l, tc_arg(&c, "ReturnValue"), 12);
        if (dist3(l, me) > range) continue;
        UObject *h = NULL;
        if (tc_prep(&c, x, "GetHealthComponent")) { ue_process_event(c.obj, c.fn, c.p); h = *(UObject **)tc_arg(&c, "ReturnValue"); }
        if (h && tc_prep(&c, h, "GetHealth")) { ue_process_event(c.obj, c.fn, c.p); hp = *(float *)tc_arg(&c, "ReturnValue"); }
        out_printf(o, "%s %p at (%.0f %.0f %.0f) dist %.0f hp %.1f\n", ue_obj_name(x, nm, sizeof nm), (void *)x, l[0], l[1], l[2],
                   dist3(l, me), hp);
        shown++;
    }
    out_printf(o, "%d target(s) within %.0f of (%.0f %.0f %.0f)\n", shown, range, me[0], me[1], me[2]);
}

// ---- #27 probes: what the local hero would use (HeroUseComponent) and the usables around it ----
static UObject *use_comp_of(UObject *pawn) {
    static UObject *pw, *uc; static int32_t pwi = -1, uci = -1;
    if (pawn == pw && alive(pw, pwi) && alive(uc, uci)) return uc;
    UClass *c = ue_find_class("HeroUseComponent");
    pw = pawn; pwi = pawn ? U_INDEX(pawn) : -1; uc = NULL; uci = -1;
    for (int32_t i = 0, n = ue_num_objects(); c && pawn && i < n; i++) {
        UObject *x = ue_object_at(i);
        if (x && !(U_FLAGS(x) & LIVE_FLAGS) && ue_is_a(x, c) && COMP_OWNER(x) == pawn) { uc = x; uci = i; break; }
    }
    return uc;
}
static void text_str(const void *ftext, char *buf, size_t n) {   // FText -> ASCII (dev: the result string leaks)
    buf[0] = 0;
    UClass *k = ue_find_class("KismetTextLibrary");
    TpCall c;
    if (!k || !tc_prep(&c, UC_CDO(k), "Conv_TextToString")) return;
    void *in = tc_arg(&c, "InText"); FString *out = tc_arg(&c, "ReturnValue");
    if (!in || !out) return;
    memcpy(in, ftext, 0x18);
    ue_process_event(c.obj, c.fn, c.p);
    size_t j = 0;
    for (int i = 0; out->data && i < out->num && out->data[i] && j + 1 < n; i++) buf[j++] = out->data[i] < 128 ? (char)out->data[i] : '?';
    buf[j] = 0;
}
static int actor_loc(UObject *a, float l[3]) {
    TpCall c;
    if (!a || !tc_prep(&c, a, "K2_GetActorLocation")) return 0;
    ue_process_event(c.obj, c.fn, c.p);
    memcpy(l, tc_arg(&c, "ReturnValue"), 12);
    return 1;
}
static void pickup_detail(UObject *uc, Out *o);
static void use_dump(Out *o) {
    UObject *pawn = local_hero(), *uc = use_comp_of(pawn);
    if (!uc) { out_printf(o, "no hero / HeroUseComponent\n"); return; }
    char a[128], b[128], t[160] = "";
    uint8_t *u = (uint8_t *)uc;
    UObject *pa = *(UObject **)(u + 0x140), *pu = *(UObject **)(u + 0x150), *sp = *(UObject **)(u + 0x158),
            *au = *(UObject **)(u + 0x148), *ab = *(UObject **)(u + 0x138);
    TpCall c;
    if (tc_prep(&c, uc, "GetUsePrompt")) { ue_process_event(c.obj, c.fn, c.p); text_str(tc_arg(&c, "ReturnValue"), t, sizeof t); }
    float *f = (float *)(u + 0x118);
    out_printf(o, "view %d potential %s / %s  spotting %s  active %s  using %s  state %d  prompt \"%s\"\n",
               tp_pvc && alive(tp_pvc, tp_pvci) ? *((uint8_t *)tp_pvc + 0x215) + 1 : 0,
               pa ? ue_obj_name(pa, a, sizeof a) : "-", pu ? ue_obj_name(U_CLASS(pu), b, sizeof b) : "-",
               sp ? "yes" : "-", au ? "yes" : "-", ab ? "yes" : "-", u[0x170], t);
    out_printf(o, "probe r %.0f len %.0f / r %.0f len %.0f  spot +%.0f  angle %.1f  unrefl 27c (%.1f %.1f %.1f %.1f) 28c (%.1f "
                  "%.1f %.1f %.1f) 2c8 %02x %02x %02x %02x %02x\n", f[0], f[1], f[2], f[3], f[4], f[5],
               *(float *)(u + 0x27c), *(float *)(u + 0x280), *(float *)(u + 0x284), *(float *)(u + 0x288),
               *(float *)(u + 0x28c), *(float *)(u + 0x290), *(float *)(u + 0x294), *(float *)(u + 0x298),
               u[0x2c8], u[0x2c9], u[0x2ca], u[0x2cb], u[0x2cc]);
    pickup_detail(pu ? pu : sp, o);
    UObject *obs = hero_observer(pawn), *vc = obs ? *(UObject **)((char *)obs + 0x110) : NULL;
    out_printf(o, "  observer %s enabled %d view comp %s  (itemfix %d hooks %d %d, %u first-person refreshes, %u views "
                  "corrected, now %d)\n", obs ? "yes" : "none", obs ? *((uint8_t *)obs + 0x108) : -1,
               vc ? ue_obj_name(vc, a, sizeof a) : "-", item_fix, obs_hooked, gather_hooked, obs_n, gather_n, obs_aim != NULL);
}
// an ItemPickupUsableComponent's weak pointer at +0x660 and that object's per-user table at +0x5e0 (0x20-byte entries:
// user, ..., +0x18, +0x19), which its strict CanUse (vtable +0x410, bool 0) checks for the user
static void pickup_detail(UObject *uc, Out *o) {
    UClass *ipc = ue_find_class("ItemPickupUsableComponent");
    if (!uc || !ipc || !ue_is_a(uc, ipc)) return;
    int32_t wi = *(int32_t *)((char *)uc + 0x660);
    UObject *t = wi > 0 && wi < ue_num_objects() ? ue_object_at(wi) : NULL;
    char a[128], b[128];
    if (!t) { out_printf(o, "  +660 -> none (%d)\n", wi); return; }
    float *r = (float *)((char *)t + 0x4fc), *ov = (float *)((char *)t + 0x5d8);   // ObservationStartRules, overrides
    out_printf(o, "  +660 -> %s %s %p  rules flags %d dist %.0f-%.0f angle %.1f fwd %.1f coll %d sphere %.0f  override r %.0f d %.0f\n",
               ue_obj_name(U_CLASS(t), a, sizeof a), ue_obj_name(t, b, sizeof b), (void *)t, *(int *)r, r[1], r[2], r[3], r[4],
               *(int *)(r + 5), r[9], ov[0], ov[1]);
    TArray *arr = (TArray *)((char *)t + 0x5e0);
    for (int i = 0; i < arr->num && i < 8; i++) {
        uint8_t *e = (uint8_t *)arr->data + i * 0x20;
        UObject *u = *(UObject **)e;
        out_printf(o, "  [%d] %s %p  +8 %016llx +10 %016llx +18 %02x +19 %02x\n", i, u ? ue_obj_name(u, a, sizeof a) : "-", (void *)u,
                   *(unsigned long long *)(e + 8), *(unsigned long long *)(e + 0x10), e[0x18], e[0x19]);
    }
}
static struct { UObject *c; int32_t i; float l[3]; } us_list[96];
static int n_us;
static void usables_list(char *a1, Out *o) {
    UObject *pawn = local_hero();
    UClass *c = ue_find_class("UsableComponent");
    float range = a1 ? (float)atof(a1) : 1500.f, me[3] = {0};
    char *filt = a1 ? strtok(NULL, " ") : NULL;
    actor_loc(pawn, me);
    n_us = 0;
    char a[128], b[128], t[128];
    for (int32_t i = 0, n = ue_num_objects(); c && i < n && n_us < 96; i++) {
        UObject *x = ue_object_at(i);
        if (!x || (U_FLAGS(x) & LIVE_FLAGS) || !ue_is_a(x, c)) continue;
        UObject *ow = COMP_OWNER(x);
        float l[3];
        if (!ow || ow == pawn || !actor_loc(ow, l) || dist3(l, me) > range) continue;
        ue_obj_name(ow, a, sizeof a); ue_obj_name(U_CLASS(x), b, sizeof b);
        if (filt && *filt == '!' ? strstr(a, filt + 1) || strstr(b, filt + 1) : filt && !strstr(a, filt) && !strstr(b, filt)) continue;
        uint8_t *u = (uint8_t *)x;
        text_str(u + 0x290, t, sizeof t);
        out_printf(o, "[%d] %s %s at (%.0f %.0f %.0f) dist %.0f en %d los %d traceloc %d front %d prio %d vt410 0x%llx \"%s\"\n",
                   n_us, a, b, l[0], l[1], l[2], dist3(l, me), u[0x280], u[0x5d9], u[0x5da], u[0x528], u[0x658],
                   (unsigned long long)((uintptr_t)U_VTBL(x)[0x410 / 8] - g_base_delta), t);
        us_list[n_us].c = x; us_list[n_us].i = i; memcpy(us_list[n_us].l, l, 12); n_us++;
        if (filt && *filt != '!') pickup_detail(x, o);
    }
    out_printf(o, "%d usable(s) within %.0f\n", n_us, range);
}
// `thirdperson los <n> [chan]`: a trace from the eyes to usable n's owner location: what it hits first
static void usables_los(char *a1, Out *o) {
    char *sc = a1 ? strtok(NULL, " ") : NULL;
    int k = a1 ? atoi(a1) : -1;
    UObject *pawn = local_hero();
    TpCall c;
    if (k < 0 || k >= n_us || !pawn || !tc_prep(&c, pawn, "GetActorEyesViewPoint")) { out_printf(o, "usage: thirdperson los <n> [chan]\n"); return; }
    ue_process_event(c.obj, c.fn, c.p);
    float el[3], d[3], hit[3];
    memcpy(el, tc_arg(&c, "OutLocation"), 12);
    for (int i = 0; i < 3; i++) d[i] = us_list[k].l[i] - el[i];
    float n = sqrtf(dot3(d, d));
    for (int i = 0; i < 3; i++) d[i] /= n;
    char a[128];
    trace_hit_actor = -1;
    if (!trace_ch(pawn, el, d, n + 50, sc ? atoi(sc) : 0, hit)) { out_printf(o, "clear (%.0f)\n", n); return; }
    UObject *ha = trace_hit_actor >= 0 ? ue_object_at(trace_hit_actor) : NULL;
    out_printf(o, "hit %s at %.0f of %.0f (%.0f %.0f %.0f)\n", ha ? ue_obj_name(ha, a, sizeof a) : "?", dist3(hit, el), n, hit[0], hit[1], hit[2]);
}
// `thirdperson lookat <n> [dist [dz [z]]]`: face usable n of the last list (control rotation from the eyes to its owner's
// location + dz; in our offset 3P the crosshair); with dist (host: its own hero) first stand dist units from it, on the
// side the hero is now (at actor height z if given)
static void usables_lookat(char *a1, Out *o) {
    char *sd = a1 ? strtok(NULL, " ") : NULL, *sz = sd ? strtok(NULL, " ") : NULL, *sfz = sz ? strtok(NULL, " ") : NULL;
    int k = a1 ? atoi(a1) : -1;
    UObject *pawn = local_hero(), *pc = ue_local_pc();
    if (k < 0 || k >= n_us || !alive(us_list[k].c, us_list[k].i) || !pawn || !pc) { out_printf(o, "usage: thirdperson lookat <n from usables> [dist [dz]]\n"); return; }
    float t[3], me[3], el[3], er[3];
    memcpy(t, us_list[k].l, 12);
    if (sz) t[2] += (float)atof(sz);
    actor_loc(pawn, me);
    TpCall c;
    if (sd && *sd != '-') {
        float d = (float)atof(sd), v[2] = {me[0] - t[0], me[1] - t[1]}, n = sqrtf(v[0] * v[0] + v[1] * v[1]);
        if (n < 1) { v[0] = 1; v[1] = 0; n = 1; }
        float p[3] = {t[0] + v[0] / n * d, t[1] + v[1] / n * d, sfz ? (float)atof(sfz) : fabsf(me[2] - us_list[k].l[2]) < 150 ? me[2] : us_list[k].l[2] + 60};
        if (tc_prep(&c, pawn, "K2_SetActorLocation")) {
            memcpy(tc_arg(&c, "NewLocation"), p, 12);
            uint8_t *tp = tc_arg(&c, "bTeleport"); if (tp) *tp = 1;
            ue_process_event(c.obj, c.fn, c.p);
        }
        memcpy(me, p, 12);
    }
    if (!tc_prep(&c, pawn, "GetActorEyesViewPoint")) return;
    ue_process_event(c.obj, c.fn, c.p);
    memcpy(el, tc_arg(&c, "OutLocation"), 12); memcpy(er, tc_arg(&c, "OutRotation"), 12);
    float d[3] = {t[0] - el[0], t[1] - el[1], t[2] - el[2]}, n = sqrtf(dot3(d, d));
    if (n < 1) return;
    float r[3] = {asinf(d[2] / n) * 180.f / 3.14159265f, atan2f(d[1], d[0]) * 180.f / 3.14159265f, 0};
    // our offset 3P camera: put the crosshair (the camera ray) on the target instead of the eyes' ray, the way a
    // player aims (the aim correction then turns the eyes onto the crosshair point); fixed-point on the rotation
    for (int it = 0; aim_ok && it < 8; it++) {
        float f[3], rt[3], up[3], cam[3];
        basis(r, f, rt, up);
        for (int i = 0; i < 3; i++) cam[i] = el[i] + f[i] * aim_local[0] + rt[i] * aim_local[1] + up[i] * aim_local[2];
        for (int i = 0; i < 3; i++) d[i] = t[i] - cam[i];
        float m = sqrtf(dot3(d, d));
        if (m < 1) break;
        r[0] = asinf(d[2] / m) * 180.f / 3.14159265f; r[1] = atan2f(d[1], d[0]) * 180.f / 3.14159265f;
    }
    if (tc_prep(&c, pc, "SetControlRotation")) { memcpy(tc_arg(&c, "NewRotation"), r, 12); ue_process_event(c.obj, c.fn, c.p); }
    out_printf(o, "facing [%d] at %.0f (pitch %.1f yaw %.1f) from (%.0f %.0f %.0f)\n", k, n, r[0], r[1], me[0], me[1], me[2]);
}

// Dev CLI: `thirdperson [on|off|status]` = the chat command; `thirdperson view [1|2|3]` dumps the local hero's
// PlayerViewComponent (view bytes, tag lists, owner tags, ADS), a digit sets the view once; `thirdperson aim|arm|decals`.
int thirdperson_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "thirdperson")) return 0;
    char *what = rest ? strtok(rest, " ") : NULL, *arg = what ? strtok(NULL, " ") : NULL;
    if (what && !strcmp(what, "aim")) { tp_aim(arg, o); return 1; }
    if (what && !strcmp(what, "arm")) { tp_arms(arg, o); return 1; }
    if (what && !strcmp(what, "decals")) { tp_decals(arg, o); return 1; }
    if (what && !strcmp(what, "targets")) { tp_targets(arg, o); return 1; }
    if (what && !strcmp(what, "use")) { use_dump(o); return 1; }
    if (what && !strcmp(what, "itemfix")) {   // itemfix [0|1]: the item observer override (#27), refreshed at once
        if (arg) item_fix = atoi(arg) != 0;
        obs_hook();
        obs_kick();
        UObject *obs = hero_observer(local_hero());
        out_printf(o, "itemfix %d hook %d observer enabled %d\n", item_fix, obs_hooked, obs ? *((uint8_t *)obs + 0x108) : -1);
        return 1;
    }
    if (what && !strcmp(what, "usables")) { usables_list(arg, o); return 1; }
    if (what && !strcmp(what, "los")) { usables_los(arg, o); return 1; }
    if (what && !strcmp(what, "lookat")) { usables_lookat(arg, o); return 1; }
    if (what && !strcmp(what, "callers")) {   // who asks for the local hero's eyes / base aim (the fire path)
        UObject *pawn = local_hero();
        aim_hook(pawn);
        if (!arg) {
            for (int i = 0; i < n_probe_ra; i++)
                out_printf(o, "%s ret 0x%llx x%d\n", (const char *[]){"eyes   ", "baseaim", "other eyes", "other baseaim"}[probe_ra[i].slot],
                           (unsigned long long)((uintptr_t)probe_ra[i].ra - g_base_delta), probe_ra[i].n);
            out_printf(o, "%d callers%s (hooks %d)\n", n_probe_ra, probe_left > 0 ? ", still recording" : "", aim_hooked);
            return 1;
        }
        char *all = strtok(NULL, " ");
        n_probe_ra = 0; probe_left = (float)atof(arg); probe_all = all && !strcmp(all, "all");
        out_printf(o, "recording %.1f s (hooks %d)\n", probe_left, aim_hooked);
        return 1;
    }
    if (what && !strcmp(what, "aimtest")) {   // aimtest <eyes|base> <yaw>: skew one of them for the local hero
        char *v = strtok(NULL, " ");
        aim_hook(local_hero());
        if (arg && v) aim_test[!strcmp(arg, "base")] = (float)atof(v);
        out_printf(o, "aimtest eyes %+.1f base %+.1f (hooks %d)\n", aim_test[0], aim_test[1], aim_hooked);
        return 1;
    }
    if (what && !strcmp(what, "aimfix")) {   // aimfix [0|1] [use 1|2|3] [chan n]: the correction and its status
        char *v = strtok(NULL, " "), *w = v ? strtok(NULL, " ") : NULL;
        if (arg && (arg[0] == '0' || arg[0] == '1')) aim_fix = atoi(arg);
        for (char *k = arg; k; k = NULL) {
            if (!strcmp(k, "use") && v) aim_use = atoi(v);
            else if (!strcmp(k, "chan") && v) aim_chan = atoi(v);
        }
        (void)w;
        out_printf(o, "aimfix %d use %d chan %d hooks %d ok %d local (%.1f %.1f %.1f) n %d last point (%.1f %.1f %.1f) "
                      "%.2f deg\n", aim_fix, aim_use, aim_chan, aim_hooked, aim_ok, aim_local[0], aim_local[1],
                   aim_local[2], aim_n, aim_last_p[0], aim_last_p[1], aim_last_p[2], aim_last_deg);
        return 1;
    }
    if (what && !strcmp(what, "watch")) { watch_left = arg ? (float)atof(arg) : 3.f; watch_t = 0; out_printf(o, "watching\n"); return 1; }
    if (!what || strcmp(what, "view")) {   // the chat command: pass the words through
        char line[64];
        snprintf(line, sizeof line, "%s %s", what ? what : "", arg ? arg : "");
        cmd_thirdperson(what ? line : NULL, o);
        return 1;
    }
    char a[256], b[256], c[256];
    UObject *pawn = local_hero(), *pvc = view_comp(pawn);
    if (!pvc) { out_printf(o, "no local hero / PlayerViewComponent\n"); return 1; }
    if (arg && arg[0] >= '1' && arg[0] <= '3') view_set(pvc, (uint8_t)(arg[0] - '0'));
    ads_refresh(pawn);
    uint8_t *v = (uint8_t *)pvc;
    out_printf(o, "pawn %p %s pvc %p want %d applied %d is3p %d supports1p %d  ads %d (%d comps)  on %d written %d\n",
               (void *)pawn, ue_obj_name(U_CLASS(pawn), a, sizeof a), (void *)pvc, v[0x200], v[0x201], v[0x215], v[0x148],
               hero_ads(), n_tp_ads, tp_on, tp_written);
    static const struct { const char *label; int off; } tc[] = {{"ThirdPersonTags", 0x188}, {"ThirdPersonOrbitTags", 0x1a8},
                                                              {"ThirdPersonOccludedTags", 0x1c8}};
    UObject *gtc = *(UObject **)(v + 0x228);
    for (int k = 0; k < 4; k++) {
        TArray *t = k < 3 ? (TArray *)(v + tc[k].off) : gtc ? (TArray *)((char *)gtc + 0x130) : NULL;
        out_printf(o, "%s:", k < 3 ? tc[k].label : "owner tags");
        for (int j = 0; t && j < t->num && j < 64; j++) out_printf(o, " %s", ue_name(((FName *)t->data)[j], b, sizeof b));
        out_printf(o, "\n");
    }
    int32_t hold = n_tp_ads ? ue_prop_offset(tp_ads[0].c, "bIsHoldingADS") : -1;
    for (int k = 0; k < n_tp_ads; k++) {   // the hero's ADS components: weapon, held
        UObject *w = COMP_OWNER(tp_ads[k].c);
        out_printf(o, "ads[%d] %p on %s holding %d\n", k, (void *)tp_ads[k].c, w ? ue_obj_name(w, a, sizeof a) : "-",
                   hold >= 0 ? *((uint8_t *)tp_ads[k].c + hold) : -1);
    }
    for (int k = 0; k < 2; k++) {   // camera/spring arm/mesh tags of both configs
        FName *f = (FName *)(v + (k ? 0x14c : 0xf8));
        out_printf(o, "%s: camera %s arm %s mesh %s\n", k ? "1P" : "3P", ue_name(f[0], a, sizeof a), ue_name(f[1], b, sizeof b),
                   ue_name(f[2], c, sizeof c));
    }
    return 1;
}
#endif
