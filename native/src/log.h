#pragma once
void log_init(void *module);
void logf_(const char *fmt, ...);
#define LOG(...) logf_(__VA_ARGS__)
