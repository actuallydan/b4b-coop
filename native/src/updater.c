// updater: in-game updates from the ~ window's Updates tab (#34, docs/investigations/updater.md). Nothing is
// fetched or changed without the player's click, and nothing is hot-reloaded:
//   Check     GET <api>/repos/<repo>/releases/latest (or /releases/tags/v<ver>: the version of a host that refused
//             us), then that release's b4bcoop-update.txt + .sig: the manifest (version, protocol, zip name, size,
//             SHA-256) signed with the b4bcoop release key (ed25519, public key below, updcore.c checks it).
//   Download  "Download and install on next start": the zip + its .sig into memory; size, SHA-256 and signature are
//             checked before anything is written. Its files (upd_install_path: the two DLLs and b4bcoop-*.txt; never
//             b4bcoop.ini, bans, add-ons or logs) are staged in <game>\b4bcoop-update\staged\<ver>\ and swapped in
//             by renames: each running file moves to backup\<running ver>\ (Windows can rename a loaded DLL, not
//             overwrite it), the new one takes its place. This game keeps running the old code; the new version
//             loads at the next start.
//   Start     b4bcoop-update\state.txt: the new version counts its own starts from DllMain (updater_early); 15 s of
//             game ticks mark it healthy. A third start without that (it crashed twice) moves the backup back
//             (revert) and runs nothing this time; the next start is the previous version again. "Go back to <prev>"
//             in the tab does the same revert by hand.
// netguard lets GitHub's release hosts through only while a check or download runs (netguard_updater_scope).
// ini: updates=0 hides the tab. Dev builds only: update_api=<base URL> (e.g. a local test server), update_repo=,
// update_pubkey=<64 hex> (a test key), dev command `update status|check [ver]|install|goback`.
#include <windows.h>
#include <winhttp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include "log.h"
#include "cmds.h"
#include "netguard.h"
#include "overlay.h"
#include "updcore.h"
#include "b4bcoop_version.h"

// The b4bcoop release key (docs/release-signing.pub.pem; private half only in the release workflow's secret
// B4B_RELEASE_SIGNING_KEY). Rotating it: docs/investigations/updater.md "Key rotation".
static const uint8_t RELEASE_PUBKEY[32] = {
    0x3f, 0xac, 0xa0, 0x59, 0x9d, 0xda, 0xb6, 0x79, 0xf6, 0x01, 0xa5, 0x5b, 0xdd, 0xc9, 0xc4, 0x35,
    0x5a, 0xc6, 0x89, 0xd7, 0x56, 0x5b, 0x85, 0x1d, 0x73, 0x13, 0xa8, 0xe7, 0xaa, 0x78, 0x9a, 0x31};
#define DEFAULT_API "https://api.github.com"
#define DEFAULT_REPO "actuallydan/b4b-coop"
#define MANIFEST_NAME "b4bcoop-update.txt"
#define HEALTHY_SECONDS 15.f
#define MAX_STARTS 2          // starts of a fresh update that may end without reaching HEALTHY_SECONDS

static int enabled = 1;       // ini updates=0: no Updates tab
static int never_healthy;     // dev: update_never_healthy=1 (tests the automatic revert)
static char api[200] = DEFAULT_API, repo[100] = DEFAULT_REPO;
static uint8_t pubkey[32];
static int test_key;          // dev: update_pubkey= in use

// ---- paths ----
static wchar_t root_dir[MAX_PATH];   // game root (next to Back4Blood.exe), trailing backslash
static wchar_t upd_dir[MAX_PATH];    // <root>b4bcoop-update\  (trailing backslash)
static wchar_t module_path[MAX_PATH];
static const char *unavailable;      // why this install can't be updated in game (NULL = it can)

static void paths_init(HMODULE self) {
    static int done;
    if (done) return;
    done = 1;
    if (!GetModuleFileNameW(self, module_path, MAX_PATH)) { unavailable = "can't find the mod's own file"; return; }
    wchar_t *base = wcsrchr(module_path, L'\\');
    if (!base || _wcsicmp(base + 1, L"X3DAudio1_7.dll")) { unavailable = "this is a developer install (not X3DAudio1_7.dll)"; return; }
    static const wchar_t tail[] = L"\\Gobi\\Binaries\\Win64";
    size_t dl = base - module_path, tl = wcslen(tail);
    if (dl <= tl || _wcsnicmp(module_path + dl - tl, tail, tl)) { unavailable = "the mod is not in Gobi\\Binaries\\Win64 of the game folder"; return; }
    wcsncpy(root_dir, module_path, dl - tl + 1);
    root_dir[dl - tl + 1] = 0;
    _snwprintf(upd_dir, MAX_PATH, L"%lsb4bcoop-update\\", root_dir);
}

