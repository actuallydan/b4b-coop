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
    tp_pawn = pawn; tp_pawni = U_INDEX(pawn); tp_pvc = NULL; tp_pvci = -1; tp_ads_age = 99.f; tp_written = 0; n_tp_ads = 0;
    for (int32_t i = 0, n = ue_num_objects(); c && i < n; i++) {
        UObject *x = ue_object_at(i);
        if (x && !(U_FLAGS(x) & LIVE_FLAGS) && ue_is_a(x, c) && COMP_OWNER(x) == pawn) { tp_pvc = x; tp_pvci = i; break; }
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
// every tick while on: third person, first person while aiming. A view the game itself asked for (a tag-driven 2 we
// didn't write: healing, pounced, grabbed, ...; orbit 3) is left alone; when the game drops back to 1 we write 2 again.
static void tp_sync(float dt) {
    UObject *pawn = local_hero(), *pvc = view_comp(pawn);
    if (!pvc) return;
    if ((tp_ads_age += dt) > 1.f) { tp_ads_age = 0; ads_refresh(pawn); }
    uint8_t cur = PVC_WANT(pvc), want = hero_ads() ? 1 : 2;
    if (cur == want || cur == 3 || (cur == 2 && tp_written != 2)) return;
    if (view_set(pvc, want)) tp_written = want;
}

void thirdperson_tick(float dt) {
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
        }
        fclose(f);
    }
    LOG("thirdperson: start %s, key=0x%02x", tp_on ? "on" : "off", tp_key);
    return 0;
}

// /thirdperson [on|off|status] (chat, everyone)
void cmd_thirdperson(const char *arg_in, Out *o) {
    char arg[16] = "";
    if (arg_in) sscanf(arg_in, " %15s", arg);
    int en = !*arg ? !tp_on : !_stricmp(arg, "on") ? 1 : !_stricmp(arg, "off") ? 0 : !_stricmp(arg, "status") ? 2 : -1;
    if (en < 0) { out_printf(o, "usage: /thirdperson [on|off]\n"); return; }
    if (en == 2) { out_printf(o, "third person %s\n", tp_on ? "on" : "off"); return; }
    UObject *pvc = view_comp(local_hero());
    if (!en) {
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
// Dev CLI: `thirdperson [on|off|status]` = the chat command; `thirdperson view [1|2|3]` dumps the local hero's
// PlayerViewComponent (view bytes, tag lists, owner tags, ADS), a digit sets the view once.
int thirdperson_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "thirdperson")) return 0;
    char *what = rest ? strtok(rest, " ") : NULL, *arg = what ? strtok(NULL, " ") : NULL;
    if (!what || strcmp(what, "view")) { cmd_thirdperson(what, o); return 1; }
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
