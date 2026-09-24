// presence: join friends through Steam's own UI (docs/investigations/steam-invites.md).
//   advertise   while we host (listen server: offline Fort Hope or a mission), Steam rich presence `connect` =
//               "+b4bcoop_join steam:<our id64> addr:<ip:port>" -> friends get "Join Game"; cleared when we stop.
//   join        GameRichPresenceJoinRequested_t (337, friend clicked Join Game / accepted an invite while running)
//               or the same string on the command line (Steam started the game for it) -> session join target
//               (cmds_set_session_join: overrides host=/join= from the ini), auto sign-in Offline (testing.c),
//               then the auto-join machinery joins from offline Fort Hope; steam: first, the address as fallback.
//   commands    presence [on|off] | steamjoin <connect string> (simulate a join request) | invite <id64|name> |
//               friends [all]
// Steam: the game's own steam_api64.dll (v1.47, delay-loaded by OnlineSubsystemSteam), flat exports via
// GetProcAddress. Our callback object is registered from inside SteamAPI_RunCallbacks (hooked), i.e. on the OSS's own
// callback thread, so registration never races the dispatch loop (steam_api's callback map has no lock).
// b4bcoop.ini: presence=0 (don't advertise), presence_addr=host[:port] (fallback address to advertise, e.g. a public
// IP with a forwarded port; default: this machine's LAN IPv4 and the listen port).
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iphlpapi.h>
#include <windows.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define B4B_APPID        924970u
#define CB_JOIN_REQUEST  337        // GameRichPresenceJoinRequested_t (k_iSteamFriendsCallbacks + 37)
#define CONNECT_MAX      256        // k_cchMaxRichPresenceValueLength
#define JOIN_TOKEN       "+b4bcoop_join"

// ---- steam_api64 v1.47 flat API (first argument: interface pointer; CSteamID passed/returned as uint64) ----
typedef struct { uint64_t game_id; uint32_t ip; uint16_t port, query_port; uint64_t lobby; } FriendGameInfo;
static struct {
    int32_t (*GetHSteamUser)(void);
    void *(*FindOrCreateUserInterface)(int32_t user, const char *version);
    void (*RegisterCallback)(void *cb, int id);
    void (*RunCallbacks)(void);
    uint8_t (*SetRichPresence)(void *f, const char *key, const char *value);
    const char *(*GetFriendRichPresence)(void *f, uint64_t id, const char *key);
    int (*GetFriendRichPresenceKeyCount)(void *f, uint64_t id);
    const char *(*GetFriendRichPresenceKeyByIndex)(void *f, uint64_t id, int i);
    void (*RequestFriendRichPresence)(void *f, uint64_t id);
    int (*GetFriendCount)(void *f, int flags);
    uint64_t (*GetFriendByIndex)(void *f, int i, int flags);
    int (*GetFriendPersonaState)(void *f, uint64_t id);
    const char *(*GetFriendPersonaName)(void *f, uint64_t id);
    const char *(*GetPlayerNickname)(void *f, uint64_t id);
    const char *(*GetPersonaName)(void *f);
    uint8_t (*GetFriendGamePlayed)(void *f, uint64_t id, FriendGameInfo *out);
    uint8_t (*InviteUserToGame)(void *f, uint64_t id, const char *connect);
    uint64_t (*GetSteamID)(void *u);
    int (*GetLaunchCommandLine)(void *a, char *buf, int n);
} S;
static void *friends, *suser, *sapps;
static uint64_t my_id;
static int bound;

// ---- join requests: arrive on the OSS callback thread, handled on the game thread ----
typedef struct { void **vtbl; uint8_t flags; int32_t id; } CallbackBase;   // steam_api CCallbackBase
static CRITICAL_SECTION pend_cs;
static char pend_connect[CONNECT_MAX + 1], pend_source[32];
static uint64_t pend_friend;
static volatile LONG pend_set, cb_registered, cb_count;
static volatile DWORD pump_thread;
static volatile LONG pump_calls;

static void queue_connect(const char *connect, uint64_t friend_id, const char *source) {
    EnterCriticalSection(&pend_cs);
    snprintf(pend_connect, sizeof pend_connect, "%s", connect);
    snprintf(pend_source, sizeof pend_source, "%s", source);
    pend_friend = friend_id;
    pend_set = 1;
    LeaveCriticalSection(&pend_cs);
}

