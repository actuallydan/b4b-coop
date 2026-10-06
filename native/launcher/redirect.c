// b4bcoop launcher: xinput1_3.dll in the game's ROOT folder, next to the root Back4Blood.exe.
//
// Steam's only public launch entry for app 924970 runs that root exe, a UE bootstrap stub. It builds
//   "<root>\start_protected_game.exe" Gobi -SaveToUserDir <Steam launch options>
// (the Easy Anti-Cheat bootstrapper), then checks prerequisites with a plain LoadLibraryW("XINPUT1_3.DLL") (app dir
// first: this file) and calls CreateProcessW. We patch the stub's import of CreateProcessW. When the stub starts EAC,
// the player chooses (#47, docs/investigations/online-mode.md):
//   b4bcoop co-op  the b4bcoop files switched off earlier (b4bcoop-online\) move back, and
//                  "<root>\Gobi\Binaries\Win64\Back4Blood.exe" starts with the same arguments instead of EAC, so the
//                  agent (X3DAudio1_7.dll) loads.
//   Online         the agent DLLs in Gobi\Binaries\Win64 (X3DAudio1_7.dll, legacy dwmapi.dll) move to
//                  <root>\b4bcoop-online\ first (renames; the game can't load what isn't there), then the stub's own
//                  call starts EAC unchanged: the retail game with none of our code in it. This file stays: only the
//                  stub loads it (the game imports XINPUT1_4, EAC's bootstrapper loads XInput1_4 first).
// The choice: a Steam join (+b4bcoop_join) = co-op; launch option -b4bcoop=coop|off|online|ask; else b4bcoop.ini
// launch=coop|online (a remembered choice; Shift held at the start or -b4bcoop=ask shows the prompt anyway); else a
// prompt (task dialog, mouse/keyboard/controller). Closing the prompt starts nothing.
// Windows loads this file by itself. Wine/Proton prefers its builtin xinput1_3 unless the prefix has a DllOverride:
// the agent sets one for Back4Blood.exe on its first start (online.c), so Proton gets the same prompt from then on.
// Pass-through (EAC, no prompt) when the agent isn't installed (neither in place nor switched off).
// Log: Gobi\Binaries\Win64\b4bcoop-launcher.log (rewritten every start). docs/investigations/launch.md.
#include <windows.h>
#include <commctrl.h>

typedef BOOL (WINAPI *CreateProcessWFn)(LPCWSTR, LPWSTR, LPSECURITY_ATTRIBUTES, LPSECURITY_ATTRIBUTES, BOOL, DWORD,
                                         LPVOID, LPCWSTR, LPSTARTUPINFOW, LPPROCESS_INFORMATION);
static CreateProcessWFn real_cp;
static HMODULE self;
static wchar_t root[MAX_PATH];     // install dir, trailing backslash
static wchar_t bin[MAX_PATH];      // <root>Gobi\Binaries\Win64\  (trailing backslash)
static wchar_t park_dir[MAX_PATH]; // <root>b4bcoop-online\  (trailing backslash): the switched-off agent files
static HANDLE logf = INVALID_HANDLE_VALUE;

// Marker the agent looks for before it offers the launch choice (online.c): this launcher knows "launch=".
__attribute__((used)) static const char launcher_features[] = "b4bcoop-launcher-features: launch-choice-1";

// b4bcoop code the game would load from its folder (relative to root). The game never ships either name in Win64.
static const wchar_t *const AGENT[] = { L"Gobi\\Binaries\\Win64\\X3DAudio1_7.dll", L"Gobi\\Binaries\\Win64\\dwmapi.dll" };
#define NAGENT (int)(sizeof AGENT / sizeof AGENT[0])

enum { CH_NONE, CH_COOP, CH_ONLINE, CH_ASK, CH_CANCEL };
static const wchar_t *const CH_NAME[] = { L"none", L"b4bcoop co-op", L"online", L"ask", L"cancel" };

static void logw(const wchar_t *fmt, ...) {
    if (logf == INVALID_HANDLE_VALUE) return;
    static wchar_t w[4200]; static char u[12600];
    va_list ap; va_start(ap, fmt);
    int n = wvsprintfW(w, fmt, ap);   // truncates at 1024 chars; command lines are shorter
    va_end(ap);
    w[n++] = L'\r'; w[n++] = L'\n';
    int m = WideCharToMultiByte(CP_UTF8, 0, w, n, u, sizeof u, NULL, NULL);
    DWORD wr; WriteFile(logf, u, m, &wr, NULL);
}

