// `~` power-user overlay (#26, foundation spike): a translucent Dear ImGui window drawn over the game.
//
// B4B renders with D3D12 (log: "DefaultRHIFromHardware: DX12"; vkd3d-proton under Proton). The render hooks are
// installed the first time the window is opened (nothing is touched before that): a throw-away D3D12 device, queue
// and swap chain on a hidden window give the vtables of IDXGISwapChain::Present / ResizeBuffers and
// ID3D12CommandQueue::ExecuteCommandLists, which are hooked with MinHook (every swap chain / queue of that
// implementation shares them). ExecuteCommandLists records the game's direct queue (a swap chain presents from the
// queue it was created with; UE has one direct queue); Present, while the window is open, records ImGui's draw lists
// onto the current back buffer and submits them on that queue, then presents.
//
// Threads: Present runs on UE's RHI thread, the window procedure and the agent's tick on the game thread. ImGui runs
// on the RHI thread only; input events and the settings pass through `cs`. Settings edited in the window are applied
// by overlay_tick on the game thread (thirdperson_apply) and saved with cmds_ini_set.
//
// Input while open: the game window's procedure is subclassed; mouse (raw input deltas move a software cursor: the
// game hides and clips the real one), mouse buttons, wheel, key downs and characters go to ImGui and not to the game
// (key and button releases still reach it, so nothing stays held). `~` or Esc closes it.
#include <windows.h>
#include <d3d12.h>
#include <dxgi1_4.h>
#include <string.h>
#include <stdio.h>
#include "imgui.h"
#include "imgui_impl_dx12.h"
extern "C" {
#include "log.h"
#include "cmds.h"
#include "MinHook.h"
}

// ImGui's DX12 backend compiles its two tiny shaders with D3DCompile: resolve it at run time instead of importing
// d3dcompiler_47.dll (the agent must load even where it is missing; Wine and Windows 10+ ship it).
extern "C" HRESULT WINAPI D3DCompile(LPCVOID src, SIZE_T len, LPCSTR name, const D3D_SHADER_MACRO *defs, ID3DInclude *inc,
                                     LPCSTR entry, LPCSTR target, UINT f1, UINT f2, ID3DBlob **code, ID3DBlob **errs) {
    typedef HRESULT(WINAPI * Fn)(LPCVOID, SIZE_T, LPCSTR, const D3D_SHADER_MACRO *, ID3DInclude *, LPCSTR, LPCSTR, UINT,
                                 UINT, ID3DBlob **, ID3DBlob **);
    static Fn fn;
    if (!fn) {
        HMODULE m = LoadLibraryA("d3dcompiler_47.dll");
        fn = m ? (Fn)(void *)GetProcAddress(m, "D3DCompile") : nullptr;
    }
    return fn ? fn(src, len, name, defs, inc, entry, target, f1, f2, code, errs) : E_NOTIMPL;
}

// ... and the backend makes a DXGI factory (tearing support query): same, from dxgi.dll at run time
extern "C" HRESULT WINAPI CreateDXGIFactory1(REFIID riid, void **out) {
    typedef HRESULT(WINAPI * Fn)(REFIID, void **);
    static Fn fn;
    if (!fn) {
        HMODULE m = LoadLibraryA("dxgi.dll");
        fn = m ? (Fn)(void *)GetProcAddress(m, "CreateDXGIFactory1") : nullptr;
    }
    return fn ? fn(riid, out) : E_NOTIMPL;
}

static int enabled = 1;          // ini overlay=0 turns it off
static int key = VK_OEM_3;       // ini overlay_key (default ~, the key left of 1 on a US layout)
static float ui_scale = 1.25f;   // ini overlay_scale
static volatile LONG open_;      // the window is shown
static int hooks;                // 0 not yet, 1 installed, -1 failed
static CRITICAL_SECTION cs;
static int cs_ready;

// ---- settings shared with the game thread (under cs) ----
static TpSettings tp_shown;      // game -> window (refreshed every tick)
static TpSettings tp_edit;       // window -> game
static int tp_dirty, save_req;
static char status_line[160];

