#pragma once
#include <stddef.h>
#include "ue.h"

typedef struct { char buf[256 * 1024]; size_t len; } Out;
void out_reset(Out *o);
void out_printf(Out *o, const char *fmt, ...);

void cmds_init(void);
void cmds_run(char *line, Out *out);   // game thread
void cmds_tick(float dt);              // game thread, every engine tick

void game_exec(const char *cmd);        // game thread: run a console command in the current world
int travel_init(void);
int uelog_init(void);
void travel_set_host(const char *addr);
void travel_tick(float dt);
void travel_on_handshake_failed(void);
int cards_init(void);
int rewards_init(void);
int burncards_init(void);
int cmds_auto_host(void);
const char *cmds_config_path(void);    // b4bcoop.ini, or B4B_COOP_CONFIG
int flashlight_init(void);
void flashlight_tick(float dt);
void cmd_flashlight(const char *arg, Out *o);
void testing_tick(float dt);                        // testing.c: unattended sign-in / mission start
int testing_cmd(const char *verb, char *rest, Out *o); // 1 if handled
int teamsize_init(void);
int teamsize_cmd(const char *verb, char *rest, Out *o);  // game thread; 1 if handled
int lineup_init(void);                                   // lineup.c: 5th+ hero in the character lineups (#8)
int lineup_cmd(const char *verb, char *rest, Out *o);    // `lineup [off dx dy | fov deg | apply]`
void teamsize_tick(float dt);
int slotguard_init(void);
int slotguard_cmd(const char *verb, char *rest, Out *o);  // game thread; 1 if handled
void slotguard_tick(float dt);
void cmds_auto_join_backoff(double seconds);  // client: the host rejected us as full
// Join / host entry points (chat /join, Steam invites, CLI join). Any thread: off the game thread the request is
// queued for the next tick. target: "ip[:port]" (default 7777) or "steam:<steamid64>" (Steam P2P, steamnet.c).
void coop_join(const char *target);
void coop_host(void);                  // host the current offline camp (UDP + Steam P2P)
void coop_leave(void);                 // client: disconnect, back to own camp, no auto-rejoin
int slotguard_kick(UObject *pc);       // host: close a remote player's connection
int chat_init(void);
void chat_tick(float dt);
int chat_cmd(const char *verb, char *rest, Out *o);
void chat_local(const char *fmt, ...);  // local-only chat line(s)
#define CHAT_NOTICE_TYPE L"b4bcoop"      // ClientTeamMessage Type of host notices (shown by chat.c on the receiver)
#define CHAT_KICK_TYPE L"b4bcoopkick"    // ... a notice after which the receiving client leaves (kick/ban)
FName chat_notice_type(int kick);
void chat_local_later(const char *text); // show after the next map load (e.g. why a join was refused)
void chat_on_join_failed(const char *error);  // uelog.c: PendingConnectionFailure on this client
void cmds_auto_join_stop(void);        // client: no more ini auto-join attempts this session
int admin_init(void);
void admin_tick(float dt);
int admin_cmd(const char *verb, char *rest, Out *o);
void admin_slash(char *line, Out *o);  // a chat command typed by the local player (without the '/')
void admin_on_initslots(UObject *psm); // teamsize.c: right before APlayerSlotManager::InitSlots
void cmds_set_session_join(const char *targets); // Steam join target(s), comma-separated; overrides host=/join=
const char *cmds_session_join(void);
void cmds_join_now(void);                      // attempt the session target now (leaves the current session)
void testing_arm_signin(void);                 // auto sign-in Offline (testing.c), for a Steam join
int testing_signin_pending(void);
int testing_on_title(void);                    // sign-in screen up (not signed in yet)              // auto sign-in armed and not finished
void presence_init(void);                      // presence.c: Steam rich presence, Join Game, invites
void presence_tick(float dt);
int presence_cmd(const char *verb, char *rest, Out *o);  // game thread; 1 if handled

// steamnet.c: Steam P2P transport (docs/investigations/steam-p2p.md)
#include <stdint.h>
uint64_t steamnet_local_id(void);                          // 0 if Steam is not available
const char *steamnet_last_error(void);                     // why Steam P2P is unavailable (last check)
int steamnet_p2p_on(void);                                 // Steam P2P usable (enabled, Steam up, hooks in)
// "steam:<id64>" -> url "<fake ip>:7777" (1); "host[:port]" -> "host:port" (0); -1 malformed; -2 no Steam P2P here
int steamnet_resolve_target(const char *target, char *url, size_t n);
void steamnet_init(void);                                  // init_thread: ws2_32 hooks, Steam callbacks
void presence_add_callback(void *cb, int id);              // presence.c: register a Steam callback (steamnet.c)
void steamnet_on_log(const char *cat, const char *msg);    // uelog.c: every engine log line
void steamnet_status(Out *o);
int steamnet_cmd(const char *verb, char *rest, Out *o);    // `steamnet [on|off]`; 1 if handled
void steamnet_config(const char *key, const char *value);  // b4bcoop.ini keys steam_p2p=, transport=
