// b4bcoop launch redirect: xinput1_3.dll in the game's ROOT folder, next to the root Back4Blood.exe.
//
// Steam's only public launch entry for app 924970 runs that root exe, a UE bootstrap stub. It builds
//   "<root>\start_protected_game.exe" Gobi -SaveToUserDir <Steam launch options>
// (the Easy Anti-Cheat bootstrapper, which keeps the agent from loading on Windows), then checks prerequisites with a
// plain LoadLibraryW("XINPUT1_3.DLL") (app dir first: this file) and calls CreateProcessW. We patch the stub's import
// of CreateProcessW and start "<root>\Gobi\Binaries\Win64\Back4Blood.exe" with the same arguments instead, so a plain
// Steam "Play" starts the game directly (like the old "Play B4B co-op.cmd" did) and the agent (X3DAudio1_7.dll) loads.
// Pass-through (EAC as usual) when the agent isn't installed or the launch options contain -b4bcoop=off.
// Wine/Proton ignores this file (builtin xinput1_3 wins); nothing there needs it. docs/investigations/launch.md.
#include <windows.h>

typedef BOOL (WINAPI *CreateProcessWFn)(LPCWSTR, LPWSTR, LPSECURITY_ATTRIBUTES, LPSECURITY_ATTRIBUTES, BOOL, DWORD,
                                         LPVOID, LPCWSTR, LPSTARTUPINFOW, LPPROCESS_INFORMATION);
static CreateProcessWFn real_cp;
static wchar_t root[MAX_PATH];     // install dir, trailing backslash
static wchar_t bin[MAX_PATH];      // <root>Gobi\Binaries\Win64\  (trailing backslash)
static HANDLE logf = INVALID_HANDLE_VALUE;

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

static int exists(const wchar_t *dir, const wchar_t *name) {
    wchar_t p[MAX_PATH * 2];
    lstrcpyW(p, dir); lstrcatW(p, name);
    return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES;
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

static BOOL WINAPI cp_hook(LPCWSTR app, LPWSTR cmd, LPSECURITY_ATTRIBUTES pa, LPSECURITY_ATTRIBUTES ta, BOOL inh,
                           DWORD flags, LPVOID env, LPCWSTR cwd, LPSTARTUPINFOW si, LPPROCESS_INFORMATION pi) {
    const wchar_t *tok; int len;
    const wchar_t *rest = cmd ? skip_token(cmd, &tok, &len) : NULL;
    static const wchar_t eac[] = L"start_protected_game.exe";
    int is_eac = !app && rest && len >= 24 && CompareStringOrdinal(tok + len - 24, 24, eac, 24, TRUE) == CSTR_EQUAL;
    logw(L"stub: CreateProcessW(%s, %s)", app ? app : L"NULL", cmd ? cmd : L"NULL");
    if (!is_eac) return real_cp(app, cmd, pa, ta, inh, flags, env, cwd, si, pi);
    const wchar_t *why = NULL;
    if (find_ci(rest, L"-b4bcoop=off")) why = L"-b4bcoop=off in the launch options";
    else if (!exists(bin, L"Back4Blood.exe")) why = L"no Gobi\\Binaries\\Win64\\Back4Blood.exe";
    else if (!exists(bin, L"X3DAudio1_7.dll") && !exists(bin, L"dwmapi.dll")) why = L"agent DLL not installed";
    if (why) { logw(L"pass-through to Easy Anti-Cheat: %s", why); return real_cp(app, cmd, pa, ta, inh, flags, env, cwd, si, pi); }

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

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID _) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(inst);
    // Only in the root stub: <our dir>\Back4Blood.exe with a Gobi\ tree next to it.
    wchar_t exe[MAX_PATH], me[MAX_PATH];
    if (!GetModuleFileNameW(NULL, exe, MAX_PATH) || !GetModuleFileNameW(inst, me, MAX_PATH)) return TRUE;
    wchar_t *se = wcsrchr(exe, L'\\'), *sm = wcsrchr(me, L'\\');
    if (!se || !sm || se - exe != sm - me || CompareStringOrdinal(exe, (int)(se - exe), me, (int)(sm - me), TRUE)
        != CSTR_EQUAL || lstrcmpiW(se + 1, L"Back4Blood.exe")) return TRUE;
    se[1] = 0;
    lstrcpyW(root, exe);
    lstrcpyW(bin, root); lstrcatW(bin, L"Gobi\\Binaries\\Win64\\");
    if (!exists(bin, L"")) return TRUE;
    // stay loaded whatever the stub does with its module handle
    HMODULE pin; GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN | GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                                    (LPCWSTR)DllMain, &pin);
    wchar_t lp[MAX_PATH * 2];
    lstrcpyW(lp, bin); lstrcatW(lp, L"b4bcoop-launcher.log");
    logf = CreateFileW(lp, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    SYSTEMTIME t; GetLocalTime(&t);
    logw(L"b4bcoop launcher (xinput1_3.dll) %04d-%02d-%02d %02d:%02d:%02d, game root %s", t.wYear, t.wMonth, t.wDay,
         t.wHour, t.wMinute, t.wSecond, exe);
    logw(patch_iat(GetModuleHandleW(NULL)) ? L"hooked CreateProcessW" : L"CreateProcessW import not found");
    return TRUE;
}
