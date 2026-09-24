// steamnet: co-op over Steam P2P (Steam's relay network) instead of raw IP + port forwarding.
//
// The game ships Unreal's Steam OSS with USteamNetDriver (ISteamNetworking P2P, steam_api 1.47), and the Steam OSS is
// initialized in every session (STEAM auth tickets, achievements). The world's net driver comes from the engine's
// NetDriverDefinitions entry "GameNetDriver" (retail: PacketRelayNetDriver = IpNetDriver + TRS relay, passthrough
// offline). UEngine::CreateNamedNetDriver loads DriverClassName, and if the class is missing or its CDO says
// !IsAvailable() (no Steam OSS or no STEAM socket subsystem) it uses DriverClassNameFallback. So:
//   transport steam: GameNetDriver = SteamNetDriver, fallback = the retail driver (IP). The listen server then
//                    accepts `steam.<id64>:<port>` (P2P channel = listen port) and nothing else: one world has one
//                    game net driver, so a Steam host is not reachable over IP at the same time.
//   transport ip:    the retail definition, untouched.
// The definition is switched right before every host/join/rejoin and stays switched for the session (server travel
// and a client's follow re-create the driver from it). SteamNetDriver's own config section is not shipped, so its CDO
// gets the retail driver's connection class/timeouts/rates mirrored in (see mirror_defaults).
// Findings, test plan: docs/investigations/steam-p2p.md.
//
// b4bcoop.ini: transport=ip|steam (hosting; default ip). A join picks its transport from the target:
//              `steam:<id64>[:port]` or `ip[:port]`.
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define ADDR_FNAME_CTOR   VA(0x1424BC8E0ull)  // FName::FName(const TCHAR*, EFindName)
#define ADDR_SOCKSUB_GET  VA(0x1429AE6A0ull)  // ISocketSubsystem* ISocketSubsystem::Get(const FName&)
#define STEAM_DRIVER_PASSTHROUGH 0x7D0        // USteamNetDriver::bIsPassthrough (InitBase 0x140D262A0 tests it)
static const uint8_t SIG_SOCKSUB_GET[] = {0x40,0x57,0x48,0x83,0xec,0x30,0x65,0x48,0x8b,0x04,0x25,0x58,0x00,0x00,0x00,
                                          0x48,0x8b,0xf9,0xb9,0x68,0x00,0x00,0x00};
static const uint8_t SIG_FNAME_CTOR[] = {0x48,0x89,0x5c,0x24,0x08,0x57,0x48,0x83,0xec,0x30,0x48,0x8b,0xd9};

typedef FName *(*FNameCtorFn)(FName *self, const wchar_t *name, int find_type);
typedef void *(*SockSubGetFn)(const FName *name);

static const wchar_t STEAM_DRIVER[] = L"/Script/OnlineSubsystemSteam.SteamNetDriver";
typedef struct { FName def, cls, fallback; } NetDriverDef;   // FNetDriverDefinition (0x18)

static int g_transport_steam;      // ini transport=steam (hosting)
static int g_orig_saved;
static FName g_orig_cls, g_orig_fallback;
static int g_mode = -1;            // what the GameNetDriver definition currently says: -1 untouched, 0 ip, 1 steam
static int g_sigs_ok = -1;
static char g_last_why[160];       // why Steam P2P is unavailable (last check)

// ---- steam_api64 (flat API of SDK 1.47; the game loads the DLL, we only look it up) ----
typedef int32_t (*GetHSteamUser_t)(void);
typedef void *(*FindIface_t)(int32_t user, const char *version);
typedef uint64_t (*GetSteamID_t)(void *self);
typedef uint8_t (*BLoggedOn_t)(void *self);
typedef uint8_t (*AllowRelay_t)(void *self, uint8_t allow);
typedef struct {
    uint8_t active, connecting, error, relay;
    int32_t bytes_queued, packets_queued;
    uint32_t remote_ip;
    uint16_t remote_port;
} P2PState;                                                  // P2PSessionState_t
typedef uint8_t (*GetP2PState_t)(void *self, uint64_t remote, P2PState *st);
static struct {
    HMODULE dll;
    GetHSteamUser_t user; FindIface_t find;
    GetSteamID_t id; BLoggedOn_t logged_on; AllowRelay_t relay; GetP2PState_t p2p;
} S;