// ---- D3D12 state (RHI thread) ----
typedef HRESULT(STDMETHODCALLTYPE *PresentFn)(IDXGISwapChain3 *, UINT, UINT);
typedef HRESULT(STDMETHODCALLTYPE *ResizeFn)(IDXGISwapChain3 *, UINT, UINT, UINT, DXGI_FORMAT, UINT);
typedef void(STDMETHODCALLTYPE *EclFn)(ID3D12CommandQueue *, UINT, ID3D12CommandList *const *);
static PresentFn orig_present;
static ResizeFn orig_resize;
static EclFn orig_ecl;
static ID3D12CommandQueue *queue;   // the game's direct queue
static ID3D12Device *dev;
static IDXGISwapChain3 *chain;      // the swap chain we initialized for
static HWND hwnd;
static WNDPROC orig_wndproc;
#define MAXBUF 8
static UINT nbuf;
static ID3D12DescriptorHeap *rtv_heap, *srv_heap;
static UINT rtv_inc, srv_inc, srv_used;
static D3D12_CPU_DESCRIPTOR_HANDLE rtv[MAXBUF];
static ID3D12Resource *backbuf[MAXBUF];
static ID3D12CommandAllocator *alloc[MAXBUF];
static UINT64 alloc_fence[MAXBUF];
static ID3D12GraphicsCommandList *cmdlist;
static ID3D12Fence *fence;
static UINT64 fence_val;
static HANDLE fence_ev;
static int imgui_ready, render_failed;
static float mouse_x = -1, mouse_y = -1, disp_w, disp_h;
static LARGE_INTEGER t_last, t_freq;

static void lock(int on) { if (cs_ready) { if (on) EnterCriticalSection(&cs); else LeaveCriticalSection(&cs); } }

static void wait_gpu(UINT64 v) {
    if (v && fence->GetCompletedValue() < v) { fence->SetEventOnCompletion(v, fence_ev); WaitForSingleObject(fence_ev, 2000); }
}
static void release_buffers(void) {
    if (fence) wait_gpu(fence_val);
    for (UINT i = 0; i < MAXBUF; i++) if (backbuf[i]) { backbuf[i]->Release(); backbuf[i] = nullptr; }
}
static int get_buffers(IDXGISwapChain3 *sc) {
    D3D12_CPU_DESCRIPTOR_HANDLE h = rtv_heap->GetCPUDescriptorHandleForHeapStart();
    for (UINT i = 0; i < nbuf; i++) {
        if (FAILED(sc->GetBuffer(i, IID_PPV_ARGS(&backbuf[i])))) return 0;
        rtv[i] = h; rtv[i].ptr += (SIZE_T)i * rtv_inc;
        dev->CreateRenderTargetView(backbuf[i], nullptr, rtv[i]);
    }
    return 1;
}
static void srv_alloc(ImGui_ImplDX12_InitInfo *, D3D12_CPU_DESCRIPTOR_HANDLE *c, D3D12_GPU_DESCRIPTOR_HANDLE *g) {
    UINT i = srv_used < 64 ? srv_used++ : 63;   // the font atlas (and a few more textures in 1.92): never freed here
    *c = srv_heap->GetCPUDescriptorHandleForHeapStart(); c->ptr += (SIZE_T)i * srv_inc;
    *g = srv_heap->GetGPUDescriptorHandleForHeapStart(); g->ptr += (UINT64)i * srv_inc;
}
static void srv_free(ImGui_ImplDX12_InitInfo *, D3D12_CPU_DESCRIPTOR_HANDLE, D3D12_GPU_DESCRIPTOR_HANDLE) {}

