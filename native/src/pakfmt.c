// pakfmt: pak footer + index hash checks for the add-on loader (see pakfmt.h). docs/investigations/addons.md §1.
#include <stdio.h>
#include <string.h>
#include "pakfmt.h"

// SHA1 (the pak footer's index hash)
typedef struct { uint32_t h[5]; uint64_t len; uint8_t buf[64]; size_t n; } Sha1;
#define ROL(x, k) (((x) << (k)) | ((x) >> (32 - (k))))
static void sha1_block(Sha1 *s, const uint8_t *p) {
    uint32_t w[80], a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3], e = s->h[4];
    for (int i = 0; i < 16; i++) w[i] = (uint32_t)p[4 * i] << 24 | p[4 * i + 1] << 16 | p[4 * i + 2] << 8 | p[4 * i + 3];
    for (int i = 16; i < 80; i++) w[i] = ROL(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; }
        else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
        else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
        else { f = b ^ c ^ d; k = 0xCA62C1D6; }
        uint32_t t = ROL(a, 5) + f + e + k + w[i];
        e = d; d = c; c = ROL(b, 30); b = a; a = t;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e;
}
void pak_sha1(const uint8_t *p, size_t n, uint8_t out[20]) {
    Sha1 s = {{0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0}, 0, {0}, 0};
    s.len = (uint64_t)n * 8;
    for (; n >= 64; n -= 64, p += 64) sha1_block(&s, p);
    uint8_t t[128] = {0};
    memcpy(t, p, n);
    t[n] = 0x80;
    size_t tl = n + 9 <= 64 ? 64 : 128;
    for (int i = 0; i < 8; i++) t[tl - 1 - i] = (uint8_t)(s.len >> (8 * i));
    for (size_t o = 0; o < tl; o += 64) sha1_block(&s, t + o);
    for (int i = 0; i < 20; i++) out[i] = (uint8_t)(s.h[i / 4] >> (24 - 8 * (i % 4)));
}

static uint32_t le32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint64_t le64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }

int pak_footer_parse(const uint8_t *tail, size_t tail_len, uint64_t file_size, PakFooter *f, char *why, size_t why_n) {
    memset(f, 0, sizeof *f);
    // Probe v9 (222 bytes) then v8 (221), like FPakFile::Initialize: the first size whose magic matches wins.
    const uint8_t *ft = NULL; uint32_t ver = 0, magic = 0;
    for (uint32_t fs = 222; fs >= 221; fs--) {
        if (tail_len < fs || file_size < fs) continue;
        const uint8_t *p = tail + tail_len - fs;
        ver = le32(p); magic = le32(p + 4);
        if (magic == PAK_MAGIC) { ft = p; f->footer_size = fs; break; }
    }
    if (!ft) {
        if (tail_len < 221) snprintf(why, why_n, "not a pak file");
        else {
            const uint8_t *p = tail + tail_len - (tail_len >= 222 ? 222 : 221);
            snprintf(why, why_n, "damaged, or not a Back 4 Blood add-on pak (magic %#x v%u)", le32(p + 4), le32(p));
        }
        return 0;
    }
    if (ver != (f->footer_size == 222 ? 9u : 8u)) {
        snprintf(why, why_n, "damaged, or not a Back 4 Blood add-on pak (magic %#x v%u)", magic, ver); return 0;
    }
    f->version = ver;
    if (ft[24]) { snprintf(why, why_n, "encrypted pak index: not made with modkit/addon.py"); return 0; }
    memcpy(f->index_sha1, ft + 25, 20);
    f->index_size = le64(ft + 45); f->index_offset = le64(ft + 53);
    int frozen = ver >= 9 && ft[61];
    uint64_t end = file_size - f->footer_size;
    if (frozen || f->index_size > PAK_MAX_INDEX || f->index_offset > end || f->index_size > end - f->index_offset) {
        snprintf(why, why_n, "damaged pak (bad index position)"); return 0;
    }
    return 1;
}

int pak_index_ok(const uint8_t *idx, size_t n, const PakFooter *f, char *why, size_t why_n) {
    uint8_t h[20];
    pak_sha1(idx, n, h);
    if (n != f->index_size || memcmp(h, f->index_sha1, 20)) {
        snprintf(why, why_n, "damaged pak (index checksum wrong): download it again"); return 0;
    }
    return 1;
}
