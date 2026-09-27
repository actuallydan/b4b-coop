#pragma once
// netguard: in-process outbound traffic guard (offline co-op must not reach third-party services).
// See docs/investigations/outbound-traffic.md.
#include "cmds.h"
void netguard_init(void);                   // DllMain (process attach): installs the hooks before any game code runs
void netguard_allow_host(const char *addr); // allow a hostname at runtime (e.g. a `join` target); "host:port" is fine
// updater.c: open (on=1) / close (0) the scoped exception for GitHub's release hosts (api.github.com, github.com,
// *.githubusercontent.com; dev builds: + extra_host, the update_api= test server) around one player-started request.
void netguard_updater_scope(int on, const char *extra_host);
void netguard_cmd(char *args, Out *o);      // dev builds: `netguard [allow <host>]`, blocked / allowed destinations seen