static int init_render(IDXGISwapChain3 *sc) {
    DXGI_SWAP_CHAIN_DESC d;
    if (FAILED(sc->GetDesc(&d)) || FAILED(sc->GetDevice(IID_PPV_ARGS(&dev)))) return 0;
    nbuf = d.BufferCount < MAXBUF ? d.BufferCount : MAXBUF;
    D3D12_DESCRIPTOR_HEAP_DESC hd = {};
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV; hd.NumDescriptors = MAXBUF;
    if (FAILED(dev->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&rtv_heap)))) return 0;
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV; hd.NumDescriptors = 64; hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    if (FAILED(dev->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&srv_heap)))) return 0;
    rtv_inc = dev->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV);
    srv_inc = dev->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    for (UINT i = 0; i < nbuf; i++)
        if (FAILED(dev->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&alloc[i])))) return 0;
    if (FAILED(dev->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, alloc[0], nullptr, IID_PPV_ARGS(&cmdlist)))) return 0;
    cmdlist->Close();
    if (FAILED(dev->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence)))) return 0;
    fence_ev = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    if (!get_buffers(sc)) return 0;

    ImGui::CreateContext();
    ImGuiIO &io = ImGui::GetIO();
    io.IniFilename = nullptr;   // no imgui.ini in the game folder
    io.MouseDrawCursor = true;  // the game hides the OS cursor
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    ImGui::StyleColorsDark();
    ImGuiStyle &st = ImGui::GetStyle();
    st.FontScaleMain = ui_scale;
    st.WindowRounding = 6.f;
    st.Colors[ImGuiCol_WindowBg].w = 0.78f;   // translucent
    ImGui_ImplDX12_InitInfo ii;
    ii.Device = dev; ii.CommandQueue = queue; ii.NumFramesInFlight = (int)nbuf;
    ii.RTVFormat = d.BufferDesc.Format; ii.DSVFormat = DXGI_FORMAT_UNKNOWN;
    ii.SrvDescriptorHeap = srv_heap; ii.SrvDescriptorAllocFn = srv_alloc; ii.SrvDescriptorFreeFn = srv_free;
    if (!ImGui_ImplDX12_Init(&ii)) return 0;
    hwnd = d.OutputWindow;
    chain = sc;
    QueryPerformanceFrequency(&t_freq); QueryPerformanceCounter(&t_last);
    LOG("overlay: renderer ready (D3D12, %u buffers, format %d, window %p)", nbuf, (int)d.BufferDesc.Format, (void *)hwnd);
    return 1;
}

