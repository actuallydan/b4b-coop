// Add-ons in multiplayer (#22, epic #23). docs/investigations/addons.md §7, player page docs/COMMANDS.md "Add-ons".
//
// L4D-like: add-ons stay local. Every player sees the add-ons they installed; nothing is sent to other machines
// except a small summary in the join URL, so the host can decide who may join:
//   client: every join and rejoin carries ?b4bcoopaddons=<C>c<G>g[,g<id8>-<Title>...][,c<id8>...][,+<n>]
//     C/G = cosmetic / gameplay-affecting add-ons mounted in this game (addonclass.c decides, not the author),
//     id8 = first 8 hex digits of the content id (pak index SHA1), Title only for gameplay ones (letters/digits/_,
//     24 max). The whole value is capped at SUMMARY_MAX (the login URL is one FString, max 1024 chars on the wire);
//     entries that don't fit are counted in +<n>. No option at all = an older b4bcoop (no add-on loader).
//   host (admin.c PreLogin, after the join policy and the protocol check): ini addons_policy=
//     any       everyone may join, whatever they run
//     cosmetic  (default, ADDONS_POLICY_DEFAULT) gameplay-affecting add-ons are refused, naming them
//     none      no add-ons at all
//     match     cosmetic ones are free; gameplay ones must be exactly the host's (same content ids)
//   Both sides get the reason: the joiner as its login error ("Could not join: Host allows ..."), the host as a chat
//   line. /addons players (host) lists what everyone runs; /addons policy [x] shows or sets it for this session.
// No protocol bump: an older client sends no summary and has no add-on loader, so "absent" really means "none", and
// an older host ignores the option (addons.md §7).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "ue.h"
#include "log.h"
#include "cmds.h"
#include "overlay.h"

enum { POL_ANY, POL_COSMETIC, POL_NONE, POL_MATCH };
#define ADDONS_POLICY_DEFAULT POL_COSMETIC   // the one place to change the default (docs: COMMANDS.md, addons.md)
static const char *POLICY_NAMES[] = {"any", "cosmetic", "none", "match"};
static int policy = ADDONS_POLICY_DEFAULT;

#define SUMMARY_MAX 400
#define TITLE_MAX 24
#define MAX_ENTRIES 64

int addons_policy_set(const char *v) {
    for (int i = 0; i < 4; i++) if (!_stricmp(v, POLICY_NAMES[i])) { policy = i; return 0; }
    return -1;
}
const char *addons_policy_name(void) { return POLICY_NAMES[policy]; }

// b4bcoop.ini addons_policy= changed while the game runs (or set from the ~ window): NULL = back to the default
int addons_live(const char *key, const char *v) {
    if (strcmp(key, "addons_policy")) return 0;
    if (!v) policy = ADDONS_POLICY_DEFAULT;
    else if (addons_policy_set(v)) LOG("addons: bad addons_policy=%s (any|cosmetic|none|match), keeping %s", v, addons_policy_name());
    LOG("addons: policy %s (b4bcoop.ini)", POLICY_NAMES[policy]);
    return 1;
}

// ---- summary ----
typedef struct { char g; char id[9]; char title[TITLE_MAX + 1]; } Entry;
typedef struct { int present, nc, ng, omitted, n; Entry e[MAX_ENTRIES]; } Summary;

static void title_token(const char *in, char *out, size_t n) {   // "Heavy physics!" -> "Heavy_physics"
    size_t k = 0;
    int us = 0;
    for (; *in && k + 1 < n; in++) {
        if (isalnum((unsigned char)*in) && (unsigned char)*in < 128) { out[k++] = *in; us = 0; }
        else if (!us && k) { out[k++] = '_'; us = 1; }
    }
    while (k && out[k - 1] == '_') k--;
    out[k] = 0;
}
static void title_show(const char *tok, char *out, size_t n) {   // "Heavy_physics" -> "Heavy physics"
    snprintf(out, n, "%s", tok);
    for (char *c = out; *c; c++) if (*c == '_') *c = ' ';
}

static int own(AddonRef *r, int max) { return addons_active(r, max); }

