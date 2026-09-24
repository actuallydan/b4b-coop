// Host join policy: who may join this session. Default: only the host's Steam friends (and the host's own account).
// b4bcoop.ini (host):
//   allow_joins=friends|anyone          default friends
//   allow_steamids=<id64>[,<id64>...]   always allowed, whatever allow_joins says (e.g. a friend of a friend)
// Checked on both join paths, before the game's own login code runs:
//   - Steam P2P (steamnet.c): at the P2P session request. Steam authenticates the remote SteamID, so this cannot be
//     spoofed; a refused peer's session is never accepted and its packets never reach the game.
//   - IP (admin.c, PreLogin hook): the SteamID the joiner's game sends with its login (FUniqueNetIdRepl). Offline mode
//     has no Steam auth ticket check, so this ID is the joiner's claim: it keeps strangers out, but someone who knows a
//     friend's SteamID and the host's address could pretend to be that friend. Refused with a clear login error.
// Friends = ISteamFriends::HasFriend(id, k_EFriendFlagImmediate) (presence.c). If the host's Steam friends list is not
// available, only the allowlist and the host's own account get in (fails closed; allow_joins=anyone opens it).
// Dev builds: allow_self=0 stops treating the host's own SteamID as allowed (every local test copy shares one account),
// and `joinpolicy [check <id64>]` shows the policy.
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define MAX_ALLOW 32
static int anyone;                       // allow_joins=anyone
static uint64_t allow[MAX_ALLOW];
static int n_allow;
static int allow_self = 1;               // dev builds: allow_self=0

int joinpolicy_config(const char *key, const char *v) {
    if (!strcmp(key, "allow_joins")) {
        if (!_stricmp(v, "anyone")) anyone = 1;
        else if (!_stricmp(v, "friends")) anyone = 0;
        else LOG("joinpolicy: unknown allow_joins=%s (friends|anyone), keeping %s", v, anyone ? "anyone" : "friends");
        return 1;
    }
    if (!strcmp(key, "allow_steamids")) {
        char buf[600];
        snprintf(buf, sizeof buf, "%s", v);
        for (char *t = strtok(buf, ", "); t; t = strtok(NULL, ", ")) {
            if (!_strnicmp(t, "steam:", 6)) t += 6;
            char *end;
            unsigned long long id = strtoull(t, &end, 10);
            if (*end || id < 0x0110000100000000ull) { LOG("joinpolicy: ignoring allow_steamids entry '%s' (not a SteamID64)", t); continue; }
            if (n_allow < MAX_ALLOW) allow[n_allow++] = id;
        }
        return 1;
    }
#ifndef B4B_RELEASE
    if (!strcmp(key, "allow_self")) { allow_self = atoi(v) != 0; return 1; }
#endif
    return 0;
}

void joinpolicy_init(void) {
    LOG("joinpolicy: joins from %s, %d allowlisted SteamID(s)%s", anyone ? "anyone" : "Steam friends only", n_allow,
        allow_self ? "" : ", own SteamID NOT allowed (allow_self=0, dev)");
}

// Client-facing login error (IP path). chat.c's chat_on_join_failed keys on "Steam friends".
const char *joinpolicy_error(void) {
    return "This host only accepts their Steam friends. Ask the host to add you (or to allow_steamids).";
}

// 1 if `id` (SteamID64, 0 = unknown) may join; `why` says which rule decided (for logs).
int joinpolicy_check(uint64_t id, char *why, size_t n) {
    if (anyone) { snprintf(why, n, "allow_joins=anyone"); return 1; }
    if (!id) { snprintf(why, n, "no SteamID in the login"); return 0; }
    if (allow_self && id == steamnet_local_id()) { snprintf(why, n, "the host's own account"); return 1; }
    for (int i = 0; i < n_allow; i++) if (allow[i] == id) { snprintf(why, n, "allow_steamids"); return 1; }
    int f = presence_has_friend(id);
    if (f > 0) { snprintf(why, n, "Steam friend"); return 1; }
    snprintf(why, n, f < 0 ? "the host's Steam friends list is unavailable" : "not on the host's Steam friends list");
    return 0;
}

// Tell the host (local chat line) that someone was turned away. Any thread: shown on the next tick; one line per
// SteamID per session.
static CRITICAL_SECTION cs;
static INIT_ONCE once = INIT_ONCE_STATIC_INIT;
static BOOL CALLBACK init_cs(PINIT_ONCE a, PVOID b, PVOID *c) { (void)a; (void)b; (void)c; InitializeCriticalSection(&cs); return TRUE; }
static uint64_t told[32];
static int n_told;
static uint64_t pend_id;
static char pend_why[96];

void joinpolicy_notify_refused(uint64_t id, const char *why) {
    InitOnceExecuteOnce(&once, init_cs, NULL, NULL);
    EnterCriticalSection(&cs);
    int seen = 0;
    for (int i = 0; i < n_told; i++) if (told[i] == id) seen = 1;
    if (!seen && !pend_id) {
        if (n_told < 32) told[n_told++] = id;
        pend_id = id ? id : 1;
        snprintf(pend_why, sizeof pend_why, "%s", why);
    }
    LeaveCriticalSection(&cs);
}

void joinpolicy_tick(float dt) {
    (void)dt;
    if (!pend_id) return;
    InitOnceExecuteOnce(&once, init_cs, NULL, NULL);
    EnterCriticalSection(&cs);
    uint64_t id = pend_id; char why[96];
    snprintf(why, sizeof why, "%s", pend_why);
    pend_id = 0;
    LeaveCriticalSection(&cs);
    if (id > 1) chat_local("Refused a join from %s (steam:%llu): %s. To let them in: allow_steamids=%llu in b4bcoop.ini.",
                           presence_persona(id), (unsigned long long)id, why, (unsigned long long)id);
    else chat_local("Refused a join: %s.", why);
}

#ifndef B4B_RELEASE
// joinpolicy [check <id64>]
int joinpolicy_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "joinpolicy")) return 0;
    out_printf(o, "joinpolicy: %s, allow_self=%d, own id %llu, allowlist:", anyone ? "anyone" : "friends", allow_self,
               (unsigned long long)steamnet_local_id());
    for (int i = 0; i < n_allow; i++) out_printf(o, " %llu", (unsigned long long)allow[i]);
    out_printf(o, "%s\n", n_allow ? "" : " -");
    if (rest && !strncmp(rest, "check ", 6)) {
        char why[96];
        uint64_t id = strtoull(rest + 6, NULL, 10);
        int ok = joinpolicy_check(id, why, sizeof why);
        out_printf(o, "%llu: %s (%s)\n", (unsigned long long)id, ok ? "allowed" : "refused", why);
    }
    return 1;
}
#endif
