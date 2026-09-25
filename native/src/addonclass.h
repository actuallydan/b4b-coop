// addonclass.c: cosmetic vs. gameplay-affecting add-on content (#22). modkit/addon.py mirrors the rules.
#pragma once
#include <stddef.h>
#include <stdint.h>

enum { AK_TEXTURES = 1, AK_MATERIALS = 2, AK_MESHES = 4, AK_SOUNDS = 8, AK_UI = 16, AK_EFFECTS = 32, AK_ANIMATIONS = 64 };
typedef struct {
    int gameplay;       // 1: at least one file affects gameplay
    int n_gameplay;     // how many files do
    unsigned kinds;     // AK_* of the cosmetic files
    char reason[160];   // first gameplay file: "<why>: <file name>"
} AddonClass;

void addonclass_begin(AddonClass *c);
void addonclass_file(AddonClass *c, const char *key, const char *path, int has_uasset, const uint8_t *data, size_t n);
const char *addonclass_kinds(unsigned kinds, char *buf, size_t n);   // "textures, meshes"
