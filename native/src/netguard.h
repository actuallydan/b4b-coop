#pragma once
// netguard: in-process outbound traffic guard (offline co-op must not reach third-party services).
// See docs/investigations/outbound-traffic.md.
#include "cmds.h"
void netguard_init(void);                   // DllMain (process attach): installs the hooks before any game code runs
void netguard_allow_host(const char *addr); // allow a hostname at runtime (e.g. a `join` target); "host:port" is fine
void netguard_cmd(char *args, Out *o);      // dev builds: `netguard [allow <host>]`, blocked / allowed destinations seen
