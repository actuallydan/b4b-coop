// Manual flashlight toggle (issue #2). Findings: docs/investigations/flashlight.md.
//
// UHeroLightComponent (on each hero) holds the replicated bool bLightVisibilityRequested (+0xF8). The server sets
// it from FlashlightVolume overlaps (a priority stack of requests) and the "dark card" tag, and every machine applies
// it to the spotlights in OnRep. The class also ships an unused server RPC, ServerUpdateLightVisibilityRequested(),
// whose _Implementation simply toggles the bool on the server. Calling it through ProcessEvent works on the host
// (runs locally) and on a client (sent to the host for the client's own hero), and the result replicates to everyone.
//
// Sticky mode (host side, flashlight_sticky=1, default): once a hero's light was toggled by hand, FlashlightVolume
// enter/exit no longer overrides it (until the hero is destroyed, e.g. map change, or `flashlight auto` on the host).
//
// Beam tuning (#29, flashlight_width/_range/_brightness, percent): the light that renders is a set of SpotLightComponents
// (FlashLightComponents +0x1B8) that every machine builds locally from the class's FlashLightBPs[EFlashlightMode]
// templates, and rebuilds whenever the mode changes (first/third person, HDR output, quality). Nothing about them
// replicates, so scaling their cone angles, attenuation radius and intensity on this machine changes only how the
// local player's own light looks on this screen. Values are scaled from what the game last put there (it rewrites
// intensity for flicker/gameplay effects and makes new components on every view change), checked every frame.
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"
#include "overlay.h"

// void UHeroLightComponent::SetLightState(bool bVisible, bool bThirdPersonShadows) (internal, non-reflected)
#define ADDR_HLC_SET        VA(0x141BF2090ull)
// UHeroLightComponent::ServerUpdateLightVisibilityRequested_Implementation (vtable +0x420): Set(!bVisible, shadows)
#define ADDR_HLC_TOGGLE     VA(0x141BF2270ull)
// return addresses of the two SetLightState calls in AFlashlightVolume::OnOverlapBegin / OnOverlapEnd
#define RET_VOLUME_BEGIN    VA(0x142066ED6ull)
#define RET_VOLUME_END      VA(0x142066AF3ull)
// non-reflected UHeroLightComponent fields (from the disassembly above)
#define HLC_VISIBLE(c)      (*(uint8_t *)((char *)(c) + 0xF8))   // bLightVisibilityRequested (reflected too)
#define HLC_SHADOWS(c)      (*(uint8_t *)((char *)(c) + 0xF9))   // bCastsThirdPersonShadowsRequested
#define HLC_REQUESTS(c)     ((TArray *)((char *)(c) + 0x178))     // TArray<FFlashlightEnableRequest> sorted by Priority
#define HLC_DARKCARD(c)     (*(uint8_t *)((char *)(c) + 0x190))  // dark-card tag present: light forced on
typedef struct { uint8_t bEnable, bShadows, pad[6]; UObject *Requestor; int32_t Priority, pad2; } FlashlightEnableRequest;

static const uint8_t SIG_SET[] = {0x48,0x89,0x5c,0x24,0x10,0x57,0x48,0x83,0xec,0x20,0x48,0x8b,0x05,0x5f,0x72,0xd1,
                                  0x04,0x0f,0xb6,0xfa,0x48,0x8b,0xd9,0x80};
static const uint8_t SIG_TOGGLE[] = {0x80,0xb9,0xf8,0x00,0x00,0x00,0x00,0x44,0x0f,0xb6,0x81,0xf9,0x00,0x00,0x00,0x0f,
                                     0x94,0xc2,0xe9,0x09,0xfe,0xff,0xff};

typedef void (*SetFn)(UObject *comp, uint8_t visible, uint8_t shadows);
typedef void (*ToggleFn)(UObject *comp);
static SetFn orig_set;
static ToggleFn orig_toggle;
static int hooked, sticky = 1, hotkey = 'L';

// ---- manual-override table (host): components whose light was toggled by hand ----
typedef struct { UObject *comp; int32_t idx; } Manual;
static Manual manual[32];

static int alive(const Manual *m) { return m->comp && ue_object_at(m->idx) == m->comp; }
static Manual *manual_find(UObject *c) {
    for (int i = 0; i < 32; i++) if (manual[i].comp == c && alive(&manual[i])) return &manual[i];
    return NULL;
}
static void manual_add(UObject *c) {
    if (manual_find(c)) return;
    for (int i = 0; i < 32; i++) if (!alive(&manual[i])) { manual[i].comp = c; manual[i].idx = U_INDEX(c); return; }
}

