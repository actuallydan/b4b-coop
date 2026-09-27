// updcore: the in-game updater's checks and file formats (#34, docs/investigations/updater.md). Portable C with no
// Windows calls, so native/test/updcore_test.c runs the same code on Linux: SHA-256, the ed25519 release signature
// (Monocypher), the signed update manifest, the zip reader (zlib's puff for deflate), the GitHub release JSON and
// version comparison. updater.c does the downloads, files and the ~ window.
#pragma once
#include <stddef.h>
#include <stdint.h>

#define UPD_MAX_ZIP (64u << 20)       // largest release zip the updater accepts (0.6.1: 0.7 MB)
#define UPD_MAX_FILE (32u << 20)      // largest single file inside it

void upd_sha256(const uint8_t *p, size_t n, uint8_t out[32]);
int upd_hex_decode(const char *hex, uint8_t *out, size_t n);   // exactly 2n hex digits -> 1
void upd_hex(const uint8_t *p, size_t n, char *out);           // out: 2n+1 chars
// 1 = sig is a valid ed25519 signature of msg by pub
int upd_sig_ok(const uint8_t *msg, size_t n, const uint8_t *sig, size_t sig_len, const uint8_t pub[32]);

// b4bcoop-update.txt, the release's update manifest (launch/package.sh writes it, release.yml signs it):
//   b4bcoop-update 1
//   version=0.6.2
//   protocol=2
//   zip=b4bcoop-0.6.2.zip
//   size=685901
//   sha256=<64 hex digits>
typedef struct { char version[40]; int protocol; char zip[96]; uint64_t size; uint8_t sha256[32]; } UpdManifest;
// 1 = sig verifies over txt and every field is present and well formed
int upd_manifest_verify(const char *txt, size_t n, const uint8_t *sig, size_t sig_len, const uint8_t pub[32],
                        UpdManifest *m, char *err, size_t en);
int upd_manifest_parse(const char *txt, size_t n, UpdManifest *m, char *err, size_t en);   // no signature check
// The downloaded zip against a verified manifest: size, SHA-256, and its own signature (b4bcoop-<v>.zip.sig). 1 = ok
int upd_zip_verify(const uint8_t *zip, size_t n, const uint8_t *sig, size_t sig_len, const UpdManifest *m,
                   const uint8_t pub[32], char *err, size_t en);

// Zip entries (stored or deflate; no zip64, no encryption), CRC-checked. fn returns 0 to go on, else stops with it.
typedef int (*UpdZipFn)(const char *name, const uint8_t *data, size_t n, void *ctx);
int upd_zip_each(const uint8_t *zip, size_t n, UpdZipFn fn, void *ctx, char *err, size_t en);   // 0 ok, <0 error
// The zip paths the updater installs: xinput1_3.dll and b4bcoop-*.txt in the game root, the agent
// Gobi/Binaries/Win64/X3DAudio1_7.dll. Never b4bcoop.ini (the player's settings), bans, add-ons or logs.
int upd_install_path(const char *name);

// GitHub release JSON (api.github.com/repos/<o>/<r>/releases/latest or /releases/tags/<tag>)
typedef struct { char name[96]; char url[600]; uint64_t size; } UpdAsset;
typedef struct {
    char tag[48], title[120], page[300];
    char body[4096];   // release notes (truncated)
    UpdAsset assets[16]; int n_assets;
} UpdRelease;
int upd_release_parse(const char *json, size_t n, UpdRelease *r, char *err, size_t en);   // 1 = ok
const UpdAsset *upd_release_asset(const UpdRelease *r, const char *name);

// Semantic versions "1.2.3" / "1.2.3-test.1": <0, 0, >0. Unparsable sorts lowest.
int upd_version_cmp(const char *a, const char *b);
int upd_version_valid(const char *v);
