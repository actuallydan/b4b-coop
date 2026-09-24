// Survivor team size (issue #1: 5+ player co-op). Retail has no fixed slot array: APlayerSlotManager::InitSlots
// builds TeamSlots from Config.NumTeams/Config.TeamSize (+0x2a0/+0x2a4), and the only source of TeamSize is the
// class default (native ctor 0x14224C850 writes 4). InitSlots runs on the authority only, once per map, right after
// the GameState spawns the slot manager. We raise Config.TeamSize just before it runs; clients get the extra slots
// through the replicated TeamSlots array. Opt-in: `teamsize=N` in b4bcoop.ini or the `teamsize N` command (applies
// from the next map load). See docs/investigations/five-players.md.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define ADDR_INITSLOTS    VA(0x141A14860ull)  // void APlayerSlotManager::InitSlots(APlayerSlotManager*)
#define ADDR_MAXPL_LOAD   VA(0x143CC0643ull)  // AGameSession::ApproveLogin: mov rax,[rip+x] -> int* net.MaxPlayersOverride
static const uint8_t SIG_INITSLOTS[] = {0x40,0x55,0x53,0x41,0x55,0x41,0x56,0x48,0x8d,0x6c,0x24,0xc1,0x48,0x81,0xec,0xf8,
                                        0x00,0x00,0x00,0x48,0x8b,0x05,0x66,0x2f,0x9e,0x04,0x48,0x33,0xc4,0x48,0x89,0x45};
static const uint8_t SIG_MAXPL[] = {0x48,0x8b,0x05,0xc6,0x75,0xca,0x02,0x8b,0x30,0x85,0xf6,0x7f,0x0e,0x8b,0xb7,0xa4,
                                    0x02,0x00,0x00};

#define OFF_CONFIG     0x2a0   // APlayerSlotManager.Config (SlotManagerConfig: NumTeams i32, TeamSize i32, CharacterSelectClass)
#define TEAMSLOTS_SZ   0x20    // FTeamSlots: Team u8 +0, MatchmakingTeam u8 +1, Slots TArray +8, CharacterSelect +0x18
#define MAX_TEAM       8

typedef void (*InitSlotsFn)(UObject *psm, void *a2);
static InitSlotsFn orig_initslots;
static int team_size;          // 0 = leave the game's value alone
static int *maxplayers_cvar;   // net.MaxPlayersOverride storage (0 = use GameSession.MaxPlayers)

static void apply_maxplayers(void) {
    // "Server full." check: NumPlayers >= (cvar > 0 ? cvar : GameSession.MaxPlayers). Leave headroom for a
    // client whose stale connection has not timed out yet when it rejoins after a failed follow.
    if (maxplayers_cvar && team_size > 0 && *maxplayers_cvar < team_size + 2) {
        *maxplayers_cvar = team_size + 2;
        LOG("teamsize: net.MaxPlayersOverride=%d", *maxplayers_cvar);
    }
}

static void initslots_detour(UObject *psm, void *a2) {
    int32_t *cfg = (int32_t *)((char *)psm + OFF_CONFIG);
    if (team_size > 0 && cfg[1] < team_size) {
        LOG("teamsize: InitSlots %d team(s): TeamSize %d -> %d", cfg[0] + 1, cfg[1], team_size);
        cfg[1] = team_size;
    }
    orig_initslots(psm, a2);
}

static void set_team_size(int n) {
    team_size = n < 0 ? 0 : n > MAX_TEAM ? MAX_TEAM : n;
    LOG("teamsize: %d%s", team_size, team_size ? " (from next map load)" : " (game default)");
    apply_maxplayers();
}

static void load_ini(void) {
    const char *path = cmds_config_path();   // b4bcoop.ini or B4B_COOP_CONFIG (per-instance testing)
    char line[200];
    FILE *f = fopen(path, "r");
    if (!f) return;
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, "teamsize=", 9)) set_team_size(atoi(line + 9));
    fclose(f);
}

int teamsize_init(void) {
    if (!memcmp((void *)ADDR_MAXPL_LOAD, SIG_MAXPL, sizeof SIG_MAXPL)) {
        uint8_t *ins = (uint8_t *)ADDR_MAXPL_LOAD;
        maxplayers_cvar = *(int **)(ins + 7 + *(int32_t *)(ins + 3));
    } else LOG("teamsize: MaxPlayersOverride signature mismatch");
    if (memcmp((void *)ADDR_INITSLOTS, SIG_INITSLOTS, sizeof SIG_INITSLOTS)) { LOG("teamsize: signature mismatch"); return -1; }
    if (MH_CreateHook((void *)ADDR_INITSLOTS, (void *)initslots_detour, (void **)&orig_initslots) != MH_OK ||
        MH_EnableHook((void *)ADDR_INITSLOTS) != MH_OK) { LOG("teamsize: hook failed"); return -1; }
    LOG("teamsize: InitSlots hooked");
    load_ini();
    return 0;
}

