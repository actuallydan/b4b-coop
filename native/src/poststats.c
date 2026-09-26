// Post-round stats of remote players (docs/investigations/post-round-stats.md).
// Diagnostics for now: every build logs each player's post-round values once per post-round screen
// ("poststats: ..." lines), so a real session's logs (host and clients) show what each machine had; dev builds also
// have the `poststats` command (same dump, any time).
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "ue.h"
#include "cmds.h"
#include "log.h"

// GobiPlayerState: PostRoundStatValues (TArray<int32>, one per PostRoundStatConfigs entry), PostRoundLineupStats
// (double[11], EPostRoundLineupStat), ScoreboardStats (double[2]); all Replicated, written on the server in
// GobiPlayerState::Tick (0x141BCE320 -> 0x141BCE650) from PlayerStatsComponent (PS+0x768). Engine PlayerState's
// bool byte (bIsABot .. bFromPreviousLevel) at the bIsABot offset.
static int32_t off_values = -1, off_lineup = -1, off_score = -1, off_configs = -1, off_stats = -1, off_bits = -1;

static int offsets(UObject *ps) {
    if (off_values < 0) {
        off_values = ue_prop_offset(ps, "PostRoundStatValues");
        off_lineup = ue_prop_offset(ps, "PostRoundLineupStats");
        off_score = ue_prop_offset(ps, "ScoreboardStats");
        off_configs = ue_prop_offset(ps, "PostRoundStatConfigs");
        off_stats = ue_prop_offset(ps, "PlayerStatsComponent");
        off_bits = ue_prop_offset(ps, "bIsABot");
    }
    return off_values >= 0 && off_lineup >= 0 && off_score >= 0;
}

// PlayerStatsComponent stat banks (EPlayerProfileStat, 0x58 bytes each, value first): +0x158 this map (reconciled
// into the profile and reset on leaving the map), +0x1078 since the component was created (the post-round values),
// +0x1F98 not yet sent to the owning client (ClientApplyStatDeltas). IncrementStatValue 0x1422B6890.
#define BANK_MAP  0x158
#define BANK_ALL  0x1078
#define STAT_KILLS 0   // EPlayerProfileStat::RiddenKilled

typedef void (*Emit)(void *ctx, const char *line);

static void dump(Emit emit, void *ctx) {
    UObject **pa; int n = admin_player_array(&pa);
    char line[512];
    if (!n) { emit(ctx, "no players"); return; }
    if (!offsets(pa[0])) { emit(ctx, "properties not found"); return; }
    for (int i = 0; i < n; i++) {
        UObject *ps = pa[i];
        char nm[64];
        admin_display_name(ps, nm, sizeof nm);
        TArray *v = (TArray *)((char *)ps + off_values);
        double *l = (double *)((char *)ps + off_lineup), *s = (double *)((char *)ps + off_score);
        UObject *sc = off_stats >= 0 ? *(UObject **)((char *)ps + off_stats) : NULL;
        int k = snprintf(line, sizeof line, "#%d %s values[%d]:", i, nm, v->num);
        for (int j = 0; j < v->num && j < 16 && k < (int)sizeof line - 16; j++)
            k += snprintf(line + k, sizeof line - k, " %d", ((int32_t *)v->data)[j]);
        k += snprintf(line + k, sizeof line - k, " lineup:");
        for (int j = 0; j < 11 && k < (int)sizeof line - 16; j++) k += snprintf(line + k, sizeof line - k, " %.0f", l[j]);
        k += snprintf(line + k, sizeof line - k, " scoreboard: %.0f %.0f bits=%02x", s[0], s[1],
                      off_bits >= 0 ? *((uint8_t *)ps + off_bits) : 0xff);
        if (sc) snprintf(line + k, sizeof line - k, " stats: kills map %d all %d",
                         *(int32_t *)((char *)sc + BANK_MAP + STAT_KILLS * 0x58),
                         *(int32_t *)((char *)sc + BANK_ALL + STAT_KILLS * 0x58));
        else snprintf(line + k, sizeof line - k, " stats: none");
        emit(ctx, line);
    }
}

static void emit_log(void *ctx, const char *line) { (void)ctx; LOG("poststats: %s", line); }

// Once per post-round screen (GameState.MatchState WaitingPostMatch), 5 s in: the replicated values have arrived.
static int logged;
static float in_post;
void poststats_tick(float dt) {
    static float acc;
    if ((acc += dt) < 0.5f) return;
    float step = acc; acc = 0;
    UObject *w = ue_world(), *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    int32_t off = gs ? ue_prop_offset(gs, "MatchState") : -1;
    char ms[64] = "";
    if (off >= 0) ue_name(*(FName *)((char *)gs + off), ms, sizeof ms);
    if (strcmp(ms, "WaitingPostMatch")) { in_post = 0; logged = 0; return; }
    if (logged || (in_post += step) < 5.f) return;
    logged = 1;
    LOG("poststats: post-round values (%s)", admin_is_client() ? "client" : "host");
    dump(emit_log, NULL);
}

#ifndef B4B_RELEASE
static void emit_out(void *ctx, const char *line) { out_printf((Out *)ctx, "%s\n", line); }

int poststats_cmd(const char *verb, char *rest, Out *o) {
    (void)rest;
    if (strcmp(verb, "poststats")) return 0;
    dump(emit_out, o);
    return 1;
}
#endif