static void set_detour(UObject *comp, uint8_t visible, uint8_t shadows) {
    uintptr_t ret = (uintptr_t)__builtin_return_address(0);
    if (sticky && (ret == RET_VOLUME_BEGIN || ret == RET_VOLUME_END) && manual_find(comp)) {
        LOG("flashlight: kept manual state %d, ignored volume request %d", HLC_VISIBLE(comp), visible);
        return;
    }
    orig_set(comp, visible, shadows);
}

static void toggle_detour(UObject *comp) {
    manual_add(comp);   // runs on the authority only: our own call on the host, or a client's RPC arriving
    orig_toggle(comp);
    LOG("flashlight: toggled -> %d (%s)", HLC_VISIBLE(comp), sticky ? "manual, sticky" : "manual");
}

// ---- local hero ----
static UClass *cls_hlc;
static UObject *local_light(void) {
    static UObject *cache, *cache_pawn, *miss_pawn; static int32_t cache_idx; static DWORD miss_t;
    UObject *pc = ue_local_pc();
    UObject *pawn = pc ? ue_get_ptr(pc, "Pawn") : NULL;
    if (!pawn) return NULL;
    if (cache && cache_pawn == pawn && ue_object_at(cache_idx) == cache && U_OUTER(cache) == pawn) return cache;
    if (pawn == miss_pawn && GetTickCount() - miss_t < 2000) return NULL;   // a pawn without a light: the beam tuning
    miss_pawn = pawn; miss_t = GetTickCount();                              // asks every frame, scan every 2 s at most
    if (!cls_hlc) cls_hlc = ue_find_class("HeroLightComponent");
    if (!cls_hlc) return NULL;
    int32_t n = ue_num_objects();
    for (int32_t i = 0; i < n; i++) {
        UObject *o = ue_object_at(i);
        if (o && U_OUTER(o) == pawn && !(U_FLAGS(o) & 0x10) && ue_is_a(o, cls_hlc)) {
            cache = o; cache_pawn = pawn; cache_idx = i;
            return o;
        }
    }
    return NULL;
}

static int is_authority(UObject *comp) {
    UObject *actor = U_OUTER(comp);
    int32_t off = actor ? ue_prop_offset(actor, "Role") : -1;
    return off >= 0 && *(uint8_t *)((char *)actor + off) == 3;   // ROLE_Authority
}

// Ask for a toggle: runs locally on the host/standalone, becomes a server RPC on a client.
static int request_toggle(UObject *comp) {
    static UFunction *fn;
    if (!fn) fn = ue_find_function(U_CLASS(comp), "ServerUpdateLightVisibilityRequested");
    if (!fn) return -1;
    uint8_t params[16] = {0};
    ue_process_event(comp, fn, params);
    return 0;
}

// Re-apply what the volumes/dark card want (host only), and drop the manual override.
static void restore_auto(UObject *comp) {
    for (int i = 0; i < 32; i++) if (manual[i].comp == comp) manual[i].comp = NULL;
    if (!hooked || !is_authority(comp)) return;
    TArray *rq = HLC_REQUESTS(comp);
    uint8_t vis = 0, sh = HLC_SHADOWS(comp);
    if (HLC_DARKCARD(comp)) vis = 1;
    else if (rq->num > 0) {
        FlashlightEnableRequest *top = &((FlashlightEnableRequest *)rq->data)[rq->num - 1];
        vis = top->bEnable; sh = top->bShadows;
    }
    orig_set(comp, vis, sh);
}

// ---- beam tuning (#29): the local hero's spotlights, on this machine only ----
// Engine offsets (sdk/Script_Engine.txt): LightComponentBase.Intensity +0x244, LocalLightComponent.AttenuationRadius
// +0x398, SpotLightComponent.InnerConeAngle +0x3C8 / OuterConeAngle +0x3CC (half angles, degrees).
#define HLC_SPOTS(c)   ((TArray *)((char *)(c) + 0x1B8))   // FlashLightComponents: TArray<USpotLightComponent*>
#define HLC_MODE(c)    (*(uint8_t *)((char *)(c) + 0x170))   // EFlashlightMode the spotlights were built for
static const int32_t BEAM_OFF[4] = {0x3C8, 0x3CC, 0x398, 0x244};
static const char *const BEAM_SET[4] = {"SetInnerConeAngle", "SetOuterConeAngle", "SetAttenuationRadius", "SetIntensity"};
enum { B_INNER, B_OUTER, B_RADIUS, B_INTENSITY };
#define BEAM_MAX_OUTER 80.f       // half angle: a 160-degree cone (the editor's spot light max; the proxy clamps at 89)
static float beam_w = 100, beam_r = 100, beam_b = 100;   // percent of the game's values
typedef struct { UObject *c; int32_t idx; float base[4], put[4]; } Beam;
static Beam beams[8];
static int n_beams;
static UObject *beam_light;       // the light component the beams belong to
static UClass *cls_spot;
static UFunction *beam_fn[4];

