#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include "log.h"

static FILE *fp;
static CRITICAL_SECTION cs;

void log_init(void *module) {
    wchar_t path[MAX_PATH];
    GetModuleFileNameW((HMODULE)module, path, MAX_PATH);
    wchar_t *slash = wcsrchr(path, L'\\');
    if (slash) wsprintfW(slash + 1, L"b4bcoop-%lu.log", GetCurrentProcessId());
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