static void cat3(wchar_t *out, const wchar_t *a, const wchar_t *b) {   // out = a + b (MAX_PATH * 2)
    lstrcpynW(out, a, MAX_PATH * 2);
    int n = lstrlenW(out);
    lstrcpynW(out + n, b, MAX_PATH * 2 - n);
}
static int fexists(const wchar_t *p) { return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES; }
static int exists(const wchar_t *dir, const wchar_t *name) { wchar_t p[MAX_PATH * 2]; cat3(p, dir, name); return fexists(p); }
static void mkparents(const wchar_t *file) {
    wchar_t p[MAX_PATH * 2];
    lstrcpynW(p, file, MAX_PATH * 2);
    for (wchar_t *c = p + 3; *c; c++)
        if (*c == L'\\') { *c = 0; CreateDirectoryW(p, NULL); *c = L'\\'; }
}

static const wchar_t *find_ci(const wchar_t *s, const wchar_t *sub) {
    int n = lstrlenW(sub);
    for (; *s; s++) if (CompareStringOrdinal(s, n, sub, n, TRUE) == CSTR_EQUAL) return s;
    return NULL;
}

// First token of a command line (quoted or not) -> pointer just past it.
static const wchar_t *skip_token(const wchar_t *s, const wchar_t **tok, int *len) {
    while (*s == L' ' || *s == L'\t') s++;
    if (*s == L'"') { *tok = ++s; while (*s && *s != L'"') s++; *len = (int)(s - *tok); if (*s) s++; }
    else { *tok = s; while (*s && *s != L' ' && *s != L'\t') s++; *len = (int)(s - *tok); }
    return s;
}

static int word_is(const wchar_t *v, int n, const wchar_t *w) {
    return n == lstrlenW(w) && CompareStringOrdinal(v, n, w, n, TRUE) == CSTR_EQUAL;
}
static int choice_of(const wchar_t *v, int n) {   // "coop" / "online" / "ask" (and -b4bcoop=off = online)
    if (word_is(v, n, L"coop") || word_is(v, n, L"co-op") || word_is(v, n, L"on")) return CH_COOP;
    if (word_is(v, n, L"online") || word_is(v, n, L"off")) return CH_ONLINE;
    if (word_is(v, n, L"ask")) return CH_ASK;
    return CH_NONE;
}
// -b4bcoop=<value> in the launch options
static int option_choice(const wchar_t *rest) {
    const wchar_t *p = find_ci(rest, L"-b4bcoop=");
    if (!p) return CH_NONE;
    p += 9;
    int n = 0;
    while (p[n] && p[n] != L' ' && p[n] != L'\t' && p[n] != L'"') n++;
    return choice_of(p, n);
}

// ---- moving the agent out of the game's way and back ----
static int n_live(void) { int n = 0; for (int i = 0; i < NAGENT; i++) n += exists(root, AGENT[i]); return n; }
static int n_parked(void) { int n = 0; for (int i = 0; i < NAGENT; i++) n += exists(park_dir, AGENT[i]); return n; }

