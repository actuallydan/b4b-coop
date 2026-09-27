#pragma once
void log_init(void *module);
void logf_(const char *fmt, ...);
void log_redact_addons(char *s);   // cuts an older client's b4bcoopaddons= list out of a line (#35)
#define LOG(...) logf_(__VA_ARGS__)
