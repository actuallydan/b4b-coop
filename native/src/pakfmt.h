// pakfmt: the add-on loader's pak footer and index-hash checks (addons.c, docs/investigations/addons.md §1). Portable C
// with no Windows calls, so native/test/pakfmt_test.c runs the same code on Linux.
// B4B pak footer (model-mods-paks.md §3): Version u32, Magic u32 0x18772, EncryptionKeyGuid[16], bEncryptedIndex u8,
// IndexHash[20], IndexSize u64, IndexOffset u64, [bIndexIsFrozen u8: v9 only], CompressionMethods 5 x char[32].
// v9 = 222 bytes, v8 = 221 (same fields, no bIndexIsFrozen; the engine probes 9..1 by footer size, like here).
#pragma once
#include <stddef.h>
#include <stdint.h>

#define PAK_MAGIC 0x18772u
#define PAK_FOOTER_MAX 222        // v9; read this many bytes (or the whole file if smaller) from the end
#define PAK_MAX_INDEX (64u << 20)

typedef struct {
    uint32_t version;             // 8 or 9
    uint32_t footer_size;         // 221 or 222
    uint64_t index_size, index_offset;
    uint8_t index_sha1[20];
} PakFooter;

void pak_sha1(const uint8_t *p, size_t n, uint8_t out[20]);
// tail = the last tail_len bytes of a file of file_size bytes (tail_len = min(file_size, PAK_FOOTER_MAX)).
// 1 = one of our paks (v9 or v8, B4B magic, plain unfrozen index inside the file); 0 = why says what's wrong.
int pak_footer_parse(const uint8_t *tail, size_t tail_len, uint64_t file_size, PakFooter *f, char *why, size_t why_n);
// 1 = SHA1(index) matches the footer's IndexHash
int pak_index_ok(const uint8_t *idx, size_t n, const PakFooter *f, char *why, size_t why_n);