// ---- commands ----
static UObject *slot_manager(void) {
    UObject *w = ue_world();
    UObject *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    return gs ? ue_get_ptr(gs, "PlayerSlotManager") : NULL;
}

static void print_fstring(Out *o, FString *s) {
    for (int i = 0; s->data && i < s->num && s->data[i]; i++) out_printf(o, "%c", s->data[i] < 128 ? (char)s->data[i] : '?');
}

static void print_ps(Out *o, const char *label, UObject *ps) {
    out_printf(o, " %s=", label);
    int32_t off = ps ? ue_prop_offset(ps, "PlayerNamePrivate") : -1;
    if (off < 0) { out_printf(o, "-"); return; }
    out_printf(o, "'"); print_fstring(o, (FString *)((char *)ps + off)); out_printf(o, "'");
    UObject *owner = ue_get_ptr(ps, "Owner");
    char b[128];
    if (owner) out_printf(o, "(%s)", ue_obj_name(U_CLASS(owner), b, sizeof b));
}

static void cmd_slots(Out *o) {
    UObject *psm = slot_manager();
    if (!psm) { out_printf(o, "no PlayerSlotManager\n"); return; }
    char b[256];
    int32_t *cfg = (int32_t *)((char *)psm + OFF_CONFIG);
    int32_t bots = ue_prop_offset(psm, "bSupportsBots");
    out_printf(o, "%s: Config NumTeams=%d TeamSize=%d bSupportsBots=%d | teamsize setting=%d",
               ue_obj_name(U_CLASS(psm), b, sizeof b), cfg[0], cfg[1], bots >= 0 ? *((uint8_t *)psm + bots) : -1, team_size);
    if (maxplayers_cvar) out_printf(o, " net.MaxPlayersOverride=%d", *maxplayers_cvar);
    UObject *w = ue_world(), *gm = w ? ue_get_ptr(w, "AuthorityGameMode") : NULL, *sess = gm ? ue_get_ptr(gm, "GameSession") : NULL;
    int32_t mp = sess ? ue_prop_offset(sess, "MaxPlayers") : -1;
    if (mp >= 0) out_printf(o, " GameSession.MaxPlayers=%d", *(int32_t *)((char *)sess + mp));
    out_printf(o, "\n");
    TArray *teams = (TArray *)((char *)psm + ue_prop_offset(psm, "TeamSlots"));
    for (int t = 0; t < teams->num; t++) {
        uint8_t *ts = (uint8_t *)teams->data + t * TEAMSLOTS_SZ;
        TArray *slots = (TArray *)(ts + 8);
        out_printf(o, "team[%d] GobiTeam=%d MatchmakingTeam=%d slots=%d\n", t, ts[0], ts[1], slots->num);
        for (int i = 0; i < slots->num; i++) {
            UObject *s = ((UObject **)slots->data)[i];
            if (!s) { out_printf(o, "  [%d] null\n", i); continue; }
            int32_t hero = ue_prop_offset(s, "CurrentHeroRowHandle"), res = ue_prop_offset(s, "bReserved");
            out_printf(o, "  [%d] hero=%s", i, hero >= 0 ? ue_name(*(FName *)((char *)s + hero + 8), b, sizeof b) : "?");
            print_ps(o, "owner", ue_get_ptr(s, "OwningPlayer"));
            print_ps(o, "ctrl", ue_get_ptr(s, "ControllingPlayer"));
            UObject *pawn = ue_get_ptr(s, "AssignedPawn");
            out_printf(o, " pawn=%s reserved=%d\n", pawn ? ue_obj_name(U_CLASS(pawn), b, sizeof b) : "-",
                       res >= 0 ? *((uint8_t *)s + res) : -1);
        }
    }
}

// Returns 1 if the verb was ours.
int teamsize_cmd(const char *verb, char *rest, Out *o) {
    if (!strcmp(verb, "slots")) { cmd_slots(o); return 1; }
    if (!strcmp(verb, "teamsize")) {
        if (rest && *rest) set_team_size(atoi(rest));
        out_printf(o, "teamsize=%d%s\n", team_size, team_size ? " (applies from the next map load, host only)" : " (game default)");
        return 1;
    }
    return 0;
}

// Clients: Config is not replicated, so their slot manager keeps the class default (4). UI reads it through
// PartyPlayerBlueprintFunctionLibrary::GetTeamSizeData; keep it in step with the replicated hero team.
void teamsize_tick(float dt) {
    static float acc;
    if (!team_size || (acc += dt) < 1.f) return;
    acc = 0;
    UObject *psm = slot_manager();
    if (!psm) return;
    int32_t *cfg = (int32_t *)((char *)psm + OFF_CONFIG);
    TArray *teams = (TArray *)((char *)psm + ue_prop_offset(psm, "TeamSlots"));
    if (teams->num > 0) {
        int n = ((TArray *)((uint8_t *)teams->data + 8))->num;
        if (n > cfg[1]) cfg[1] = n;
    }
}
