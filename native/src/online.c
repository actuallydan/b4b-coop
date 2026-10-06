// online: keeping b4bcoop out of the official online game (#47, docs/investigations/online-mode.md).
// The choice itself is made before the game starts, by the launcher (native/launcher/redirect.c, the root
// xinput1_3.dll): "b4bcoop co-op" or "Online"; Online moves this DLL out of Gobi\Binaries\Win64 before Easy Anti-Cheat
// starts the game, so an online game never contains it. This file is the agent's part:
//   - Wine/Proton: Wine prefers its builtin xinput1_3, so the launcher would never load. online_early sets
//     HKCU\Software\Wine\AppDefaults\Back4Blood.exe\DllOverrides xinput1_3=native,builtin in the game's prefix (once,
//     only when our launcher is next to the root Back4Blood.exe): from the next start Proton shows the same prompt.
//   - -b4bcoop=off/online on our command line means the launcher didn't keep us out (Proton's first start, or no
//     launcher): the game is closed before any game code runs, with a message, instead of going online with us in it.
//   - The game's own Online/Offline sign-in question: by default (auto sign-in, signin.c) answered Offline before it is
//     shown (hook on the task's start); with auto_signin=0 it shows, and every answer becomes Offline (hook on
//     SignInTask_OnlineOfflinePopup::OnPopupClosed); a chat line says how to play online.
//   - ini launch=ask|coop|online (read by the launcher; "Remember my choice" writes it), ~ window Settings.
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"
#include "overlay.h"

static wchar_t root_dir[MAX_PATH];   // game root (next to Back4Blood.exe), trailing backslash; "" = not a player install
static int launcher_ok;              // root xinput1_3.dll is our launcher with the launch choice
static int is_wine;
static int override_set_now;         // Wine: this start set the DllOverride (the prompt comes from the next start)
static char launch_val[16];          // ini launch= ("" = ask)

static int file_has(const wchar_t *p, const char *needle) {
    HANDLE h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    DWORD n = GetFileSize(h, NULL), got = 0;
    char *b = n && n < (4u << 20) ? malloc(n) : NULL;
    int r = 0;
    if (b && ReadFile(h, b, n, &got, NULL) && got == n) {
        size_t k = strlen(needle);
        for (DWORD i = 0; i + k <= n && !r; i++) r = b[i] == needle[0] && !memcmp(b + i, needle, k);
    }
    free(b);
    CloseHandle(h);
    return r;
}

static void paths(HMODULE self) {
    wchar_t m[MAX_PATH];
    if (!GetModuleFileNameW(self, m, MAX_PATH)) return;
    wchar_t *base = wcsrchr(m, L'\\');
    static const wchar_t tail[] = L"\\Gobi\\Binaries\\Win64";
    size_t tl = wcslen(tail);
    if (!base || (size_t)(base - m) <= tl || _wcsnicmp(base - tl, tail, tl)) return;
    size_t rl = base - m - tl + 1;
    wcsncpy(root_dir, m, rl); root_dir[rl] = 0;
    wchar_t p[MAX_PATH * 2];
    _snwprintf(p, MAX_PATH * 2, L"%lsxinput1_3.dll", root_dir);
    launcher_ok = file_has(p, "b4bcoop-launcher-features: launch-choice-1");
}

static void wine_override(void) {
    static const wchar_t key[] = L"Software\\Wine\\AppDefaults\\Back4Blood.exe\\DllOverrides";
    HKEY k;
    if (RegCreateKeyExW(HKEY_CURRENT_USER, key, 0, NULL, 0, KEY_QUERY_VALUE | KEY_SET_VALUE, NULL, &k, NULL)) {
        LOG("online: can't open HKCU\\%ls", key); return;
    }
    wchar_t v[64] = L""; DWORD sz = sizeof v - sizeof(wchar_t), type = 0;
    if (RegQueryValueExW(k, L"xinput1_3", NULL, &type, (BYTE *)v, &sz) || type != REG_SZ || wcscmp(v, L"native,builtin")) {
        static const wchar_t nb[] = L"native,builtin";
        if (!RegSetValueExW(k, L"xinput1_3", 0, REG_SZ, (const BYTE *)nb, sizeof nb)) {
            override_set_now = 1;
            LOG("online: Wine: xinput1_3=native,builtin for Back4Blood.exe (the b4bcoop launcher asks co-op or online from the next start)");
        } else LOG("online: Wine: can't set the xinput1_3 DllOverride");
    }
    RegCloseKey(k);
}