// rel "Gobi/Binaries/Win64/X3DAudio1_7.dll" under dir -> wide path
static void join_path(wchar_t *out, const wchar_t *dir, const char *rel) {
    wchar_t w[MAX_PATH];
    if (!MultiByteToWideChar(CP_UTF8, 0, rel, -1, w, MAX_PATH)) w[0] = 0;
    for (wchar_t *c = w; *c; c++) if (*c == L'/') *c = L'\\';
    _snwprintf(out, MAX_PATH * 2, L"%ls%ls", dir, w);
    out[MAX_PATH * 2 - 1] = 0;
}
static void mkdirs_for(const wchar_t *file) {   // every parent directory of file
    wchar_t p[MAX_PATH * 2];
    wcsncpy(p, file, MAX_PATH * 2 - 1); p[MAX_PATH * 2 - 1] = 0;
    for (wchar_t *c = p + 3; *c; c++)
        if (*c == L'\\') { *c = 0; CreateDirectoryW(p, NULL); *c = L'\\'; }
}
static int exists_w(const wchar_t *p) { return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES; }
// delete a tree under upd_dir (refuses anything else)
static void rmtree(const wchar_t *dir) {
    if (!upd_dir[0] || _wcsnicmp(dir, upd_dir, wcslen(upd_dir)) || wcsstr(dir, L"..")) return;
    wchar_t pat[MAX_PATH * 2]; WIN32_FIND_DATAW fd;
    _snwprintf(pat, MAX_PATH * 2, L"%ls\\*", dir);
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
            wchar_t p[MAX_PATH * 2];
            _snwprintf(p, MAX_PATH * 2, L"%ls\\%ls", dir, fd.cFileName);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) rmtree(p);
            else { SetFileAttributesW(p, FILE_ATTRIBUTE_NORMAL); DeleteFileW(p); }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryW(dir);
}
static int write_file_w(const wchar_t *p, const void *data, size_t n) {
    mkdirs_for(p);
    HANDLE h = CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    DWORD w = 0;
    BOOL ok = WriteFile(h, data, (DWORD)n, &w, NULL) && w == n;
    ok = FlushFileBuffers(h) && ok;
    CloseHandle(h);
    return ok;
}
static int move_w(const wchar_t *from, const wchar_t *to) {
    mkdirs_for(to);
    return MoveFileExW(from, to, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}

// ---- state.txt ----
typedef struct { char installed[40], previous[40], files[600], result[200]; int pending, starts; } State;
static State st;
static CRITICAL_SECTION st_cs;
static int st_cs_ready;

static void state_read(State *s) {
    memset(s, 0, sizeof *s);
    wchar_t p[MAX_PATH * 2]; _snwprintf(p, MAX_PATH * 2, L"%lsstate.txt", upd_dir);
    HANDLE h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    char buf[2048]; DWORD n = 0;
    ReadFile(h, buf, sizeof buf - 1, &n, NULL);
    CloseHandle(h);
    buf[n] = 0;
    for (char *line = strtok(buf, "\r\n"); line; line = strtok(NULL, "\r\n")) {
        char *v = strchr(line, '=');
        if (!v) continue;
        *v++ = 0;
        if (!strcmp(line, "installed")) snprintf(s->installed, sizeof s->installed, "%s", v);
        else if (!strcmp(line, "previous")) snprintf(s->previous, sizeof s->previous, "%s", v);
        else if (!strcmp(line, "files")) snprintf(s->files, sizeof s->files, "%s", v);
        else if (!strcmp(line, "result")) snprintf(s->result, sizeof s->result, "%s", v);
        else if (!strcmp(line, "pending")) s->pending = atoi(v);
        else if (!strcmp(line, "starts")) s->starts = atoi(v);
    }
    if (!upd_version_valid(s->installed)) s->installed[0] = 0;
    if (!upd_version_valid(s->previous)) s->previous[0] = 0;
}
static int state_write(const State *s) {
    char buf[2048];
    int n = snprintf(buf, sizeof buf, "; b4bcoop in-game updater (docs: b4bcoop-COMMANDS.txt, \"Updates\"). Safe to delete.\r\n"
                     "installed=%s\r\nprevious=%s\r\npending=%d\r\nstarts=%d\r\nfiles=%s\r\nresult=%s\r\n",
                     s->installed, s->previous, s->pending, s->starts, s->files, s->result);
    wchar_t p[MAX_PATH * 2], t[MAX_PATH * 2];
    _snwprintf(p, MAX_PATH * 2, L"%lsstate.txt", upd_dir);
    _snwprintf(t, MAX_PATH * 2, L"%lsstate.txt.tmp", upd_dir);
    return write_file_w(t, buf, n) && MoveFileExW(t, p, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}

// Put backup\<previous>\ back and move the files the update installed to failed\<installed>\. Works while the new
// DLL runs (renaming a loaded DLL is allowed). Caller holds nothing; called from DllMain or the game thread.
static int revert(State *s, const char *why, char *err, size_t en) {
    if (!s->previous[0]) { snprintf(err, en, "no previous version kept"); return 0; }
    wchar_t bdir[MAX_PATH], fdir[MAX_PATH];
    wchar_t wp[40], wi[40];
    MultiByteToWideChar(CP_UTF8, 0, s->previous, -1, wp, 40);
    MultiByteToWideChar(CP_UTF8, 0, s->installed[0] ? s->installed : "unknown", -1, wi, 40);
    _snwprintf(bdir, MAX_PATH, L"%lsbackup\\%ls\\", upd_dir, wp);
    _snwprintf(fdir, MAX_PATH, L"%lsfailed\\%ls\\", upd_dir, wi);
    if (!exists_w(bdir)) { snprintf(err, en, "the backup of %s is missing (b4bcoop-update\\backup)", s->previous); return 0; }
    int bad = 0;
    char files[600]; snprintf(files, sizeof files, "%s", s->files);
    for (char *rel = strtok(files, "|"); rel; rel = strtok(NULL, "|")) {
        if (!upd_install_path(rel)) continue;
        wchar_t live[MAX_PATH * 2], to[MAX_PATH * 2];
        join_path(live, root_dir, rel); join_path(to, fdir, rel);
        if (exists_w(live) && !move_w(live, to)) { LOG("update: revert: can't move %s aside (%lu)", rel, GetLastError()); bad++; }
    }
    // what the install moved to the backup goes back (the same list: only files that existed then are there)
    snprintf(files, sizeof files, "%s", s->files);
    for (char *rel = strtok(files, "|"); rel; rel = strtok(NULL, "|")) {
        wchar_t from[MAX_PATH * 2], live[MAX_PATH * 2];
        join_path(from, bdir, rel); join_path(live, root_dir, rel);
        if (upd_install_path(rel) && exists_w(from) && !move_w(from, live)) { LOG("update: revert: can't restore %s (%lu)", rel, GetLastError()); bad++; }
    }
    LOG("update: reverted %s -> %s (%s)%s", s->installed, s->previous, why, bad ? ", with errors" : "");
    State n; memset(&n, 0, sizeof n);
    snprintf(n.installed, sizeof n.installed, "%s", s->previous);
    snprintf(n.result, sizeof n.result, "%s", why);
    *s = n;
    state_write(s);
    if (bad) { snprintf(err, en, "%d file(s) could not be moved back; see the log", bad); return 0; }
    return 1;
}

// DllMain, right after the log opened (loader lock: kernel32 file calls only). 1 = the fresh update failed to
// start twice and was reverted: run nothing this time.
int updater_early(void *module) {
    paths_init((HMODULE)module);
    if (unavailable) return 0;
    State s; state_read(&s);
    if (!s.pending) return 0;
    if (strcmp(s.installed, B4B_VERSION)) {   // files replaced by hand since (a zip extracted over it)
        LOG("update: state says %s was installed, this is %s: forgetting the pending update", s.installed, B4B_VERSION);
        s.pending = 0; s.starts = 0;
        state_write(&s);
        return 0;
    }
    s.starts++;
    if (s.starts > MAX_STARTS) {
        char why[200], err[200];
        snprintf(why, sizeof why, "%s did not start properly %d times, so %s was put back", B4B_VERSION, MAX_STARTS, s.previous);
        if (revert(&s, why, err, sizeof err)) {
            LOG("update: %s; running nothing this time, restart the game", why);
            return 1;
        }
        LOG("update: revert failed: %s", err);
        return 0;
    }
    LOG("update: start %d of the freshly installed %s (healthy after %.0f s of play)", s.starts, B4B_VERSION, HEALTHY_SECONDS);
    state_write(&s);
    return 0;
}

// ---- HTTP (WinHTTP, loaded on the first request) ----
#define WH(f) static __typeof__(f) *p##f
WH(WinHttpOpen); WH(WinHttpConnect); WH(WinHttpOpenRequest); WH(WinHttpSendRequest); WH(WinHttpReceiveResponse);
WH(WinHttpQueryHeaders); WH(WinHttpReadData); WH(WinHttpCloseHandle); WH(WinHttpSetTimeouts); WH(WinHttpCrackUrl);
static int winhttp_load(char *err, size_t en) {
    static int ok;
    if (ok) return 1;
    HMODULE m = LoadLibraryW(L"winhttp.dll");   // netguard hooks WinHttpConnect as it maps
    if (!m) { snprintf(err, en, "winhttp.dll not available (%lu)", GetLastError()); return 0; }
#define LD(f) if (!(p##f = (__typeof__(f) *)(void *)GetProcAddress(m, #f))) { snprintf(err, en, "winhttp.dll has no " #f); return 0; }
    LD(WinHttpOpen); LD(WinHttpConnect); LD(WinHttpOpenRequest); LD(WinHttpSendRequest); LD(WinHttpReceiveResponse);
    LD(WinHttpQueryHeaders); LD(WinHttpReadData); LD(WinHttpCloseHandle); LD(WinHttpSetTimeouts); LD(WinHttpCrackUrl);
    ok = 1;
    return 1;
}

static volatile LONG64 dl_got;   // bytes of the running download (progress)

// GET url into a malloc'd buffer (max bytes). 1 = HTTP 200 and the body read.
static int http_get(const char *url, const char *accept, size_t max, uint8_t **out, size_t *outn, char *err, size_t en) {
    *out = NULL; *outn = 0;
    if (!winhttp_load(err, en)) return 0;
    wchar_t wurl[1024], host[256], path[1024];
    if (!MultiByteToWideChar(CP_UTF8, 0, url, -1, wurl, 1024)) { snprintf(err, en, "bad URL"); return 0; }
    URL_COMPONENTS u; memset(&u, 0, sizeof u);
    u.dwStructSize = sizeof u;
    u.lpszHostName = host; u.dwHostNameLength = 256; u.lpszUrlPath = path; u.dwUrlPathLength = 1024;
    wchar_t extra[512]; u.lpszExtraInfo = extra; u.dwExtraInfoLength = 512;
    if (!pWinHttpCrackUrl(wurl, 0, 0, &u)) { snprintf(err, en, "bad URL %s", url); return 0; }
    int https = u.nScheme == INTERNET_SCHEME_HTTPS;
    if (!https) {
#ifdef B4B_RELEASE
        snprintf(err, en, "refusing a non-HTTPS URL"); return 0;
#else
        if (_wcsicmp(host, L"127.0.0.1") && _wcsicmp(host, L"localhost")) { snprintf(err, en, "plain HTTP only to 127.0.0.1 (dev test server)"); return 0; }
#endif
    }
    wcsncat(path, extra, 1023 - wcslen(path));
    wchar_t ua[64]; _snwprintf(ua, 64, L"b4bcoop-updater/%hs", B4B_VERSION);
    HINTERNET s = pWinHttpOpen(ua, WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    HINTERNET c = NULL, r = NULL;
    int ok = 0;
    uint8_t *buf = NULL; size_t n = 0, cap = 0;
    if (!s) { snprintf(err, en, "WinHttpOpen failed (%lu)", GetLastError()); goto done; }
    pWinHttpSetTimeouts(s, 15000, 15000, 20000, 30000);
    c = pWinHttpConnect(s, host, u.nPort, 0);
    if (!c) { DWORD e = GetLastError(); snprintf(err, en, e == 12007 ? "can't reach %ls (no internet, or blocked)" : "can't connect to %ls (%lu)", host, e); goto done; }
    r = pWinHttpOpenRequest(c, L"GET", path, NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, https ? WINHTTP_FLAG_SECURE : 0);
    wchar_t hdr[256]; _snwprintf(hdr, 256, L"Accept: %hs\r\nX-GitHub-Api-Version: 2022-11-28\r\n", accept);
    if (!r || !pWinHttpSendRequest(r, hdr, (DWORD)-1, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) || !pWinHttpReceiveResponse(r, NULL)) {
        DWORD e = GetLastError();
        const char *what = e == 12002 ? "timed out" : e == 12007 ? "name not resolved (no internet, or blocked)" :
                           e == 12029 || e == 12030 ? "connection failed" : (e >= 12037 && e <= 12045) || e == 12175 ? "secure connection (TLS/certificate) failed" : "failed";
        snprintf(err, en, "request to %ls %s (%lu)", host, what, e);
        goto done;
    }
    DWORD status = 0, sl = sizeof status;
    pWinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &status, &sl, WINHTTP_NO_HEADER_INDEX);
    if (status != 200) {
        wchar_t rem[32] = L"", reset[32] = L""; DWORD l1 = sizeof rem, l2 = sizeof reset;
        pWinHttpQueryHeaders(r, WINHTTP_QUERY_CUSTOM, L"x-ratelimit-remaining", rem, &l1, WINHTTP_NO_HEADER_INDEX);
        pWinHttpQueryHeaders(r, WINHTTP_QUERY_CUSTOM, L"x-ratelimit-reset", reset, &l2, WINHTTP_NO_HEADER_INDEX);
        if ((status == 403 || status == 429) && !wcscmp(rem, L"0")) {
            time_t t = (time_t)_wtoi64(reset);
            struct tm *lt = localtime(&t);
            if (lt) snprintf(err, en, "GitHub's limit for update checks from your internet address is used up (60 per hour); try again after %02d:%02d", lt->tm_hour, lt->tm_min);
            else snprintf(err, en, "GitHub's limit for update checks from your internet address is used up (60 per hour); try again later");
        } else if (status == 404) snprintf(err, en, "not found on GitHub (404)");
        else snprintf(err, en, "GitHub answered HTTP %lu", status);
        goto done;
    }
    for (;;) {
        if (n + 65536 + 1 > cap) {
            size_t nc = cap ? cap * 2 : 131072;
            while (nc < n + 65536 + 1) nc *= 2;
            uint8_t *nb = realloc(buf, nc);
            if (!nb) { snprintf(err, en, "out of memory"); goto done; }
            buf = nb; cap = nc;
        }
        DWORD got = 0;
        if (!pWinHttpReadData(r, buf + n, 65536, &got)) { snprintf(err, en, "download interrupted (%lu)", GetLastError()); goto done; }
        if (!got) break;
        n += got;
        InterlockedExchange64(&dl_got, (LONG64)n);
        if (n > max) { snprintf(err, en, "download larger than expected (over %zu bytes)", max); goto done; }
    }
    buf[n] = 0;   // text replies are NUL-terminated
    *out = buf; *outn = n; buf = NULL;
    ok = 1;
done:
    free(buf);
    if (r) pWinHttpCloseHandle(r);
    if (c) pWinHttpCloseHandle(c);
    if (s) pWinHttpCloseHandle(s);
    return ok;
}

// ---- the update state machine (worker thread + game-thread UI) ----
enum { P_IDLE, P_CHECKING, P_CHECKED, P_DOWNLOADING, P_INSTALLED, P_ERROR };
static struct {
    int phase;
    char msg[400];              // error or status text
    char target[40];            // the version being checked ("" = latest)
    UpdRelease rel;             // last check
    int have_manifest;          // rel has a verified manifest (m)
    UpdManifest m;
    char zip_url[600], sig_url[600];
    char host_ver[40], host_proto[16];   // a host with another version refused us
} U;
static CRITICAL_SECTION u_cs;
static HANDLE worker;
static int installed_this_session;   // one install/go-back per game session (the running files moved)

static int u_ready;
static void set_phase(int phase, const char *fmt, ...) {
    char m[400];
    va_list ap; va_start(ap, fmt); vsnprintf(m, sizeof m, fmt, ap); va_end(ap);
    EnterCriticalSection(&u_cs);
    U.phase = phase;
    snprintf(U.msg, sizeof U.msg, "%s", m);
    LeaveCriticalSection(&u_cs);
    LOG("update: %s", m);
}

static void scope_host(char *h, size_t n) {   // dev: the update_api host, for netguard's scope
    h[0] = 0;
    const char *p = strstr(api, "://");
    if (!p) return;
    p += 3;
    size_t k = strcspn(p, ":/");
    snprintf(h, n, "%.*s", (int)(k < n ? k : n - 1), p);
}

static DWORD WINAPI check_worker(void *arg) {
    (void)arg;
    char target[40], url[400], err[300], extra[112];
    EnterCriticalSection(&u_cs); snprintf(target, sizeof target, "%s", U.target); LeaveCriticalSection(&u_cs);
    if (target[0]) snprintf(url, sizeof url, "%s/repos/%s/releases/tags/v%s", api, repo, target);
    else snprintf(url, sizeof url, "%s/repos/%s/releases/latest", api, repo);
    scope_host(extra, sizeof extra);
    netguard_updater_scope(1, extra);
    uint8_t *json = NULL, *man = NULL, *sig = NULL; size_t jn, mn, sn;
    UpdRelease *rel = calloc(1, sizeof *rel);
    UpdManifest m; memset(&m, 0, sizeof m);
    char zip_url[600] = "", sig_url[600] = "";
    LOG("update: checking %s", url);
    if (!rel) { set_phase(P_ERROR, "Out of memory."); goto out; }
    if (!http_get(url, "application/vnd.github+json", 2u << 20, &json, &jn, err, sizeof err)) {
        if (target[0] && strstr(err, "404")) set_phase(P_ERROR, "There is no b4bcoop %s release on GitHub.", target);
        else set_phase(P_ERROR, "Check failed: %s.", err);
        goto out;
    }
    if (!upd_release_parse((char *)json, jn, rel, err, sizeof err)) { set_phase(P_ERROR, "Check failed: %s.", err); goto out; }
    const UpdAsset *am = upd_release_asset(rel, MANIFEST_NAME), *as = upd_release_asset(rel, MANIFEST_NAME ".sig");
    if (!am || !as) {   // a release from before the updater (<= 0.6.1)
        EnterCriticalSection(&u_cs); U.rel = *rel; U.have_manifest = 0; LeaveCriticalSection(&u_cs);
        set_phase(P_CHECKED, "Release %s has no in-game update files (it is older than the updater). Get it from GitHub: %s", rel->tag, rel->page);
        goto out;
    }
    if (!http_get(am->url, "application/octet-stream", 16384, &man, &mn, err, sizeof err) ||
        !http_get(as->url, "application/octet-stream", 1024, &sig, &sn, err, sizeof err)) {
        set_phase(P_ERROR, "Check failed: %s.", err); goto out;
    }
    if (!upd_manifest_verify((char *)man, mn, sig, sn, pubkey, &m, err, sizeof err)) { set_phase(P_ERROR, "Not safe to install: %s.", err); goto out; }
    const char *tagv = rel->tag[0] == 'v' ? rel->tag + 1 : rel->tag;
    if (strcmp(tagv, m.version)) { set_phase(P_ERROR, "Not safe to install: release %s carries update files for %s.", rel->tag, m.version); goto out; }
    const UpdAsset *az = upd_release_asset(rel, m.zip);
    char zsig[120]; snprintf(zsig, sizeof zsig, "%s.sig", m.zip);
    const UpdAsset *azs = upd_release_asset(rel, zsig);
    if (!az || !azs) { set_phase(P_ERROR, "Release %s is missing %s or its signature.", rel->tag, m.zip); goto out; }
    snprintf(zip_url, sizeof zip_url, "%s", az->url);
    snprintf(sig_url, sizeof sig_url, "%s", azs->url);
    EnterCriticalSection(&u_cs);
    U.rel = *rel; U.m = m; U.have_manifest = 1;
    snprintf(U.zip_url, sizeof U.zip_url, "%s", zip_url); snprintf(U.sig_url, sizeof U.sig_url, "%s", sig_url);
    LeaveCriticalSection(&u_cs);
    int c = upd_version_cmp(m.version, B4B_VERSION);
    if (c == 0) set_phase(P_CHECKED, "You have b4bcoop %s already.", m.version);
    else set_phase(P_CHECKED, c > 0 ? "b4bcoop %s is available (protocol %d)." : "b4bcoop %s (protocol %d) is older than yours.", m.version, m.protocol);
out:
    netguard_updater_scope(0, NULL);
    free(json); free(man); free(sig); free(rel);
    return 0;
}

// Staging: each installable zip entry -> staged\<ver>\<rel>; the list of rels in files
typedef struct { wchar_t dir[MAX_PATH]; char files[600]; int n, agent; char err[200]; } Stage;
static int stage_cb(const char *name, const uint8_t *data, size_t n, void *ctx) {
    Stage *s = ctx;
    if (!upd_install_path(name)) { LOG("update: zip: skipping %s (never replaced by the updater)", name); return 0; }
    wchar_t p[MAX_PATH * 2];
    join_path(p, s->dir, name);
    if (!write_file_w(p, data, n)) { snprintf(s->err, sizeof s->err, "can't write %s (%lu)", name, GetLastError()); return 1; }
    if (strlen(s->files) + strlen(name) + 2 >= sizeof s->files) { snprintf(s->err, sizeof s->err, "too many files"); return 1; }
    if (s->files[0]) strcat(s->files, "|");
    strcat(s->files, name);
    s->n++;
    if (!strcmp(name, "Gobi/Binaries/Win64/X3DAudio1_7.dll")) s->agent = 1;
    return 0;
}

// staged files -> live, running files -> backup\<running version>\ (renames). 1 = done.
static int swap_in(const Stage *sg, const char *newver, char *err, size_t en) {
    wchar_t bdir[MAX_PATH], wv[40];
    MultiByteToWideChar(CP_UTF8, 0, B4B_VERSION, -1, wv, 40);
    wchar_t b[MAX_PATH];
    _snwprintf(b, MAX_PATH, L"%lsbackup", upd_dir); rmtree(b);    // only the newest backup is kept
    _snwprintf(b, MAX_PATH, L"%lsfailed", upd_dir); rmtree(b);
    _snwprintf(bdir, MAX_PATH, L"%lsbackup\\%ls\\", upd_dir, wv);
    char list[600]; snprintf(list, sizeof list, "%s", sg->files);
    char *rels[16]; int n = 0;
    for (char *r = strtok(list, "|"); r && n < 16; r = strtok(NULL, "|")) rels[n++] = r;
    int old_moved[16] = {0}, new_moved[16] = {0}, i;
    for (i = 0; i < n; i++) {
        wchar_t live[MAX_PATH * 2], bk[MAX_PATH * 2];
        join_path(live, root_dir, rels[i]); join_path(bk, bdir, rels[i]);
        if (!exists_w(live)) continue;
        if (!move_w(live, bk)) { snprintf(err, en, "can't move %s to the backup (Windows error %lu)", rels[i], GetLastError()); goto undo; }
        old_moved[i] = 1;
    }
    for (i = 0; i < n; i++) {
        wchar_t live[MAX_PATH * 2], from[MAX_PATH * 2];
        join_path(live, root_dir, rels[i]); join_path(from, sg->dir, rels[i]);
        if (!move_w(from, live)) { snprintf(err, en, "can't put the new %s in place (Windows error %lu)", rels[i], GetLastError()); goto undo; }
        new_moved[i] = 1;
    }
    State s; memset(&s, 0, sizeof s);
    snprintf(s.installed, sizeof s.installed, "%s", newver);
    snprintf(s.previous, sizeof s.previous, "%s", B4B_VERSION);
    snprintf(s.files, sizeof s.files, "%s", sg->files);
    snprintf(s.result, sizeof s.result, "installed %s over %s", newver, B4B_VERSION);
    s.pending = 1;
    if (!state_write(&s)) LOG("update: warning: can't write state.txt (%lu); the start check won't run", GetLastError());
    EnterCriticalSection(&st_cs); st = s; LeaveCriticalSection(&st_cs);
    return 1;
undo:
    LOG("update: %s; undoing", err);
    for (i = n - 1; i >= 0; i--) {
        wchar_t live[MAX_PATH * 2], p[MAX_PATH * 2];
        join_path(live, root_dir, rels[i]);
        if (new_moved[i]) { join_path(p, sg->dir, rels[i]); move_w(live, p); }
        if (old_moved[i]) { join_path(p, bdir, rels[i]); if (!move_w(p, live)) LOG("update: undo: can't restore %s (%lu)", rels[i], GetLastError()); }
    }
    return 0;
}

static DWORD WINAPI download_worker(void *arg) {
    (void)arg;
    UpdManifest m; char zip_url[600], sig_url[600], err[300], extra[112];
    EnterCriticalSection(&u_cs);
    m = U.m; snprintf(zip_url, sizeof zip_url, "%s", U.zip_url); snprintf(sig_url, sizeof sig_url, "%s", U.sig_url);
    LeaveCriticalSection(&u_cs);
    scope_host(extra, sizeof extra);
    netguard_updater_scope(1, extra);
    uint8_t *zip = NULL, *sig = NULL; size_t zn = 0, sn = 0;
    InterlockedExchange64(&dl_got, 0);
    LOG("update: downloading %s (%llu bytes)", zip_url, (unsigned long long)m.size);
    if (!http_get(zip_url, "application/octet-stream", (size_t)m.size, &zip, &zn, err, sizeof err) ||
        !http_get(sig_url, "application/octet-stream", 1024, &sig, &sn, err, sizeof err)) {
        netguard_updater_scope(0, NULL);
        set_phase(P_ERROR, "Download failed: %s. Nothing was changed.", err);
        goto out;
    }
    netguard_updater_scope(0, NULL);
    if (!upd_zip_verify(zip, zn, sig, sn, &m, pubkey, err, sizeof err)) { set_phase(P_ERROR, "Not installed: %s. Nothing was changed.", err); goto out; }
    LOG("update: %s verified (size, SHA-256, signature)", m.zip);
    Stage sg; memset(&sg, 0, sizeof sg);
    wchar_t wv[40]; MultiByteToWideChar(CP_UTF8, 0, m.version, -1, wv, 40);
    _snwprintf(sg.dir, MAX_PATH, L"%lsstaged\\%ls\\", upd_dir, wv);
    wchar_t sd[MAX_PATH]; _snwprintf(sd, MAX_PATH, L"%lsstaged", upd_dir); rmtree(sd);
    CreateDirectoryW(upd_dir, NULL);
    int r = upd_zip_each(zip, zn, stage_cb, &sg, err, sizeof err);
    if (r < 0 || r > 0 || !sg.agent) {
        rmtree(sd);
        set_phase(P_ERROR, "Not installed: %s. Nothing was changed.", r < 0 ? err : r > 0 ? sg.err : "the zip has no X3DAudio1_7.dll");
        goto out;
    }
    LOG("update: staged %d files: %s", sg.n, sg.files);
    if (!swap_in(&sg, m.version, err, sizeof err)) {
        rmtree(sd);
        set_phase(P_ERROR, "Not installed: %s. Your current version is unchanged.", err);
        goto out;
    }
    rmtree(sd);
    installed_this_session = 1;
    set_phase(P_INSTALLED, "b4bcoop %s is installed. Restart the game to finish (this game keeps running %s).", m.version, B4B_VERSION);
    overlay_note("[update] installed; restart the game to finish");
out:
    free(zip); free(sig);
    return 0;
}

static int busy(void) { return worker && WaitForSingleObject(worker, 0) == WAIT_TIMEOUT; }
static void start(LPTHREAD_START_ROUTINE fn) {
    if (worker) { CloseHandle(worker); worker = NULL; }
    worker = CreateThread(NULL, 0, fn, NULL, 0, NULL);
}
static const char *check(const char *ver) {   // NULL = started
    if (unavailable) return unavailable;
    if (busy()) return "busy";
    EnterCriticalSection(&u_cs);
    snprintf(U.target, sizeof U.target, "%s", ver && upd_version_valid(ver) ? ver : "");
    U.have_manifest = 0;
    LeaveCriticalSection(&u_cs);
    set_phase(P_CHECKING, ver ? "Checking GitHub for b4bcoop %s..." : "Checking GitHub for the latest b4bcoop...", ver);
    start(check_worker);
    return NULL;
}
static const char *install(void) {
    if (unavailable) return unavailable;
    if (busy()) return "busy";
    if (installed_this_session) return "already changed this session: restart the game first";
    EnterCriticalSection(&u_cs);
    int ok = U.phase == P_CHECKED && U.have_manifest && strcmp(U.m.version, B4B_VERSION);
    LeaveCriticalSection(&u_cs);
    if (!ok) return "check first";
    set_phase(P_DOWNLOADING, "Downloading b4bcoop %s...", U.m.version);
    start(download_worker);
    return NULL;
}
static const char *go_back(void) {
    if (unavailable) return unavailable;
    if (busy()) return "busy";
    if (installed_this_session) return "already changed this session: restart the game first";
    State s; state_read(&s);
    if (!s.previous[0] || strcmp(s.installed, B4B_VERSION)) return "no previous version kept";
    char why[200], err[200];
    snprintf(why, sizeof why, "you went back from %s to %s", B4B_VERSION, s.previous);
    if (!revert(&s, why, err, sizeof err)) { set_phase(P_ERROR, "Going back failed: %s.", err); return NULL; }
    EnterCriticalSection(&st_cs); st = s; LeaveCriticalSection(&st_cs);
    installed_this_session = 1;
    set_phase(P_INSTALLED, "b4bcoop %s is back in place. Restart the game to finish.", s.installed);
    return NULL;
}

// ---- host version hints (a host with another protocol refused us) ----
void updater_note_host(const char *ver, const char *proto) {
    if (!ver || !upd_version_valid(ver) || !u_ready) return;
    EnterCriticalSection(&u_cs);
    snprintf(U.host_ver, sizeof U.host_ver, "%s", ver);
    snprintf(U.host_proto, sizeof U.host_proto, "%s", proto ? proto : "");
    LeaveCriticalSection(&u_cs);
    LOG("update: noted the host's version %s (protocol %s)", ver, proto ? proto : "?");
}
const char *updater_hint(void) {
    return enabled && !unavailable ? " Press ~, tab Updates, to get the same version." : "";
}

// ---- ~ window ----
static void notes_short(const char *body, char *out, size_t n) {   // up to the provenance part, ~12 lines
    size_t k = 0; int lines = 0;
    for (const char *p = body; *p && k + 1 < n; p++) {
        if (!strncmp(p, "\n---", 4)) break;
        if (*p == '\r') continue;
        if (*p == '\n' && ++lines >= 12) { snprintf(out + k, n - k, "\n..."); return; }
        out[k++] = *p;
    }
    while (k && (out[k - 1] == '\n' || out[k - 1] == ' ')) k--;
    out[k] = 0;
}
static void panel(void) {
    EnterCriticalSection(&u_cs);
    int phase = U.phase, have = U.have_manifest;
    char msg[400], host_ver[40], host_proto[16], page[300], notes[1200], title[120];
    UpdManifest m = U.m;
    snprintf(msg, sizeof msg, "%s", U.msg);
    snprintf(host_ver, sizeof host_ver, "%s", U.host_ver); snprintf(host_proto, sizeof host_proto, "%s", U.host_proto);
    snprintf(page, sizeof page, "%s", U.rel.page); snprintf(title, sizeof title, "%s", U.rel.title[0] ? U.rel.title : U.rel.tag);
    notes_short(U.rel.body, notes, sizeof notes);
    LeaveCriticalSection(&u_cs);
    EnterCriticalSection(&st_cs); State s = st; LeaveCriticalSection(&st_cs);
    int running = busy();

    ov_text("Installed: b4bcoop %s (protocol %d)", B4B_VERSION, B4B_PROTOCOL);
    if (test_key) ov_text_warn("Dev build: update_pubkey= test key and %s", api);
    if (unavailable) { ov_text_warn("In-game updates are not available here: %s.", unavailable); return; }
    ov_text_dim("Nothing is downloaded or changed until you click. Releases come from GitHub and must carry the b4bcoop "
                "release signature. b4bcoop.ini, add-ons, bans and logs are never touched. A new version starts with "
                "your next game start.");
    if (s.result[0] && strcmp(s.installed, B4B_VERSION) == 0 && strstr(s.result, "put back"))
        ov_text_warn("Last update: %s.", s.result);
    else if (s.result[0] && strstr(s.result, "went back"))
        ov_text_dim("Last change: %s.", s.result);

    if (host_ver[0] && strcmp(host_ver, B4B_VERSION)) {
        ov_heading("The host's version");
        ov_text("A host you tried to join runs b4bcoop %s%s%s%s.", host_ver, host_proto[0] ? " (protocol " : "", host_proto, host_proto[0] ? ")" : "");
        ov_begin_disabled(running || installed_this_session, running ? "busy" : "restart the game first");
        char lab[80]; snprintf(lab, sizeof lab, "Check %s##hostver", host_ver);
        if (ov_button(lab)) check(host_ver);
        ov_end_disabled();
    }

    ov_heading("Updates");
    ov_begin_disabled(running || installed_this_session, running ? "busy" : "restart the game first");
    if (ov_button("Check for updates")) check(NULL);
    ov_end_disabled();
    if (phase == P_CHECKING) ov_text_dim("%s", msg);
    else if (phase == P_DOWNLOADING) {
        LONG64 got = dl_got;
        ov_text_dim("Downloading b4bcoop %s: %lld of %llu KB...", m.version, (long long)got / 1024, (unsigned long long)m.size / 1024);
    } else if (phase == P_ERROR) ov_text_warn("%s", msg);
    else if (phase == P_INSTALLED) ov_text_warn("%s", msg);
    else if (phase == P_CHECKED) {
        if (!have) ov_text_warn("%s", msg);
        else {
            int c = upd_version_cmp(m.version, B4B_VERSION);
            if (c == 0) ov_text("You have b4bcoop %s. Nothing to update.", m.version);
            else {
                ov_text("%s b4bcoop %s (protocol %d)", c > 0 ? "New:" : "Older:", m.version, m.protocol);
                if (m.protocol == B4B_PROTOCOL) ov_text_dim("Same protocol as yours: you can still play with friends who haven't changed version.");
                else ov_text_warn("Protocol %d (yours: %d): afterwards you can only play with friends on protocol %d, and they only with you once they have it too.",
                                  m.protocol, B4B_PROTOCOL, m.protocol);
                if (notes[0]) { ov_text_dim("%s", title); ov_text_dim("%s", notes); }
                if (page[0]) { if (ov_button("Copy the release page link")) ov_copy(page); }
                ov_begin_disabled(running || installed_this_session, running ? "busy" : "restart the game first");
                if (ov_button(c > 0 ? "Download and install on next start" : "Download and install this older version on next start##older")) install();
                ov_end_disabled();
                ov_text_dim("%.1f MB. The files you have now are kept in b4bcoop-update\\backup (\"Go back\" below).", m.size / 1048576.0);
            }
        }
    }
    if (s.previous[0] && !strcmp(s.installed, B4B_VERSION)) {
        ov_heading("Previous version");
        ov_text_dim("b4bcoop %s is kept in b4bcoop-update\\backup.%s", s.previous, s.pending ? " (If this version doesn't start "
                    "properly twice, it is put back automatically.)" : "");
        char lab[80]; snprintf(lab, sizeof lab, "Go back to %s on next start##goback", s.previous);
        ov_begin_disabled(running || installed_this_session, running ? "busy" : "restart the game first");
        if (ov_button_confirm(lab, "Click again to go back")) { const char *e = go_back(); if (e) overlay_note(e); }
        ov_end_disabled();
    }
}

// ---- init / tick / ini ----
static void ini_pair(const char *k, const char *v, void *ctx);
int updater_live(const char *k, const char *v) {
    if (!strcmp(k, "updates")) {
        enabled = !v || atoi(v) != 0;
        overlay_add_panel("Updates", 95, enabled ? panel : NULL);
        return 1;
    }
#ifndef B4B_RELEASE
    if (!strcmp(k, "update_api") || !strcmp(k, "update_repo") || !strcmp(k, "update_pubkey")) {
        if (busy()) { LOG("update: %s changes after the running request", k); }
        if (v) ini_pair(k, v, NULL);
        else if (!strcmp(k, "update_api")) snprintf(api, sizeof api, "%s", DEFAULT_API);
        else if (!strcmp(k, "update_repo")) snprintf(repo, sizeof repo, "%s", DEFAULT_REPO);
        else { memcpy(pubkey, RELEASE_PUBKEY, 32); test_key = 0; }
        LOG("update: api %s repo %s key %s", api, repo, test_key ? "test" : "release");
        return 1;
    }
#endif
    return 0;
}
static void ini_pair(const char *k, const char *v, void *ctx) {
    (void)ctx;
    if (!strcmp(k, "updates")) enabled = atoi(v) != 0;
#ifndef B4B_RELEASE
    else if (!strcmp(k, "update_api") && *v) { snprintf(api, sizeof api, "%s", v); size_t l = strlen(api); while (l && api[l - 1] == '/') api[--l] = 0; }
    else if (!strcmp(k, "update_repo") && *v) snprintf(repo, sizeof repo, "%s", v);
    else if (!strcmp(k, "update_never_healthy")) never_healthy = atoi(v) != 0;
    else if (!strcmp(k, "update_pubkey")) { if (upd_hex_decode(v, pubkey, 32)) test_key = 1; else LOG("update: bad update_pubkey (64 hex digits)"); }
#endif
}
void updater_init(void) {
    InitializeCriticalSection(&u_cs); u_ready = 1;
    InitializeCriticalSection(&st_cs); st_cs_ready = 1;
    memcpy(pubkey, RELEASE_PUBKEY, 32);
    HMODULE self = NULL;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCWSTR)(void *)updater_init, &self);
    paths_init(self);
    cmds_ini_each(ini_pair, NULL);
    if (!unavailable) state_read(&st);
    if (enabled) overlay_add_panel("Updates", 95, panel);
    LOG("update: %s%s%s; api %s%s; state: installed=%s previous=%s pending=%d starts=%d", enabled ? "Updates tab on" : "off (updates=0)",
        unavailable ? ", unavailable: " : "", unavailable ? unavailable : "", api, test_key ? " (test key)" : "",
        st.installed[0] ? st.installed : "-", st.previous[0] ? st.previous : "-", st.pending, st.starts);
}
void updater_tick(float dt) {
    static float t;
    static int done;
    if (done || !st_cs_ready) return;
    if (!st.pending || strcmp(st.installed, B4B_VERSION)) { done = 1; return; }
    if (never_healthy) { done = 1; LOG("update: update_never_healthy=1: not marking %s healthy (test)", B4B_VERSION); return; }
    t += dt;
    if (t < HEALTHY_SECONDS) return;
    done = 1;
    EnterCriticalSection(&st_cs);
    st.pending = 0; st.starts = 0;
    snprintf(st.result, sizeof st.result, "%s started fine", B4B_VERSION);
    State s = st;
    LeaveCriticalSection(&st_cs);
    state_write(&s);
    LOG("update: %s started fine (%.0f s of play); previous version %s stays in the backup", B4B_VERSION, t, s.previous);
}

