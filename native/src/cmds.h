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