static void draw_ui(void) {
    lock(1);
    TpSettings s = tp_shown;
    char st[160]; memcpy(st, status_line, sizeof st);
    lock(0);
    static TpSettings e;
    static int editing;
    if (!editing) e = s;   // follow the game (chat commands, ini) unless a control is being dragged
    ImGui::SetNextWindowPos(ImVec2(40, 40), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSize(ImVec2(470, 0), ImGuiCond_FirstUseEver);
    ImGui::Begin("b4bcoop", nullptr, ImGuiWindowFlags_NoCollapse);
    ImGui::PushTextWrapPos(0);
    ImGui::TextDisabled("%s", st);
    int changed = 0;
    if (ImGui::CollapsingHeader("Third person", ImGuiTreeNodeFlags_DefaultOpen)) {
        bool on = e.on != 0, fix = e.aimfix != 0;
        if (ImGui::Checkbox("Third person", &on)) { e.on = on; changed = 1; }
        ImGui::SameLine();
        if (ImGui::Checkbox("Aim correction", &fix)) { e.aimfix = fix; changed = 1; }
        changed |= ImGui::SliderFloat("Distance", &e.dist, 50, 600, "%.0f");
        changed |= ImGui::SliderFloat("Side", &e.side, -150, 150, "%.0f");
        changed |= ImGui::SliderFloat("Height", &e.height, -100, 150, "%.0f");
        changed |= ImGui::SliderFloat("FOV (0 = game's)", &e.fov, 0, 130, "%.0f");
        if (ImGui::Button("Swap shoulder")) { e.side = -e.side; changed = 1; }
        ImGui::SameLine();
        if (ImGui::Button("Reset")) { e.dist = 180; e.side = 40; e.height = 0; e.fov = 0; changed = 1; }
        ImGui::SameLine();
        if (ImGui::Button("Save to b4bcoop.ini")) { lock(1); save_req = 1; lock(0); }
    }
    editing = ImGui::IsAnyItemActive();
    if (changed) {
        if (e.fov > 0 && e.fov < 60) e.fov = 60;
        lock(1); tp_edit = e; tp_dirty = 1; lock(0);
    }
    ImGui::Separator();
    ImGui::TextDisabled("~ or Esc closes. Models, add-ons, admin: later (#26).");
    ImGui::PopTextWrapPos();
    ImGui::End();
}

static void render(IDXGISwapChain3 *sc) {
    if (render_failed || !queue) return;
    if (!imgui_ready) {
        if (!init_render(sc)) { render_failed = 1; LOG("overlay: renderer init FAILED"); return; }
        imgui_ready = 1;
    }
    if (sc != chain) return;   // another swap chain (only ever one here)
    if (!backbuf[0] && !get_buffers(sc)) return;
    DXGI_SWAP_CHAIN_DESC d;
    sc->GetDesc(&d);
    disp_w = (float)d.BufferDesc.Width; disp_h = (float)d.BufferDesc.Height;
    ImGuiIO &io = ImGui::GetIO();
    LARGE_INTEGER now; QueryPerformanceCounter(&now);
    io.DeltaTime = (float)(now.QuadPart - t_last.QuadPart) / (float)t_freq.QuadPart;
    if (io.DeltaTime <= 0) io.DeltaTime = 1e-4f;
    t_last = now;
    io.DisplaySize = ImVec2(disp_w, disp_h);
    lock(1);
    if (mouse_x < 0) { mouse_x = disp_w / 2; mouse_y = disp_h / 2; io.AddMousePosEvent(mouse_x, mouse_y); }   // input events queued by the window procedure are consumed by NewFrame
    ImGui_ImplDX12_NewFrame();
    ImGui::NewFrame();
    lock(0);
    draw_ui();
    ImGui::Render();

    UINT i = sc->GetCurrentBackBufferIndex() % nbuf;
    wait_gpu(alloc_fence[i]);
    alloc[i]->Reset();
    cmdlist->Reset(alloc[i], nullptr);
    D3D12_RESOURCE_BARRIER b = {};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource = backbuf[i];
    b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_PRESENT;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
    cmdlist->ResourceBarrier(1, &b);
    cmdlist->OMSetRenderTargets(1, &rtv[i], FALSE, nullptr);
    cmdlist->SetDescriptorHeaps(1, &srv_heap);
    ImGui_ImplDX12_RenderDrawData(ImGui::GetDrawData(), cmdlist);
    b.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
    b.Transition.StateAfter = D3D12_RESOURCE_STATE_PRESENT;
    cmdlist->ResourceBarrier(1, &b);
    cmdlist->Close();
    ID3D12CommandList *l = cmdlist;
    orig_ecl(queue, 1, &l);
    queue->Signal(fence, ++fence_val);
    alloc_fence[i] = fence_val;
}

static HRESULT STDMETHODCALLTYPE present_hook(IDXGISwapChain3 *sc, UINT sync, UINT flags) {
    if (open_ && !(flags & DXGI_PRESENT_TEST)) render(sc);
    return orig_present(sc, sync, flags);
}
static HRESULT STDMETHODCALLTYPE resize_hook(IDXGISwapChain3 *sc, UINT n, UINT w, UINT h, DXGI_FORMAT f, UINT fl) {
    if (sc == chain) release_buffers();   // the swap chain's buffers must have no other references
    return orig_resize(sc, n, w, h, f, fl);
}
static void STDMETHODCALLTYPE ecl_hook(ID3D12CommandQueue *q, UINT n, ID3D12CommandList *const *lists) {
    if (!queue && q->GetDesc().Type == D3D12_COMMAND_LIST_TYPE_DIRECT) {
        queue = q;
        LOG("overlay: game's direct queue %p", (void *)q);
    }
    orig_ecl(q, n, lists);
}

// vtables from a throw-away device / queue / swap chain on a hidden window, then MinHook on the implementations
static int install_hooks(void) {
    HMODULE d3d12 = LoadLibraryA("d3d12.dll"), dxgi = LoadLibraryA("dxgi.dll");
    typedef HRESULT(WINAPI * CreateDev)(IUnknown *, D3D_FEATURE_LEVEL, REFIID, void **);
    typedef HRESULT(WINAPI * CreateFac)(REFIID, void **);
    CreateDev cd = d3d12 ? (CreateDev)(void *)GetProcAddress(d3d12, "D3D12CreateDevice") : nullptr;
    CreateFac cf = dxgi ? (CreateFac)(void *)GetProcAddress(dxgi, "CreateDXGIFactory1") : nullptr;
    if (!cd || !cf) { LOG("overlay: no d3d12/dxgi"); return 0; }
    WNDCLASSEXA wc = {sizeof wc};
    wc.lpfnWndProc = DefWindowProcA; wc.hInstance = GetModuleHandleA(nullptr); wc.lpszClassName = "b4bcoop_overlay_probe";
    RegisterClassExA(&wc);
    HWND w = CreateWindowA(wc.lpszClassName, "", WS_OVERLAPPEDWINDOW, 0, 0, 64, 64, nullptr, nullptr, wc.hInstance, nullptr);
    ID3D12Device *d = nullptr; ID3D12CommandQueue *q = nullptr; IDXGIFactory2 *fac = nullptr; IDXGISwapChain1 *sc1 = nullptr;
    int ok = 0;
    D3D12_COMMAND_QUEUE_DESC qd = {};
    DXGI_SWAP_CHAIN_DESC1 sd = {};
    sd.Width = 64; sd.Height = 64; sd.Format = DXGI_FORMAT_R8G8B8A8_UNORM; sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT; sd.BufferCount = 2; sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    if (w && SUCCEEDED(cd(nullptr, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&d))) &&
        SUCCEEDED(d->CreateCommandQueue(&qd, IID_PPV_ARGS(&q))) && SUCCEEDED(cf(IID_PPV_ARGS(&fac))) &&
        SUCCEEDED(fac->CreateSwapChainForHwnd(q, w, &sd, nullptr, nullptr, &sc1))) {
        void **vs = *(void ***)sc1, **vq = *(void ***)q;
        void *p = vs[8], *r = vs[13], *e = vq[10];   // IDXGISwapChain::Present, ResizeBuffers; ExecuteCommandLists
        ok = MH_CreateHook(p, (void *)present_hook, (void **)&orig_present) == MH_OK &&
             MH_CreateHook(r, (void *)resize_hook, (void **)&orig_resize) == MH_OK &&
             MH_CreateHook(e, (void *)ecl_hook, (void **)&orig_ecl) == MH_OK &&
             MH_EnableHook(p) == MH_OK && MH_EnableHook(r) == MH_OK && MH_EnableHook(e) == MH_OK;
        LOG("overlay: hooks %s (Present %p, ResizeBuffers %p, ExecuteCommandLists %p)", ok ? "installed" : "FAILED", p, r, e);
    } else LOG("overlay: probe device/swap chain failed");
    if (sc1) sc1->Release();
    if (fac) fac->Release();
    if (q) q->Release();
    if (d) d->Release();
    if (w) DestroyWindow(w);
    return ok;
}