static int steam_api(void) {
    if (S.dll) return 1;
    HMODULE m = GetModuleHandleA("steam_api64.dll");
    if (!m) return 0;
    S.user = (GetHSteamUser_t)GetProcAddress(m, "SteamAPI_GetHSteamUser");
    S.find = (FindIface_t)GetProcAddress(m, "SteamInternal_FindOrCreateUserInterface");
    S.id = (GetSteamID_t)GetProcAddress(m, "SteamAPI_ISteamUser_GetSteamID");
    S.logged_on = (BLoggedOn_t)GetProcAddress(m, "SteamAPI_ISteamUser_BLoggedOn");
    S.relay = (AllowRelay_t)GetProcAddress(m, "SteamAPI_ISteamNetworking_AllowP2PPacketRelay");
    S.p2p = (GetP2PState_t)GetProcAddress(m, "SteamAPI_ISteamNetworking_GetP2PSessionState");
    if (!S.user || !S.find || !S.id || !S.logged_on || !S.relay || !S.p2p) { LOG("steamnet: steam_api64 exports missing"); return 0; }
    S.dll = m;
    return 1;
}
static void *iface(const char *ver) { int32_t u = steam_api() ? S.user() : 0; return u ? S.find(u, ver) : NULL; }
static void *steam_user(void) { return iface("SteamUser020"); }            // versions the game itself requests
static void *steam_networking(void) { return iface("SteamNetworking006"); }

uint64_t steamnet_local_id(void) {
    void *u = steam_user();
    return u ? S.id(u) : 0;
}

// ---- engine side ----
static int sigs_ok(void) {
    if (g_sigs_ok < 0) {
        g_sigs_ok = !memcmp((void *)ADDR_SOCKSUB_GET, SIG_SOCKSUB_GET, sizeof SIG_SOCKSUB_GET) &&
                    !memcmp((void *)ADDR_FNAME_CTOR, SIG_FNAME_CTOR, sizeof SIG_FNAME_CTOR);
        if (!g_sigs_ok) LOG("steamnet: signature mismatch, Steam transport disabled");
    }
    return g_sigs_ok;
}
static FName make_name(const wchar_t *s) { FName n = {0}; ((FNameCtorFn)ADDR_FNAME_CTOR)(&n, s, 1 /*FNAME_Add*/); return n; }

// 1 if Steam P2P can carry a session from this process; otherwise 0 and the reason in g_last_why.
int steamnet_available(void) {
    const char *why = NULL;
    void *u;
    if (!sigs_ok()) why = "unsupported game build";
    else if (!steam_api()) why = "steam_api64.dll not loaded (Steam not running?)";
    else if (!(u = steam_user())) why = "no Steam user (SteamAPI not initialized)";
    else if (!S.logged_on(u)) why = "Steam user not logged on (Steam offline mode?)";
    else if (!steam_networking()) why = "no ISteamNetworking";
    else {
        FName n = make_name(L"STEAM");
        if (!((SockSubGetFn)ADDR_SOCKSUB_GET)(&n)) why = "no STEAM socket subsystem (Steam OSS networking off)";
    }
    snprintf(g_last_why, sizeof g_last_why, "%s", why ? why : "");
    return why == NULL;
}

static NetDriverDef *game_def(void) {
    UObject *eng = ue_engine();
    int32_t off = eng ? ue_prop_offset(eng, "NetDriverDefinitions") : -1;
    if (off < 0) return NULL;
    TArray *a = (TArray *)((char *)eng + off);
    char b[64];
    for (int i = 0; i < a->num; i++) {
        NetDriverDef *d = (NetDriverDef *)a->data + i;
        if (!strcmp(ue_name(d->def, b, sizeof b), "GameNetDriver")) return d;
    }
    return NULL;
}

static void copy_prop(UObject *dst, UObject *src, const char *prop, int size) {
    int32_t so = ue_prop_offset(src, prop), dof = ue_prop_offset(dst, prop);
    if (so >= 0 && dof >= 0) memcpy((char *)dst + dof, (char *)src + so, size);
}
static int fstring_eq(UObject *o, const char *prop, const char *want) {
    int32_t off = ue_prop_offset(o, prop);
    if (off < 0) return 0;
    FString *s = (FString *)((char *)o + off);
    size_t i = 0;
    for (; s->data && i < (size_t)s->num && s->data[i]; i++) if (!want[i] || s->data[i] != (wchar_t)want[i]) return 0;
    return want[i] == 0;
}
static const char *fstring_short(UObject *o, const char *prop, char *buf, size_t n) {   // "/Script/X.Class" -> "Class"
    int32_t off = ue_prop_offset(o, prop);
    buf[0] = 0;
    if (off < 0) return buf;
    FString *s = (FString *)((char *)o + off);
    size_t k = 0;
    for (int i = 0; s->data && i < s->num && s->data[i] && k + 1 < n; i++) {
        wchar_t c = s->data[i];
        if (c == '.' || c == '/') { k = 0; continue; }
        buf[k++] = c < 128 ? (char)c : '?';
    }
    buf[k] = 0;
    return buf;
}