static int beam_default(void) { return beam_w == 100 && beam_r == 100 && beam_b == 100; }
static float *bf(UObject *spot, int k) { return (float *)((char *)spot + BEAM_OFF[k]); }
static void beam_call(UObject *spot, int k, float v) {
    if (!beam_fn[k]) beam_fn[k] = ue_find_function(U_CLASS(spot), BEAM_SET[k]);
    if (!beam_fn[k]) { *bf(spot, k) = v; return; }
    uint8_t p[16] = {0};
    memcpy(p, &v, sizeof v);
    ue_process_event(spot, beam_fn[k], p);   // the engine setter: value + render-state update
}
static void beam_targets(const Beam *b, float t[4]) {
    float outer = b->base[B_OUTER] * beam_w / 100.f;
    if (outer > BEAM_MAX_OUTER && outer > b->base[B_OUTER]) outer = b->base[B_OUTER] > BEAM_MAX_OUTER ? b->base[B_OUTER] : BEAM_MAX_OUTER;
    t[B_OUTER] = outer;
    t[B_INNER] = b->base[B_OUTER] > 0 ? b->base[B_INNER] * outer / b->base[B_OUTER] : b->base[B_INNER];
    t[B_RADIUS] = b->base[B_RADIUS] * beam_r / 100.f;
    t[B_INTENSITY] = b->base[B_INTENSITY] * beam_b / 100.f;
}
static void beam_apply(Beam *b) {
    float t[4];
    beam_targets(b, t);
    for (int k = 0; k < 4; k++) {
        if (*bf(b->c, k) != t[k]) beam_call(b->c, k, t[k]);
        b->put[k] = *bf(b->c, k);   // what is there now (a setter may refuse or clamp)
    }
}
static int beam_alive(const Beam *b) { return b->c && ue_object_at(b->idx) == b->c; }
static void beam_restore_all(void) {   // the game's values back on spotlights we changed (another hero now)
    for (int i = 0; i < n_beams; i++) {
        Beam *b = &beams[i];
        if (!beam_alive(b)) continue;
        for (int k = 0; k < 4; k++)
            if (*bf(b->c, k) == b->put[k] && b->put[k] != b->base[k]) beam_call(b->c, k, b->base[k]);
    }
    n_beams = 0;
}

// Every frame: find new spotlights (view change = new components), pick up values the game wrote since our last
// write as the new base, and scale. At the defaults with nothing tracked this is one pointer check.
static void beam_tick(void) {
    if (beam_default() && !n_beams) return;
    UObject *c = local_light();
    if (c != beam_light) { beam_restore_all(); beam_light = c; }
    if (!c) return;
    if (!cls_spot) cls_spot = ue_find_class("SpotLightComponent");
    TArray *a = HLC_SPOTS(c);
    if (!cls_spot || a->num <= 0 || a->num > 8) return;
    for (int j = 0; j < a->num; j++) {
        UObject *s = ((UObject **)a->data)[j];
        if (!s || (U_FLAGS(s) & 0x30) || !ue_is_a(s, cls_spot)) continue;
        Beam *b = NULL;
        for (int i = 0; i < n_beams; i++) if (beams[i].c == s && beam_alive(&beams[i])) { b = &beams[i]; break; }
        if (!b) {
            int i = 0;
            while (i < n_beams && beam_alive(&beams[i])) i++;   // reuse a dead slot (the old view's spotlights)
            if (i == n_beams) { if (n_beams == 8) continue; n_beams++; }
            b = &beams[i];
            b->c = s; b->idx = U_INDEX(s);
            for (int k = 0; k < 4; k++) { b->base[k] = *bf(s, k); b->put[k] = NAN; }
        }
        int changed = 0;
        for (int k = 0; k < 4; k++) {
            float cur = *bf(s, k);
            if (cur == b->put[k]) continue;
            // the game wrote it: that is its value now. One exception is relative: with HDR output on, the game
            // halves the intensity once after a rebuild (0x141BF172B), which must halve the base, not our result.
            if (k == B_INTENSITY && b->put[k] > 0 && fabsf(cur - b->put[k] * 0.5f) <= b->put[k] * 1e-4f)
                b->base[k] *= 0.5f;
            else if (!isnan(b->put[k]) || cur != b->base[k]) b->base[k] = cur;
            changed = 1;
        }
        if (changed) beam_apply(b);
    }
    if (beam_default()) {   // back at 100%: our values are gone again, stop watching
        for (int i = 0; i < n_beams; i++) if (beam_alive(&beams[i])) beam_apply(&beams[i]);
        n_beams = 0;
    }
}
static void beam_settings_changed(void) {   // re-apply to the tracked spotlights at once (beam_tick adds new ones)
    for (int i = 0; i < n_beams; i++) if (beam_alive(&beams[i])) beam_apply(&beams[i]);
}