// ---- input (game thread: the window procedure) ----
static ImGuiKey vk_to_key(WPARAM vk) {
    if (vk >= 'A' && vk <= 'Z') return (ImGuiKey)(ImGuiKey_A + (vk - 'A'));
    if (vk >= '0' && vk <= '9') return (ImGuiKey)(ImGuiKey_0 + (vk - '0'));
    switch (vk) {
    case VK_TAB: return ImGuiKey_Tab; case VK_LEFT: return ImGuiKey_LeftArrow; case VK_RIGHT: return ImGuiKey_RightArrow;
    case VK_UP: return ImGuiKey_UpArrow; case VK_DOWN: return ImGuiKey_DownArrow; case VK_HOME: return ImGuiKey_Home;
    case VK_END: return ImGuiKey_End; case VK_DELETE: return ImGuiKey_Delete; case VK_BACK: return ImGuiKey_Backspace;
    case VK_RETURN: return ImGuiKey_Enter; case VK_SPACE: return ImGuiKey_Space; case VK_CONTROL: return ImGuiKey_LeftCtrl;
    case VK_SHIFT: return ImGuiKey_LeftShift; case VK_MENU: return ImGuiKey_LeftAlt;
    default: return ImGuiKey_None;
    }
}
static void set_open(int on) {
    InterlockedExchange(&open_, on);
    lock(1);
    if (imgui_ready && on) {   // start the software cursor in the middle
        mouse_x = disp_w / 2; mouse_y = disp_h / 2;
        ImGui::GetIO().AddMousePosEvent(mouse_x, mouse_y);
    }
    lock(0);
    LOG("overlay: %s", on ? "open" : "closed");
}
static LRESULT CALLBACK wndproc(HWND h, UINT m, WPARAM wp, LPARAM lp) {
    if (!open_ || !imgui_ready) return CallWindowProcW(orig_wndproc, h, m, wp, lp);
    ImGuiIO &io = ImGui::GetIO();
    switch (m) {
    case WM_KEYDOWN: case WM_SYSKEYDOWN:
        if (wp == (WPARAM)key || wp == VK_ESCAPE) { set_open(0); return 0; }
        lock(1); if (ImGuiKey k = vk_to_key(wp)) io.AddKeyEvent(k, true);
        if (wp == VK_CONTROL) io.AddKeyEvent(ImGuiMod_Ctrl, true);
        if (wp == VK_SHIFT) io.AddKeyEvent(ImGuiMod_Shift, true);
        lock(0);
        return 0;
    case WM_KEYUP: case WM_SYSKEYUP:
        lock(1); if (ImGuiKey k = vk_to_key(wp)) io.AddKeyEvent(k, false);
        if (wp == VK_CONTROL) io.AddKeyEvent(ImGuiMod_Ctrl, false);
        if (wp == VK_SHIFT) io.AddKeyEvent(ImGuiMod_Shift, false);
        lock(0);
        break;   // releases reach the game too
    case WM_CHAR:
        if (wp != (WPARAM)'`' && wp != (WPARAM)'~') { lock(1); io.AddInputCharacterUTF16((unsigned short)wp); lock(0); }
        return 0;
    case WM_INPUT: {
        RAWINPUT ri; UINT sz = sizeof ri;
        if (GetRawInputData((HRAWINPUT)lp, RID_INPUT, &ri, &sz, sizeof(RAWINPUTHEADER)) != (UINT)-1 && ri.header.dwType == RIM_TYPEMOUSE) {
            const RAWMOUSE &rm = ri.data.mouse;
            lock(1);
            if (!(rm.usFlags & MOUSE_MOVE_ABSOLUTE) && (rm.lLastX || rm.lLastY)) {
                mouse_x += rm.lLastX; mouse_y += rm.lLastY;
                if (mouse_x < 0) mouse_x = 0; if (mouse_y < 0) mouse_y = 0;
                if (mouse_x > disp_w - 1) mouse_x = disp_w - 1; if (mouse_y > disp_h - 1) mouse_y = disp_h - 1;
                io.AddMousePosEvent(mouse_x, mouse_y);
            }
            static const USHORT dn[] = {RI_MOUSE_LEFT_BUTTON_DOWN, RI_MOUSE_RIGHT_BUTTON_DOWN, RI_MOUSE_MIDDLE_BUTTON_DOWN};
            static const USHORT up[] = {RI_MOUSE_LEFT_BUTTON_UP, RI_MOUSE_RIGHT_BUTTON_UP, RI_MOUSE_MIDDLE_BUTTON_UP};
            for (int b = 0; b < 3; b++) {
                if (rm.usButtonFlags & dn[b]) io.AddMouseButtonEvent(b, true);
                if (rm.usButtonFlags & up[b]) io.AddMouseButtonEvent(b, false);
            }
            if (rm.usButtonFlags & RI_MOUSE_WHEEL) io.AddMouseWheelEvent(0, (float)(SHORT)rm.usButtonData / WHEEL_DELTA);
            lock(0);
            // button releases still go to the game (nothing stays held); everything else stops here
            if (rm.usButtonFlags & (RI_MOUSE_LEFT_BUTTON_UP | RI_MOUSE_RIGHT_BUTTON_UP | RI_MOUSE_MIDDLE_BUTTON_UP))
                return CallWindowProcW(orig_wndproc, h, m, wp, lp);
        }
        return DefWindowProcW(h, m, wp, lp);
    }
    case WM_MOUSEMOVE: case WM_LBUTTONDOWN: case WM_RBUTTONDOWN: case WM_MBUTTONDOWN: case WM_MOUSEWHEEL:
    case WM_LBUTTONDBLCLK: case WM_RBUTTONDBLCLK: case WM_XBUTTONDOWN:
        return 0;   // the software cursor follows raw input
    }
    return CallWindowProcW(orig_wndproc, h, m, wp, lp);
}

