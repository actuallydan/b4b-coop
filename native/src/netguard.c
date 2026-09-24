// netguard: keep an offline co-op session from contacting third-party services.
//
// Hooks (MinHook, inline on the exported functions so every module is covered: the exe's libcurl/libwebsockets,
// the delay-loaded EOS SDK and Vivox, WinHTTP users):
//   name resolution  ws2_32 getaddrinfo / GetAddrInfoW / GetAddrInfoExA/W / gethostbyname / WSAConnectByNameA/W,
//                    winhttp WinHttpConnect  -> non-allowlisted hostnames fail with "host not found"
//   raw-IP TCP       ws2_32 connect / WSAConnect / ConnectEx (captured via WSAIoctl) -> TCP to public IPs that did
//                    not come from an allowed lookup fails with WSAENETUNREACH. UDP is never touched (game traffic).
//   EOS              EOS_Platform_Create (hooked when EOSSDK-Win64-Shipping.dll maps) -> SetNetworkStatus(Disabled)
//
// Installed from DllMain: dwmapi.dll is a static import of Back4Blood.exe, so this runs during loader init, before
// the exe's entry point and before any game thread exists. Details: docs/investigations/outbound-traffic.md.
//
// b4bcoop.ini keys: netguard=block|log|off (default block), netguard_eos=0|1 (default 1),
//                   netguard_allow=host[,*.suffix,1.2.3.4...] (repeatable)
#include <winsock2.h>
#include <ws2tcpip.h>
#include <mswsock.h>
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "MinHook.h"
#include "log.h"
#include "cmds.h"
#include "netguard.h"

enum { NG_OFF, NG_BLOCK, NG_LOG };
enum { V_ALLOW, V_BLOCK, V_WOULD };          // verdicts recorded in the table
enum { D_BLOCK, D_ALLOW, D_ALLOW_REMEMBER };  // decide_name(): allow + remember resolved IPs for TCP

static int g_mode = NG_BLOCK, g_eos_off = 1, g_hooks;
static CRITICAL_SECTION cs;
static DWORD g_tls = TLS_OUT_OF_INDEXES;      // per-thread "inside an allowed call" flag (nested ws2_32 calls)
static const char *g_eos_state = "not loaded";
static int g_eos_result = -1;
static unsigned g_blocked_total;

// ---- tables ----
typedef struct { char kind[5]; char host[112]; char via[40]; char why[20]; int verdict; unsigned count; SYSTEMTIME first; } Seen;
#define MAX_SEEN 512
static Seen seen[MAX_SEEN];
static int nseen;
static unsigned seen_dropped;

#define MAX_NAMES 64
static char cfg_allow[MAX_NAMES][112];
static int ncfg;
static char dyn_allow[MAX_NAMES][112];
static int ndyn, dyn_next;

typedef struct { int fam; uint8_t a[16]; } IpKey;
#define MAX_IPS 256
static IpKey ok_ips[MAX_IPS];
static int nips, ip_next;

static char own_host[256];
static int own_host_done;

// ---- small helpers ----
static void lower_trim(char *dst, size_t n, const char *src) {
    size_t i = 0;
    while (*src == ' ' || *src == '\t') src++;
    for (; *src && i < n - 1; src++) dst[i++] = (*src >= 'A' && *src <= 'Z') ? *src + 32 : *src;
    while (i && (dst[i - 1] == '.' || dst[i - 1] == ' ' || dst[i - 1] == '\t')) i--;
    dst[i] = 0;
}

static void w2u(char *dst, size_t n, const wchar_t *src) {
    dst[0] = 0;
    if (src && !WideCharToMultiByte(CP_UTF8, 0, src, -1, dst, (int)n, NULL, NULL)) dst[0] = 0;
    dst[n - 1] = 0;
}

static int ends_with(const char *s, const char *suf) {
    size_t a = strlen(s), b = strlen(suf);
    return a >= b && !strcmp(s + a - b, suf);
}

static int pattern_match(const char *pat, const char *h) {
    if (pat[0] == '*' && pat[1] == '.') return !strcmp(h, pat + 2) || ends_with(h, pat + 1);
    if (pat[0] == '.') return !strcmp(h, pat + 1) || ends_with(h, pat);
    return !strcmp(pat, h);
}

static int is_literal(const char *h) {
    if (strchr(h, ':')) return 1;                    // IPv6 (hostnames never contain ':')
    int digit = 0;
    for (const char *c = h; *c; c++) { if (*c >= '0' && *c <= '9') digit = 1; else if (*c != '.') return 0; }
    return digit;
}

