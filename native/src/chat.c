// In-game chat commands. Findings: docs/investigations/chat-commands.md.
//
// Sending: the chat box (ChatBoxUserWidget, inside ChatScreen) sends with OnSendMessage(Message), whose native code
// strips an optional "/all " prefix and calls AGobiPlayerControllerBase::Say (all) or ::SayTeam (team) on its owning
// player. The `say`/`sayteam` console commands call the same two functions. Both run on the sending machine only:
// Say hands the text to the game's online chat service (and shows it locally), nothing has left the machine yet. We
// hook them: a message starting with '/' is queued as a command for the next tick and not passed on, so it is never
// sent. "//text" sends "/text" as a normal message.
// Commands typed by other players never pass through Say on this machine, so they are never executed here.
//
// Local replies: we call the listeners bound to the local player controller's OnChatMessageReceived delegate (the
// chat box's OnChatMessageReceived), as the game does for an incoming message, with our own sender name. Local only.
//
// Host notices (/say, "you were kicked"): APlayerController::ClientTeamMessage is a reliable client RPC, but Gobi's
// override drops the chat types (Say/TeamSay) and passes the rest to the engine, which shows nothing. We send it with
// our own Type name (CHAT_NOTICE_TYPE) and, on the receiving machine (clients run this agent too), hook the override
// and show such messages as chat lines. A client without the agent ignores them.
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "MinHook.h"
#include "ue.h"
#include "log.h"
#include "cmds.h"

#define ADDR_SAY      VA(0x141BA6CA0ull)  // void AGobiPlayerControllerBase::Say(this, const FString& Msg)
#define ADDR_SAYTEAM  VA(0x141BA6EF0ull)  // void AGobiPlayerControllerBase::SayTeam(this, const FString& Msg)
#define ADDR_CTM      VA(0x141BA7420ull)  // AGobiPlayerControllerBase::ClientTeamMessage_Implementation (PC vtable +0x790):
                                          //   (this, APlayerState* Sender, const FString& S, FName Type, float MsgLifeTime);
                                          //   returns if Type is the PC's Say/TeamSay name (+0x6d8/+0x6e0), else the engine's
static const uint8_t SIG_SAY[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x48,0x89,0x7c,0x24,0x18,0x55,
                                  0x41,0x56,0x41,0x57,0x48,0x8b,0xec,0x48,0x83,0xec,0x50,0x48,0x63,0x5a,0x08};
static const uint8_t SIG_SAYTEAM[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x18,0x48,0x89,0x7c,0x24,0x20,0x55,
                                      0x41,0x56,0x41,0x57,0x48,0x8b,0xec,0x48,0x83,0xec,0x70,0x48,0x63,0x5a,0x08};
static const uint8_t SIG_CTM[] = {0x4c,0x3b,0x89,0xd8,0x06,0x00,0x00,0x74,0x0d,0x4c,0x3b,0x89,0xe0,0x06,0x00,0x00,
                                  0x0f,0x85};

typedef void (*SayFn)(UObject *pc, const FString *msg);
typedef void (*CtmFn)(UObject *pc, UObject *sender, const FString *s, FName type, float life);
typedef FName *(*FNameCtorFn)(FName *self, const wchar_t *name, int find_type);
#define ADDR_FNAME_CTOR VA(0x1424BC8E0ull)
static SayFn orig_say, orig_sayteam;
static CtmFn orig_ctm;
static int n_intercepted, n_received, leave_pending;

static void ascii_of(const FString *s, char *buf, size_t n) {
    size_t k = 0;
    for (int i = 0; s && s->data && i < s->num && s->data[i] && k + 1 < n; i++)
        buf[k++] = s->data[i] < 128 ? (char)s->data[i] : '?';
    buf[k] = 0;
}

static void fstring_set(FString *s, const char *utf8, wchar_t *storage, int cap) {
    int n = 0;
    for (; utf8[n] && n < cap - 1; n++) storage[n] = (wchar_t)(unsigned char)utf8[n];
    storage[n] = 0;
    s->data = storage; s->num = n + 1; s->max = cap;
}

FName chat_notice_type(int kick) {
    static FName n[2];
    if (!n[kick].idx) ((FNameCtorFn)ADDR_FNAME_CTOR)(&n[kick], kick ? CHAT_KICK_TYPE : CHAT_NOTICE_TYPE, 1);
    return n[kick];
}

// ---- local chat lines ----
static UObject *live_chatbox(void);

