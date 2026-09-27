#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "log.h"

static FILE *fp;
char g_module_dir[520];   // directory of this DLL, with trailing backslash
static CRITICAL_SECTION cs;

void log_init(void *module) {
    wchar_t path[MAX_PATH];
    GetModuleFileNameW((HMODULE)module, path, MAX_PATH);
    wchar_t *slash = wcsrchr(path, L'\\');
    if (slash) { size_t n = 0; for (wchar_t *c = path; c <= slash && n < sizeof g_module_dir - 1; c++) g_module_dir[n++] = (char)*c; g_module_dir[n] = 0; }
    // B4B_COOP_TAG (multi-instance testing): separate prefixes have separate wineservers, so Windows PIDs collide
    wchar_t tag[32];
    DWORD tn = GetEnvironmentVariableW(L"B4B_COOP_TAG", tag, 32);
    if (slash && tn > 0 && tn < 32) wsprintfW(slash + 1, L"b4bcoop-%s-%lu.log", tag, GetCurrentProcessId());
    else if (slash) wsprintfW(slash + 1, L"b4bcoop-%lu.log", GetCurrentProcessId());
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