static const char *static_rule(const char *h) {
    if (!*h) return "local";
    if (is_literal(h)) return "ip-literal";          // no DNS query; TCP to it is judged by the connect hook
    if (!strcmp(h, "localhost") || ends_with(h, ".localhost")) return "loopback";
    if (!strchr(h, '.')) return "single-label";      // LAN host / own machine name (NetBIOS/LLMNR stay on the LAN)
    if (ends_with(h, ".local") || ends_with(h, ".lan") || ends_with(h, ".home.arpa") || ends_with(h, ".internal"))
        return "lan-suffix";
    if (!own_host_done) {           // needs WSAStartup; retried until it succeeds
        char b[256];
        if (!gethostname(b, sizeof b)) { lower_trim(own_host, sizeof own_host, b); own_host_done = 1; }
    }
    if (own_host[0] && !strcmp(h, own_host)) return "own-host";
    return NULL;
}

// caller holds cs
static const char *list_rule(const char *h) {
    for (int i = 0; i < ncfg; i++) if (pattern_match(cfg_allow[i], h)) return "allowlist";
    for (int i = 0; i < ndyn; i++) if (!strcmp(dyn_allow[i], h)) return "runtime-allow";
    return NULL;
}

// caller holds cs
static void dyn_add_locked(const char *h) {
    if (!*h) return;
    for (int i = 0; i < ndyn; i++) if (!strcmp(dyn_allow[i], h)) return;
    snprintf(dyn_allow[dyn_next], sizeof dyn_allow[0], "%s", h);
    dyn_next = (dyn_next + 1) % MAX_NAMES;
    if (ndyn < MAX_NAMES) ndyn++;
}

static void module_of(void *addr, char *out, size_t n) {
    HMODULE m = NULL;
    wchar_t p[MAX_PATH];
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCWSTR)addr, &m) || !GetModuleFileNameW(m, p, MAX_PATH)) {
        snprintf(out, n, "%p", addr);
        return;
    }
    wchar_t *b = wcsrchr(p, L'\\');
    w2u(out, n, b ? b + 1 : p);
}

// Steam client libraries living in the game process (Windows: steamclient64 & co; Proton: lsteamclient).
static int trusted_module(const char *m) {
    char l[40];
    lower_trim(l, sizeof l, m);
    static const char *pre[] = {"steam", "lsteamclient", "gameoverlayrenderer", "tier0_s", "vstdlib_s"};
    for (size_t i = 0; i < sizeof pre / sizeof *pre; i++) if (!strncmp(l, pre[i], strlen(pre[i]))) return 1;
    return 0;
}

static int bypassing(void) { return g_tls != TLS_OUT_OF_INDEXES && TlsGetValue(g_tls); }
static void bypass(int on) { if (g_tls != TLS_OUT_OF_INDEXES) TlsSetValue(g_tls, on ? (void *)1 : NULL); }

static void note(const char *kind, const char *host, const char *via, const char *why, int verdict) {
    int fresh = 0;
    EnterCriticalSection(&cs);
    if (verdict == V_BLOCK) g_blocked_total++;
    int i = 0;
    for (; i < nseen; i++)
        if (seen[i].verdict == verdict && !strcmp(seen[i].kind, kind) && !strcmp(seen[i].host, host)) break;
    if (i < nseen) seen[i].count++;
    else if (nseen < MAX_SEEN) {
        Seen *s = &seen[nseen++];
        snprintf(s->kind, sizeof s->kind, "%s", kind);
        snprintf(s->host, sizeof s->host, "%s", host);
        snprintf(s->via, sizeof s->via, "%s", via);
        snprintf(s->why, sizeof s->why, "%s", why);
        s->verdict = verdict;
        s->count = 1;
        GetLocalTime(&s->first);
        fresh = 1;
    } else seen_dropped++;
    LeaveCriticalSection(&cs);
    if (fresh)
        LOG("netguard: %s %s %s via %s (%s)", verdict == V_BLOCK ? "BLOCK" : verdict == V_WOULD ? "WOULD-BLOCK" : "allow",
            kind, host, via, why);
}

// ---- decisions ----
static int decide_name(const char *kind, const char *raw, void *ra) {
    char h[112], via[40];
    lower_trim(h, sizeof h, raw ? raw : "");
    const char *why = static_rule(h);
    int remember = 0;
    if (!why) {
        EnterCriticalSection(&cs);
        why = list_rule(h);
        LeaveCriticalSection(&cs);
        remember = why != NULL;
    }
    module_of(ra, via, sizeof via);
    if (!why && trusted_module(via)) {
        why = "trusted-caller";
        remember = 1;
        EnterCriticalSection(&cs);
        dyn_add_locked(h);          // the library may resolve the same name again from a worker thread
        LeaveCriticalSection(&cs);
    }
    if (why) { note(kind, h, via, why, V_ALLOW); return remember ? D_ALLOW_REMEMBER : D_ALLOW; }
    note(kind, h, via, "not-allowlisted", g_mode == NG_LOG ? V_WOULD : V_BLOCK);
    return g_mode == NG_LOG ? D_ALLOW : D_BLOCK;
}