const char *addons_login_option(void) {
    static char opt[SUMMARY_MAX + 32];
    AddonRef r[MAX_ADDONS];
    int n = own(r, MAX_ADDONS), nc = 0, ng = 0, omitted = 0;
    for (int i = 0; i < n; i++) { if (r[i].gameplay) ng++; else nc++; }
    char v[SUMMARY_MAX + 1];
    int k = snprintf(v, sizeof v, "%dc%dg", nc, ng);
    for (int pass = 1; pass >= 0; pass--)   // gameplay ones first: they are the ones a host checks
        for (int i = 0; i < n; i++) {
            if (r[i].gameplay != pass) continue;
            char e[48], t[TITLE_MAX + 1];
            if (pass) { title_token(r[i].title, t, sizeof t); snprintf(e, sizeof e, ",g%.8s%s%s", r[i].hash, t[0] ? "-" : "", t); }
            else snprintf(e, sizeof e, ",c%.8s", r[i].hash);
            if (k + (int)strlen(e) + 6 > SUMMARY_MAX) { omitted++; continue; }
            k += snprintf(v + k, sizeof v - k, "%s", e);
        }
    if (omitted) snprintf(v + k, sizeof v - k, ",+%d", omitted);
    snprintf(opt, sizeof opt, "?b4bcoopaddons=%s", v);
    return opt;
}

static void parse(const char *v, Summary *s) {
    memset(s, 0, sizeof *s);
    if (!v || !*v) return;
    s->present = 1;
    if (sscanf(v, "%dc%dg", &s->nc, &s->ng) != 2) { s->nc = s->ng = 0; }
    if (s->nc < 0 || s->nc > 9999) s->nc = 0;
    if (s->ng < 0 || s->ng > 9999) s->ng = 0;
    for (const char *p = strchr(v, ','); p; p = strchr(p + 1, ',')) {
        const char *q = p + 1;
        if (*q == '+') { s->omitted = atoi(q + 1); continue; }
        if ((*q != 'g' && *q != 'c') || s->n == MAX_ENTRIES) continue;
        Entry *e = &s->e[s->n];
        e->g = *q == 'g';
        int k = 0;
        for (q++; k < 8 && isxdigit((unsigned char)*q); q++) e->id[k++] = (char)tolower((unsigned char)*q);
        e->id[k] = 0;
        if (k != 8) continue;
        if (*q == '-') {
            k = 0;
            for (q++; *q && *q != ',' && k < TITLE_MAX; q++) e->title[k++] = isalnum((unsigned char)*q) ? *q : '_';
            e->title[k] = 0;
        }
        s->n++;
    }
}

// "Heavy physics, Big guns and 2 more" from the gameplay (g=1) or cosmetic (g=0) entries; "" if none is listed
static void names_of(const Summary *s, int g, char *out, size_t n) {
    size_t k = 0; int shown = 0;
    out[0] = 0;
    for (int i = 0; i < s->n; i++) {
        if (s->e[i].g != g) continue;
        if (shown == 3) continue;
        char t[TITLE_MAX + 1];
        if (s->e[i].title[0]) title_show(s->e[i].title, t, sizeof t); else snprintf(t, sizeof t, "%s", s->e[i].id);
        k += (size_t)snprintf(out + k, k < n ? n - k : 0, "%s%s", shown ? ", " : "", t);
        shown++;
    }
    int all = g ? s->ng : s->nc;
    if (all > shown && shown) snprintf(out + k, k < n ? n - k : 0, " and %d more", all - shown);
}