// Call a chat line function (PlayerName, Message, bIsOnMyTeam, bIsGlobalMessage) on obj. 1 if called.
static int call_line_fn(UObject *obj, const char *fname, const char *sender, const char *text) {
    UFunction *f = obj && U_CLASS(obj) ? ue_find_function(U_CLASS(obj), fname) : NULL;
    FField *pn = f ? ue_find_prop(f, "PlayerName") : NULL, *pm = f ? ue_find_prop(f, "Message") : NULL;
    FField *pt = f ? ue_find_prop(f, "bIsOnMyTeam") : NULL, *pg = f ? ue_find_prop(f, "bIsGlobalMessage") : NULL;
    if (!pn || !pm || UFN_PARMSSIZE(f) > 128) return 0;
    static wchar_t wn[64], wm[512];
    uint8_t p[128] = {0};
    fstring_set((FString *)(p + FP_OFFSET(pn)), sender, wn, 64);
    fstring_set((FString *)(p + FP_OFFSET(pm)), text, wm, 512);
    if (pt) p[FP_OFFSET(pt)] = 1;
    if (pg) p[FP_OFFSET(pg)] = 1;
    ue_process_event(obj, f, p);
    return 1;
}

// One line into the local chat box: call every listener bound to the local PC's OnChatMessageReceived delegate
// (TArray of FScriptDelegate {weak object index, serial, FName function}), as the game's own display path
// (0x141BA7440, called by Say) does. The chat box binds itself only once it has an owning player (not always the case
// in Fort Hope right after loading); then we call the live chat box's handler directly.
static int show_line(const char *sender, const char *text) {
    UObject *pc = ue_local_pc();
    int32_t off = pc ? ue_prop_offset(pc, "OnChatMessageReceived") : -1;
    if (off < 0) return 0;
    TArray *inv = (TArray *)((char *)pc + off);
    int shown = 0;
    for (int i = 0; i < inv->num; i++) {
        char *e = (char *)inv->data + i * 16, fname[128];
        ue_name(*(FName *)(e + 8), fname, sizeof fname);
        shown += call_line_fn(ue_object_at(*(int32_t *)e), fname, sender, text);
    }
    if (!shown) shown = call_line_fn(live_chatbox(), "OnChatMessageReceived", sender, text);
    return shown;
}

static void show_text(const char *sender, char *buf) {
    int lines = 0;
    for (char *line = strtok(buf, "\n"); line; line = strtok(NULL, "\n")) {
        if (!*line) continue;
        if (++lines > 16) { show_line(sender, "..."); break; }
        int shown = show_line(sender, line);
        LOG("chat: [local %s%s] %s", sender, shown ? "" : ", no chat box", line);
    }
}

// Print text (several lines allowed) into the local chat. Always logged, so it is visible even with no chat box.
void chat_local(const char *fmt, ...) {
    char buf[2048];
    va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    show_text("coop", buf);
}

// ---- interception (sending side) ----
static char queue[8][320];
static int q_head, q_tail;

// 1 if the message was a command (queued, not sent)
static int intercept(UObject *pc, const FString *msg, const char *which) {
    char text[320];
    ascii_of(msg, text, sizeof text);
    const char *t = text;
    while (*t == ' ') t++;
    if (t[0] != '/' || t[1] == '/') return 0;
    if (!pc || pc != ue_local_pc()) { LOG("chat: %s from a non-local controller %p, not intercepted", which, (void *)pc); return 0; }
    n_intercepted++;
    LOG("chat: intercepted %s \"%s\" (not sent)", which, t);
    if ((q_tail + 1) % 8 == q_head) { LOG("chat: command queue full, dropped"); return 1; }
    snprintf(queue[q_tail], sizeof queue[0], "%s", t + 1);
    q_tail = (q_tail + 1) % 8;
    return 1;
}

// "//text" -> send "/text"
static int unescape(const FString *msg, FString *out, wchar_t *storage, int cap) {
    int i = 0;
    while (msg->data && i < msg->num && msg->data[i] == L' ') i++;
    if (!msg->data || i + 1 >= msg->num || msg->data[i] != L'/' || msg->data[i + 1] != L'/') return 0;
    int n = 0;
    for (int k = i + 1; k < msg->num && msg->data[k] && n < cap - 1; k++) storage[n++] = msg->data[k];
    storage[n] = 0;
    out->data = storage; out->num = n + 1; out->max = cap;
    return 1;
}

static void say_common(SayFn orig, UObject *pc, const FString *msg, const char *which) {
    if (intercept(pc, msg, which)) return;
    static wchar_t esc[320];
    FString copy;
    if (msg && unescape(msg, &copy, esc, 320)) { orig(pc, &copy); return; }
    orig(pc, msg);
}
static void say_detour(UObject *pc, const FString *msg) { say_common(orig_say, pc, msg, "Say"); }
static void sayteam_detour(UObject *pc, const FString *msg) { say_common(orig_sayteam, pc, msg, "SayTeam"); }