static int arg_online(const wchar_t *cl) {   // -b4bcoop=off / -b4bcoop=online
    for (const wchar_t *p = cl; p && *p; p++)
        if (!_wcsnicmp(p, L"-b4bcoop=", 9) && (!_wcsnicmp(p + 9, L"off", 3) || !_wcsnicmp(p + 9, L"online", 6))) return 1;
    return 0;
}

// DllMain, right after log_init. 1 = don't start (the process is being closed).
int online_early(void *module) {
    paths((HMODULE)module);
    HMODULE nt = GetModuleHandleW(L"ntdll.dll");
    is_wine = nt && GetProcAddress(nt, "wine_get_version");
    if (is_wine && launcher_ok) wine_override();
    if (!arg_online(GetCommandLineW())) return 0;
    LOG("online: -b4bcoop=off/online on the command line, but b4bcoop was loaded: closing the game before it starts");
    const wchar_t *msg = launcher_ok
        ? L"This start was meant to be the online game without b4bcoop (-b4bcoop=off), but b4bcoop was loaded anyway, "
          L"so the game is closed before it starts.\n\nStart the game again: b4bcoop now switches itself off first "
          L"(on Linux/Steam Deck this needed one start to set up)."
        : L"This start was meant to be the online game without b4bcoop (-b4bcoop=off), but b4bcoop was loaded anyway, "
          L"so the game is closed before it starts.\n\nThe b4bcoop launcher (xinput1_3.dll next to Back4Blood.exe) is "
          L"missing or old: extract the b4bcoop zip again, or remove X3DAudio1_7.dll from Gobi\\Binaries\\Win64 to "
          L"play online.";
#ifndef B4B_RELEASE
    if (!GetEnvironmentVariableW(L"B4B_LAUNCHER_ANSWER", NULL, 0))   // unattended tests: no box
#endif
    MessageBoxW(NULL, msg, L"b4bcoop", MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST);
    TerminateProcess(GetCurrentProcess(), 0);
    return 1;
}

// ---- the game's Online/Offline sign-in popup ----
// void SignInTask_OnlineOfflinePopup::OnPopupClosed(this, UPopupUserWidget *Popup, FName Command) (exec thunk
// 0x14229B280 calls it): "Offline" -> OnlineModeSubsystem SetOnlineMode(Offline); anything else leaves the sign-in
// Online. Engine log: "online/offline prompt closed with response %s".
#define ADDR_POPUP_CLOSED VA(0x141B43CA0ull)
static const uint8_t SIG_POPUP_CLOSED[] = {0x4c,0x89,0x44,0x24,0x18,0x48,0x89,0x4c,0x24,0x08,0x55,0x53,0x57,0x41,0x56,
                                           0x41,0x57,0x48,0x8b,0xec,0x48,0x83,0xec,0x60};
typedef FName *(*FNameCtorFn)(FName *self, const wchar_t *name, int find_type);
#define ADDR_FNAME_CTOR VA(0x1424BC8E0ull)
typedef void (*PopupClosedFn)(UObject *task, UObject *popup, FName cmd);
static PopupClosedFn orig_closed;
int online_hook_active(void) { return orig_closed != NULL; }
// void SignInTask_OnlineOfflinePopup start (this): an earlier choice (startup options) answers at once, else it
// creates the popup widget, logs "[%d] prompting online/offline" and binds OnPopupClosed.
#define ADDR_POPUP_START VA(0x141B43960ull)
static const uint8_t SIG_POPUP_START[] = {0x48,0x89,0x5c,0x24,0x18,0x55,0x56,0x57,0x41,0x56,0x41,0x57,0x48,0x8b,0xec,0x48,
                                          0x83,0xec,0x40,0x4c,0x8b,0x41,0x38,0x48};
typedef void (*PopupStartFn)(UObject *task);
static PopupStartFn orig_start;
extern int g_auto_offline;
static int notice_pending;

static void closed_detour(UObject *task, UObject *popup, FName cmd) {
    char n[64];
    ue_name(cmd, n, sizeof n);
    if (_stricmp(n, "Offline")) {
        FName off = {0};
        ((FNameCtorFn)ADDR_FNAME_CTOR)(&off, L"Offline", 1 /*FNAME_Add*/);
        LOG("online: the sign-in popup answered \"%s\"; b4bcoop is running, so this game signs in Offline", n);
        cmd = off;
        notice_pending = 1;
    }
    orig_closed(task, popup, cmd);
    if (notice_pending == 1) {
        notice_pending = 2;
        char m[400];
        snprintf(m, sizeof m, "b4bcoop: you chose Online, but this game runs b4bcoop, so you are signed in Offline. To play "
                 "online, quit and pick Online when the game starts%s.",
                 !launcher_ok ? " (that needs b4bcoop's xinput1_3.dll next to Back4Blood.exe)"
                 : is_wine && override_set_now ? " (on Linux/Steam Deck the choice appears from the next start)"
                 : !strcmp(launch_val, "coop") ? " (hold Shift while it starts: you chose to always play co-op)" : "");
        chat_local_later(m);
        overlay_note(m);
    }
}

