#!/usr/bin/env bash
set -euo pipefail

if command -v gh >/dev/null 2>&1; then
  exit 0
fi

case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) echo 'Unsupported runner architecture' >&2; exit 1 ;;
esac
version=2.74.0
download_dir=$(mktemp -d)
trap 'rm -rf "$download_dir"' EXIT
# Retry transient release-asset failures before extracting anything. A failed
# download must never leave a partial executable in the runner's PATH.
curl --fail --show-error --location --retry 5 --retry-all-errors \
  --retry-delay 2 --retry-max-time 180 --connect-timeout 20 --max-time 60 \
  --output "$download_dir/gh.tar.gz" \
  "https://github.com/cli/cli/releases/download/v${version}/gh_${version}_linux_${arch}.tar.gz"
tar xzf "$download_dir/gh.tar.gz" -C "$download_dir"
"$download_dir/gh_${version}_linux_${arch}/bin/gh" --version
mkdir -p "$HOME/.local/bin"
install -m 755 "$download_dir/gh_${version}_linux_${arch}/bin/gh" "$HOME/.local/bin/gh"
echo "$HOME/.local/bin" >> "$GITHUB_PATH"