// Readout of the local hero's spotlights: current values and the game's (base) values.
static void beam_status(Out *o, UObject *c) {
    TArray *a = HLC_SPOTS(c);
    if (!cls_spot) cls_spot = ue_find_class("SpotLightComponent");
    out_printf(o, "beam: width %.0f%% range %.0f%% brightness %.0f%%, mode %d, %d spotlight(s)\n", beam_w, beam_r, beam_b,
               HLC_MODE(c), a->num);
    for (int j = 0; j < a->num && j < 8; j++) {
        UObject *s = ((UObject **)a->data)[j];
        if (!s || !cls_spot || !ue_is_a(s, cls_spot)) continue;
        const Beam *b = NULL;
        for (int i = 0; i < n_beams; i++) if (beams[i].c == s && beam_alive(&beams[i])) b = &beams[i];
        int32_t units = ue_prop_offset(s, "IntensityUnits"), ies = ue_prop_offset(s, "IESTexture"),
                lf = ue_prop_offset(s, "LightFunctionMaterial"), sr = ue_prop_offset(s, "SourceRadius");
        char n1[128], n2[128];
        UObject *iesp = ies >= 0 ? *(UObject **)((char *)s + ies) : NULL, *lfp = lf >= 0 ? *(UObject **)((char *)s + lf) : NULL;
        out_printf(o, "  [%d] inner %.1f outer %.1f radius %.0f intensity %.2f units %d source %.1f ies %s lightfn %s",
                   j, *bf(s, 0), *bf(s, 1), *bf(s, 2), *bf(s, 3), units >= 0 ? *((uint8_t *)s + units) : -1,
                   sr >= 0 ? *(float *)((char *)s + sr) : -1.f, iesp ? ue_obj_name(iesp, n1, sizeof n1) : "-",
                   lfp ? ue_obj_name(lfp, n2, sizeof n2) : "-");
        if (b) out_printf(o, " (game: %.1f %.1f %.0f %.2f)", b->base[0], b->base[1], b->base[2], b->base[3]);
        out_printf(o, "\n");
    }
}

// flashlight list: every hero light in this world, as this machine sees it (replicated state on clients)
static void list_lights(Out *o) {
    if (!cls_hlc) cls_hlc = ue_find_class("HeroLightComponent");
    UObject *pc = ue_local_pc(), *mine = pc ? ue_get_ptr(pc, "Pawn") : NULL;
    int32_t n = ue_num_objects(), hits = 0;
    char b[256], b2[256];
    for (int32_t i = 0; cls_hlc && i < n; i++) {
        UObject *c = ue_object_at(i);
        if (!c || (U_FLAGS(c) & 0x30) || !ue_is_a(c, cls_hlc) || !U_OUTER(c) || (U_FLAGS(U_OUTER(c)) & 0x30)) continue;   // skip CDOs and templates
        UObject *hero = U_OUTER(c);
        UObject *ps = ue_get_ptr(hero, "PlayerState"), *owner = ps ? ue_get_ptr(ps, "Owner") : NULL;
        out_printf(o, "%s%s light=%s role=%s manual=%s volume_requests=%d dark_card=%d owner=%s\n",
                   ue_obj_name(hero, b, sizeof b), hero == mine ? " (mine)" : "", HLC_VISIBLE(c) ? "on" : "off",
                   is_authority(c) ? "authority" : "proxy", manual_find(c) ? "yes" : "no", HLC_REQUESTS(c)->num,
                   HLC_DARKCARD(c), owner ? ue_obj_name(owner, b2, sizeof b2) : "-");
        hits++;
    }
    out_printf(o, "%d hero light(s)\n", hits);
}