// SteamNetDriver has no config section in this game: give its CDO the retail driver's tuning and the right
// connection class (USteamNetConnection registers the P2P session with the socket subsystem). Pointers only, no
// FString writes: UNetDriver::InitConnectionClass/replication driver setup use the UClass* when it is set.
static void mirror_defaults(void) {
    static int done;
    if (done) return;
    UClass *sc = ue_find_class("SteamNetDriver"), *rc = ue_find_class("PacketRelayNetDriver");
    UObject *s = sc ? UC_CDO(sc) : NULL, *r = rc ? UC_CDO(rc) : NULL;
    if (!s) return;
    done = 1;
    if (r) {
        static const char *i32[] = {"MaxDownloadSize", "NetServerMaxTickRate", "MaxNetTickRate", "MaxInternetClientRate",
                                    "MaxClientRate", "ServerTravelPause", "SpawnPrioritySeconds", "RelevantTimeout",
                                    "KeepAliveTime", "InitialConnectTimeout", "ConnectionTimeout", "MaxPortCountToTry",
                                    "ServerDesiredSocketReceiveBufferBytes", "ServerDesiredSocketSendBufferBytes",
                                    "ClientDesiredSocketReceiveBufferBytes", "ClientDesiredSocketSendBufferBytes",
                                    "NbPacketsBetweenReceiveTimeTest", "ResolutionConnectionTimeout",
                                    "RecentlyDisconnectedTrackingTime"};
        for (size_t i = 0; i < sizeof i32 / sizeof *i32; i++) copy_prop(s, r, i32[i], 4);
        copy_prop(s, r, "MaxSecondsInReceive", 8);
        copy_prop(s, r, "bClampListenServerTickRate", 1);
        char rep[128];
        fstring_short(r, "ReplicationDriverClassName", rep, sizeof rep);
        UClass *repc = rep[0] ? ue_find_class(rep) : NULL;
        int32_t ro = ue_prop_offset(s, "ReplicationDriverClass");
        if (repc && ro >= 0 && !*(UClass **)((char *)s + ro)) *(UClass **)((char *)s + ro) = repc;
        LOG("steamnet: SteamNetDriver defaults mirrored from PacketRelayNetDriver (replication driver: %s)", rep[0] ? rep : "none");
    }
    if (!fstring_eq(s, "NetConnectionClassName", "/Script/OnlineSubsystemSteam.SteamNetConnection")) {
        UClass *conn = ue_find_class("SteamNetConnection");
        int32_t co = ue_prop_offset(s, "NetConnectionClass");
        char cur[128];
        if (conn && co >= 0) *(UClass **)((char *)s + co) = conn;
        LOG("steamnet: SteamNetDriver connection class %s -> SteamNetConnection", fstring_short(s, "NetConnectionClassName", cur, sizeof cur)[0] ? cur : "(unset)");
    }
}

// Point the GameNetDriver definition at SteamNetDriver (steam=1) or back at the retail driver (steam=0).
// Returns the mode now in effect (0 when Steam was asked for but is unavailable).
static int use_transport(int steam, const char *what) {
    if (steam && !steamnet_available()) {
        LOG("steamnet: Steam P2P unavailable (%s); %s falls back to IP", g_last_why, what);
        steam = 0;
    }
    if (!sigs_ok()) return 0;
    NetDriverDef *d = game_def();
    if (!d) { LOG("steamnet: no GameNetDriver definition on the engine"); return 0; }
    if (!g_orig_saved) {
        char a[128], b[128];
        g_orig_cls = d->cls; g_orig_fallback = d->fallback; g_orig_saved = 1;
        LOG("steamnet: retail GameNetDriver = %s (fallback %s)", ue_name(d->cls, a, sizeof a), ue_name(d->fallback, b, sizeof b));
    }
    if (steam) {
        mirror_defaults();
        d->cls = make_name(STEAM_DRIVER);
        d->fallback = g_orig_cls;          // engine-level fallback if SteamNetDriver says !IsAvailable()
        if (S.relay) { void *n = steam_networking(); if (n) S.relay(n, 1); }   // allow SDR relay (not only NAT punch)
    } else {
        d->cls = g_orig_cls; d->fallback = g_orig_fallback;
    }
    if (g_mode != steam) LOG("steamnet: %s over %s", what, steam ? "Steam P2P (SteamNetDriver)" : "IP (retail driver)");
    g_mode = steam;
    return steam;
}

