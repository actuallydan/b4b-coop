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
// third-person.md): side/height move the camera off the line of fire, so hits land that far beside the crosshair.
#define TP_DIST_DEF 180.f
static float tp_dist = TP_DIST_DEF, tp_side, tp_height, tp_fov;   // tp_fov 0 = the game's
static UObject *tp_arm, *tp_cam;                                     // on tp_pawn
static int32_t tp_armi = -1, tp_cami = -1;
static float tp_arm_orig[4] = {-1}, tp_fov_orig = -1;               // length, socket xyz; FOV (-1 = not saved)
#define ARM_LEN(a)    ((float *)((char *)(a) + 0x230))
#define ARM_SOCKET(a) ((float *)((char *)(a) + 0x234))
#define CAM_FOV(c)    ((float *)((char *)(c) + 0x230))

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

// every tick while on: third person, first person while aiming. A view the game itself asked for (a tag-driven 2 we
// didn't write: healing, pounced, grabbed, ...; orbit 3) is left alone; when the game drops back to 1 we write 2 again.
static void tp_sync(float dt) {
    UObject *pawn = local_hero(), *pvc = view_comp(pawn);
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
        int down = (GetAsyncKeyState(tp_key) & 0x8000) != 0;
        if (down && !was_down && cmds_game_focused()) {
            static Out tmp;
            out_reset(&tmp);
            cmd_thirdperson(NULL, &tmp);
            LOG("thirdperson: hotkey: %.*s", (int)strcspn(tmp.buf, "\n"), tmp.buf);
        }
        was_down = down;
    }
    if (tp_on) tp_sync(dt);
}

static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

// b4bcoop.ini: thirdperson=1 (start in third person), thirdperson_key=N (toggle key; off = none)
int thirdperson_init(void) {
    FILE *f = fopen(cmds_config_path(), "r");
    if (f) {
        char line[300];
        while (fgets(line, sizeof line, f)) {
            char *nl = strpbrk(line, "\r\n"); if (nl) *nl = 0;
            char *v = strchr(line, '='); if (!v || line[0] == '#' || line[0] == ';') continue;
            *v++ = 0;
            while (*v == ' ') v++;
            if (!strcmp(line, "thirdperson")) tp_on = atoi(v) != 0;
            else if (!strcmp(line, "thirdperson_key")) tp_key = cmds_parse_key(v);
            else if (!strcmp(line, "thirdperson_distance")) tp_dist = clampf((float)atof(v), 50, 600);
            else if (!strcmp(line, "thirdperson_side")) tp_side = clampf((float)atof(v), -150, 150);
            else if (!strcmp(line, "thirdperson_height")) tp_height = clampf((float)atof(v), -100, 150);
            else if (!strcmp(line, "thirdperson_fov")) tp_fov = atof(v) > 0 ? clampf((float)atof(v), 60, 130) : 0;
        }
        fclose(f);
    }
    LOG("thirdperson: start %s, key=0x%02x, distance %.0f side %.0f height %.0f fov %.0f", tp_on ? "on" : "off", tp_key,
        tp_dist, tp_side, tp_height, tp_fov);
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
    } else if (!_stricmp(arg, "reset")) { tp_dist = TP_DIST_DEF; tp_side = tp_height = tp_fov = 0; }
    else if (!_stricmp(arg, "distance") || !_stricmp(arg, "side") || !_stricmp(arg, "height") || !_stricmp(arg, "fov")) {
        out_printf(o, "usage: /thirdperson distance <50-600> | side <-150..150|left|right|swap> | height <-100..150> | "
                      "fov <60-130|0> | reset\n");
        return 1;
    } else return 0;
    if (tp_fov <= 0) tune(0);   // FOV back to the game's at once (the rest is re-applied below)
    if (tp_on) tune(1);
    tune_status(o);
    if (tp_side || tp_height)
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
// KismetSystemLibrary::LineTraceSingle (Visibility, ignoring the pawn): the first blocking point, 0 = none
static int trace(UObject *pawn, const float s[3], const float d[3], float hit[3]) {
    UClass *k = ue_find_class("KismetSystemLibrary");
    TpCall c;
    if (!k || !tc_prep(&c, UC_CDO(k), "LineTraceSingle")) return 0;
    *(UObject **)tc_arg(&c, "WorldContextObject") = pawn;
    float *st = tc_arg(&c, "Start"), *en = tc_arg(&c, "End");
    for (int i = 0; i < 3; i++) { st[i] = s[i]; en[i] = s[i] + d[i] * 50000.f; }
    *(uint8_t *)tc_arg(&c, "bIgnoreSelf") = 1;
    ue_process_event(c.obj, c.fn, c.p);
    uint8_t *h = tc_arg(&c, "OutHit");
    if (!(h[0] & 1)) return 0;
    memcpy(hit, h + 0x1c, 12);   // HitResult.ImpactPoint
    return 1;
}
static float dist3(const float a[3], const float b[3]) {
    float x = a[0] - b[0], y = a[1] - b[1], z = a[2] - b[2];
    return sqrtf(x * x + y * y + z * z);
}
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

// Dev CLI: `thirdperson [on|off|status]` = the chat command; `thirdperson view [1|2|3]` dumps the local hero's
// PlayerViewComponent (view bytes, tag lists, owner tags, ADS), a digit sets the view once; `thirdperson aim|arm|decals`.
int thirdperson_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "thirdperson")) return 0;
    char *what = rest ? strtok(rest, " ") : NULL, *arg = what ? strtok(NULL, " ") : NULL;
    if (what && !strcmp(what, "aim")) { tp_aim(arg, o); return 1; }
    if (what && !strcmp(what, "arm")) { tp_arms(arg, o); return 1; }
    if (what && !strcmp(what, "decals")) { tp_decals(arg, o); return 1; }
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