// Host: is this joiner's summary acceptable under our policy? 1 = refused, err = login error for the joiner.
int addons_login_check(const char *value, const char *name, const char *key, char *err, size_t en) {
    Summary s;
    parse(value, &s);
    char list[200], why[240] = "";
    err[0] = 0;
    if (policy == POL_ANY) return 0;
    if (policy == POL_COSMETIC && s.ng > 0) {
        names_of(&s, 1, list, sizeof list);
        if (!list[0]) snprintf(list, sizeof list, "%d unlisted", s.ng);
        snprintf(err, en, "Host allows cosmetic add-ons only; you have gameplay add-ons: %s. Switch them off (/addons off <#>) and restart the game.", list);
        snprintf(why, sizeof why, "they have gameplay add-ons (%s); addons_policy=cosmetic", list);
    } else if (policy == POL_NONE && s.nc + s.ng > 0) {
        names_of(&s, 1, list, sizeof list);
        snprintf(err, en, "Host allows no add-ons; you have %d%s%s%s. Switch them off (/addons off <#>) and restart the game.",
                 s.nc + s.ng, list[0] ? " (gameplay: " : "", list, list[0] ? ")" : "");
        snprintf(why, sizeof why, "they have %d add-on(s); addons_policy=none", s.nc + s.ng);
    } else if (policy == POL_MATCH) {
        AddonRef r[MAX_ADDONS];
        int n = own(r, MAX_ADDONS);
        char lack[200] = "", extra[200] = "";
        size_t kl = 0, ke = 0;
        int nl = 0, nx = 0;
        for (int i = 0; i < n; i++) {   // host's gameplay add-ons the joiner doesn't have
            if (!r[i].gameplay) continue;
            int have = 0;
            for (int j = 0; j < s.n; j++) if (s.e[j].g && !strncmp(s.e[j].id, r[i].hash, 8)) have = 1;
            if (have) continue;
            if (nl++ < 3) kl += (size_t)snprintf(lack + kl, kl < sizeof lack ? sizeof lack - kl : 0, "%s%.24s", kl ? ", " : "", r[i].title);
        }
        for (int j = 0; j < s.n; j++) {   // joiner's gameplay add-ons the host doesn't have
            if (!s.e[j].g) continue;
            int have = 0;
            for (int i = 0; i < n; i++) if (r[i].gameplay && !strncmp(s.e[j].id, r[i].hash, 8)) have = 1;
            if (have) continue;
            char t[TITLE_MAX + 1];
            if (s.e[j].title[0]) title_show(s.e[j].title, t, sizeof t); else snprintf(t, sizeof t, "%s", s.e[j].id);
            if (nx++ < 3) ke += (size_t)snprintf(extra + ke, ke < sizeof extra ? sizeof extra - ke : 0, "%s%s", ke ? ", " : "", t);
        }
        int listed = 0;
        for (int j = 0; j < s.n; j++) listed += s.e[j].g;
        if (s.ng > listed) {   // gameplay ids cut from the summary: can't be matched
            nx += s.ng - listed;
            ke += (size_t)snprintf(extra + ke, ke < sizeof extra ? sizeof extra - ke : 0, "%s%d unlisted", ke ? ", " : "", s.ng - listed);
        }
        if (nl || nx) {
            size_t k = (size_t)snprintf(err, en, "Host requires the same gameplay add-ons as theirs.");
            if (nl) k += (size_t)snprintf(err + k, k < en ? en - k : 0, " You lack: %s%s.", lack, nl > 3 ? " ..." : "");
            if (nx) k += (size_t)snprintf(err + k, k < en ? en - k : 0, " Switch off: %s%s.", extra, nx > 3 ? " ..." : "");
            snprintf(err + k, k < en ? en - k : 0, " Then restart the game.");
            snprintf(why, sizeof why, "their gameplay add-ons differ (%d missing, %d extra); addons_policy=match", nl, nx);
        }
    }
    if (!err[0]) return 0;
    LOG("addons: login %s (%s) refused: %s [summary %s]", name, key, err, value && *value ? value : "none");
    static char told[16][96]; static int n_told;   // one host notice per player and policy per session (clients retry)
    char tk[96];
    snprintf(tk, sizeof tk, "%s|%d", key, policy);
    for (int i = 0; i < n_told; i++) if (!strcmp(told[i], tk)) return 1;
    if (n_told < 16) snprintf(told[n_told++], sizeof told[0], "%s", tk);
    chat_local("%s could not join: %s.", name && *name ? name : "A player", why);
    return 1;
}

// ---- who runs what (host): the summary of each accepted login, by its connection ----
typedef struct { UObject *conn; int32_t idx; char name[64], value[SUMMARY_MAX + 1]; } Rec;
static Rec recs[16];

