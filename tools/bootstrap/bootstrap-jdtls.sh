#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${ROOT}/.jdtls"
URL_DEFAULT="https://download.eclipse.org/jdtls/snapshots/jdt-language-server-latest.tar.gz"

force=0
url="$URL_DEFAULT"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--force] [--url URL]

Downloads and extracts Eclipse JDTLS into:
  ${DEST}

Idempotent: if a launcher jar already exists under ${DEST}/plugins, it does nothing.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) force=1; shift ;;
    --url) url="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ $force -eq 0 ]] && compgen -G "${DEST}/plugins/org.eclipse.equinox.launcher_*.jar" > /dev/null; then
  echo "JDTLS already present at ${DEST}"
  exit 0
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

archive="${tmp_dir}/jdtls.tar.gz"
echo "Downloading JDTLS: ${url}"
curl -fSL --retry 6 --retry-delay 2 --retry-all-errors "$url" -o "$archive"

rm -rf "$DEST"
mkdir -p "$DEST"
tar -xzf "$archive" -C "$DEST"

if ! compgen -G "${DEST}/plugins/org.eclipse.equinox.launcher_*.jar" > /dev/null; then
  echo "JDTLS install looks wrong: missing launcher jar under ${DEST}/plugins" >&2
  exit 1
fi

echo "Installed JDTLS into ${DEST}"
