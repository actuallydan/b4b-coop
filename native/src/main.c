// b4bcoop: in-process agent. Hooks UGameEngine::Tick to run commands on the game thread; commands arrive
// over a localhost TCP socket (127.0.0.1:47112), one line per connection, reply is the command's output.
#include <winsock2.h>
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"
#include "netguard.h"

#define PORT 47112
int g_agent_port;   // 0 until the command server binds; PORT for the first game instance on this machine

typedef void (*TickFn)(void *engine, float dt, uint8_t idle);
static TickFn orig_tick;

static CRITICAL_SECTION job_cs;
static HANDLE job_done;
static char *job_cmd;        // pending command (owned by server thread)
static Out job_out;

static void tick_detour(void *engine, float dt, uint8_t idle) {
    EnterCriticalSection(&job_cs);
    if (job_cmd) {
        cmds_run(job_cmd, &job_out);
        job_cmd = NULL;
        SetEvent(job_done);
    }
    LeaveCriticalSection(&job_cs);
    cmds_tick(dt);
    orig_tick(engine, dt, idle);
}

static void run_on_game_thread(char *cmd, Out *reply) {
    EnterCriticalSection(&job_cs);
    out_reset(&job_out);
    job_cmd = cmd;
    ResetEvent(job_done);
    LeaveCriticalSection(&job_cs);
    if (WaitForSingleObject(job_done, 15000) != WAIT_OBJECT_0) {
        EnterCriticalSection(&job_cs); job_cmd = NULL; LeaveCriticalSection(&job_cs);
        out_printf(reply, "timeout waiting for game thread\n");
        return;
    }
    out_printf(reply, "%s", job_out.buf);
}

static DWORD WINAPI server_thread(LPVOID _) {
    WSADATA wsa; WSAStartup(MAKEWORD(2, 2), &wsa);
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        struct sockaddr_in a = {0};
    a.sin_family = AF_INET; a.sin_port = htons(PORT); a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    // one agent per game instance: take the first free port in PORT..PORT+7, or exactly B4B_COOP_PORT if set
    char env[16];
    int lo = PORT, hi = PORT + 8;
    DWORD en = GetEnvironmentVariableA("B4B_COOP_PORT", env, sizeof env);
    if (en > 0 && en < sizeof env && atoi(env) > 0) { lo = atoi(env); hi = lo + 1; }
    int port = lo;
    for (; port < hi; port++) {
        a.sin_port = htons(port);
        if (!bind(s, (struct sockaddr *)&a, sizeof a)) break;
    }
    if (port == hi || listen(s, 4)) { LOG("server: bind/listen failed %d", WSAGetLastError()); return 1; }
    g_agent_port = port;
    LOG("server: listening on 127.0.0.1:%d", port);
    static Out reply;
    for (;;) {
        SOCKET c = accept(s, NULL, NULL);
        if (c == INVALID_SOCKET) continue;
        char line[4096]; int n = 0, r;
        while (n < (int)sizeof line - 1 && (r = recv(c, line + n, sizeof line - 1 - n, 0)) > 0) {
            n += r;
            if (memchr(line, '\n', n)) break;
        }
        line[n] = 0;
        char *nl = strpbrk(line, "\r\n"); if (nl) *nl = 0;
        out_reset(&reply);
        LOG("cmd: %s", line);
        run_on_game_thread(line, &reply);
        send(c, reply.buf, (int)reply.len, 0);
        closesocket(c);
    }
}

static DWORD WINAPI init_thread(LPVOID _) {
    char err[256];
    if (ue_init(err, sizeof err)) { LOG("init: %s", err); return 1; }
    // wait until the engine has created its object array
    while (ue_num_objects() < 1000) Sleep(100);
    InitializeCriticalSection(&job_cs);
    job_done = CreateEventW(NULL, TRUE, FALSE, NULL);
    MH_STATUS mh = MH_Initialize();   // netguard_init may have initialized MinHook already (DllMain)
    if ((mh != MH_OK && mh != MH_ERROR_ALREADY_INITIALIZED) ||
        MH_CreateHook((void *)ADDR_GAMEENGINETICK, (void *)tick_detour, (void **)&orig_tick) != MH_OK ||
        MH_EnableHook((void *)ADDR_GAMEENGINETICK) != MH_OK) { LOG("init: hook failed"); return 1; }
    LOG("init: tick hooked, %d objects", ue_num_objects());
    *(volatile uint8_t *)ADDR_LOG_GATE = 1;
    LOG("init: image base delta %+lld", (long long)g_base_delta);
    travel_init();
    uelog_init();
    cards_init();
    flashlight_init();
    rewards_init();
    burncards_init();
    teamsize_init();
    slotguard_init();
    chat_init();
    admin_init();
    cmds_init();
    steamnet_init();
    presence_init();
    CreateThread(NULL, 0, server_thread, NULL, 0, NULL);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID _) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(inst);
        log_init(inst);
        LOG("b4bcoop loaded");
        netguard_init();   // before any game code runs: hooks name resolution / TCP connect / EOS (netguard.c)
        CreateThread(NULL, 0, init_thread, NULL, 0, NULL);
    }
    return TRUE;
}
