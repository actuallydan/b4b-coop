#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "cmds.h"
#include "ue.h"
#include "log.h"

void out_reset(Out *o) { o->len = 0; o->buf[0] = 0; }
void out_printf(Out *o, const char *fmt, ...) {
    if (o->len >= sizeof o->buf - 1) return;
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(o->buf + o->len, sizeof o->buf - o->len, fmt, ap);
    va_end(ap);
    if (n > 0) o->len += (size_t)n < sizeof o->buf - o->len ? (size_t)n : sizeof o->buf - o->len - 1;
}

static char nb[512], pb[1024];
#define NAME(o) ((o) ? ue_obj_name((o), nb, sizeof nb) : "null")
#define PATH(o) ((o) ? ue_full_path((o), pb, sizeof pb) : "null")

static const char *class_of(UObject *o) { static char b[256]; return o ? ue_obj_name(U_CLASS(o), b, sizeof b) : "null"; }

static void fstring_from(FString *s, const char *utf8, wchar_t *storage, int cap) {
    int n = 0;
    for (; utf8[n] && n < cap - 1; n++) storage[n] = (wchar_t)(unsigned char)utf8[n];
    storage[n] = 0;
    s->data = storage; s->num = n + 1; s->max = cap;
}

static void cmd_status(Out *o) {
    UObject *w = ue_world();
    out_printf(o, "objects: %d\n", ue_num_objects());
    out_printf(o, "engine: %s\n", PATH(ue_engine()));
    out_printf(o, "world: %s\n", PATH(w));
    if (!w) return;
    UObject *gi = ue_get_ptr(w, "OwningGameInstance");
    UObject *gm = ue_get_ptr(w, "AuthorityGameMode");
    UObject *gs = ue_get_ptr(w, "GameState");
    UObject *nd = ue_get_ptr(w, "NetDriver");
    out_printf(o, "gameinstance: %s (%s)\n", NAME(gi), class_of(gi));
    out_printf(o, "gamemode: %s (%s)\n", NAME(gm), class_of(gm));
    out_printf(o, "gamestate: %s\n", class_of(gs));
    out_printf(o, "localpc: %s\n", class_of(ue_local_pc()));
    if (nd) {
        UObject *sc = ue_get_ptr(nd, "ServerConnection");
        TArray *cc = (TArray *)((char *)nd + ue_prop_offset(nd, "ClientConnections"));
        out_printf(o, "netdriver: %s (%s) server_conn=%s client_conns=%d\n", NAME(nd), class_of(nd), sc ? "yes" : "no", cc->num);
        for (int i = 0; i < cc->num; i++) out_printf(o, "  conn[%d]: %s\n", i, class_of(((UObject **)cc->data)[i]));
    } else out_printf(o, "netdriver: none (standalone)\n");
}

static int exec_console(const char *cmd) {
    static UClass *ksl; static UFunction *fn;
    if (!ksl) ksl = ue_find_class("KismetSystemLibrary");
    if (ksl && !fn) fn = ue_find_function(ksl, "ExecuteConsoleCommand");
    UObject *w = ue_world();
    if (!fn || !w) { LOG("exec: missing %s", fn ? "world" : "ExecuteConsoleCommand"); return -1; }
    static wchar_t wbuf[2048];
    struct { UObject *ctx; FString cmd; UObject *pc; } p = {0};
    p.ctx = w;
    fstring_from(&p.cmd, cmd, wbuf, 2048);
    p.pc = ue_local_pc();
    ue_process_event(UC_CDO(ksl), fn, &p);
    return 0;
}

void game_exec(const char *cmd) { LOG("exec: %s", cmd); exec_console(cmd); }

static void cmd_exec(const char *cmd, Out *o) {
    out_printf(o, exec_console(cmd) ? "exec failed: %s\n" : "exec: %s\n", cmd);
}

static void cmd_host(Out *o) {
    UObject *w = ue_world();
    if (!w) { out_printf(o, "no world\n"); return; }
    if (ue_is_listen_server(w)) { out_printf(o, "already hosting (%d client(s))\n", ue_num_clients(w)); return; }
    char pkg[512], cmd[600];
    ue_world_package(w, pkg, sizeof pkg);
    snprintf(cmd, sizeof cmd, "open %s?listen", pkg);
    game_exec(cmd);
    out_printf(o, "hosting: %s\n", cmd);
}

static void cmd_join(const char *addr, Out *o) {
    char cmd[300];
    snprintf(cmd, sizeof cmd, "open %s%s", addr, strchr(addr, ':') ? "" : ":7777");
    travel_set_host(cmd + 5);
    game_exec(cmd);
    out_printf(o, "joining: %s\n", cmd);
}

static void cmd_find(const char *needle, int max, Out *o) {
    int n = ue_num_objects(), hits = 0;
    for (int i = 0; i < n && hits < max; i++) {
        UObject *x = ue_object_at(i);
        if (!x || !U_CLASS(x)) continue;
        ue_full_path(x, pb, sizeof pb);
        if (strstr(pb, needle)) { out_printf(o, "%p %s %s\n", (void *)x, class_of(x), pb); hits++; }
    }
    out_printf(o, "%d hit(s)\n", hits);
}