// Receive side: every ClientTeamMessage this machine gets (logged: evidence for "a / message was not sent");
// our notices are shown in the chat box. Text only, never executed.
static void ctm_detour(UObject *pc, UObject *sender, const FString *s, FName type, float life) {
    char text[320], name[64] = "host", tn[64];
    ascii_of(s, text, sizeof text);
    int32_t off = sender ? ue_prop_offset(sender, "PlayerNamePrivate") : -1;
    if (off >= 0) ascii_of((FString *)((char *)sender + off), name, sizeof name);
    n_received++;
    ue_name(type, tn, sizeof tn);
    if (strcmp(tn, "Event")) LOG("chat: received %s message from %s: %s", tn, name, text);   // Event: game debug lines
    FName nt = chat_notice_type(0), kt = chat_notice_type(1);
    if (type.idx == nt.idx && type.num == nt.num) { show_text(name[0] ? name : "host", text); return; }
    if (type.idx == kt.idx && type.num == kt.num) {
        // only honoured from the server we are connected to (a client RPC can only come from it)
        LOG("chat: the host removed us: %s", text);
        show_text(name[0] ? name : "host", text);
        chat_local_later(text);
        leave_pending = 1;   // not from inside the net driver's receive path: next tick
        return;
    }
    orig_ctm(pc, sender, s, type, life);
}

// ---- open game popups (e.g. "DISCONNECTED FROM SERVER" after a refused join) ----
static UObject *next_open_popup(UObject *after) {
    static UClass *pc;
    static UFunction *is_open;
    if (!pc) pc = ue_find_class("PopupUserWidget");
    int32_t n = ue_num_objects();
    for (int32_t i = after ? U_INDEX(after) + 1 : 0; pc && i < n; i++) {
        UObject *o = ue_object_at(i);
        if (!o || (U_FLAGS(o) & 0x30) || !ue_is_a(o, pc)) continue;
        if (!is_open) is_open = ue_find_function(U_CLASS(o), "IsOpen");
        uint8_t p[16] = {0};
        if (is_open) { ue_process_event(o, is_open, p); if (p[0] == 1) return o; }
    }
    return NULL;
}

// ---- notices that must survive a map change (kick, refused join): shown once the next map has a local player,
// no game popup is covering the screen, and a chat box takes the line ----
static char later[256];
static UObject *later_world;
static float later_wait;
static int later_tries;
void chat_local_later(const char *text) {
    snprintf(later, sizeof later, "%s", text);
    later_world = ue_world();
    later_wait = 4;
    later_tries = 0;
}

static void later_tick(float dt) {
    if (!later[0] || ue_world() == later_world || !ue_local_pc()) return;
    if ((later_wait -= dt) > 0) return;
    later_wait = 2;
    if (++later_tries > 150) { LOG("chat: dropped notice: %s", later); later[0] = 0; return; }
    if (next_open_popup(NULL) || !show_line("coop", later)) return;
    LOG("chat: [local coop, delayed] %s", later);
    later[0] = 0;
}

// A join attempt failed with an error from the host. Ours: banned or another b4bcoop version -> stop auto-join
// (retrying can't help); locked / not a friend -> retry less often.
void chat_on_join_failed(const char *error) {
    const char *e = strstr(error, "Error: '");
    char msg[256];
    snprintf(msg, sizeof msg, "Could not join: %.*s", e ? (int)strcspn(e + 8, "'") : 40, e ? e + 8 : "connection failed");
    if (strstr(error, "same version")) {
        cmds_auto_join_stop();
        if (cmds_session_join()[0]) cmds_set_session_join(NULL);
    } else if (strstr(error, "banned")) cmds_auto_join_stop();
    else if (strstr(error, "locked") || strstr(error, "Steam friends")) cmds_auto_join_backoff(60);
    chat_local_later(msg);
}

#ifndef B4B_RELEASE
// ---- simulated typing (testing, dev builds): real key messages to the game window, one step per tick ----
static HWND game_window(void) {
    HWND w = NULL;
    while ((w = FindWindowExW(NULL, w, L"UnrealWindow", NULL))) {
        DWORD pid = 0;
        GetWindowThreadProcessId(w, &pid);
        if (pid == GetCurrentProcessId() && IsWindowVisible(w)) return w;
    }
    return NULL;
}
static char typing[320];
static int type_pos = -1, type_open_vk = VK_RETURN;
static float type_wait;