static int ip_of(const struct sockaddr *sa, int len, IpKey *k, unsigned *port) {
    memset(k, 0, sizeof *k);
    if (!sa) return 0;
    if (sa->sa_family == AF_INET && len >= (int)sizeof(struct sockaddr_in)) {
        const struct sockaddr_in *a = (const void *)sa;
        k->fam = AF_INET; memcpy(k->a, &a->sin_addr, 4); *port = ntohs(a->sin_port);
        return 1;
    }
    if (sa->sa_family == AF_INET6 && len >= (int)sizeof(struct sockaddr_in6)) {
        const struct sockaddr_in6 *a = (const void *)sa;
        static const uint8_t mapped[12] = {0,0,0,0,0,0,0,0,0,0,0xff,0xff};
        *port = ntohs(a->sin6_port);
        if (!memcmp(&a->sin6_addr, mapped, 12)) { k->fam = AF_INET; memcpy(k->a, (const uint8_t *)&a->sin6_addr + 12, 4); }
        else { k->fam = AF_INET6; memcpy(k->a, &a->sin6_addr, 16); }
        return 1;
    }
    return 0;
}

static const char *ip_class(const IpKey *k) {
    const uint8_t *a = k->a;
    if (k->fam == AF_INET) {
        if (a[0] == 127) return "loopback";
        if (a[0] == 0) return "unspecified";
        if (a[0] == 10 || (a[0] == 172 && (a[1] & 0xF0) == 16) || (a[0] == 192 && a[1] == 168)) return "private";
        if (a[0] == 169 && a[1] == 254) return "link-local";
        if (a[0] == 100 && (a[1] & 0xC0) == 64) return "cgnat";      // 100.64/10 (Tailscale etc.)
        if (a[0] >= 224) return "multicast";
        return NULL;
    }
    static const uint8_t zero[16];
    if (!memcmp(a, zero, 15) && a[15] == 1) return "loopback";
    if (!memcmp(a, zero, 16)) return "unspecified";
    if (a[0] == 0xfe && (a[1] & 0xC0) == 0x80) return "link-local";
    if ((a[0] & 0xFE) == 0xfc) return "private";                     // ULA fc00::/7
    if (a[0] == 0xff) return "multicast";
    return NULL;
}

static void remember_ip(const struct sockaddr *sa, size_t len) {
    IpKey k; unsigned port;
    if (!ip_of(sa, (int)len, &k, &port) || ip_class(&k)) return;
    EnterCriticalSection(&cs);
    int i = 0;
    for (; i < nips; i++) if (!memcmp(&ok_ips[i], &k, sizeof k)) break;
    if (i == nips) { ok_ips[ip_next] = k; ip_next = (ip_next + 1) % MAX_IPS; if (nips < MAX_IPS) nips++; }
    LeaveCriticalSection(&cs);
}

// 1 = let the connect through
static int decide_addr(SOCKET s, const struct sockaddr *sa, int len, void *ra) {
    IpKey k; unsigned port;
    if (!ip_of(sa, len, &k, &port)) return 1;
    int type = 0, tl = sizeof type;
    if (getsockopt(s, SOL_SOCKET, SO_TYPE, (char *)&type, &tl)) type = 0;
    const char *why = ip_class(&k);
    char ip[64], host[112], via[40];
    if (!inet_ntop(k.fam, k.a, ip, sizeof ip)) snprintf(ip, sizeof ip, "?");
    module_of(ra, via, sizeof via);
    if (type != SOCK_STREAM) {           // UDP: game traffic goes to any peer IP; never filtered, only noted
        if (!why || strcmp(why, "loopback")) note("udp", ip, via, why ? why : "udp-unfiltered", V_ALLOW);
        return 1;
    }
    snprintf(host, sizeof host, k.fam == AF_INET6 ? "[%s]:%u" : "%s:%u", ip, port);
    if (!why) {
        EnterCriticalSection(&cs);
        for (int i = 0; i < nips && !why; i++) if (!memcmp(&ok_ips[i], &k, sizeof k)) why = "resolved-allowed";
        for (int i = 0; i < ncfg && !why; i++) if (!strcmp(cfg_allow[i], ip)) why = "allowlist";
        LeaveCriticalSection(&cs);
    }
    if (!why && trusted_module(via)) why = "trusted-caller";
    if (why) { note("tcp", host, via, why, V_ALLOW); return 1; }
    note("tcp", host, via, "public-ip", g_mode == NG_LOG ? V_WOULD : V_BLOCK);
    return g_mode == NG_LOG;
}

// ---- ws2_32 hooks ----
typedef int (WSAAPI *getaddrinfo_t)(PCSTR, PCSTR, const ADDRINFOA *, PADDRINFOA *);
typedef int (WSAAPI *GetAddrInfoW_t)(PCWSTR, PCWSTR, const ADDRINFOW *, PADDRINFOW *);
typedef int (WSAAPI *GetAddrInfoExA_t)(PCSTR, PCSTR, DWORD, LPGUID, const ADDRINFOEXA *, PADDRINFOEXA *,
                                       struct timeval *, LPOVERLAPPED, LPLOOKUPSERVICE_COMPLETION_ROUTINE, LPHANDLE);