// call <Class> <Function> [cdo]: call a parameterless function (return value, if any, is printed raw)
static void cmd_call(const char *cls, const char *func, int on_cdo, Out *o) {
    UClass *c = ue_find_class(cls);
    if (!c) { out_printf(o, "no class %s\n", cls); return; }
    UObject *target = on_cdo ? UC_CDO(c) : ue_find_first_of(cls);
    UFunction *f = target ? ue_find_function(U_CLASS(target), func) : NULL;
    if (!f) { out_printf(o, "no %s on %s\n", target ? "function" : "instance", cls); return; }
    static uint8_t params[4096];
    memset(params, 0, sizeof params);
    ue_process_event(target, f, params);
    out_printf(o, "called %s.%s on %s; params[0..16]:", cls, func, PATH(target));
    for (int i = 0; i < 16 && i < UFN_PARMSSIZE(f); i++) out_printf(o, " %02x", params[i]);
    out_printf(o, "\n");
}

static void fstring_print(Out *o, FString *s) {
    for (int i = 0; s->data && i < s->num && s->data[i]; i++) out_printf(o, "%c", s->data[i] < 128 ? (char)s->data[i] : '?');
}

static void cmd_players(Out *o) {
    UObject *w = ue_world();
    UObject *gs = w ? ue_get_ptr(w, "GameState") : NULL;
    if (!gs) { out_printf(o, "no gamestate\n"); return; }
    TArray *pa = (TArray *)((char *)gs + ue_prop_offset(gs, "PlayerArray"));
    out_printf(o, "%d player state(s)\n", pa->num);
    for (int i = 0; i < pa->num; i++) {
        UObject *ps = ((UObject **)pa->data)[i];
        int32_t off = ue_prop_offset(ps, "PlayerNamePrivate");
        out_printf(o, "  [%d] %s name=", i, class_of(ps));
        if (off >= 0) fstring_print(o, (FString *)((char *)ps + off));
        UObject *pawn = ue_get_ptr(ps, "PawnPrivate");
        out_printf(o, " pawn=%s\n", class_of(pawn));
    }
}

void cmds_init(void) {}
static void set_float(UObject *o, const char *prop, float v) {
    int32_t off = o ? ue_prop_offset(o, prop) : -1;
    if (off >= 0) { *(float *)((char *)o + off) = v; }
}

// Raise net timeouts on the driver class defaults so slow (Proton, first-run shader) map loads don't
// drop clients mid-travel. Retail values are tuned for dedicated servers.
static void tune_net_defaults(void) {
    const char *classes[] = {"NetDriver", "IpNetDriver", "PacketRelayNetDriver"};
    for (int i = 0; i < 3; i++) {
        UClass *c = ue_find_class(classes[i]);
        if (!c || !UC_CDO(c)) continue;
        set_float(UC_CDO(c), "InitialConnectTimeout", 180.f);
        set_float(UC_CDO(c), "ConnectionTimeout", 90.f);
        LOG("net: tuned %s defaults", classes[i]);
    }
}

void cmds_tick(float dt) {
    static int tuned;
    if (!tuned) { tuned = 1; tune_net_defaults(); }
    travel_tick(dt);
}

void cmds_run(char *line, Out *o) {
    char *verb = strtok(line, " ");
    char *rest = strtok(NULL, "");
    if (!verb) { out_printf(o, "empty\n"); return; }
    if (!strcmp(verb, "ping")) out_printf(o, "pong\n");
    else if (!strcmp(verb, "status")) cmd_status(o);
    else if (!strcmp(verb, "players")) cmd_players(o);
    else if (!strcmp(verb, "host")) cmd_host(o);
    else if (!strcmp(verb, "peek") && rest) {
        char *a = strtok(rest, " "), *l = strtok(NULL, " ");
        unsigned char *p = (unsigned char *)(uintptr_t)strtoull(a, NULL, 16);
        int n = l ? atoi(l) : 64;
        for (int i = 0; i < n; i++) out_printf(o, "%02x%s", p[i], (i % 16 == 15) ? "\n" : " ");
        out_printf(o, "\n");
    }
    else if (!strcmp(verb, "join") && rest) cmd_join(rest, o);
    else if (!strcmp(verb, "exec") && rest) cmd_exec(rest, o);
    else if (!strcmp(verb, "find") && rest) {
        char *needle = strtok(rest, " "), *m = strtok(NULL, " ");
        cmd_find(needle, m ? atoi(m) : 50, o);
    } else if (!strcmp(verb, "call") && rest) {
        char *c = strtok(rest, " "), *f = strtok(NULL, " "), *cdo = strtok(NULL, " ");
        if (c && f) cmd_call(c, f, cdo && !strcmp(cdo, "cdo"), o); else out_printf(o, "usage: call <Class> <Func> [cdo]\n");
    } else out_printf(o, "unknown command: %s\n", verb);
}
