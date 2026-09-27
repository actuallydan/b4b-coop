// Thumbnails for the add-on shop (#36, docs/investigations/shop.md): PNG/JPEG decoding with stb_image (public domain,
// pinned in tools/fetch-deps.sh), then a box downscale. Only bytes whose SHA-256 the signed catalog lists reach this
// code (shop.c checks first), and stb_image runs with PNG + JPEG only and a 2048-pixel limit per side.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_PNG
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STBI_NO_LINEAR
#define STBI_NO_HDR
#define STBI_MAX_DIMENSIONS 2048
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#pragma GCC diagnostic ignored "-Wunused-function"
#include "stb_image.h"
#pragma GCC diagnostic pop
#include "imgdecode.h"

uint8_t *img_thumb(const uint8_t *data, size_t n, int maxside, int *ow, int *oh) {
    int w, h, c;
    if (!data || n < 8 || n > (64u << 20) || maxside < 1) return NULL;
    uint8_t *src = stbi_load_from_memory(data, (int)n, &w, &h, &c, 4);
    if (!src) return NULL;
    int tw = w, th = h;
    if (w > maxside || h > maxside) {
        if (w >= h) { tw = maxside; th = (int)((int64_t)h * maxside / w); }
        else { th = maxside; tw = (int)((int64_t)w * maxside / h); }
        if (tw < 1) tw = 1;
        if (th < 1) th = 1;
    }
    uint8_t *dst = malloc((size_t)tw * th * 4);
    if (!dst) { stbi_image_free(src); return NULL; }
    for (int y = 0; y < th; y++) {   // box filter: average every source pixel that falls into the target pixel
        int y0 = (int)((int64_t)y * h / th), y1 = (int)((int64_t)(y + 1) * h / th);
        if (y1 <= y0) y1 = y0 + 1;
        for (int x = 0; x < tw; x++) {
            int x0 = (int)((int64_t)x * w / tw), x1 = (int)((int64_t)(x + 1) * w / tw);
            if (x1 <= x0) x1 = x0 + 1;
            uint32_t acc[4] = {0}, cnt = 0;
            for (int yy = y0; yy < y1; yy++)
                for (int xx = x0; xx < x1; xx++) {
                    const uint8_t *p = src + ((size_t)yy * w + xx) * 4;
                    acc[0] += p[0]; acc[1] += p[1]; acc[2] += p[2]; acc[3] += p[3]; cnt++;
                }
            uint8_t *q = dst + ((size_t)y * tw + x) * 4;
            for (int k = 0; k < 4; k++) q[k] = (uint8_t)(acc[k] / cnt);
        }
    }
    stbi_image_free(src);
    *ow = tw; *oh = th;
    return dst;
}