typedef int (WSAAPI *GetAddrInfoExW_t)(PCWSTR, PCWSTR, DWORD, LPGUID, const ADDRINFOEXW *, PADDRINFOEXW *,
                                       struct timeval *, LPOVERLAPPED, LPLOOKUPSERVICE_COMPLETION_ROUTINE, LPHANDLE);
typedef struct hostent *(WSAAPI *gethostbyname_t)(const char *);
typedef BOOL (PASCAL *WSAConnectByNameA_t)(SOCKET, LPCSTR, LPCSTR, LPDWORD, LPSOCKADDR, LPDWORD, LPSOCKADDR,
                                           const struct timeval *, LPWSAOVERLAPPED);
typedef BOOL (PASCAL *WSAConnectByNameW_t)(SOCKET, LPWSTR, LPWSTR, LPDWORD, LPSOCKADDR, LPDWORD, LPSOCKADDR,
                                           const struct timeval *, LPWSAOVERLAPPED);
typedef int (WSAAPI *connect_t)(SOCKET, const struct sockaddr *, int);
typedef int (WSAAPI *WSAConnect_t)(SOCKET, const struct sockaddr *, int, LPWSABUF, LPWSABUF, LPQOS, LPQOS);
typedef int (WSAAPI *WSAIoctl_t)(SOCKET, DWORD, LPVOID, DWORD, LPVOID, DWORD, LPDWORD, LPWSAOVERLAPPED,
                                 LPWSAOVERLAPPED_COMPLETION_ROUTINE);
typedef BOOL (PASCAL *ConnectEx_t)(SOCKET, const struct sockaddr *, int, PVOID, DWORD, LPDWORD, LPOVERLAPPED);

static getaddrinfo_t o_getaddrinfo;
static GetAddrInfoW_t o_GetAddrInfoW;
static GetAddrInfoExA_t o_GetAddrInfoExA;
static GetAddrInfoExW_t o_GetAddrInfoExW;
static gethostbyname_t o_gethostbyname;
static WSAConnectByNameA_t o_WSAConnectByNameA;
static WSAConnectByNameW_t o_WSAConnectByNameW;
static connect_t o_connect;
static WSAConnect_t o_WSAConnect;
static WSAIoctl_t o_WSAIoctl;

#define NOT_FOUND() do { WSASetLastError(WSAHOST_NOT_FOUND); return WSAHOST_NOT_FOUND; } while (0)

static int WSAAPI h_getaddrinfo(PCSTR node, PCSTR svc, const ADDRINFOA *hints, PADDRINFOA *res) {
    if (!node || !*node || bypassing()) return o_getaddrinfo(node, svc, hints, res);
    int d = decide_name("dns", node, __builtin_return_address(0));
    if (d == D_BLOCK) { if (res) *res = NULL; NOT_FOUND(); }
    bypass(1); int r = o_getaddrinfo(node, svc, hints, res); bypass(0);
    if (!r && res && d == D_ALLOW_REMEMBER) for (ADDRINFOA *a = *res; a; a = a->ai_next) remember_ip(a->ai_addr, a->ai_addrlen);
    return r;
}

static int WSAAPI h_GetAddrInfoW(PCWSTR node, PCWSTR svc, const ADDRINFOW *hints, PADDRINFOW *res) {
    if (!node || !*node || bypassing()) return o_GetAddrInfoW(node, svc, hints, res);
    char n[256]; w2u(n, sizeof n, node);
    int d = decide_name("dns", n, __builtin_return_address(0));
    if (d == D_BLOCK) { if (res) *res = NULL; NOT_FOUND(); }
    bypass(1); int r = o_GetAddrInfoW(node, svc, hints, res); bypass(0);
    if (!r && res && d == D_ALLOW_REMEMBER) for (ADDRINFOW *a = *res; a; a = a->ai_next) remember_ip(a->ai_addr, a->ai_addrlen);
    return r;
}

static int WSAAPI h_GetAddrInfoExA(PCSTR name, PCSTR svc, DWORD ns, LPGUID nsid, const ADDRINFOEXA *hints,
                                   PADDRINFOEXA *res, struct timeval *tv, LPOVERLAPPED ov,
                                   LPLOOKUPSERVICE_COMPLETION_ROUTINE cr, LPHANDLE h) {
    if (!name || !*name || bypassing()) return o_GetAddrInfoExA(name, svc, ns, nsid, hints, res, tv, ov, cr, h);
    int d = decide_name("dns", name, __builtin_return_address(0));
    if (d == D_BLOCK) { if (res) *res = NULL; if (h) *h = NULL; NOT_FOUND(); }
    bypass(1); int r = o_GetAddrInfoExA(name, svc, ns, nsid, hints, res, tv, ov, cr, h); bypass(0);
    if (!r && !ov && res && d == D_ALLOW_REMEMBER) for (ADDRINFOEXA *a = *res; a; a = a->ai_next) remember_ip(a->ai_addr, a->ai_addrlen);
    return r;
}

