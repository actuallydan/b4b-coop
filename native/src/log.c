#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "log.h"

static FILE *fp;
char g_module_dir[520];   // directory of this DLL, with trailing backslash
static CRITICAL_SECTION cs;

#define LOG_KEEP 20   // player logs kept, this start's included

static int cmp_name(const void *a, const void *b) { return wcscmp((const wchar_t *)a, (const wchar_t *)b); }

// Deletes the oldest b4bcoop-<yyyymmdd>-<hhmmss>-<pid>.log files beyond LOG_KEEP - 1 (the names sort by time).
// Test logs (b4bcoop-<tag>-<pid>.log) and older b4bcoop-<pid>.log files don't match the pattern and are left alone.
static void prune_logs(wchar_t *path, wchar_t *slash) {
    static wchar_t names[256][64];
    int n = 0;
    wcscpy(slash + 1, L"b4bcoop-20*.log");
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(path, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        const wchar_t *f = fd.cFileName;   // b4bcoop-yyyymmdd-hhmmss-<pid>.log
        int ok = !(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && wcslen(f) < 64 && wcslen(f) > 27 &&
                 f[16] == L'-' && f[23] == L'-';
        for (int i = 8; ok && i < 23; i++) ok = i == 16 || (f[i] >= L'0' && f[i] <= L'9');
        if (ok && n < 256) wcscpy(names[n++], f);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    qsort(names, n, sizeof names[0], cmp_name);
    for (int i = 0; i + (LOG_KEEP - 1) < n; i++) { wcscpy(slash + 1, names[i]); DeleteFileW(path); }
}

void log_init(void *module) {
    wchar_t path[MAX_PATH];
    GetModuleFileNameW((HMODULE)module, path, MAX_PATH);
    wchar_t *slash = wcsrchr(path, L'\\');
    if (slash) { size_t n = 0; for (wchar_t *c = path; c <= slash && n < sizeof g_module_dir - 1; c++) g_module_dir[n++] = (char)*c; g_module_dir[n] = 0; }
    // B4B_COOP_TAG (multi-instance testing): separate prefixes have separate wineservers, so Windows PIDs collide
    wchar_t tag[32];
    DWORD tn = GetEnvironmentVariableW(L"B4B_COOP_TAG", tag, 32);
    if (slash && tn > 0 && tn < 32) wsprintfW(slash + 1, L"b4bcoop-%s-%lu.log", tag, GetCurrentProcessId());
    else if (slash) {
        // Player logs carry the start time: Wine hands out the same few Windows PIDs on every start (552, 556, ...),
        // so b4bcoop-<pid>.log alone overwrote an earlier session's log. The oldest are pruned (LOG_KEEP).
        prune_logs(path, slash);
        SYSTEMTIME t; GetLocalTime(&t);
        wsprintfW(slash + 1, L"b4bcoop-%04u%02u%02u-%02u%02u%02u-%lu.log", t.wYear, t.wMonth, t.wDay, t.wHour,
                  t.wMinute, t.wSecond, GetCurrentProcessId());
    }
    InitializeCriticalSection(&cs);
    fp = _wfopen(path, L"w");
}

void logf_(const char *fmt, ...) {
    if (!fp) return;
    SYSTEMTIME t; GetLocalTime(&t);
    EnterCriticalSection(&cs);
    fprintf(fp, "[%02d:%02d:%02d.%03d] ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_list ap; va_start(ap, fmt); vfprintf(fp, fmt, ap); va_end(ap);
    fputc('\n', fp); fflush(fp);
    LeaveCriticalSection(&cs);
}

// An older b4bcoop's add-on summary (login option b4bcoopaddons=<list>) never reaches the log (#35): the value is cut
// in place, in our own lines and in captured engine lines (login URLs). s must hold at least strlen(s)+1 bytes.
void log_redact_addons(char *s) {
    static const char KEY[] = "b4bcoopaddons=";
    for (char *p = s; (p = strstr(p, KEY));) {
        char *v = p + sizeof KEY - 1, *e = v;
        while (*e && *e != '?' && *e != ' ' && *e != '\'' && *e != '"' && *e != '&') e++;
        if (e > v) { *v = '-'; memmove(v + 1, e, strlen(e) + 1); }
        p = v;
    }
}