static void key(HWND w, int vk) {
    UINT sc = MapVirtualKeyW(vk, 0);
    PostMessageW(w, WM_KEYDOWN, vk, 1 | (sc << 16));
    PostMessageW(w, WM_KEYUP, vk, 1 | (sc << 16) | 0xC0000000u);
}

static UObject *live_chatbox(void);
static void type_tick(float dt) {
    if (type_pos < 0 || (type_wait -= dt) > 0) return;
    HWND w = game_window();
    if (!w) { LOG("chat: type: no game window"); type_pos = -1; return; }
    if (type_pos == 0) {   // open the chat input (unless it is open already: ChatBoxUserWidget +0x498, IsChatVisible)
        UObject *cb = live_chatbox();
        int open = cb && *((uint8_t *)cb + 0x498);
        if (!open && type_open_vk) key(w, type_open_vk);
        type_pos = 1; type_wait = open ? 0 : 0.6f;
        return;
    }
    int i = type_pos - 1;
    if (typing[i]) { PostMessageW(w, WM_CHAR, (unsigned char)typing[i], 1); type_pos++; type_wait = 0.03f; return; }
    key(w, VK_RETURN);   // send
    LOG("chat: type: sent \"%s\" as key presses", typing);
    type_pos = -1;
}
#endif  // !B4B_RELEASE

void chat_tick(float dt) {
#ifndef B4B_RELEASE
    type_tick(dt);
#endif
    later_tick(dt);
    if (leave_pending) { leave_pending = 0; coop_leave(); }
    while (q_head != q_tail) {
        static char line[320];
        static Out out;
        snprintf(line, sizeof line, "%s", queue[q_head]);
        q_head = (q_head + 1) % 8;
        out_reset(&out);
        admin_slash(line, &out);
        if (out.len) chat_local("%s", out.buf);
    }
}

// The live chat box: a ChatBoxUserWidget under the current game instance (the class also has widget-tree templates
// that are not live; calling OnSendMessage on one of those crashes in HideInput).
static UObject *live_chatbox(void) {
    static UClass *cls;
    if (!cls) cls = ue_find_class("ChatBoxUserWidget");
    UObject *w = ue_world(), *gi = w ? ue_get_ptr(w, "OwningGameInstance") : NULL, *best = NULL;
    int32_t n = ue_num_objects();
    for (int32_t i = 0; cls && gi && i < n; i++) {
        UObject *o = ue_object_at(i);
        if (!o || (U_FLAGS(o) & 0x30) || !ue_is_a(o, cls)) continue;
        for (UObject *p = U_OUTER(o); p; p = U_OUTER(p))
            if (p == gi) { best = o; break; }
    }
    return best;
}