static int WSAAPI h_GetAddrInfoExW(PCWSTR name, PCWSTR svc, DWORD ns, LPGUID nsid, const ADDRINFOEXW *hints,
                                   PADDRINFOEXW *res, struct timeval *tv, LPOVERLAPPED ov,
                                   LPLOOKUPSERVICE_COMPLETION_ROUTINE cr, LPHANDLE h) {
    if (!name || !*name || bypassing()) return o_GetAddrInfoExW(name, svc, ns, nsid, hints, res, tv, ov, cr, h);
    char n[256]; w2u(n, sizeof n, name);
    int d = decide_name("dns", n, __builtin_return_address(0));
    if (d == D_BLOCK) { if (res) *res = NULL; if (h) *h = NULL; NOT_FOUND(); }
    bypass(1); int r = o_GetAddrInfoExW(name, svc, ns, nsid, hints, res, tv, ov, cr, h); bypass(0);
    if (!r && !ov && res && d == D_ALLOW_REMEMBER) for (ADDRINFOEXW *a = *res; a; a = a->ai_next) remember_ip(a->ai_addr, a->ai_addrlen);
    return r;
}

static struct hostent *WSAAPI h_gethostbyname(const char *name) {
    if (!name || !*name || bypassing()) return o_gethostbyname(name);
    int d = decide_name("dns", name, __builtin_return_address(0));
    if (d == D_BLOCK) { WSASetLastError(WSAHOST_NOT_FOUND); return NULL; }
    bypass(1); struct hostent *he = o_gethostbyname(name); bypass(0);
    if (he && d == D_ALLOW_REMEMBER && he->h_addrtype == AF_INET)
        for (char **p = he->h_addr_list; p && *p; p++) {
            struct sockaddr_in a = {0}; a.sin_family = AF_INET; memcpy(&a.sin_addr, *p, 4);
            remember_ip((struct sockaddr *)&a, sizeof a);
        }
    return he;
}

static BOOL PASCAL h_WSAConnectByNameA(SOCKET s, LPCSTR node, LPCSTR svc, LPDWORD ll, LPSOCKADDR la, LPDWORD rl,
                                       LPSOCKADDR ra_, const struct timeval *tv, LPWSAOVERLAPPED rsv) {
    if (!bypassing() && node && decide_name("dns", node, __builtin_return_address(0)) == D_BLOCK) {
        WSASetLastError(WSAHOST_NOT_FOUND); return FALSE;
    }
    bypass(1); BOOL r = o_WSAConnectByNameA(s, node, svc, ll, la, rl, ra_, tv, rsv); bypass(0);
    return r;
}

static BOOL PASCAL h_WSAConnectByNameW(SOCKET s, LPWSTR node, LPWSTR svc, LPDWORD ll, LPSOCKADDR la, LPDWORD rl,
                                       LPSOCKADDR ra_, const struct timeval *tv, LPWSAOVERLAPPED rsv) {
    if (!bypassing() && node) {
        char n[256]; w2u(n, sizeof n, node);
        if (decide_name("dns", n, __builtin_return_address(0)) == D_BLOCK) { WSASetLastError(WSAHOST_NOT_FOUND); return FALSE; }
    }
    bypass(1); BOOL r = o_WSAConnectByNameW(s, node, svc, ll, la, rl, ra_, tv, rsv); bypass(0);
    return r;
}

static int WSAAPI h_connect(SOCKET s, const struct sockaddr *sa, int len) {
    if (bypassing()) return o_connect(s, sa, len);
    if (!decide_addr(s, sa, len, __builtin_return_address(0))) { WSASetLastError(WSAENETUNREACH); return SOCKET_ERROR; }
    bypass(1); int r = o_connect(s, sa, len); bypass(0);
    return r;
}

static int WSAAPI h_WSAConnect(SOCKET s, const struct sockaddr *sa, int len, LPWSABUF cd, LPWSABUF cld, LPQOS q, LPQOS gq) {
    if (bypassing()) return o_WSAConnect(s, sa, len, cd, cld, q, gq);
    if (!decide_addr(s, sa, len, __builtin_return_address(0))) { WSASetLastError(WSAENETUNREACH); return SOCKET_ERROR; }
    bypass(1); int r = o_WSAConnect(s, sa, len, cd, cld, q, gq); bypass(0);
    return r;
}

