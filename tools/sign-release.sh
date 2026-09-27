#!/usr/bin/env bash
# Release signing for the in-game updater (#34, docs/investigations/updater.md "Signing"). ed25519 with OpenSSL 3;
# the agent checks the signatures with Monocypher against the public key built into it (native/src/updater.c).
#   sign-release.sh manifest <zip> <version> <protocol>   print b4bcoop-update.txt for that zip (launch/package.sh)
#   sign-release.sh sign <private key PEM> <file>...       write <file>.sig (64 raw bytes) next to each (release.yml)
#   sign-release.sh verify <public key: PEM or 64 hex> <file>...   check <file>.sig for each; exit 1 on any failure
#   sign-release.sh pubhex <PEM key, private or public>    the raw public key as 64 hex digits
# The release key lives only in the GitHub Actions secret B4B_RELEASE_SIGNING_KEY (and Dan's offline copy); the public
# half is committed in docs/release-signing.pub.pem and native/src/updater.c.
set -euo pipefail
die() { echo "sign-release.sh: $*" >&2; exit 1; }
cmd=${1:-}; shift || true
pubhex() {   # PEM (private or public) -> 64 hex
  local der
  if grep -q 'PRIVATE KEY' "$1"; then der=$(openssl pkey -in "$1" -pubout -outform DER | od -An -tx1 -v | tr -d ' \n')
  else der=$(openssl pkey -pubin -in "$1" -outform DER | od -An -tx1 -v | tr -d ' \n'); fi
  [[ ${#der} == 88 && $der == 302a300506032b6570032100* ]] || die "$1 is not an ed25519 key"
  echo "${der: -64}"
}
pem_of_hex() {   # 64 hex -> public key PEM file
  [[ $1 =~ ^[0-9a-fA-F]{64}$ ]] || die "not a 64-hex public key: $1"
  printf "$(printf '302a300506032b6570032100%s' "$1" | sed 's/../\\x&/g')" | openssl pkey -pubin -inform DER -out "$2"
}
case "$cmd" in
  manifest)
    [[ $# == 3 ]] || die "usage: manifest <zip> <version> <protocol>"
    zip=$1
    [[ -f $zip ]] || die "no $zip"
    printf 'b4bcoop-update 1\nversion=%s\nprotocol=%s\nzip=%s\nsize=%s\nsha256=%s\n' "$2" "$3" "$(basename "$zip")" \
      "$(stat -c %s "$zip")" "$(sha256sum "$zip" | cut -d' ' -f1)"
    ;;
  sign)
    [[ $# -ge 2 ]] || die "usage: sign <private key PEM> <file>..."
    key=$1; shift
    [[ -f $key ]] || die "no key file $key"
    for f in "$@"; do
      openssl pkeyutl -sign -inkey "$key" -rawin -in "$f" -out "$f.sig"
      [[ $(stat -c %s "$f.sig") == 64 ]] || die "bad signature size for $f"
      echo "signed $f"
    done
    ;;
  verify)
    [[ $# -ge 2 ]] || die "usage: verify <public key PEM or hex> <file>..."
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    if [[ -f $1 ]]; then pem_of_hex "$(pubhex "$1")" "$tmp/pub.pem"; else pem_of_hex "$1" "$tmp/pub.pem"; fi
    shift
    rc=0
    for f in "$@"; do
      if openssl pkeyutl -verify -pubin -inkey "$tmp/pub.pem" -rawin -in "$f" -sigfile "$f.sig" >/dev/null 2>&1
      then echo "ok  $f"; else echo "BAD $f"; rc=1; fi
    done
    exit $rc
    ;;
  pubhex) [[ $# == 1 ]] || die "usage: pubhex <key PEM>"; pubhex "$1" ;;
  *) sed -n '2,9p' "$0" >&2; exit 2 ;;
esac