int steamnet_prepare_host(void) { return use_transport(g_transport_steam, "hosting"); }

// Parse a join target: "steam:<id64>[:port]" / "steam.<id64>[:port]" -> url "steam.<id64>:<port>", returns 1;
// "host[:port]" -> url "host:port" (default 7777), returns 0; -1 if malformed.
int steamnet_parse_target(const char *t, char *url, size_t n) {
    while (*t == ' ') t++;
    if (!_strnicmp(t, "steam:", 6) || !_strnicmp(t, "steam.", 6)) {
        char *end;
        unsigned long long id = strtoull(t + 6, &end, 10);
        int port = *end == ':' ? atoi(end + 1) : 7777;
        if (id < 0x0110000100000000ull || (*end && *end != ':') || port <= 0 || port > 65535) return -1;
        snprintf(url, n, "steam.%llu:%d", id, port);
        return 1;
    }
    if (!*t || strchr(t, ' ')) return -1;
    snprintf(url, n, "%s%s", t, strchr(t, ':') ? "" : ":7777");
    return 0;
}

// Before opening a client URL (join or rejoin): pick the driver that can reach it.
int steamnet_prepare_url(const char *url) {
    if (!_strnicmp(url, "steam.", 6)) {
        if (!use_transport(1, "joining")) {
            LOG("steamnet: cannot reach %s without Steam P2P (%s); ask the host for an IP join instead", url, g_last_why);
            return -1;
        }
        return 1;
    }
    use_transport(0, "joining");
    return 0;
}

// ---- peers (from the Steam OSS log, see steamnet_on_log) ----
#define MAX_PEERS 16
static uint64_t peers[MAX_PEERS];
static int npeers;
static uint64_t g_join_id;          // client: the steam host we joined
static int g_listen_port, g_listen_steam = -1;
static void add_peer(uint64_t id) {
    if (!id) return;
    for (int i = 0; i < npeers; i++) if (peers[i] == id) return;
    if (npeers < MAX_PEERS) peers[npeers++] = id;
}

// Called from uelog.c for every engine log line.
void steamnet_on_log(const char *cat, const char *msg) {
    const char *p;
    if ((p = strstr(msg, "Adding P2P connection information with user "))) {
        add_peer(strtoull(p + 45, NULL, 10));
        LOG("steamnet: P2P session accepted from %s", p + 45);
    } else if (strstr(msg, "k_EP2PSessionError") || strstr(msg, "Rejected P2P connection")) {
        LOG("steamnet: P2P problem: %s", msg);
    } else if (!strcmp(cat, "LogNet") && strstr(msg, "GameNetDriver") && (p = strstr(msg, "listening on port "))) {
        g_listen_port = atoi(p + 18);
        g_listen_steam = strstr(msg, "SteamNetDriver") != NULL;
    }
}

static const char *driver_state(UObject *nd, char *buf, size_t n) {
    char cls[64];
    ue_obj_name(U_CLASS(nd), cls, sizeof cls);
    if (!strcmp(cls, "SteamNetDriver"))
        snprintf(buf, n, "%s", *((uint8_t *)nd + STEAM_DRIVER_PASSTHROUGH) ? "SteamNetDriver in IP passthrough" : "Steam P2P");
    else snprintf(buf, n, "IP (%s)", cls);
    return buf;
}

