#pragma once
#include <stddef.h>
#include <stdint.h>
// PNG/JPEG bytes -> malloc'd RGBA, scaled down to fit maxside x maxside (aspect kept); NULL if not decodable
uint8_t *img_thumb(const uint8_t *data, size_t n, int maxside, int *w, int *h);
