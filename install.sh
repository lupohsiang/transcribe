#!/bin/sh
set -eu
LC_ALL=C
export LC_ALL

# Installs release files only. Model downloads happen when you run transcribe setup.
repo=lupohsiang/transcribe
asset=transcribe-darwin-arm64.tar.gz
fail() { printf 'transcribe: %s\n' "$*" >&2; exit 1; }
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || fail 'requires an Apple Silicon Mac'
[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 14 ] || fail 'requires macOS 14 or newer'
install_root=${TRANSCRIBE_INSTALL_ROOT:-"$HOME/.local/share/transcribe/releases"}
bin_dir=${TRANSCRIBE_BIN_DIR:-"$HOME/.local/bin"}
case "$install_root:$bin_dir" in /*:/*) ;; *) fail 'install directories must be absolute paths' ;; esac
mkdir -p "$install_root" "$bin_dir"
work=$(mktemp -d "$install_root/.install.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -n "${TRANSCRIBE_ARCHIVE:-}" ]; then
  [ -n "${TRANSCRIBE_CHECKSUMS:-}" ] || fail 'TRANSCRIBE_CHECKSUMS is required with TRANSCRIBE_ARCHIVE'
  cp "$TRANSCRIBE_ARCHIVE" "$work/$asset"
  cp "$TRANSCRIBE_CHECKSUMS" "$work/SHA256SUMS"
elif command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  if [ -n "${TRANSCRIBE_VERSION:-}" ]; then
    gh release download "$TRANSCRIBE_VERSION" --repo "$repo" --pattern "$asset" --pattern SHA256SUMS --dir "$work"
  else
    gh release download --repo "$repo" --pattern "$asset" --pattern SHA256SUMS --dir "$work"
  fi
else
  # Anonymous downloads work only if the repository is public.
  release=latest/download
  if [ -n "${TRANSCRIBE_VERSION:-}" ]; then
    case "$TRANSCRIBE_VERSION" in *[!A-Za-z0-9._-]*) fail 'invalid release version' ;; esac
    release="download/$TRANSCRIBE_VERSION"
  fi
  base="https://github.com/$repo/releases/$release"
  curl -fsSL "$base/$asset" -o "$work/$asset" || fail 'download failed; private repositories require gh auth login'
  curl -fsSL "$base/SHA256SUMS" -o "$work/SHA256SUMS"
fi
expected=$(awk -v asset="$asset" '$2 == asset { print $1 }' "$work/SHA256SUMS")
[ "${#expected}" -eq 64 ] || fail 'missing or invalid SHA256SUMS entry'
case "$expected" in *[!a-f0-9]*) fail 'invalid SHA-256 checksum' ;; esac
actual=$(shasum -a 256 "$work/$asset" | awk '{print $1}')
[ "$actual" = "$expected" ] || fail 'archive checksum mismatch'

# Extract only the five expected regular files; reject links and extra archive paths.
tar -tzf "$work/$asset" > "$work/entries"
[ "$(wc -l < "$work/entries" | tr -d ' ')" -eq 5 ] || fail 'unexpected archive contents'
while IFS= read -r entry; do
  case "$entry" in transcribe|python/worker.py|model-pins.json|pyproject.toml|uv.lock) ;; *) fail "unexpected archive entry: $entry" ;; esac
done < "$work/entries"
tar -tvzf "$work/$asset" | awk 'substr($1,1,1) != "-" { bad=1 } END {exit bad}' || fail 'archive contains non-regular files'
mkdir "$work/bundle"
tar -xzf "$work/$asset" -C "$work/bundle"
for entry in transcribe python/worker.py model-pins.json pyproject.toml uv.lock; do
  [ -f "$work/bundle/$entry" ] || fail "archive is missing $entry"
done
chmod 755 "$work/bundle/transcribe"
# Content-addressed versions keep old installs intact and make upgrades reversible.
destination="$install_root/$expected"
if [ ! -d "$destination" ]; then mv "$work/bundle" "$destination"; fi
if [ -e "$bin_dir/transcribe" ] || [ -L "$bin_dir/transcribe" ]; then
  [ -L "$bin_dir/transcribe" ] || fail "$bin_dir/transcribe already exists and is not an installer symlink"
  case "$(readlink "$bin_dir/transcribe")" in "$install_root/"*/transcribe) ;; *) fail 'refusing to replace an unrelated symlink' ;; esac
fi
ln -s "$destination/transcribe" "$work/transcribe-link"
mv -f "$work/transcribe-link" "$bin_dir/transcribe"
printf 'Installed %s\n' "$bin_dir/transcribe"
case ":$PATH:" in *":$bin_dir:"*) ;; *) printf 'Add to your shell profile: export PATH="%s:$PATH"\n' "$bin_dir" ;; esac
printf 'Next: brew install uv ffmpeg && transcribe setup && transcribe doctor\n'