void addons_login_record(UObject *conn, const char *name, const char *value) {
    if (!conn) { LOG("addons: login %s: no connection found, /addons players won't list its add-ons", name); return; }
    Rec *slot = NULL;
    for (int i = 0; i < 16 && !slot; i++) if (recs[i].conn == conn) slot = &recs[i];
    for (int i = 0; i < 16 && !slot; i++) if (!recs[i].conn || ue_object_at(recs[i].idx) != recs[i].conn) slot = &recs[i];
    if (!slot) slot = &recs[0];
    slot->conn = conn; slot->idx = U_INDEX(conn);
    snprintf(slot->name, sizeof slot->name, "%s", name);
    snprintf(slot->value, sizeof slot->value, "%s", value ? value : "");
    Summary s; parse(value, &s);
    LOG("addons: login %s: %s", name, !s.present ? "no add-on summary (older b4bcoop)" : value);
}

static const Rec *rec_of(UObject *conn) {
    for (int i = 0; i < 16; i++) if (conn && recs[i].conn == conn && ue_object_at(recs[i].idx) == conn) return &recs[i];
    return NULL;
}

static void show_summary(const char *value, Out *o) {
    Summary s; parse(value, &s);
    if (!s.present) { out_printf(o, "  unknown (older b4bcoop without add-ons)\n"); return; }
    if (!s.nc && !s.ng) { out_printf(o, "  no add-ons\n"); return; }
    AddonRef r[MAX_ADDONS];
    int n = own(r, MAX_ADDONS);
    out_printf(o, "  %d cosmetic, %d gameplay\n", s.nc, s.ng);
    for (int j = 0; j < s.n; j++) {
        char t[TITLE_MAX + 1] = "";
        if (s.e[j].title[0]) title_show(s.e[j].title, t, sizeof t);
        const char *mine = NULL;
        for (int i = 0; i < n; i++) if (!strncmp(r[i].hash, s.e[j].id, 8)) mine = r[i].title;
        out_printf(o, "  %s %s %s%s\n", s.e[j].g ? "gameplay" : "cosmetic", s.e[j].id, mine ? mine : t[0] ? t : "(not installed here)",
                   mine ? " (you have it too)" : "");
    }
    if (s.omitted) out_printf(o, "  ... %d more not listed\n", s.omitted);
}

// Host: the other human players (player state index, name, their login's add-on summary or NULL if unknown)
typedef struct { int idx; char name[64]; const Rec *rec; } Remote;
static int remotes(Remote *out, int max) {
    UObject **pa; int n = admin_player_array(&pa), k = 0;
    static UClass *pcc;
    if (!pcc) pcc = ue_find_class("PlayerController");
    UObject *me = ue_local_pc();
    for (int i = 0; i < n && k < max; i++) {
        UObject *pc = ue_get_ptr(pa[i], "Owner");
        if (!pc || !pcc || !ue_is_a(pc, pcc) || pc == me) continue;   // bots, the host
        out[k].idx = i;
        admin_display_name(pa[i], out[k].name, sizeof out[k].name);
        out[k].rec = rec_of(ue_get_ptr(pc, "Player"));
        k++;
    }
    return k;
}

static void players(Out *o) {
    Remote pl[16];
    int n = remotes(pl, 16);
    AddonRef r[MAX_ADDONS];
    int mine = own(r, MAX_ADDONS), ng = 0;
    for (int i = 0; i < mine; i++) ng += r[i].gameplay;
    out_printf(o, "add-ons policy: %s\n", POLICY_NAMES[policy]);
    out_printf(o, "you (host): %d cosmetic, %d gameplay\n", mine - ng, ng);
    for (int i = 0; i < n; i++) {
        out_printf(o, "#%d %s:\n", pl[i].idx, pl[i].name);
        if (pl[i].rec) show_summary(pl[i].rec->value, o); else out_printf(o, "  unknown (joined before the host's agent saw the login)\n");
    }
}