static void park_readme(void) {
    static const char txt[] =
        "b4bcoop is switched off: you chose Online when the game started.\r\n"
        "Its game files are kept in this folder, so the online game (with Easy Anti-Cheat) runs without them.\r\n"
        "They move back by themselves the next time you choose \"b4bcoop co-op\" when the game starts.\r\n"
        "\r\n"
        "By hand: move the Gobi folder from here into the game folder (merge it with the game's Gobi folder),\r\n"
        "or extract the b4bcoop zip again.\r\n";
    wchar_t p[MAX_PATH * 2]; cat3(p, park_dir, L"README.txt");
    mkparents(p);
    HANDLE h = CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w; WriteFile(h, txt, sizeof txt - 1, &w, NULL);
    CloseHandle(h);
}
// 1 when no agent file is left in the game's folders
static int park(void) {
    for (int i = 0; i < NAGENT; i++) {
        wchar_t live[MAX_PATH * 2], dst[MAX_PATH * 2];
        cat3(live, root, AGENT[i]); cat3(dst, park_dir, AGENT[i]);
        if (!fexists(live)) continue;
        mkparents(dst);
        if (MoveFileExW(live, dst, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) logw(L"switched off: %s -> b4bcoop-online\\%s", AGENT[i], AGENT[i]);
        else logw(L"can't move %s: error %lu", AGENT[i], GetLastError());
    }
    park_readme();
    int left = n_live();
    if (left) logw(L"%d agent file(s) still in place", left);
    return !left;
}
static void rm_park_dirs(void) {
    static const wchar_t *const dirs[] = { L"Gobi\\Binaries\\Win64", L"Gobi\\Binaries", L"Gobi", L"" };
    wchar_t p[MAX_PATH * 2];
    cat3(p, park_dir, L"README.txt");
    DeleteFileW(p);
    for (int i = 0; i < 4; i++) { cat3(p, park_dir, dirs[i]); RemoveDirectoryW(p); }   // only empty ones go
}
// 1 when nothing is left switched off
static int unpark(void) {
    for (int i = 0; i < NAGENT; i++) {
        wchar_t live[MAX_PATH * 2], dst[MAX_PATH * 2];
        cat3(live, root, AGENT[i]); cat3(dst, park_dir, AGENT[i]);
        if (!fexists(dst)) continue;
        if (fexists(live)) {   // installed again in the meantime (a zip extracted over it): that copy wins
            if (DeleteFileW(dst)) logw(L"kept the installed %s, dropped the switched-off copy", AGENT[i]);
            else logw(L"can't delete b4bcoop-online\\%s: error %lu", AGENT[i], GetLastError());
        } else if (MoveFileExW(dst, live, MOVEFILE_WRITE_THROUGH)) logw(L"switched on: b4bcoop-online\\%s -> %s", AGENT[i], AGENT[i]);
        else logw(L"can't move b4bcoop-online\\%s back: error %lu", AGENT[i], GetLastError());
    }
    int left = n_parked();
    if (!left) rm_park_dirs();
    return !left;
}

// ---- the offline save, before an online start ----
// An online sign-in checks the local PlayerProfileSettings against the online account and may reset it
// (docs/investigations/test-profiles.md). Before each online start a copy of the save (.sav = the save, .json = the
// game's export) goes to PlayerProfileSettings-b4bcoop-before-online-<yyyymmdd-hhmmss>.sav/.json next to it; the 5
// newest are kept. The game runs with -SaveToUserDir: %LOCALAPPDATA%\Back4Blood\Steam\Saved\SaveGames.
#define KEEP_SAVE_BACKUPS 5
static void backup_save(void) {
    wchar_t dir[MAX_PATH * 2];
    DWORD k = GetEnvironmentVariableW(L"LOCALAPPDATA", dir, MAX_PATH);
    if (!k || k >= MAX_PATH) { logw(L"save backup: no LOCALAPPDATA"); return; }
    lstrcatW(dir, L"\\Back4Blood\\Steam\\Saved\\SaveGames\\");
    if (!exists(dir, L"PlayerProfileSettings.sav")) { logw(L"save backup: no PlayerProfileSettings.sav in %s", dir); return; }
    SYSTEMTIME t; GetLocalTime(&t);
    wchar_t stamp[32];
    wsprintfW(stamp, L"%04d%02d%02d-%02d%02d%02d", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    static const wchar_t *const ext[] = { L".sav", L".json" };
    for (int i = 0; i < 2; i++) {
        wchar_t from[MAX_PATH * 2], to[MAX_PATH * 2], name[96];
        wsprintfW(name, L"PlayerProfileSettings%s", ext[i]); cat3(from, dir, name);
        wsprintfW(name, L"PlayerProfileSettings-b4bcoop-before-online-%s%s", stamp, ext[i]); cat3(to, dir, name);
        if (fexists(from)) {
            if (CopyFileW(from, to, TRUE)) logw(L"save backup: %s", name);
            else logw(L"save backup: can't copy to %s: error %lu", name, GetLastError());
        }
    }
    // keep the newest KEEP_SAVE_BACKUPS (names sort by time)
    static wchar_t names[64][64];
    int n = 0;
    wchar_t pat[MAX_PATH * 2]; cat3(pat, dir, L"PlayerProfileSettings-b4bcoop-before-online-*.sav");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do if (n < 64 && lstrlenW(fd.cFileName) < 60) lstrcpyW(names[n++], fd.cFileName); while (FindNextFileW(h, &fd));
    FindClose(h);
    for (; n > KEEP_SAVE_BACKUPS; n--) {   // drop the oldest (smallest name) each round
        int o = 0;
        for (int i = 1; i < n; i++) if (lstrcmpW(names[i], names[o]) < 0) o = i;
        wchar_t p[MAX_PATH * 2], base[64];
        lstrcpyW(base, names[o]); base[lstrlenW(base) - 4] = 0;   // without ".sav"
        wsprintfW(pat, L"%s.sav", base); cat3(p, dir, pat); DeleteFileW(p);
        wsprintfW(pat, L"%s.json", base); cat3(p, dir, pat); DeleteFileW(p);
        lstrcpyW(names[o], names[n - 1]);
    }
}

// ---- b4bcoop.ini: launch= ----
static void ini_path(wchar_t *p) {   // the agent's rule (cmds_config_path): B4B_COOP_CONFIG, else next to the agent
    DWORD n = GetEnvironmentVariableW(L"B4B_COOP_CONFIG", p, MAX_PATH);
    if (!n || n >= MAX_PATH) cat3(p, bin, L"b4bcoop.ini");
}
static char *read_all(const wchar_t *p, DWORD *len) {
    HANDLE h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    DWORD n = GetFileSize(h, NULL), got = 0;
    char *b = n < (1 << 20) ? HeapAlloc(GetProcessHeap(), 0, n + 1) : NULL;
    if (b && (!ReadFile(h, b, n, &got, NULL) || got != n)) { HeapFree(GetProcessHeap(), 0, b); b = NULL; }
    CloseHandle(h);
    if (b) { b[n] = 0; *len = n; }
    return b;
}
// an active "launch=" line (key at the start of the line, like the agent's reader)
static int ini_launch(void) {
    wchar_t p[MAX_PATH * 2]; ini_path(p);
    DWORD len; char *b = read_all(p, &len);
    if (!b) return CH_NONE;
    int r = CH_NONE;
    for (DWORD i = 0; i < len; ) {
        DWORD e = i;
        while (e < len && b[e] != '\n') e++;
        if (e - i > 7 && !memcmp(b + i, "launch=", 7)) {
            DWORD v = i + 7, ve = e;
            while (v < ve && b[v] == ' ') v++;
            while (ve > v && (b[ve - 1] == '\r' || b[ve - 1] == ' ' || b[ve - 1] == '\t')) ve--;
            wchar_t w[16]; int n = 0;
            for (DWORD k = v; k < ve && n < 15; k++) w[n++] = (wchar_t)(unsigned char)b[k];
            r = choice_of(w, n);
            break;
        }
        i = e + 1;
    }
    HeapFree(GetProcessHeap(), 0, b);
    return r;
}
// "Remember my choice": launch=<v> in b4bcoop.ini, like the agent's writer (cmds_ini_set): the first active line is
// replaced, else a commented ";launch=" line, else it is appended; everything else stays as it is.
static void ini_set_launch(const char *v) {
    wchar_t p[MAX_PATH * 2], tmp[MAX_PATH * 2];
    ini_path(p);
    cat3(tmp, p, L".tmp");
    DWORD len = 0; char *b = read_all(p, &len);
    const char *eol = b && strstr(b, "\r\n") ? "\r\n" : b ? "\n" : "\r\n";
    long act = -1, com = -1;
    for (DWORD i = 0; b && i < len; ) {
        DWORD e = i;
        while (e < len && b[e] != '\n') e++;
        if (act < 0 && e - i >= 7 && !memcmp(b + i, "launch=", 7)) act = i;
        else if (com < 0 && e - i >= 8 && (b[i] == ';' || b[i] == '#') && !memcmp(b + i + 1, "launch=", 7)) com = i;
        i = e + 1;
    }
    long at = act >= 0 ? act : com;
    char line[64];
    wsprintfA(line, "launch=%s", v);
    HANDLE h = CreateFileW(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) { logw(L"can't write %s: error %lu", tmp, GetLastError()); if (b) HeapFree(GetProcessHeap(), 0, b); return; }
    DWORD w;
    if (at >= 0) {
        DWORD e = at;
        while (e < len && b[e] != '\n' && b[e] != '\r') e++;
        WriteFile(h, b, at, &w, NULL);
        WriteFile(h, line, lstrlenA(line), &w, NULL);
        WriteFile(h, b + e, len - e, &w, NULL);
    } else {
        if (b) WriteFile(h, b, len, &w, NULL);
        if (b && len && b[len - 1] != '\n') WriteFile(h, eol, lstrlenA(eol), &w, NULL);
        WriteFile(h, line, lstrlenA(line), &w, NULL);
        WriteFile(h, eol, lstrlenA(eol), &w, NULL);
    }
    FlushFileBuffers(h);
    CloseHandle(h);
    if (b) HeapFree(GetProcessHeap(), 0, b);
    if (MoveFileExW(tmp, p, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) logw(L"remembered: launch=%S in %s", v, p);
    else { logw(L"can't replace %s: error %lu", p, GetLastError()); DeleteFileW(tmp); }
}

// ---- the prompt ----
#define ID_COOP 1001
#define ID_ONLINE 1002
typedef DWORD (WINAPI *XInputGetStateFn)(DWORD, void *);
static XInputGetStateFn xget;
static WORD pad_last = 0xFFFF;     // buttons held when the prompt opened count only after they were released
#ifndef B4B_RELEASE
static char auto_answer[32];       // dev: B4B_LAUNCHER_ANSWER=coop|online|coop+remember|online+remember|cancel
#endif

static HRESULT CALLBACK td_cb(HWND h, UINT msg, WPARAM wp, LPARAM lp, LONG_PTR ref) {
    (void)lp; (void)ref;
    if (msg == TDN_CREATED) { SetForegroundWindow(h); return S_OK; }
    if (msg == TDN_VERIFICATION_CLICKED) { logw(L"prompt: remember %s", wp ? L"on" : L"off"); return S_OK; }
    if (msg != TDN_TIMER) return S_OK;
#ifndef B4B_RELEASE
    if (auto_answer[0] && wp >= 1500) {   // ms since the dialog opened: long enough to see it (screenshots)
        const char *a = auto_answer;
        logw(L"prompt: dev auto-answer %S", a);
        if (strstr(a, "remember")) SendMessageW(h, TDM_CLICK_VERIFICATION, TRUE, FALSE);
        SendMessageW(h, TDM_CLICK_BUTTON, !strncmp(a, "coop", 4) ? ID_COOP : !strncmp(a, "online", 6) ? ID_ONLINE : IDCANCEL, 0);
        auto_answer[0] = 0;
        return S_OK;
    }
#endif
    if (!xget) return S_OK;
    // controller (Steam Deck, couch): A = co-op, Y = online, X = remember, B = close
    struct { DWORD packet; WORD buttons; BYTE lt, rt; SHORT lx, ly, rx, ry; } st;
    WORD now = 0;
    for (DWORD i = 0; i < 4; i++) if (xget(i, &st) == ERROR_SUCCESS) now |= st.buttons;
    WORD down = now & ~pad_last;
    pad_last = now;
    if (down & 0x1000) SendMessageW(h, TDM_CLICK_BUTTON, ID_COOP, 0);
    else if (down & 0x8000) SendMessageW(h, TDM_CLICK_BUTTON, ID_ONLINE, 0);
    else if (down & 0x2000) SendMessageW(h, TDM_CLICK_BUTTON, IDCANCEL, 0);
    else if (down & 0x4000) {
        static BOOL on;
        on = !on;
        SendMessageW(h, TDM_CLICK_VERIFICATION, on, FALSE);
    }
    return S_OK;
}

static int prompt(int *remember) {
    *remember = 0;
    // comctl32 v6 (task dialogs, themed controls): our own manifest, resource 3 (launcher.rc)
    wchar_t me[MAX_PATH];
    GetModuleFileNameW(self, me, MAX_PATH);
    ACTCTXW ac = { sizeof ac };
    ac.dwFlags = ACTCTX_FLAG_HMODULE_VALID | ACTCTX_FLAG_RESOURCE_NAME_VALID;
    ac.lpSource = me; ac.hModule = self; ac.lpResourceName = MAKEINTRESOURCEW(3);
    HANDLE ctx = CreateActCtxW(&ac);
    ULONG_PTR cookie = 0;
    if (ctx != INVALID_HANDLE_VALUE && !ActivateActCtx(ctx, &cookie)) cookie = 0;
    typedef HRESULT (WINAPI *TDI)(const TASKDIALOGCONFIG *, int *, int *, BOOL *);
    HMODULE cc = LoadLibraryW(L"comctl32.dll");
    TDI tdi = cc ? (TDI)(void *)GetProcAddress(cc, "TaskDialogIndirect") : NULL;
    wchar_t xp[MAX_PATH];
    UINT sl = GetSystemDirectoryW(xp, MAX_PATH);
    if (sl && sl < MAX_PATH - 20) {
        lstrcatW(xp, L"\\XInput1_4.dll");
        HMODULE x = LoadLibraryW(xp);
        if (x) xget = (XInputGetStateFn)(void *)GetProcAddress(x, "XInputGetState");
    }
    int r = CH_CANCEL;
    if (tdi) {
        static const TASKDIALOG_BUTTON buttons[] = {
            { ID_COOP, L"b4bcoop co-op\nOffline mode, with your Steam friends." },
            { ID_ONLINE, L"Online\nThe official online game with Easy Anti-Cheat. b4bcoop switches itself off until you "
                         L"pick co-op again." },
        };
        TASKDIALOGCONFIG c = { sizeof c };
        c.dwFlags = TDF_USE_COMMAND_LINKS | TDF_ALLOW_DIALOG_CANCELLATION | TDF_CALLBACK_TIMER;
        c.pszWindowTitle = L"Back 4 Blood";
        c.pszMainInstruction = L"How do you want to play?";
        c.pszContent = L"b4bcoop is installed.";
        c.cButtons = 2; c.pButtons = buttons; c.nDefaultButton = ID_COOP;
        c.pszVerificationText = L"Remember my choice";
        c.pszFooter = L"Ask again later: hold Shift while the game starts, or ~ window > Settings in co-op. "
                      L"Controller: A co-op, Y online, X remember.";
        c.pfCallback = td_cb;
        int btn = 0; BOOL ver = FALSE;
        HRESULT hr = tdi(&c, &btn, NULL, &ver);
        if (FAILED(hr)) { logw(L"prompt: TaskDialogIndirect failed 0x%08lx", (unsigned long)hr); tdi = NULL; }
        else { r = btn == ID_COOP ? CH_COOP : btn == ID_ONLINE ? CH_ONLINE : CH_CANCEL; *remember = ver && r != CH_CANCEL; }
    }
    if (!tdi) {   // no task dialogs (very old comctl32): a plain message box
        logw(L"prompt: message box (no task dialog)");
#ifndef B4B_RELEASE
        if (auto_answer[0]) r = !strncmp(auto_answer, "coop", 4) ? CH_COOP : !strncmp(auto_answer, "online", 6) ? CH_ONLINE : CH_CANCEL;
        else
#endif
        {
            int m = MessageBoxW(NULL, L"b4bcoop is installed. How do you want to play?\n\n"
                                      L"Yes: b4bcoop co-op (offline mode with your Steam friends)\n"
                                      L"No: Online (Easy Anti-Cheat; b4bcoop switches itself off until you pick co-op again)\n"
                                      L"Cancel: don't start",
                                L"Back 4 Blood", MB_YESNOCANCEL | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST);
            r = m == IDYES ? CH_COOP : m == IDNO ? CH_ONLINE : CH_CANCEL;
        }
    }
    if (cookie) DeactivateActCtx(0, cookie);
    if (ctx != INVALID_HANDLE_VALUE) ReleaseActCtx(ctx);
    return r;
}

static void fail_box(const wchar_t *text) {
    logw(L"error: %s", text);
#ifndef B4B_RELEASE
    if (auto_answer[0] || GetEnvironmentVariableW(L"B4B_LAUNCHER_ANSWER", NULL, 0)) return;   // unattended tests
#endif
    MessageBoxW(NULL, text, L"b4bcoop", MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST);
}

static BOOL WINAPI cp_hook(LPCWSTR app, LPWSTR cmd, LPSECURITY_ATTRIBUTES pa, LPSECURITY_ATTRIBUTES ta, BOOL inh,
                           DWORD flags, LPVOID env, LPCWSTR cwd, LPSTARTUPINFOW si, LPPROCESS_INFORMATION pi) {
    const wchar_t *tok; int len;
    const wchar_t *rest = cmd ? skip_token(cmd, &tok, &len) : NULL;
    static const wchar_t eac[] = L"start_protected_game.exe";
    int is_eac = !app && rest && len >= 24 && CompareStringOrdinal(tok + len - 24, 24, eac, 24, TRUE) == CSTR_EQUAL;
    logw(L"stub: CreateProcessW(%s, %s)", app ? app : L"NULL", cmd ? cmd : L"NULL");
    if (!is_eac) return real_cp(app, cmd, pa, ta, inh, flags, env, cwd, si, pi);
    int live = n_live(), parked = n_parked();
    logw(L"agent: %d file(s) in place, %d switched off (b4bcoop-online)", live, parked);
    const wchar_t *why = NULL;
    if (!exists(bin, L"Back4Blood.exe")) why = L"no Gobi\\Binaries\\Win64\\Back4Blood.exe";
    else if (!live && !parked) why = L"agent DLL not installed";
    if (why) { logw(L"pass-through to Easy Anti-Cheat: %s", why); return real_cp(app, cmd, pa, ta, inh, flags, env, cwd, si, pi); }

    int choice, opt = option_choice(rest);
    const wchar_t *how;
    if (find_ci(rest, L"+b4bcoop_join")) { choice = CH_COOP; how = L"a Steam join"; }
    else if (opt == CH_COOP || opt == CH_ONLINE) { choice = opt; how = L"launch option -b4bcoop="; }
    else {
        int shift = (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0, ini = ini_launch();
        if (ini == CH_COOP || ini == CH_ONLINE) {
            if (opt == CH_ASK || shift) logw(L"b4bcoop.ini launch=%s, but %s: asking", ini == CH_COOP ? L"coop" : L"online",
                                             shift ? L"Shift is held" : L"-b4bcoop=ask");
            else { choice = ini; how = L"b4bcoop.ini launch="; goto chosen; }
        }
        int remember;
        choice = prompt(&remember);
        how = L"the prompt";
        if (remember) ini_set_launch(choice == CH_COOP ? "coop" : "online");
    }
chosen:
    logw(L"choice: %s (%s)", CH_NAME[choice], how);
    if (choice == CH_CANCEL) { logw(L"nothing started"); CloseHandle(logf); ExitProcess(0); }

    if (choice == CH_ONLINE) {
        if (!park()) {
            unpark();
            fail_box(L"b4bcoop couldn't switch itself off (a file in Gobi\\Binaries\\Win64 couldn't be moved), so the "
                     L"online game was not started. Close anything that uses the game folder and try again, or remove "
                     L"X3DAudio1_7.dll from Gobi\\Binaries\\Win64 by hand.");
            CloseHandle(logf); ExitProcess(1);
        }
        backup_save();
#ifndef B4B_RELEASE
        // tests (-b4bcoop_test_suspend, or any unattended run: B4B_LAUNCHER_ANSWER set): EAC's launcher is created
        // but never runs, so a test can't reach the online game
        if (find_ci(rest, L"-b4bcoop_test_suspend") || GetEnvironmentVariableW(L"B4B_LAUNCHER_ANSWER", NULL, 0)) {
            flags |= CREATE_SUSPENDED;
            logw(L"test: starting Easy Anti-Cheat's launcher suspended");
        }
#endif
        logw(L"online: b4bcoop is switched off; starting Easy Anti-Cheat unchanged");
        BOOL ok = real_cp(app, cmd, pa, ta, inh, flags, env, cwd, si, pi);
        logw(ok ? L"started pid %lu" : L"CreateProcessW failed: %lu", ok ? pi->dwProcessId : GetLastError());
        return ok;
    }

    if (!unpark() || !n_live()) {
        fail_box(L"b4bcoop couldn't switch itself back on: its files in the b4bcoop-online folder (next to "
                 L"Back4Blood.exe) couldn't be moved back. Move the Gobi folder from there into the game folder by hand, "
                 L"or extract the b4bcoop zip again.");
        CloseHandle(logf); ExitProcess(1);
    }
    static wchar_t newcmd[32768];
    wsprintfW(newcmd, L"\"%sBack4Blood.exe\"", bin);
    if (lstrlenW(newcmd) + lstrlenW(rest) >= 32767) return real_cp(app, cmd, pa, ta, inh, flags, env, cwd, si, pi);
    lstrcatW(newcmd, rest);
    // Steam sets these for the stub; make sure the game's steam_api sees them (no relaunch through Steam).
    wchar_t v[16];
    if (!GetEnvironmentVariableW(L"SteamAppId", v, 16)) SetEnvironmentVariableW(L"SteamAppId", L"924970");
    if (!GetEnvironmentVariableW(L"SteamGameId", v, 16)) SetEnvironmentVariableW(L"SteamGameId", L"924970");
    logw(L"redirect (no EAC): %s   cwd %s", newcmd, bin);
    BOOL ok = real_cp(NULL, newcmd, pa, ta, inh, flags, env, bin, si, pi);
    logw(ok ? L"started pid %lu" : L"CreateProcessW failed: %lu", ok ? pi->dwProcessId : GetLastError());
    return ok;
}

// Point the main module's CreateProcessW import at cp_hook.
static int patch_iat(HMODULE exe) {
    BYTE *b = (BYTE *)exe;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(b + ((IMAGE_DOS_HEADER *)b)->e_lfanew);
    IMAGE_DATA_DIRECTORY dd = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dd.VirtualAddress) return 0;
    for (IMAGE_IMPORT_DESCRIPTOR *d = (IMAGE_IMPORT_DESCRIPTOR *)(b + dd.VirtualAddress); d->Name; d++) {
        if (lstrcmpiA((char *)(b + d->Name), "kernel32.dll") || !d->OriginalFirstThunk) continue;
        IMAGE_THUNK_DATA *names = (IMAGE_THUNK_DATA *)(b + d->OriginalFirstThunk);
        IMAGE_THUNK_DATA *iat = (IMAGE_THUNK_DATA *)(b + d->FirstThunk);
        for (; names->u1.AddressOfData; names++, iat++) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME *ibn = (IMAGE_IMPORT_BY_NAME *)(b + names->u1.AddressOfData);
            if (lstrcmpA((char *)ibn->Name, "CreateProcessW")) continue;
            DWORD old;
            if (!VirtualProtect(&iat->u1.Function, sizeof(void *), PAGE_READWRITE, &old)) return 0;
            real_cp = (CreateProcessWFn)iat->u1.Function;
            iat->u1.Function = (ULONG_PTR)cp_hook;
            VirtualProtect(&iat->u1.Function, sizeof(void *), old, &old);
            return 1;
        }
    }
    return 0;
}

#ifndef B4B_RELEASE
// Dev: the prompt alone (rundll32 xinput1_3.dll,b4bcoop_prompt_test <result file>), for screenshots and the dialog's
// behaviour under Wine without a game folder.
__declspec(dllexport) void CALLBACK b4bcoop_prompt_test(HWND h, HINSTANCE i, LPSTR arg, int show) {
    (void)h; (void)i; (void)show;
    GetEnvironmentVariableA("B4B_LAUNCHER_ANSWER", auto_answer, sizeof auto_answer);
    int rem, c = prompt(&rem);
    char line[80]; wsprintfA(line, "choice=%d remember=%d\r\n", c, rem);
    HANDLE f = CreateFileA(arg && *arg ? arg : "prompt-test.txt", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (f != INVALID_HANDLE_VALUE) { DWORD w; WriteFile(f, line, lstrlenA(line), &w, NULL); CloseHandle(f); }
}
#endif

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID _) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(inst);
    self = inst;
    // Only in the root stub: <our dir>\Back4Blood.exe with a Gobi\ tree next to it.
    wchar_t exe[MAX_PATH], me[MAX_PATH];
    if (!GetModuleFileNameW(NULL, exe, MAX_PATH) || !GetModuleFileNameW(inst, me, MAX_PATH)) return TRUE;
    wchar_t *se = wcsrchr(exe, L'\\'), *sm = wcsrchr(me, L'\\');
    if (!se || !sm || se - exe != sm - me || CompareStringOrdinal(exe, (int)(se - exe), me, (int)(sm - me), TRUE)
        != CSTR_EQUAL || lstrcmpiW(se + 1, L"Back4Blood.exe")) return TRUE;
    se[1] = 0;
    lstrcpyW(root, exe);
    lstrcpyW(bin, root); lstrcatW(bin, L"Gobi\\Binaries\\Win64\\");
    lstrcpyW(park_dir, root); lstrcatW(park_dir, L"b4bcoop-online\\");
    if (!exists(bin, L"")) return TRUE;
    // stay loaded whatever the stub does with its module handle
    HMODULE pin; GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN | GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                                    (LPCWSTR)DllMain, &pin);
#ifndef B4B_RELEASE
    GetEnvironmentVariableA("B4B_LAUNCHER_ANSWER", auto_answer, sizeof auto_answer);
#endif
    wchar_t lp[MAX_PATH * 2];
    lstrcpyW(lp, bin); lstrcatW(lp, L"b4bcoop-launcher.log");
    logf = CreateFileW(lp, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    SYSTEMTIME t; GetLocalTime(&t);
    logw(L"b4bcoop launcher (xinput1_3.dll) %04d-%02d-%02d %02d:%02d:%02d, game root %s", t.wYear, t.wMonth, t.wDay,
         t.wHour, t.wMinute, t.wSecond, exe);
    logw(patch_iat(GetModuleHandleW(NULL)) ? L"hooked CreateProcessW" : L"CreateProcessW import not found");
    return TRUE;
}