// Both Run overloads (MSVC orders them Run(p, bIOFailure, call), Run(p)) take the payload in the 2nd argument.
static void cb_run(void *self, void *param) {
    (void)self;
    InterlockedIncrement(&cb_count);
    if (!param) return;
    char connect[CONNECT_MAX + 1];
    memcpy(connect, (char *)param + 8, CONNECT_MAX);
    connect[CONNECT_MAX] = 0;
    queue_connect(connect, *(uint64_t *)param, "steam join request");
}
static int cb_size(void *self) { (void)self; return 8 + CONNECT_MAX; }
static void *cb_vtbl[3] = { (void *)cb_run, (void *)cb_run, (void *)cb_size };
static CallbackBase join_cb = { cb_vtbl, 0, 0 };

static void (*orig_run_callbacks)(void);
static void run_callbacks_detour(void) {
    InterlockedIncrement(&pump_calls);
    if (!cb_registered && InterlockedCompareExchange(&cb_registered, 1, 0) == 0) {
        pump_thread = GetCurrentThreadId();
        S.RegisterCallback(&join_cb, CB_JOIN_REQUEST);
    }
    orig_run_callbacks();
}

// ---- connect string <-> join targets ----
static int valid_host(const char *s) {   // host[:port], letters/digits/.-, nothing that could reach the console
    size_t n = strlen(s);
    if (!n || n > 100) return 0;
    const char *colon = strchr(s, ':');
    for (const char *c = s; *c; c++) {
        if (c == colon) continue;
        int digit = *c >= '0' && *c <= '9';
        int hostch = (*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || *c == '.' || *c == '-';
        int ok = digit || (hostch && (!colon || c < colon));
        if (!ok) return 0;
    }
    return !colon || (colon[1] && strchr(colon + 1, ':') == NULL && colon > s);
}
static int valid_id64(const char *s) {
    size_t n = strlen(s);
    if (n < 5 || n > 20) return 0;
    for (; *s; s++) if (*s < '0' || *s > '9') return 0;
    return 1;
}

// "... +b4bcoop_join steam:<id64> addr:<host:port> ..." -> "steam:<id64>,<host:port>" (either may be missing).
// Returns 0 if the text carries no b4bcoop join.
static int parse_connect(const char *text, char *targets, size_t n) {
    const char *p = text ? strstr(text, JOIN_TOKEN) : NULL;
    if (!p) return 0;
    p += strlen(JOIN_TOKEN);
    char steam[32] = "", addr[112] = "";
    for (;;) {
        while (*p == ' ' || *p == '\t' || *p == '"') p++;
        if (!*p || *p == '+' || *p == '-') break;
        char tok[160]; size_t k = 0;
        while (*p && *p != ' ' && *p != '\t' && *p != '"') { if (k < sizeof tok - 1) tok[k++] = *p; p++; }
        tok[k] = 0;
        if (!strncmp(tok, "steam:", 6) && valid_id64(tok + 6)) snprintf(steam, sizeof steam, "%s", tok);
        else if (!strncmp(tok, "addr:", 5) && valid_host(tok + 5)) snprintf(addr, sizeof addr, "%s", tok + 5);
        else if (valid_host(tok) && strchr(tok, '.')) snprintf(addr, sizeof addr, "%s", tok);
        else LOG("presence: ignoring connect token '%s'", tok);
    }
    snprintf(targets, n, "%s%s%s", steam, steam[0] && addr[0] ? "," : "", addr);
    return targets[0] != 0;
}

static char launch_connect[CONNECT_MAX + 1];

static void parse_command_line(void) {
    const wchar_t *w = GetCommandLineW();
    char line[4096]; size_t k = 0;
    for (; w && w[k] && k < sizeof line - 1; k++) line[k] = w[k] < 128 ? (char)w[k] : '?';
    line[k] = 0;
    const char *p = strstr(line, JOIN_TOKEN);
    if (!p) return;
    snprintf(launch_connect, sizeof launch_connect, "%s", p);
    LOG("presence: command line carries a join: %s", launch_connect);
}

// ---- config ----
static int advertise = 1;
static char cfg_addr[112];

static void load_ini(void) {
    FILE *f = fopen(cmds_config_path(), "r");
    if (!f) return;
    char line[300];
    while (fgets(line, sizeof line, f)) {
        char *nl = strpbrk(line, "\r\n"); if (nl) *nl = 0;
        char *v = strchr(line, '='); if (!v || line[0] == '#' || line[0] == ';') continue;
        *v++ = 0;
        while (*v == ' ') v++;
        if (!strcmp(line, "presence")) advertise = atoi(v);
        else if (!strcmp(line, "presence_addr")) {
            if (valid_host(v)) snprintf(cfg_addr, sizeof cfg_addr, "%s", v);
            else LOG("presence: bad presence_addr '%s'", v);
        }
    }
    fclose(f);
}

// Listen port: -Port= on the command line, else UE's default 7777.
static int listen_port(void) {
    const wchar_t *w = GetCommandLineW();
    for (const wchar_t *p = w; p && *p; p++)
        if ((p == w || p[-1] == ' ' || p[-1] == '"') && !_wcsnicmp(p, L"-Port=", 6)) return _wtoi(p + 6);
    return 7777;
}

// First up, non-loopback IPv4 (prefer an adapter with a gateway). No network traffic.
static void lan_ipv4(char *out, size_t n) {
    out[0] = 0;
    HMODULE m = LoadLibraryA("iphlpapi.dll");
    typedef ULONG (WINAPI *GAA)(ULONG, ULONG, PVOID, PIP_ADAPTER_ADDRESSES, PULONG);
    GAA gaa = m ? (GAA)(void *)GetProcAddress(m, "GetAdaptersAddresses") : NULL;
    if (!gaa) return;
    static uint8_t buf[32768];
    ULONG len = sizeof buf;
    if (gaa(AF_INET, GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER | GAA_FLAG_INCLUDE_GATEWAYS,
            NULL, (PIP_ADAPTER_ADDRESSES)buf, &len) != NO_ERROR) return;
    for (int pass = 0; pass < 2 && !out[0]; pass++)
        for (PIP_ADAPTER_ADDRESSES a = (PIP_ADAPTER_ADDRESSES)buf; a && !out[0]; a = a->Next) {
            if (a->OperStatus != IfOperStatusUp || a->IfType == IF_TYPE_SOFTWARE_LOOPBACK) continue;
            if (pass == 0 && !a->FirstGatewayAddress) continue;
            for (PIP_ADAPTER_UNICAST_ADDRESS u = a->FirstUnicastAddress; u; u = u->Next) {
                if (u->Address.lpSockaddr->sa_family != AF_INET) continue;
                uint8_t *ip = (uint8_t *)&((struct sockaddr_in *)u->Address.lpSockaddr)->sin_addr;
                if (ip[0] == 127 || (ip[0] == 169 && ip[1] == 254)) continue;
                snprintf(out, n, "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
                break;
            }
        }
}

static const char *fallback_addr(void) {
    static char addr[112];
    if (cfg_addr[0]) snprintf(addr, sizeof addr, "%s%s", cfg_addr, strchr(cfg_addr, ':') ? "" : ":7777");
    else {
        char ip[64];
        lan_ipv4(ip, sizeof ip);
        if (ip[0]) snprintf(addr, sizeof addr, "%s:%d", ip, listen_port());
        else addr[0] = 0;
    }
    return addr;
}

// ---- binding (steam_api64 is delay-loaded; wait until the OSS has initialized it) ----
#define RESOLVE(field, name) ((S.field = (void *)GetProcAddress(m, name)) != NULL)
static int bind_steam(void) {
    HMODULE m = GetModuleHandleA("steam_api64.dll");
    if (!m) return 0;
    if (!S.GetHSteamUser) {
        int ok = RESOLVE(GetHSteamUser, "SteamAPI_GetHSteamUser") &&
                 RESOLVE(FindOrCreateUserInterface, "SteamInternal_FindOrCreateUserInterface") &&
                 RESOLVE(RegisterCallback, "SteamAPI_RegisterCallback") &&
                 RESOLVE(RunCallbacks, "SteamAPI_RunCallbacks") &&
                 RESOLVE(SetRichPresence, "SteamAPI_ISteamFriends_SetRichPresence") &&
                 RESOLVE(GetFriendRichPresence, "SteamAPI_ISteamFriends_GetFriendRichPresence") &&
                 RESOLVE(GetFriendRichPresenceKeyCount, "SteamAPI_ISteamFriends_GetFriendRichPresenceKeyCount") &&
                 RESOLVE(GetFriendRichPresenceKeyByIndex, "SteamAPI_ISteamFriends_GetFriendRichPresenceKeyByIndex") &&
                 RESOLVE(RequestFriendRichPresence, "SteamAPI_ISteamFriends_RequestFriendRichPresence") &&
                 RESOLVE(GetFriendCount, "SteamAPI_ISteamFriends_GetFriendCount") &&
                 RESOLVE(GetFriendByIndex, "SteamAPI_ISteamFriends_GetFriendByIndex") &&
                 RESOLVE(GetFriendPersonaState, "SteamAPI_ISteamFriends_GetFriendPersonaState") &&
                 RESOLVE(GetFriendPersonaName, "SteamAPI_ISteamFriends_GetFriendPersonaName") &&
                 RESOLVE(GetPlayerNickname, "SteamAPI_ISteamFriends_GetPlayerNickname") &&
                 RESOLVE(GetPersonaName, "SteamAPI_ISteamFriends_GetPersonaName") &&
                 RESOLVE(GetFriendGamePlayed, "SteamAPI_ISteamFriends_GetFriendGamePlayed") &&
                 RESOLVE(InviteUserToGame, "SteamAPI_ISteamFriends_InviteUserToGame") &&
                 RESOLVE(GetSteamID, "SteamAPI_ISteamUser_GetSteamID") &&
                 RESOLVE(GetLaunchCommandLine, "SteamAPI_ISteamApps_GetLaunchCommandLine");
        if (!ok) { LOG("presence: steam_api64 lacks an expected export; Steam features off"); bound = -1; return 0; }
    }
    int32_t user = S.GetHSteamUser();
    if (!user) return 0;   // SteamAPI_Init not done yet
    friends = S.FindOrCreateUserInterface(user, "SteamFriends017");
    suser = S.FindOrCreateUserInterface(user, "SteamUser020");
    sapps = S.FindOrCreateUserInterface(user, "STEAMAPPS_INTERFACE_VERSION008");
    if (!friends || !suser) { LOG("presence: no SteamFriends017/SteamUser020; Steam features off"); bound = -1; return 0; }
    my_id = S.GetSteamID(suser);
    bound = 1;
    LOG("presence: steam bound, user %llu (%s)", (unsigned long long)my_id, S.GetPersonaName(friends));
    if (MH_CreateHook((void *)S.RunCallbacks, (void *)run_callbacks_detour, (void **)&orig_run_callbacks) != MH_OK ||
        MH_EnableHook((void *)S.RunCallbacks) != MH_OK) {
        // no hook: register from the game thread (tiny race with the OSS's dispatch loop, once)
        LOG("presence: SteamAPI_RunCallbacks hook failed, registering from the game thread");
        cb_registered = 1;
        S.RegisterCallback(&join_cb, CB_JOIN_REQUEST);
    }
    if (!launch_connect[0] && sapps) {   // steam://run/924970//+b4bcoop_join... style launches
        char buf[1024] = "";
        if (S.GetLaunchCommandLine(sapps, buf, sizeof buf) > 0 && strstr(buf, JOIN_TOKEN)) {
            LOG("presence: Steam launch command line carries a join: %s", buf);
            queue_connect(strstr(buf, JOIN_TOKEN), 0, "launch command line (steam)");
        }
    }
    return 1;
}

// ---- advertising ----
static const char *KEYS[] = {"connect", "status", "steam_player_group", "steam_player_group_size"};
#define NKEYS 4
static char applied[NKEYS][CONNECT_MAX];
static int advertising;
static double clock_s, last_hosting = -1e9, next_check, next_verify;

static void set_key(int i, const char *v) {
    if (!strcmp(applied[i], v)) return;
    S.SetRichPresence(friends, KEYS[i], v);
    snprintf(applied[i], sizeof applied[i], "%s", v);
}

static void advertise_tick(void) {
    UObject *w = ue_world();
    int hosting = w && ue_is_listen_server(w);
    if (hosting) last_hosting = clock_s;
    int want = advertise && clock_s - last_hosting < 15;   // hysteresis: server travel briefly has no NetDriver
    if (!want) {
        if (advertising) {
            for (int i = 0; i < NKEYS; i++) set_key(i, "");
            advertising = 0;
            LOG("presence: cleared (not hosting)");
        }
        return;
    }
    if (!hosting) return;   // mid-travel: keep what is set
    char connect[CONNECT_MAX], status[128], group[40], size[8], pkg[256];
    const char *addr = fallback_addr();
    snprintf(connect, sizeof connect, JOIN_TOKEN " steam:%llu%s%s", (unsigned long long)my_id, addr[0] ? " addr:" : "", addr);
    int players = ue_num_clients(w) + 1;
    ue_world_package(w, pkg, sizeof pkg);
    snprintf(status, sizeof status, "b4bcoop: hosting %s (%d player%s)", strstr(pkg, "FortHope") ? "Fort Hope" : "a mission",
             players, players == 1 ? "" : "s");
    snprintf(group, sizeof group, "b4bcoop-%llu", (unsigned long long)my_id);
    snprintf(size, sizeof size, "%d", players);
    if (!advertising) LOG("presence: advertising connect=\"%s\"", connect);
    if (clock_s >= next_verify) {   // someone else (the game's own presence) may have replaced our keys
        next_verify = clock_s + 30;
        const char *cur = advertising ? S.GetFriendRichPresence(friends, my_id, "connect") : NULL;
        if (cur && strcmp(cur, applied[0])) {
            LOG("presence: connect was changed to \"%s\", re-applying", cur);
            for (int i = 0; i < NKEYS; i++) applied[i][0] = 0;
        }
    }
    set_key(0, connect); set_key(1, status); set_key(2, group); set_key(3, size);
    advertising = 1;
}

// ---- join handling (game thread) ----
static void handle_connect(const char *connect, uint64_t friend_id, const char *source) {
    char targets[300];
    if (!parse_connect(connect, targets, sizeof targets)) {
        LOG("presence: %s from %llu is not a b4bcoop join, ignored: %s", source, (unsigned long long)friend_id, connect);
        return;
    }
    LOG("presence: %s from %llu: \"%s\" -> %s", source, (unsigned long long)friend_id, connect, targets);
    cmds_set_session_join(targets);
    testing_arm_signin();
    UObject *w = ue_world();
    if (w && ue_get_ptr(w, "NetDriver")) {   // hosting or in someone's session: the player asked to switch
        LOG("presence: leaving the current session to join");
        cmds_join_now();
    }   // else: the auto-join machinery joins once we are signed in, standalone in offline Fort Hope
}

void presence_init(void) {
    InitializeCriticalSection(&pend_cs);
    load_ini();
    parse_command_line();
    if (launch_connect[0]) queue_connect(launch_connect, 0, "launch command line");
    LOG("presence: advertise=%d addr=%s", advertise, cfg_addr[0] ? cfg_addr : "(LAN)");
}

void presence_tick(float dt) {
    clock_s += dt;
    if (pend_set) {
        char connect[CONNECT_MAX + 1], source[32]; uint64_t fid;
        EnterCriticalSection(&pend_cs);
        memcpy(connect, pend_connect, sizeof connect); memcpy(source, pend_source, sizeof source); fid = pend_friend;
        pend_set = 0;
        LeaveCriticalSection(&pend_cs);
        handle_connect(connect, fid, source);
    }
    if (clock_s < next_check || bound < 0) return;
    next_check = clock_s + 2;
    if (!bound && !bind_steam()) return;
    advertise_tick();
}

// ---- commands ----
static void print_rp(Out *o, uint64_t id) {
    int n = S.GetFriendRichPresenceKeyCount(friends, id);
    for (int i = 0; i < n; i++) {
        const char *k = S.GetFriendRichPresenceKeyByIndex(friends, id, i);
        out_printf(o, "    %s = %s\n", k, S.GetFriendRichPresence(friends, id, k));
    }
}

static void cmd_presence(char *rest, Out *o) {
    if (rest && !strcmp(rest, "off")) { advertise = 0; next_check = 0; }
    else if (rest && !strcmp(rest, "on")) { advertise = 1; next_check = 0; }
    out_printf(o, "steam: %s", bound > 0 ? "bound" : bound < 0 ? "unavailable" : "not initialized yet");
    if (bound > 0) out_printf(o, ", user %llu (%s)", (unsigned long long)my_id, S.GetPersonaName(friends));
    out_printf(o, "\njoin callback: %s, pump thread %lu, RunCallbacks %ld, join requests %ld\n",
               cb_registered ? "registered" : "not registered", (unsigned long)pump_thread, pump_calls, cb_count);
    out_printf(o, "advertise: %s, %s; fallback addr %s\n", advertise ? "on" : "off", advertising ? "advertising" : "idle",
               fallback_addr()[0] ? fallback_addr() : "(none)");
    for (int i = 0; i < NKEYS; i++) if (applied[i][0]) out_printf(o, "  set %s = %s\n", KEYS[i], applied[i]);
    if (bound > 0) { out_printf(o, "own rich presence (read back from Steam):\n"); print_rp(o, my_id); }
    const char *sj = cmds_session_join();
    out_printf(o, "session join target: %s\nlaunch join: %s\n", sj[0] ? sj : "-", launch_connect[0] ? launch_connect : "-");
}

static int is_b4b(uint64_t id, FriendGameInfo *gi) {
    memset(gi, 0, sizeof *gi);
    return S.GetFriendGamePlayed(friends, id, gi) && (uint32_t)(gi->game_id & 0xFFFFFF) == B4B_APPID;
}

static void cmd_friends(char *rest, Out *o) {
    int all = rest && !strcmp(rest, "all"), n = S.GetFriendCount(friends, 4 /*k_EFriendFlagImmediate*/), shown = 0;
    for (int i = 0; i < n; i++) {
        uint64_t id = S.GetFriendByIndex(friends, i, 4);
        int state = S.GetFriendPersonaState(friends, id);
        FriendGameInfo gi;
        int b4b = is_b4b(id, &gi);
        if (!state || (!b4b && !all)) continue;
        const char *nick = S.GetPlayerNickname(friends, id);
        out_printf(o, "%llu  %s%s%s%s  %s\n", (unsigned long long)id, S.GetFriendPersonaName(friends, id),
                   nick ? " (" : "", nick ? nick : "", nick ? ")" : "", b4b ? "[Back 4 Blood]" : gi.game_id ? "[other game]" : "");
        if (b4b) { S.RequestFriendRichPresence(friends, id); print_rp(o, id); }
        shown++;
    }
    out_printf(o, "%d of %d friend(s) shown (%s)\n", shown, n, all ? "online" : "online, playing Back 4 Blood");
}

static void cmd_invite(char *rest, Out *o) {
    while (rest && *rest == ' ') rest++;
    if (!rest || !*rest) { out_printf(o, "usage: invite <steamid64|friend name>\n"); return; }
    if (!advertising || !applied[0][0]) { out_printf(o, "not hosting: nothing to invite to\n"); return; }
    uint64_t target = 0;
    if (valid_id64(rest)) target = strtoull(rest, NULL, 10);
    else {   // case-insensitive substring of persona name or nickname; must be unique
        int n = S.GetFriendCount(friends, 4), hits = 0;
        for (int i = 0; i < n; i++) {
            uint64_t id = S.GetFriendByIndex(friends, i, 4);
            const char *names[2] = {S.GetFriendPersonaName(friends, id), S.GetPlayerNickname(friends, id)};
            for (int k = 0; k < 2; k++) {
                if (!names[k]) continue;
                char a[128], b[128]; size_t j;
                for (j = 0; names[k][j] && j < sizeof a - 1; j++) a[j] = (char)tolower((unsigned char)names[k][j]); a[j] = 0;
                for (j = 0; rest[j] && j < sizeof b - 1; j++) b[j] = (char)tolower((unsigned char)rest[j]); b[j] = 0;
                if (strstr(a, b)) { if (target != id) hits++; target = id; out_printf(o, "match: %llu %s\n", (unsigned long long)id, names[0]); break; }
            }
        }
        if (!hits) { out_printf(o, "no friend matches '%s'\n", rest); return; }
        if (hits > 1) { out_printf(o, "ambiguous: %d friends match '%s'\n", hits, rest); return; }
    }
    uint8_t ok = S.InviteUserToGame(friends, target, applied[0]);
    LOG("presence: invite %llu -> %d (%s)", (unsigned long long)target, ok, applied[0]);
    out_printf(o, "invite %llu: %s\n", (unsigned long long)target, ok ? "sent" : "failed");
}

int presence_cmd(const char *verb, char *rest, Out *o) {
    if (!strcmp(verb, "steamjoin")) {   // simulate a Join Game / accepted invite: same path as the Steam callback
        if (!rest) { out_printf(o, "usage: steamjoin <connect string>\n"); return 1; }
        queue_connect(rest, 0, "simulated join request");
        out_printf(o, "queued: %s\n", rest);
        return 1;
    }
    if (strcmp(verb, "presence") && strcmp(verb, "invite") && strcmp(verb, "friends")) return 0;
    if (bound <= 0 && strcmp(verb, "presence")) { out_printf(o, "steam not available\n"); return 1; }
    if (!strcmp(verb, "presence")) cmd_presence(rest, o);
    else if (!strcmp(verb, "invite")) cmd_invite(rest, o);
    else cmd_friends(rest, o);
    return 1;
}
