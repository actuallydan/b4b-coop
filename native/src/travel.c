// Keeps a listen-server session alive across the host's level changes: the offline mission flow calls
// SetClientTravel(Absolute) which would drop every client, so we turn it into a server travel instead.
#include <stdio.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

typedef void (*SetClientTravelFn)(void *engine, UObject *world, const wchar_t *url, uint8_t type);
static SetClientTravelFn orig_travel;
static int redirecting;

// client auto-rejoin state
static char host_addr[256];          // last host we joined (set by `join`)
static double follow_until;           // rejoin window after following a server travel
static int retries_left;
static double retry_at;
static double now_s;

void travel_set_host(const char *addr) { snprintf(host_addr, sizeof host_addr, "%s", addr); }

// Client: a follow attempt that reached the host before it finished loading fails the DTLS handshake and
// then hangs in PendingNetGame; restart the join shortly instead of waiting for the connect timeout.
void travel_on_handshake_failed(void) {
    if (host_addr[0] && now_s < follow_until && retries_left > 0 && retry_at == 0) {
        retries_left--;
        retry_at = now_s + 3;
        LOG("travel: handshake failed during follow, rejoining %s in 3s", host_addr);
    }
}

void travel_tick(float dt) {
    now_s += dt;
    if (retry_at > 0 && now_s >= retry_at) {
        retry_at = 0;
        char cmd[300];
        snprintf(cmd, sizeof cmd, "open %s", host_addr);
        LOG("travel: rejoin attempt (%d left): %s", retries_left, cmd);
        game_exec(cmd);
    }
}

enum { TRAVEL_Absolute = 0, TRAVEL_Partial = 1, TRAVEL_Relative = 2 };

static int is_session_map(const char *u) {
    if (strstr(u, "closed") || strstr(u, "failed")) return 0;
    if (strstr(u, "MainMenu") || strstr(u, "/Maps/Legal") || strstr(u, "Entry")) return 0;
    return u[0] == '/' || strstr(u, "MAP_") != NULL;
}

static void travel_detour(void *engine, UObject *world, const wchar_t *url, uint8_t type) {
    char u[2048]; size_t n = 0;
    for (; url && url[n] && n < sizeof u - 1; n++) u[n] = url[n] < 128 ? (char)url[n] : '?';
    u[n] = 0;
    int listen = ue_is_listen_server(world), clients = ue_num_clients(world);
    LOG("SetClientTravel type=%d listen=%d clients=%d url=%s", type, listen, clients, u);
    if (!redirecting && type == TRAVEL_Absolute && listen && is_session_map(u)) {
        char cmd[2200];
        snprintf(cmd, sizeof cmd, "servertravel %s%s", u, strstr(u, "listen") ? "" : "?listen");
        LOG("travel: redirecting to %s", cmd);
        redirecting = 1;
        game_exec(cmd);
        redirecting = 0;
        return;
    }
    // auto-host: open the offline camp as a listen server directly instead of loading it twice
    if (!redirecting && cmds_auto_host() && type == TRAVEL_Absolute && strstr(u, "FortHope") && !strstr(u, "listen")) {
        static wchar_t w[2100]; size_t k = 0;
        for (; url[k] && k < 2090; k++) w[k] = url[k];
        const wchar_t *suffix = L"?listen";
        for (size_t j = 0; suffix[j]; j++) w[k++] = suffix[j];
        w[k] = 0;
        LOG("travel: auto-host, opening camp with ?listen");
        orig_travel(engine, world, w, type);
        return;
    }
    // client: server travel told us to follow the host -> open a rejoin window
    if (type == TRAVEL_Relative && !listen && host_addr[0] && is_session_map(u)) {
        follow_until = now_s + 300; retries_left = 20;
    }
    // client: the follow failed (host still loading, etc.) -> retry instead of dropping to our own camp
    if (!listen && host_addr[0] && strstr(u, "closed") && now_s < follow_until && retries_left > 0) {
        retries_left--;
        retry_at = now_s + 5;
        LOG("travel: suppressed ?closed, rejoining %s in 5s", host_addr);
        return;
    }
    orig_travel(engine, world, url, type);
}

int travel_init(void) {
    if (MH_CreateHook((void *)ADDR_SETCLIENTTRAVEL, (void *)travel_detour, (void **)&orig_travel) != MH_OK ||
        MH_EnableHook((void *)ADDR_SETCLIENTTRAVEL) != MH_OK) { LOG("travel: hook failed"); return -1; }
    LOG("travel: SetClientTravel hooked");
    return 0;
}