// ConnectEx is only reachable through WSAIoctl(SIO_GET_EXTENSION_FUNCTION_POINTER); hook each distinct pointer
// the first time someone asks for it (one per transport provider; normally just one).
static ConnectEx_t o_cex[2];
static void *cex_target[2];
static BOOL cex_common(int i, SOCKET s, const struct sockaddr *sa, int len, PVOID b, DWORD bl, LPDWORD sent,
                       LPOVERLAPPED ov, void *ra) {
    if (bypassing()) return o_cex[i](s, sa, len, b, bl, sent, ov);
    if (!decide_addr(s, sa, len, ra)) { WSASetLastError(WSAENETUNREACH); return FALSE; }
    bypass(1); BOOL r = o_cex[i](s, sa, len, b, bl, sent, ov); bypass(0);
    return r;
}
static BOOL PASCAL h_cex0(SOCKET s, const struct sockaddr *sa, int len, PVOID b, DWORD bl, LPDWORD sent, LPOVERLAPPED ov) {
    return cex_common(0, s, sa, len, b, bl, sent, ov, __builtin_return_address(0));
}
static BOOL PASCAL h_cex1(SOCKET s, const struct sockaddr *sa, int len, PVOID b, DWORD bl, LPDWORD sent, LPOVERLAPPED ov) {
    return cex_common(1, s, sa, len, b, bl, sent, ov, __builtin_return_address(0));
}

static void hook_connectex(void *fn) {
    static void *const det[2] = {(void *)h_cex0, (void *)h_cex1};
    int slot = -1;
    EnterCriticalSection(&cs);
    for (int i = 0; i < 2; i++) if (cex_target[i] == fn) { LeaveCriticalSection(&cs); return; }
    for (int i = 0; i < 2 && slot < 0; i++) if (!cex_target[i]) { cex_target[i] = fn; slot = i; }
    LeaveCriticalSection(&cs);
    if (slot < 0) { LOG("netguard: ConnectEx %p not hooked (no free slot)", fn); return; }
    if (MH_CreateHook(fn, det[slot], (void **)&o_cex[slot]) != MH_OK || MH_EnableHook(fn) != MH_OK)
        LOG("netguard: ConnectEx %p hook failed", fn);
    else { g_hooks++; LOG("netguard: ConnectEx %p hooked", fn); }
}

static int WSAAPI h_WSAIoctl(SOCKET s, DWORD code, LPVOID in, DWORD cin, LPVOID out, DWORD cout, LPDWORD ret,
                             LPWSAOVERLAPPED ov, LPWSAOVERLAPPED_COMPLETION_ROUTINE cr) {
    int r = o_WSAIoctl(s, code, in, cin, out, cout, ret, ov, cr);
    if (!r && code == SIO_GET_EXTENSION_FUNCTION_POINTER && in && cin >= sizeof(GUID) && out && cout >= sizeof(void *)) {
        static const GUID cex = WSAID_CONNECTEX;
        if (!memcmp(in, &cex, sizeof cex) && *(void **)out) {
            DWORD e = WSAGetLastError();
            hook_connectex(*(void **)out);
            WSASetLastError(e);
        }
    }
    return r;
}

// ---- winhttp ----
typedef void *(WINAPI *WinHttpConnect_t)(void *, const wchar_t *, WORD, DWORD);
static WinHttpConnect_t o_WinHttpConnect;
static void *WINAPI h_WinHttpConnect(void *sess, const wchar_t *server, WORD port, DWORD rsv) {
    char n[256]; w2u(n, sizeof n, server);
    // Allowed names are resolved later (maybe on a WinHTTP worker thread); decide_name records trusted names so
    // that the nested lookup passes too.
    if (decide_name("http", n, __builtin_return_address(0)) == D_BLOCK) {
        SetLastError(12007 /* ERROR_WINHTTP_NAME_NOT_RESOLVED */);
        return NULL;
    }
    return o_WinHttpConnect(sess, server, port, rsv);
}

static void hook_winhttp(HMODULE m) {
    static volatile LONG done;
    if (!m || InterlockedExchange(&done, 1)) return;
    void *t = (void *)GetProcAddress(m, "WinHttpConnect");
    if (t && MH_CreateHook(t, (void *)h_WinHttpConnect, (void **)&o_WinHttpConnect) == MH_OK && MH_EnableHook(t) == MH_OK) {
        g_hooks++; LOG("netguard: WinHttpConnect hooked");
    } else LOG("netguard: WinHttpConnect hook failed");
}

// ---- EOS SDK ----
typedef void *(*EOS_Platform_Create_t)(const void *);
typedef int (*EOS_Platform_SetNetworkStatus_t)(void *, int);
static EOS_Platform_Create_t o_eos_create;
static EOS_Platform_SetNetworkStatus_t p_eos_setnet;
#define EOS_NS_DISABLED 1

static void *h_EOS_Platform_Create(const void *opts) {
    void *h = o_eos_create(opts);
    if (h && p_eos_setnet) {
        g_eos_result = p_eos_setnet(h, EOS_NS_DISABLED);
        g_eos_state = g_eos_result == 0 ? "network disabled" : "SetNetworkStatus failed";
        LOG("netguard: EOS platform %p created; SetNetworkStatus(Disabled) = %d", h, g_eos_result);
    } else {
        g_eos_state = h ? "no SetNetworkStatus export" : "platform create failed";
        LOG("netguard: EOS platform create returned %p (%s)", h, g_eos_state);
    }
    return h;
}