// flashlight [on|off|toggle|auto|status|list]
void cmd_flashlight(const char *arg, Out *o) {
    if (arg && !strcmp(arg, "list")) { list_lights(o); return; }
    UObject *c = local_light();
    if (!c) { out_printf(o, "no local hero light (not in a level / no pawn)\n"); return; }
    int auth = is_authority(c), vis = HLC_VISIBLE(c);
    if (!arg || !*arg || !strcmp(arg, "status")) {
        out_printf(o, "light=%s role=%s manual=%s sticky=%d volume_requests=%d dark_card=%d hooks=%d key=0x%02x\n",
                   vis ? "on" : "off", auth ? "authority" : "client", manual_find(c) ? "yes" : "no", sticky,
                   HLC_REQUESTS(c)->num, HLC_DARKCARD(c), hooked, hotkey);
        beam_status(o, c);
        return;
    }
    if (!strcmp(arg, "auto")) {
        if (!auth) { out_printf(o, "auto: host only (a client's override lasts until the next map)\n"); return; }
        if (!hooked) { out_printf(o, "auto: hooks not installed\n"); return; }
        restore_auto(c);
        out_printf(o, "flashlight: automatic again, light=%s\n", HLC_VISIBLE(c) ? "on" : "off");
        return;
    }
    int want = !strcmp(arg, "on") ? 1 : !strcmp(arg, "off") ? 0 : !strcmp(arg, "toggle") ? !vis : -1;
    if (want < 0) { out_printf(o, "usage: flashlight [on|off|toggle|auto|status]\n"); return; }
    if (want == vis) { out_printf(o, "flashlight: already %s\n", vis ? "on" : "off"); return; }
    if (request_toggle(c)) { out_printf(o, "flashlight: RPC not found\n"); return; }
    out_printf(o, auth ? "flashlight: %s\n" : "flashlight: requested %s from host (replicates back)\n",
               auth ? (HLC_VISIBLE(c) ? "on" : "off") : (want ? "on" : "off"));
}

// ---- hotkey (only while the game window has focus) ----
void flashlight_tick(float dt) {
    static int was_down; static float cooldown;
    beam_tick();
    if (!hotkey) return;
    if (cooldown > 0) cooldown -= dt;
    int down = cmds_hotkey_down(hotkey);
    if (down && !was_down && cooldown <= 0) {
        UObject *c = local_light();
        if (c) { request_toggle(c); LOG("flashlight: hotkey toggle (was %d)", HLC_VISIBLE(c)); }
        cooldown = 0.25f;
    }
    was_down = down;
}

// b4bcoop.ini: flashlight_key=L (a letter/digit, or a VK code like 0x4C; off disables), flashlight_sticky=1,
// flashlight_width=100 (25-300), flashlight_range=100 (25-400), flashlight_brightness=100 (0-500): percent of the
// game's beam, your own view only. All change live (cmds_ini_poll; val NULL = removed: the default).
static float pct(const char *v, float lo, float hi) {
    float f = v ? (float)atof(v) : 100.f;
    if (!(f == f)) f = 100.f;
    return f < lo ? lo : f > hi ? hi : f;
}
int flashlight_live(const char *key, const char *v) {
    if (!strcmp(key, "flashlight_sticky")) sticky = v ? atoi(v) : 1;
    else if (!strcmp(key, "flashlight_key")) hotkey = v ? cmds_parse_key(v) : 'L';
    else if (!strcmp(key, "flashlight_width")) { beam_w = pct(v, 25, 300); beam_settings_changed(); }
    else if (!strcmp(key, "flashlight_range")) { beam_r = pct(v, 25, 400); beam_settings_changed(); }
    else if (!strcmp(key, "flashlight_brightness")) { beam_b = pct(v, 0, 500); beam_settings_changed(); }
    else return 0;
    return 1;
}
static void ini_pair(const char *k, const char *v, void *ctx) { (void)ctx; flashlight_live(k, v); }
static void load_config(void) { cmds_ini_each(ini_pair, NULL); }

int flashlight_hotkey(void) { return hotkey; }