#ifndef B4B_RELEASE
// ---- test commands (dev builds) ----
// chat <text>        send <text> as the chat box's Enter does (ChatBoxUserWidget::OnSendMessage)
// chat status        counters, chat box state
// chatshow <text>    print a local chat line
// type <text>        real key presses to the game window: Enter (open chat), the characters, Enter (send)
int chat_cmd(const char *verb, char *rest, Out *o) {
    if (!strcmp(verb, "chatshow")) {
        chat_local("%s", rest ? rest : "(empty)");
        out_printf(o, "shown\n");
        return 1;
    }
    if (!strcmp(verb, "popup")) {   // popup [close [Command]]: list / close open game popups
        char *a = rest ? strtok(rest, " ") : NULL, *c = a ? strtok(NULL, " ") : NULL;
        char path[512];
        for (UObject *p = next_open_popup(NULL); p; p = next_open_popup(p)) {
            out_printf(o, "%p %s\n", (void *)p, ue_full_path(p, path, sizeof path));
            if (!a || strcmp(a, "close")) continue;
            UFunction *f = ue_find_function(U_CLASS(p), "Close");
            static wchar_t wc[64];
            struct { FName cmd; } args = {{0}};
            if (c) {
                int k = 0; for (; c[k] && k < 63; k++) wc[k] = (wchar_t)c[k];
                wc[k] = 0;
                ((FNameCtorFn)ADDR_FNAME_CTOR)(&args.cmd, wc, 1);
            }
            if (f) { ue_process_event(p, f, &args); out_printf(o, "  closed\n"); }
        }
        return 1;
    }
    if (!strcmp(verb, "click")) {   // click <x> <y>: left click at window client coordinates (e.g. a post-round Continue)
        int x = -1, y = -1;
        if (rest) sscanf(rest, "%d %d", &x, &y);
        HWND w = game_window();
        if (!w || x < 0 || y < 0) { out_printf(o, "usage: click <x> <y> (client coordinates; window %p)\n", (void *)w); return 1; }
        LPARAM at = MAKELPARAM(x, y);
        PostMessageW(w, WM_MOUSEMOVE, 0, at);
        PostMessageW(w, WM_LBUTTONDOWN, MK_LBUTTON, at);
        PostMessageW(w, WM_LBUTTONUP, 0, at);
        RECT r = {0};
        GetClientRect(w, &r);
        out_printf(o, "click at %d,%d (client %ldx%ld)\n", x, y, r.right, r.bottom);
        return 1;
    }
    if (!strcmp(verb, "type")) {
        if (!rest || !*rest) { out_printf(o, "usage: type [vk=<code>] <text>\n"); return 1; }
        if (type_pos >= 0) { out_printf(o, "still typing \"%s\"\n", typing); return 1; }
        type_open_vk = VK_RETURN;
        if (!strncmp(rest, "vk=", 3)) { type_open_vk = (int)strtol(rest + 3, &rest, 0); while (*rest == ' ') rest++; }
        snprintf(typing, sizeof typing, "%s", rest);
        type_pos = 0; type_wait = 0;
        out_printf(o, "typing \"%s\" (window %p)\n", typing, (void *)game_window());
        return 1;
    }
    if (strcmp(verb, "chat")) return 0;
    UObject *cb = live_chatbox();
    if (!rest || !*rest || !strcmp(rest, "status")) {
        out_printf(o, "chat: hooks=%d/%d intercepted=%d received=%d chatbox=%p", orig_say && orig_sayteam, orig_ctm != NULL,
                   n_intercepted, n_received, (void *)cb);
        int32_t vo = cb ? ue_prop_offset(cb, "Visibility") : -1;
        if (vo >= 0) out_printf(o, " vis=%d", *((uint8_t *)cb + vo));
        UFunction *f = cb ? ue_find_function(U_CLASS(cb), "IsChatVisible") : NULL;
        if (f) { uint8_t p[16] = {0}; ue_process_event(cb, f, p); out_printf(o, " IsChatVisible=%d", p[0]); }
        out_printf(o, "\n");
        UObject *pc = ue_local_pc();   // who listens to the local PC's OnChatMessageReceived
        int32_t off = pc ? ue_prop_offset(pc, "OnChatMessageReceived") : -1;
        TArray *inv = off >= 0 ? (TArray *)((char *)pc + off) : NULL;
        for (int i = 0; inv && i < inv->num; i++) {
            char *e = (char *)inv->data + i * 16, fn[128], path[512];
            UObject *obj = ue_object_at(*(int32_t *)e);
            ue_name(*(FName *)(e + 8), fn, sizeof fn);
            out_printf(o, "  listener %p %s.%s\n", (void *)obj, obj ? ue_full_path(obj, path, sizeof path) : "-", fn);
        }
        return 1;
    }
    UFunction *f = cb ? ue_find_function(U_CLASS(cb), "OnSendMessage") : NULL;
    FField *pm = f ? ue_find_prop(f, "Message") : NULL;
    if (!pm) { out_printf(o, "no live chat box\n"); return 1; }
    static wchar_t wm[320];
    uint8_t p[64] = {0};
    fstring_set((FString *)(p + FP_OFFSET(pm)), rest, wm, 320);
    LOG("chat: test send via %p OnSendMessage: %s", (void *)cb, rest);
    ue_process_event(cb, f, p);
    out_printf(o, "sent via ChatBoxUserWidget::OnSendMessage: %s\n", rest);
    return 1;
}
#endif  // !B4B_RELEASE

static int hook(uintptr_t at, const uint8_t *sig, size_t n, void *detour, void **orig, const char *what) {
    if (memcmp((void *)at, sig, n)) { LOG("chat: %s signature mismatch", what); return -1; }
    if (MH_CreateHook((void *)at, detour, orig) != MH_OK || MH_EnableHook((void *)at) != MH_OK) {
        LOG("chat: %s hook failed", what); *orig = NULL; return -1;
    }
    return 0;
}

int chat_init(void) {
    hook(ADDR_SAY, SIG_SAY, sizeof SIG_SAY, (void *)say_detour, (void **)&orig_say, "Say");
    hook(ADDR_SAYTEAM, SIG_SAYTEAM, sizeof SIG_SAYTEAM, (void *)sayteam_detour, (void **)&orig_sayteam, "SayTeam");
    hook(ADDR_CTM, SIG_CTM, sizeof SIG_CTM, (void *)ctm_detour, (void **)&orig_ctm, "ClientTeamMessage");
    LOG("chat: commands %s, notices %s", orig_say && orig_sayteam ? "on" : "OFF", orig_ctm ? "on" : "OFF");
    return 0;
}