static void hook_eos(HMODULE m) {
    static volatile LONG done;
    if (!m || InterlockedExchange(&done, 1)) return;
    g_eos_state = "loaded";
    if (!g_eos_off) { g_eos_state = "loaded (netguard_eos=0)"; return; }
    p_eos_setnet = (EOS_Platform_SetNetworkStatus_t)GetProcAddress(m, "EOS_Platform_SetNetworkStatus");
    void *t = (void *)GetProcAddress(m, "EOS_Platform_Create");
    if (t && MH_CreateHook(t, (void *)h_EOS_Platform_Create, (void **)&o_eos_create) == MH_OK && MH_EnableHook(t) == MH_OK) {
        g_hooks++; g_eos_state = "hooked, platform not created yet";
        LOG("netguard: EOS_Platform_Create hooked");
    } else { g_eos_state = "hook failed"; LOG("netguard: EOS_Platform_Create hook failed"); }
}

// EOSSDK and (possibly) winhttp map after us: EOS is a delay-load import resolved when the online subsystem starts.
typedef struct { USHORT Length, MaximumLength; PWSTR Buffer; } NgUStr;
typedef struct { ULONG Flags; const NgUStr *FullDllName; const NgUStr *BaseDllName; PVOID DllBase; ULONG SizeOfImage; } NgDllData;
typedef VOID (CALLBACK *NgDllNotify)(ULONG, const NgDllData *, PVOID);
typedef LONG (NTAPI *LdrRegisterDllNotification_t)(ULONG, NgDllNotify, PVOID, PVOID *);

static int ustr_ieq(const NgUStr *u, const wchar_t *s) {
    size_t n = wcslen(s);
    return u && u->Buffer && u->Length / 2 == n && !_wcsnicmp(u->Buffer, s, n);
}

static VOID CALLBACK dll_notify(ULONG reason, const NgDllData *d, PVOID ctx) {
    if (reason != 1 /* LDR_DLL_NOTIFICATION_REASON_LOADED */ || !d) return;
    if (ustr_ieq(d->BaseDllName, L"EOSSDK-Win64-Shipping.dll")) hook_eos((HMODULE)d->DllBase);
    else if (ustr_ieq(d->BaseDllName, L"winhttp.dll")) hook_winhttp((HMODULE)d->DllBase);
}

// ---- config ----
static void add_allow_list(char *v) {
    for (char *t = strtok(v, ", \t"); t; t = strtok(NULL, ", \t")) {
        if (ncfg >= MAX_NAMES) break;
        lower_trim(cfg_allow[ncfg], sizeof cfg_allow[0], t);
        if (cfg_allow[ncfg][0]) ncfg++;
    }
}

static void load_config(void) {
    const char *path = cmds_config_path();   // b4bcoop.ini or B4B_COOP_CONFIG (per-instance testing)
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        char *nl = strpbrk(line, "\r\n"); if (nl) *nl = 0;
        char *v = strchr(line, '=');
        if (!v || line[0] == '#' || line[0] == ';') continue;
        *v++ = 0;
        char k[64]; lower_trim(k, sizeof k, line);
        while (*v == ' ') v++;
        if (!strcmp(k, "netguard")) {
            char m[16]; lower_trim(m, sizeof m, v);
            g_mode = (!strcmp(m, "0") || !strcmp(m, "off")) ? NG_OFF : (!strcmp(m, "2") || !strcmp(m, "log")) ? NG_LOG : NG_BLOCK;
        } else if (!strcmp(k, "netguard_eos")) g_eos_off = atoi(v) != 0;
        else if (!strcmp(k, "netguard_allow")) add_allow_list(v);
    }
    fclose(f);
}

// ---- init ----
static void qhook(const char *fn, void *det, void *orig) {
    void *target = NULL;
    MH_STATUS st = MH_CreateHookApiEx(L"ws2_32", fn, det, (void **)orig, &target);
    if (st == MH_OK) st = MH_QueueEnableHook(target);
    if (st == MH_OK) g_hooks++;
    else LOG("netguard: %s not hooked (MinHook %d)%s", fn, st,   // Wine: GetAddrInfoExA is a stub
             !strcmp(fn, "GetAddrInfoExA") ? " - optional" : "");
}

static const char *mode_name(void) { return g_mode == NG_OFF ? "off" : g_mode == NG_LOG ? "log-only" : "block"; }