#ifndef B4B_RELEASE
int updater_cmd(const char *verb, char *rest, Out *o) {
    if (strcmp(verb, "update")) return 0;
    char *a = rest ? strtok(rest, " ") : NULL, *b = a ? strtok(NULL, " ") : NULL;
    const char *e = NULL;
    if (a && !strcmp(a, "check")) e = check(b);
    else if (a && !strcmp(a, "install")) e = install();
    else if (a && !strcmp(a, "goback")) e = go_back();
    else if (a && !strcmp(a, "host") && b) { updater_note_host(b, strtok(NULL, " ")); }
    else if (a && !strcmp(a, "fetch") && b) {   // spike/test: one GET through the same path (noscope: netguard must block it)
        char *mode = strtok(NULL, " "), err[300], hex[65];
        int scoped = !mode || strcmp(mode, "noscope");
        uint8_t *buf, h[32]; size_t n;
        if (scoped) netguard_updater_scope(1, NULL);
        int ok = http_get(b, "application/octet-stream", UPD_MAX_ZIP, &buf, &n, err, sizeof err);
        if (scoped) netguard_updater_scope(0, NULL);
        if (!ok) { out_printf(o, "fetch failed: %s\n", err); return 1; }
        upd_sha256(buf, n, h); upd_hex(h, 32, hex);
        out_printf(o, "fetch ok: %zu bytes sha256 %s\n", n, hex);
        free(buf);
        return 1;
    }
    else if (a && strcmp(a, "status")) { out_printf(o, "usage: update [status|check [ver]|install|goback|host <ver> [proto]|fetch <url> [noscope]]\n"); return 1; }
    if (e) out_printf(o, "error: %s\n", e);
    EnterCriticalSection(&u_cs);
    static const char *names[] = {"idle", "checking", "checked", "downloading", "installed", "error"};
    out_printf(o, "update: phase=%s busy=%d msg=%s\n", names[U.phase], busy(), U.msg);
    if (U.have_manifest) out_printf(o, "manifest: version=%s protocol=%d zip=%s size=%llu\n", U.m.version, U.m.protocol, U.m.zip, (unsigned long long)U.m.size);
    LeaveCriticalSection(&u_cs);
    State s; state_read(&s);
    out_printf(o, "state: installed=%s previous=%s pending=%d starts=%d files=%s result=%s\n", s.installed, s.previous, s.pending,
               s.starts, s.files, s.result);
    out_printf(o, "api=%s repo=%s key=%s unavailable=%s session_changed=%d\n", api, repo, test_key ? "test" : "release",
               unavailable ? unavailable : "-", installed_this_session);
    return 1;
}
#endif
