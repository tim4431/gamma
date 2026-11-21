#!/usr/bin/env bash
# fetch.sh <dest_dir> <url> [sha256]
# Downloads one file into dest_dir (created if missing), resumes, and checks the sha256 when given.
set -u
dest="$1"; url="$2"; want="${3:-}"
mkdir -p "$dest"
name="$(basename "${url%%\?*}")"
out="$dest/$name"
if [ -f "$out" ] && [ -n "$want" ]; then
  have=$(sha256sum "$out" | cut -d' ' -f1)
  if [ "$have" = "$want" ]; then echo "OK (cached) $out"; exit 0; fi
fi
curl -sSL --retry 5 --retry-delay 3 -C - -o "$out" "$url" || { echo "FAIL curl $url"; exit 1; }
if [ -n "$want" ]; then
  have=$(sha256sum "$out" | cut -d' ' -f1)
  if [ "$have" != "$want" ]; then echo "FAIL sha256 $out have=$have want=$want"; exit 2; fi
fi
echo "OK $out $(stat -c %s "$out")"