void netguard_init(void) {
    InitializeCriticalSection(&cs);
    load_config();
    if (g_mode == NG_OFF) { LOG("netguard: off (b4bcoop.ini netguard=off)"); return; }
    g_tls = TlsAlloc();
    MH_STATUS st = MH_Initialize();
    if (st != MH_OK && st != MH_ERROR_ALREADY_INITIALIZED) { LOG("netguard: MH_Initialize failed %d", st); return; }
    qhook("getaddrinfo", (void *)h_getaddrinfo, &o_getaddrinfo);
    qhook("GetAddrInfoW", (void *)h_GetAddrInfoW, &o_GetAddrInfoW);
    qhook("GetAddrInfoExA", (void *)h_GetAddrInfoExA, &o_GetAddrInfoExA);
    qhook("GetAddrInfoExW", (void *)h_GetAddrInfoExW, &o_GetAddrInfoExW);
    qhook("gethostbyname", (void *)h_gethostbyname, &o_gethostbyname);
    qhook("WSAConnectByNameA", (void *)h_WSAConnectByNameA, &o_WSAConnectByNameA);
    qhook("WSAConnectByNameW", (void *)h_WSAConnectByNameW, &o_WSAConnectByNameW);
    qhook("connect", (void *)h_connect, &o_connect);
    qhook("WSAConnect", (void *)h_WSAConnect, &o_WSAConnect);
    qhook("WSAIoctl", (void *)h_WSAIoctl, &o_WSAIoctl);
    if ((st = MH_ApplyQueued()) != MH_OK) LOG("netguard: MH_ApplyQueued failed %d", st);
    hook_winhttp(GetModuleHandleW(L"winhttp.dll"));
    hook_eos(GetModuleHandleW(L"EOSSDK-Win64-Shipping.dll"));
    LdrRegisterDllNotification_t reg =
        (LdrRegisterDllNotification_t)GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "LdrRegisterDllNotification");
    static PVOID cookie;
    if (!reg || reg(0, dll_notify, NULL, &cookie) != 0)
        LOG("netguard: LdrRegisterDllNotification unavailable; EOS falls back to DNS blocking only");
    LOG("netguard: armed, mode=%s, %d hooks, eos=%s, %d allowlist entr%s", mode_name(), g_hooks,
        g_eos_off ? "disable" : "leave", ncfg, ncfg == 1 ? "y" : "ies");
}

void netguard_allow_host(const char *addr) {
    char h[112];
    const char *s = addr, *e;
    if (*s == '[') { s++; e = strchr(s, ']'); }                       // [v6]:port
    else { e = strrchr(s, ':'); if (e && strchr(s, ':') != e) e = NULL; }  // host:port (bare v6 has several ':')
    size_t n = e ? (size_t)(e - s) : strlen(s);
    char tmp[112]; snprintf(tmp, sizeof tmp, "%.*s", (int)(n < sizeof tmp ? n : sizeof tmp - 1), s);
    lower_trim(h, sizeof h, tmp);
    if (!*h || static_rule(h)) return;
    EnterCriticalSection(&cs);
    dyn_add_locked(h);
    LeaveCriticalSection(&cs);
    LOG("netguard: runtime allow %s", h);
}

static void print_rows(Out *o, int verdict) {
    for (int i = 0; i < nseen; i++) {
        Seen *s = &seen[i];
        if (s->verdict != verdict) continue;
        out_printf(o, "  %-4s %-48s x%-5u via %-28s %02d:%02d:%02d  %s\n", s->kind, s->host, s->count, s->via,
                   s->first.wHour, s->first.wMinute, s->first.wSecond, s->why);
    }
}

void netguard_cmd(char *args, Out *o) {
    if (args && !strncmp(args, "allow ", 6) && args[6]) {
        netguard_allow_host(args + 6);
        out_printf(o, "allowed: %s\n", args + 6);
        return;
    }
    EnterCriticalSection(&cs);
    int nb = 0, nw = 0, na = 0;
    for (int i = 0; i < nseen; i++) { if (seen[i].verdict == V_BLOCK) nb++; else if (seen[i].verdict == V_WOULD) nw++; else na++; }
    out_printf(o, "netguard: mode=%s hooks=%d eos=%s (%d) blocked_calls=%u\n", mode_name(), g_hooks, g_eos_state,
               g_eos_result, g_blocked_total);
    out_printf(o, "allowlist:");
    for (int i = 0; i < ncfg; i++) out_printf(o, " %s", cfg_allow[i]);
    out_printf(o, "%s\nruntime:", ncfg ? "" : " -");
    for (int i = 0; i < ndyn; i++) out_printf(o, " %s", dyn_allow[i]);
    out_printf(o, "%s\n", ndyn ? "" : " -");
    out_printf(o, "blocked (%d):\n", nb); print_rows(o, V_BLOCK);
    if (nw) { out_printf(o, "would block, log-only mode (%d):\n", nw); print_rows(o, V_WOULD); }
    out_printf(o, "allowed (%d):\n", na); print_rows(o, V_ALLOW);
    if (seen_dropped) out_printf(o, "(%u events not recorded: table full)\n", seen_dropped);
    LeaveCriticalSection(&cs);
}