// ~ overlay tab (overlay.h): the light itself through /flashlight, key and sticky mode as b4bcoop.ini settings
static void fl_panel(void) {
    UObject *c = local_light();
    int auth = c && is_authority(c);
    if (!c) ov_text_dim("No hero right now: your light shows here in Fort Hope and in missions.");
    else ov_text("Your flashlight: %s (%s)", HLC_VISIBLE(c) ? "on" : "off", manual_find(c) ? "set by hand" : "automatic");
    if (ov_button("Toggle")) ov_run("flashlight toggle");
    ov_same_line();
    if (ov_button("On")) ov_run("flashlight on");
    ov_same_line();
    if (ov_button("Off")) ov_run("flashlight off");
    ov_same_line();
    ov_begin_disabled(c && !auth, "Host only: on a client your choice lasts until the next map.");
    if (ov_button("Automatic")) ov_run("flashlight auto");
    ov_tooltip("Hand the light back to the game's own switching (dark and bright areas).");
    ov_end_disabled();
    int k = hotkey;
    if (ov_key("Toggle key##flashlight_key", &k)) ov_setting_key("flashlight_key", k);
    int st = sticky;
    if (ov_checkbox("Keep a manual choice (sticky)##sticky", &st)) ov_setting("flashlight_sticky", st ? "1" : "0", 1);
    ov_tooltip("Host setting: once a player switches their light by hand, dark or bright areas no longer switch it, "
               "until the next map.");
    ov_heading("Beam (your view)");
    static const struct { const char *label, *key; float lo, hi; const char *tip; } S[3] = {
        {"Width##flashlight_width", "flashlight_width", 25, 300, "Cone angle, percent of the game's. It stops at a 160-degree cone (about 175% in first person)."},
        {"Range##flashlight_range", "flashlight_range", 25, 400, "How far the light reaches, percent of the game's."},
        {"Brightness##flashlight_brightness", "flashlight_brightness", 0, 500, "Light intensity, percent of the game's."},
    };
    float *cur[3] = {&beam_w, &beam_r, &beam_b};
    for (int i = 0; i < 3; i++) {
        float v = *cur[i];
        ov_width(14);
        if (ov_slider(S[i].label, &v, S[i].lo, S[i].hi, "%.0f%%")) ov_setting_f(S[i].key, v, 0);
        if (ov_edit_done()) ov_setting_f(S[i].key, v, 1);
        ov_tooltip(S[i].tip);
    }
    if (ov_button("Reset beam")) for (int i = 0; i < 3; i++) ov_setting(S[i].key, NULL, 1);
    if (c && HLC_SPOTS(c)->num > 0) {
        UObject *s = ((UObject **)HLC_SPOTS(c)->data)[0];
        if (!cls_spot) cls_spot = ue_find_class("SpotLightComponent");
        if (s && cls_spot && ue_is_a(s, cls_spot))
            ov_text_dim("Now: cone %.0f degrees, reach %.0f m, intensity %.1f (%s person).", 2 * *bf(s, B_OUTER),
                        *bf(s, B_RADIUS) / 100.f, *bf(s, B_INTENSITY), HLC_MODE(c) >= 3 ? "third" : "first");
    }
    ov_text_dim("Only your own screen: other players see your light as the game draws it, and you see theirs that way. "
                "A wider beam spreads over more area; raise the brightness to keep it as bright.");
}

int flashlight_init(void) {
    load_config();
    overlay_add_panel("Flashlight", 40, fl_panel);
    LOG("flashlight: key=0x%02x sticky=%d beam width %.0f%% range %.0f%% brightness %.0f%%", hotkey, sticky, beam_w,
        beam_r, beam_b);
    if (memcmp((void *)ADDR_HLC_SET, SIG_SET, sizeof SIG_SET) || memcmp((void *)ADDR_HLC_TOGGLE, SIG_TOGGLE, sizeof SIG_TOGGLE)) {
        LOG("flashlight: signature mismatch, sticky mode off");
        return -1;
    }
    if (MH_CreateHook((void *)ADDR_HLC_SET, (void *)set_detour, (void **)&orig_set) != MH_OK ||
        MH_CreateHook((void *)ADDR_HLC_TOGGLE, (void *)toggle_detour, (void **)&orig_toggle) != MH_OK ||
        MH_EnableHook((void *)ADDR_HLC_SET) != MH_OK || MH_EnableHook((void *)ADDR_HLC_TOGGLE) != MH_OK) {
        LOG("flashlight: hook failed");
        return -1;
    }
    hooked = 1;
    LOG("flashlight: hooks installed");
    return 0;
}
