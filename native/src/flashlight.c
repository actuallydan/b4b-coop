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
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

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
    static UObject *cache, *cache_pawn; static int32_t cache_idx;
    UObject *pc = ue_local_pc();
    UObject *pawn = pc ? ue_get_ptr(pc, "Pawn") : NULL;
    if (!pawn) return NULL;
    if (cache && cache_pawn == pawn && ue_object_at(cache_idx) == cache && U_OUTER(cache) == pawn) return cache;
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

// b4bcoop.ini: flashlight_key=L (a letter/digit, or a VK code like 0x4C; off disables), flashlight_sticky=1. Both
// also change live (cmds_ini_poll; val NULL = removed: the default).
int flashlight_live(const char *key, const char *v) {
    if (!strcmp(key, "flashlight_sticky")) sticky = v ? atoi(v) : 1;
    else if (!strcmp(key, "flashlight_key")) hotkey = v ? cmds_parse_key(v) : 'L';
    else return 0;
    return 1;
}
static void ini_pair(const char *k, const char *v, void *ctx) { (void)ctx; flashlight_live(k, v); }
static void load_config(void) { cmds_ini_each(ini_pair, NULL); }

int flashlight_init(void) {
    load_config();
    LOG("flashlight: key=0x%02x sticky=%d", hotkey, sticky);
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