// Host: say once per listen driver whether we ended up on Steam, and keep SDR relay allowed.
void steamnet_tick(float dt) {
    static UObject *seen;
    static float t;
    if ((t += dt) < 1) return;
    t = 0;
    UObject *w = ue_world(), *nd = w ? ue_get_ptr(w, "NetDriver") : NULL;
    if (!nd || nd == seen || !ue_is_listen_server(w)) return;
    seen = nd;
    char st[64];
    driver_state(nd, st, sizeof st);
    uint64_t id = steamnet_local_id();
    if (!strcmp(st, "Steam P2P")) {
        void *n = steam_networking();
        if (n) S.relay(n, 1);
        if (g_listen_port && g_listen_port != 7777) LOG("steamnet: hosting on Steam P2P: join steam:%llu:%d", (unsigned long long)id, g_listen_port);
        else LOG("steamnet: hosting on Steam P2P: join steam:%llu", (unsigned long long)id);
    } else if (g_transport_steam) LOG("steamnet: transport=steam but hosting on %s (%s)", st, g_last_why[0] ? g_last_why : "fallback");
}

static void print_p2p(Out *o, uint64_t id) {
    void *n = steam_networking();
    P2PState s = {0};
    if (!n || !S.p2p(n, id, &s)) { out_printf(o, "  peer %llu: no P2P session\n", (unsigned long long)id); return; }
    uint32_t ip = s.remote_ip;
    out_printf(o, "  peer %llu: active=%d connecting=%d relay=%d error=%d queued=%d remote=%u.%u.%u.%u:%u\n",
               (unsigned long long)id, s.active, s.connecting, s.relay, s.error, s.packets_queued,
               ip >> 24, (ip >> 16) & 255, (ip >> 8) & 255, ip & 255, s.remote_port);
}

// One line for `status`.
void steamnet_status(Out *o) {
    uint64_t id = steamnet_local_id();
    int ok = steamnet_available();
    out_printf(o, "steam: id=%llu p2p=%s transport=%s", (unsigned long long)id, ok ? "available" : g_last_why,
               g_transport_steam ? "steam" : "ip");
    if (id && ok) {
        if (g_listen_port && g_listen_port != 7777) out_printf(o, " (share: steam:%llu:%d)", (unsigned long long)id, g_listen_port);
        else out_printf(o, " (share: steam:%llu)", (unsigned long long)id);
    }
    out_printf(o, "\n");
}

// `steamnet [transport ip|steam]`: transport details and P2P session state per peer.
int steamnet_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "steamnet")) return 0;
    if (rest && !strncmp(rest, "transport ", 10)) {
        g_transport_steam = !strcmp(rest + 10, "steam");
        out_printf(o, "hosting transport: %s (applies to the next host)\n", g_transport_steam ? "steam" : "ip");
        return 1;
    }
    steamnet_status(o);
    NetDriverDef *d = sigs_ok() ? game_def() : NULL;
    char a[128], b[128], st[64];
    if (d) out_printf(o, "GameNetDriver definition: %s (fallback %s)%s\n", ue_name(d->cls, a, sizeof a),
                      ue_name(d->fallback, b, sizeof b), g_mode < 0 ? " [untouched]" : "");
    UObject *w = ue_world(), *nd = w ? ue_get_ptr(w, "NetDriver") : NULL;
    if (nd) out_printf(o, "world net driver: %s, %s\n", driver_state(nd, st, sizeof st),
                       ue_is_listen_server(w) ? "listening" : "client");
    if (g_listen_port) out_printf(o, "last listen: port/channel %d on %s\n", g_listen_port, g_listen_steam ? "SteamNetDriver" : "IP driver");
    UClass *sc = ue_find_class("SteamNetDriver");
    if (sc && UC_CDO(sc)) {
        UObject *s = UC_CDO(sc);
        UObject *cc = ue_get_ptr(s, "NetConnectionClass");
        out_printf(o, "SteamNetDriver CDO: NetConnectionClassName=%s NetConnectionClass=%s\n",
                   fstring_short(s, "NetConnectionClassName", a, sizeof a), cc ? ue_obj_name(cc, b, sizeof b) : "null");
    }
    if (g_join_id) print_p2p(o, g_join_id);
    for (int i = 0; i < npeers; i++) if (peers[i] != g_join_id) print_p2p(o, peers[i]);
    if (!g_join_id && !npeers) out_printf(o, "no P2P peers yet\n");
    return 1;
}

void steamnet_note_join(const char *url) {
    g_join_id = !_strnicmp(url, "steam.", 6) ? strtoull(url + 6, NULL, 10) : 0;
}

void steamnet_config(const char *key, const char *v) {
    if (!strcmp(key, "transport")) {
        g_transport_steam = !_stricmp(v, "steam");
        LOG("steamnet: hosting transport %s", g_transport_steam ? "steam" : "ip");
    }
}
