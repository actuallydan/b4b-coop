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