// /addons players|policy; returns 1 if handled
int addons_mp_slash(const char *sub, char *arg, Out *o) {
    if (!strcmp(sub, "players") || !strcmp(sub, "who")) {
        if (admin_is_client()) {
            AddonRef r[MAX_ADDONS];
            int n = own(r, MAX_ADDONS), ng = 0;
            for (int i = 0; i < n; i++) ng += r[i].gameplay;
            out_printf(o, "/addons players: host only. You: %d cosmetic, %d gameplay (the host sees this summary)\n", n - ng, ng);
            return 1;
        }
        if (!ue_is_listen_server(ue_world())) { out_printf(o, "not hosting a session\n"); return 1; }
        players(o);
        return 1;
    }
    if (!strcmp(sub, "policy")) {
        while (arg && *arg == ' ') arg++;
        if (!arg || !*arg) {
            out_printf(o, "add-ons policy: %s (any | cosmetic | none | match; ini addons_policy=, default %s)\n",
                       POLICY_NAMES[policy], POLICY_NAMES[ADDONS_POLICY_DEFAULT]);
            return 1;
        }
        if (admin_is_client()) { out_printf(o, "/addons policy: host only (you are a client)\n"); return 1; }
        if (addons_policy_set(arg)) { out_printf(o, "usage: /addons policy any|cosmetic|none|match\n"); return 1; }
        LOG("addons: policy %s (chat)", POLICY_NAMES[policy]);
        out_printf(o, "add-ons policy: %s for this session (new joiners; ini addons_policy= keeps it)\n", POLICY_NAMES[policy]);
        return 1;
    }
    return 0;
}

// ~ window, Add-ons tab (addons.c): the host's policy (saved to b4bcoop.ini, like editing addons_policy=) and what
// each player runs (/addons players). A client sees its own policy greyed out, with the reason.
void addons_mp_panel(void) {
    static const char *DESC[] = {"everyone, whatever they run", "cosmetic add-ons only (default)", "only players without add-ons",
                                 "cosmetic free; gameplay add-ons must be the same as yours"};
    ov_heading("Who may join with add-ons (host)");
    ov_begin_perm(CMD_HOST);
    for (int i = 0; i < 4; i++) {
        char lab[32];
        snprintf(lab, sizeof lab, "%s##pol", POLICY_NAMES[i]);
        if (ov_radio(lab, policy == i) && policy != i) ov_setting("addons_policy", i == ADDONS_POLICY_DEFAULT ? NULL : POLICY_NAMES[i], 1);
        ov_same_line();
        ov_text_dim("%s", DESC[i]);
    }
    ov_end_perm();
    ov_text_dim("addons_policy in b4bcoop.ini; applies to the next player who joins.");
    ov_heading("Players' add-ons");
    AddonRef r[MAX_ADDONS];
    int mine = own(r, MAX_ADDONS), ng = 0;
    for (int i = 0; i < mine; i++) ng += r[i].gameplay;
    ov_text("You: %d cosmetic, %d gameplay", mine - ng, ng);
    if (admin_is_client()) {
        ov_text_dim("You are in someone else's session: the host saw this summary when you joined and decides with its "
                    "own policy. Hosts don't send their add-ons or policy.");
        return;
    }
    if (!ue_is_listen_server(ue_world())) { ov_text_dim("Not hosting a session."); return; }
    Remote pl[16];
    int n = remotes(pl, 16);
    static Out o;
    if (!n) ov_text_dim("No other players.");
    else if (ov_table_begin("addonplayers", 2)) {
        static const char *H[] = {"Player", "Add-ons (from their login)"};
        ov_table_header(H, 2);
        for (int i = 0; i < n; i++) {
            ov_table_next(); ov_text("#%d %s", pl[i].idx, pl[i].name);
            ov_table_next();
            out_reset(&o);
            if (pl[i].rec) show_summary(pl[i].rec->value, &o); else out_printf(&o, "unknown (joined before the host's agent saw the login)");
            while (o.len && o.buf[o.len - 1] == '\n') o.buf[--o.len] = 0;
            ov_text("%s", o.buf[0] == ' ' ? o.buf + 2 : o.buf);
        }
        ov_table_end();
    }
    if (ov_button("List in the log (/addons players)")) ov_run("addons players");
}
