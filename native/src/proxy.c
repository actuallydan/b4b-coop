// dwmapi.dll proxy: forwards the exports the game (and Wine/DXVK) use to the real system dwmapi.
#include <windows.h>

static HMODULE real;
static FARPROC real_fn(const char *name) {
    if (!real) {
        wchar_t path[MAX_PATH];
        GetSystemDirectoryW(path, MAX_PATH);
        lstrcatW(path, L"\\dwmapi.dll");
        real = LoadLibraryW(path);
    }
    return real ? GetProcAddress(real, name) : NULL;
}

#define FWD(ret, name, params, args, fail)                                   \
    __declspec(dllexport) ret WINAPI name params {                           \
        static ret (WINAPI *p) params;                                       \
        if (!p) p = (ret (WINAPI *) params)(void *)real_fn(#name);           \
        return p ? p args : fail;                                            \
    }

FWD(HRESULT, DwmIsCompositionEnabled, (BOOL *a), (a), E_FAIL)
FWD(HRESULT, DwmSetWindowAttribute, (HWND a, DWORD b, LPCVOID c, DWORD d), (a, b, c, d), E_FAIL)
FWD(HRESULT, DwmGetWindowAttribute, (HWND a, DWORD b, PVOID c, DWORD d), (a, b, c, d), E_FAIL)
FWD(HRESULT, DwmGetCompositionTimingInfo, (HWND a, void *b), (a, b), E_FAIL)
FWD(HRESULT, DwmFlush, (void), (), E_FAIL)
FWD(HRESULT, DwmExtendFrameIntoClientArea, (HWND a, const void *b), (a, b), E_FAIL)
FWD(HRESULT, DwmEnableComposition, (UINT a), (a), E_FAIL)
FWD(BOOL, DwmDefWindowProc, (HWND a, UINT b, WPARAM c, LPARAM d, LRESULT *e), (a, b, c, d, e), FALSE)
