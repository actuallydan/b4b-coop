// Unit tests for the add-on loader's pak checks (native/src/pakfmt.c), run on Linux by native/test/run.sh (also in CI):
// SHA1 vectors; v9 (222-byte footer) and v8 (221, no bIndexIsFrozen) accepted with the same fields; v7, v10, wrong
// magic, an encrypted or frozen index, a bad index position, a wrong index hash, garbage and short files refused.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pakfmt.h"

static int fails, checks;
#define CHECK(c) do { checks++; if (!(c)) { fails++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); } } while (0)

static void hex(const uint8_t *p, size_t n, char *out) { for (size_t i = 0; i < n; i++) sprintf(out + 2 * i, "%02x", p[i]); }

static void test_sha1(void) {
    uint8_t h[20]; char s[41];
    pak_sha1((const uint8_t *)"", 0, h); hex(h, 20, s); CHECK(!strcmp(s, "da39a3ee5e6b4b0d3255bfef95601890afd80709"));
    pak_sha1((const uint8_t *)"abc", 3, h); hex(h, 20, s); CHECK(!strcmp(s, "a9993e364706816aba3e25717850c26c9cd0d89d"));
    const char *m = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    pak_sha1((const uint8_t *)m, strlen(m), h); hex(h, 20, s); CHECK(!strcmp(s, "84983e441c3bd26ebaae4aa1f95129e5e54670f1"));
}

// A pak like modkit/b4bpak.py writes: <data><index><footer>. footer_size 222 = v9 layout, 221 = v8 layout.
typedef struct { uint8_t *p; size_t n; uint64_t ioff, isz; } Pak;
static Pak make(uint32_t ver, uint32_t magic, int footer_size, uint8_t enc, uint8_t frozen) {
    static const char idx[] = "\x0a\0\0\0../../../\0\0\0\0\0";   // mount "../../../", 0 entries
    Pak k; k.isz = sizeof idx - 1; k.ioff = 400;
    k.n = k.ioff + k.isz + footer_size;
    k.p = calloc(1, k.n);
    for (size_t i = 0; i < k.ioff; i++) k.p[i] = (uint8_t)(i * 13);
    memcpy(k.p + k.ioff, idx, k.isz);
    uint8_t *f = k.p + k.ioff + k.isz;
    memcpy(f, &ver, 4); memcpy(f + 4, &magic, 4);
    f[24] = enc;
    pak_sha1(k.p + k.ioff, k.isz, f + 25);
    memcpy(f + 45, &k.isz, 8); memcpy(f + 53, &k.ioff, 8);
    if (footer_size == 222) f[61] = frozen;
    return k;
}
static int parse(const Pak *k, PakFooter *f, char *why) {
    size_t tl = k->n < PAK_FOOTER_MAX ? k->n : PAK_FOOTER_MAX;
    return pak_footer_parse(k->p + k->n - tl, tl, k->n, f, why, 120);
}

static void test_accept(void) {
    for (int v = 8; v <= 9; v++) {
        Pak k = make(v, PAK_MAGIC, v == 9 ? 222 : 221, 0, 0);
        PakFooter f; char why[120] = "";
        CHECK(parse(&k, &f, why));
        CHECK(f.version == (uint32_t)v && f.footer_size == (v == 9 ? 222u : 221u));
        CHECK(f.index_offset == k.ioff && f.index_size == k.isz);
        CHECK(pak_index_ok(k.p + k.ioff, k.isz, &f, why, sizeof why));
        k.p[k.ioff + 3] ^= 1;                                        // index changed after packing
        CHECK(!pak_index_ok(k.p + k.ioff, k.isz, &f, why, sizeof why) && strstr(why, "checksum wrong"));
        free(k.p);
    }
}

static void refused(uint32_t ver, uint32_t magic, int fs, uint8_t enc, uint8_t frozen, const char *msg) {
    Pak k = make(ver, magic, fs, enc, frozen);
    PakFooter f; char why[120] = "";
    int ok = parse(&k, &f, why);
    CHECK(!ok && strstr(why, msg));
    if (ok || !strstr(why, msg)) printf("  v%u magic %#x footer %d enc %u frozen %u -> \"%s\" (want \"%s\")\n", ver, magic, fs, enc, frozen, why, msg);
    free(k.p);
}

static void test_refuse(void) {
    const char *nb4b = "not a Back 4 Blood add-on pak";
    refused(7, PAK_MAGIC, 221, 0, 0, nb4b);
    refused(7, PAK_MAGIC, 61, 0, 0, nb4b);                          // real v7 footer size (no compression names)
    refused(10, PAK_MAGIC, 222, 0, 0, nb4b);
    refused(11, PAK_MAGIC, 221, 0, 0, nb4b);
    refused(8, PAK_MAGIC, 222, 0, 0, nb4b);                         // v8 must have the v8 footer size
    refused(9, PAK_MAGIC, 221, 0, 0, nb4b);                         // v9 must have the v9 footer size
    refused(9, 0x5A6F12E1u, 222, 0, 0, nb4b);                       // stock UE magic
    refused(8, 0x5A6F12E1u, 221, 0, 0, nb4b);
    refused(9, PAK_MAGIC, 222, 1, 0, "encrypted pak index");
    refused(8, PAK_MAGIC, 221, 1, 0, "encrypted pak index");
    refused(9, PAK_MAGIC, 222, 0, 1, "bad index position");         // frozen index
    // index outside the file / overlapping the footer
    for (int v = 8; v <= 9; v++) {
        Pak k = make(v, PAK_MAGIC, v == 9 ? 222 : 221, 0, 0);
        uint8_t *f = k.p + k.n - (v == 9 ? 222 : 221);
        PakFooter pf; char why[120];
        uint64_t big = k.isz + 1; memcpy(f + 45, &big, 8);
        CHECK(!parse(&k, &pf, why) && strstr(why, "bad index position"));
        uint64_t huge = ~0ull - 5; memcpy(f + 45, &k.isz, 8); memcpy(f + 53, &huge, 8);
        CHECK(!parse(&k, &pf, why) && strstr(why, "bad index position"));
        free(k.p);
    }
    // garbage and short files
    uint8_t g[4096]; for (size_t i = 0; i < sizeof g; i++) g[i] = (uint8_t)(i * 7 + 3);
    PakFooter pf; char why[120];
    CHECK(!pak_footer_parse(g + sizeof g - 222, 222, sizeof g, &pf, why, sizeof why) && strstr(why, nb4b));
    CHECK(!pak_footer_parse(g, 100, 100, &pf, why, sizeof why) && !strcmp(why, "not a pak file"));
    CHECK(!pak_footer_parse(g, 221, 221, &pf, why, sizeof why) && strstr(why, nb4b));
    CHECK(!pak_footer_parse(g, 0, 0, &pf, why, sizeof why) && !strcmp(why, "not a pak file"));
}

int main(void) {
    test_sha1();
    test_accept();
    test_refuse();
    printf("pakfmt: %d/%d checks passed\n", checks - fails, checks);
    return fails != 0;
}
