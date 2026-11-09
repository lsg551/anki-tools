#!/bin/sh
# Install anki-tools from the latest GitHub release.
set -eu

repo=lsg551/anki-tools
# ANKI_TOOLS_RELEASE_URL overrides the release API URL for tests.
api=${ANKI_TOOLS_RELEASE_URL:-https://api.github.com/repos/$repo/releases/latest}

die() { printf 'install: %s\n' "$*" >&2; exit 1; }

if command -v curl >/dev/null 2>&1; then
  get() { curl -fsSL "$1"; }
  save() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  get() { wget -qO- "$1"; }
  save() { wget -qO "$2" "$1"; }
else
  die "curl or wget is required"
fi

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "unsupported operating system: $(uname -s); Linux and macOS only" ;;
esac

json=$(get "$api") || die "could not fetch the release metadata"
url=$(printf '%s' "$json" | grep -oE 'https?://[^"]*\.whl|file://[^"]*\.whl' | head -n 1 || true)
[ -n "$url" ] || die "no wheel found in the release"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
wheel=${url##*/}
save "$url" "$tmp/$wheel" || die "could not download $wheel"

if command -v gh >/dev/null 2>&1; then
  gh attestation verify "$tmp/$wheel" --repo "$repo" >/dev/null 2>&1 \
    || die "attestation verification failed for $wheel"
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force --python 3.13 "$tmp/$wheel"
  bin_dir=${UV_TOOL_BIN_DIR:-$HOME/.local/bin}
  path_hint="uv tool update-shell"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force --python 3.13 "$tmp/$wheel"
  bin_dir=${PIPX_BIN_DIR:-$HOME/.local/bin}
  path_hint="pipx ensurepath"
elif command -v python3 >/dev/null 2>&1; then
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 13))' \
    || die "python3 is older than 3.13; install uv or Python 3.13+"
  python3 -m pip install --user --force-reinstall "$tmp/$wheel"
  bin_dir=$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts", scheme="posix_user"))')
  path_hint="export PATH=\"$bin_dir:\$PATH\""
else
  die "no installer found; install uv, pipx, or Python 3.13+"
fi

if [ -x "$bin_dir/anki-tools" ]; then
  "$bin_dir/anki-tools" --install-man-pages \
    || printf 'install: could not install the man page; run %s --install-man-pages\n' "$bin_dir/anki-tools" >&2
else
  printf 'install: warning: %s not found; run the man page install later\n' "$bin_dir/anki-tools" >&2
fi

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) printf 'install: add %s to your PATH; run %s\n' "$bin_dir" "$path_hint" >&2 ;;
esac