// Auto sign-in (signin.c, the default for every co-op start): the question is answered Offline, through the game's
// own handler, before its popup exists, so it never appears on screen.
static void start_detour(UObject *task) {
    int st = *(int32_t *)((char *)task + 0x30);   // ESignInTaskState, 1 = Running
    if (g_auto_offline && st == 1) {
        FName off = {0};
        ((FNameCtorFn)ADDR_FNAME_CTOR)(&off, L"Offline", 1 /*FNAME_Add*/);
        LOG("signin: Online/Offline question answered Offline before it is shown (co-op start)");
        orig_closed(task, NULL, off);   // Popup is not used by the handler
        return;
    }
    if (g_auto_offline) LOG("signin: Online/Offline task state %d at its start: the game shows its question", st);
    orig_start(task);
}

// ---- ini launch= and the ~ window (Settings) ----
int online_live(const char *k, const char *v) {
    if (strcmp(k, "launch")) return 0;
    snprintf(launch_val, sizeof launch_val, "%s", v && (!strcmp(v, "coop") || !strcmp(v, "online")) ? v : "");
    return 1;
}
static void ini_pair(const char *k, const char *v, void *ctx) { (void)ctx; online_live(k, v); }

void online_settings_panel(void) {
    ov_heading("Game start: co-op or online");
    if (!launcher_ok) {
        ov_text_dim("Needs the b4bcoop launcher (xinput1_3.dll next to Back4Blood.exe, from the b4bcoop zip).");
        return;
    }
    int cur = !strcmp(launch_val, "coop") ? 1 : !strcmp(launch_val, "online") ? 2 : 0;
    if (ov_radio("Ask every time##launch", cur == 0)) ov_setting("launch", NULL, 1);
    if (ov_radio("Always b4bcoop co-op##launch", cur == 1)) ov_setting("launch", "coop", 1);
    if (ov_radio("Always online##launch", cur == 2)) ov_setting("launch", "online", 1);
    ov_text_dim("Online starts the official game with Easy Anti-Cheat: b4bcoop moves its game files to b4bcoop-online "
                "first and back when you pick co-op again. To get the question back: hold Shift while the game "
                "starts, or the launch option -b4bcoop=ask.");
    if (is_wine && override_set_now) ov_text_warn("Linux/Steam Deck: the question appears from the next start (this start set it up).");
}

int online_init(void) {
    cmds_ini_each(ini_pair, NULL);
    if (memcmp((void *)ADDR_POPUP_CLOSED, SIG_POPUP_CLOSED, sizeof SIG_POPUP_CLOSED)) {
        LOG("online: sign-in popup signature mismatch, not hooked"); return -1;
    }
    if (MH_CreateHook((void *)ADDR_POPUP_CLOSED, (void *)closed_detour, (void **)&orig_closed) != MH_OK ||
        MH_EnableHook((void *)ADDR_POPUP_CLOSED) != MH_OK) { LOG("online: hook failed"); return -1; }
    if (memcmp((void *)ADDR_POPUP_START, SIG_POPUP_START, sizeof SIG_POPUP_START))
        LOG("online: sign-in question start signature mismatch: the question shows and is answered like a click");
    else if (MH_CreateHook((void *)ADDR_POPUP_START, (void *)start_detour, (void **)&orig_start) != MH_OK ||
             MH_EnableHook((void *)ADDR_POPUP_START) != MH_OK) LOG("online: start hook failed");
    LOG("online: launcher %s, launch=%s%s; sign-in: %s",
        launcher_ok ? "with the launch choice" : root_dir[0] ? "missing or old" : "n/a (not a player install)",
        launch_val[0] ? launch_val : "ask", is_wine ? (override_set_now ? ", Wine override set now" : ", Wine") : "",
        g_auto_offline ? "automatic Offline, the Online/Offline question is never shown" :
        "the game's own (auto_signin=0); Online at the sign-in popup is answered Offline");
    return 0;
}
