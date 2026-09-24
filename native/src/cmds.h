#pragma once
#include <stddef.h>

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
void teamsize_tick(float dt);
int slotguard_init(void);
int slotguard_cmd(const char *verb, char *rest, Out *o);  // game thread; 1 if handled
void slotguard_tick(float dt);
void cmds_auto_join_backoff(double seconds);  // client: the host rejected us as full

// Join / host entry points for other features (Steam invites, chat commands). Any thread (queued to the game thread).
// target: "ip[:port]" (default port 7777) or "steam:<steamid64>[:port]" (Steam P2P). Also used by the `join` command.
void coop_join(const char *target);
void coop_host(void);    // reopen the current map as a listen server on the ini transport (`host` command)

// steamnet.c: Steam P2P transport (docs/investigations/steam-p2p.md)
#include <stdint.h>
uint64_t steamnet_local_id(void);                          // 0 if Steam is not available
int steamnet_available(void);
int steamnet_prepare_host(void);                           // game thread; 1 = Steam driver selected
int steamnet_prepare_url(const char *url);                 // game thread; 1 steam, 0 ip, -1 steam URL but no Steam
int steamnet_parse_target(const char *target, char *url, size_t n);   // 1 steam, 0 ip, -1 malformed
void steamnet_note_join(const char *url);
void steamnet_on_log(const char *cat, const char *msg);    // uelog.c: every engine log line
void steamnet_tick(float dt);
void steamnet_status(Out *o);
int steamnet_cmd(const char *verb, char *rest, Out *o);    // `steamnet [transport ip|steam]`; 1 if handled
void steamnet_config(const char *key, const char *value);  // b4bcoop.ini key (transport=)