// ---- C interface ----
extern "C" int overlay_live(const char *k, const char *v) {
    if (!strcmp(k, "overlay")) enabled = v ? atoi(v) != 0 : 1;
    else if (!strcmp(k, "overlay_key")) key = v ? cmds_parse_key(v) : VK_OEM_3;
    else if (!strcmp(k, "overlay_scale")) { ui_scale = v ? (float)atof(v) : 1.25f; if (ui_scale < 0.5f) ui_scale = 0.5f; if (ui_scale > 4) ui_scale = 4; }
    else return 0;
    if (!enabled && open_) set_open(0);
    return 1;
}
static void ini_pair(const char *k, const char *v, void *) { overlay_live(k, v); }
extern "C" int overlay_init(void) {
    InitializeCriticalSection(&cs);
    cs_ready = 1;
    cmds_ini_each(ini_pair, nullptr);
    LOG("overlay: %s, key 0x%02x, scale %.2f (hooks on first open)", enabled ? "on" : "off (overlay=0)", key, ui_scale);
    return 0;
}
static void save_settings(const TpSettings &s) {
    char b[32];
    int bad = 0;
    bad |= cmds_ini_set("thirdperson", s.on ? "1" : "0");
    snprintf(b, sizeof b, "%.0f", s.dist); bad |= cmds_ini_set("thirdperson_distance", b);
    snprintf(b, sizeof b, "%.0f", s.side); bad |= cmds_ini_set("thirdperson_side", b);
    snprintf(b, sizeof b, "%.0f", s.height); bad |= cmds_ini_set("thirdperson_height", b);
    snprintf(b, sizeof b, "%.0f", s.fov); bad |= cmds_ini_set("thirdperson_fov", b);
    bad |= cmds_ini_set("thirdperson_aimfix", s.aimfix ? "1" : "0");
    lock(1);
    snprintf(status_line, sizeof status_line, "%s", bad ? "could not write b4bcoop.ini" : "saved to b4bcoop.ini");
    lock(0);
}
// game thread, every tick: the open key (through the game's input, so typing in chat doesn't open it), first-open
// setup, settings both ways
extern "C" void overlay_tick(float) {
    static int was_down;
    if (!enabled) return;
    int down = key && !open_ && cmds_hotkey_down(key);
    if (down && !was_down && !open_) {
        if (!hooks) hooks = install_hooks() ? 1 : -1;
        if (hooks == 1) set_open(1);
    }
    was_down = down;
    if (hooks != 1) return;
    if (!orig_wndproc && hwnd) {
        orig_wndproc = (WNDPROC)SetWindowLongPtrW(hwnd, GWLP_WNDPROC, (LONG_PTR)wndproc);
        LOG("overlay: input hooked on window %p", (void *)hwnd);
    }
    TpSettings cur, apply;
    thirdperson_get(&cur);
    int dirty, save;
    lock(1);
    dirty = tp_dirty; save = save_req; apply = tp_edit; tp_dirty = save_req = 0;
    lock(0);
    if (dirty) { thirdperson_apply(&apply); thirdperson_get(&cur); }
    if (save) save_settings(cur);
    lock(1);
    tp_shown = cur;
    if (!status_line[0] || (!save && dirty)) snprintf(status_line, sizeof status_line, "b4bcoop overlay: changes apply at once; Save keeps them");
    lock(0);
}
extern "C" int overlay_is_open(void) { return open_ != 0; }
