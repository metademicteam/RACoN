#!/usr/bin/env bash
# Publish RACoN Linux (and optionally Windows) artifacts to the website download folder.
# Writes a unified manifest.json that keeps entries for both platforms.
#
# Usage:
#   ./scripts/publish_website.sh --linux
#   ./scripts/publish_website.sh --windows --linux --version 1.1.0
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/racon/config.py" | head -n 1)"
VERSION="${VERSION:-1.1.0}"
PUBLISH_WINDOWS=0
PUBLISH_LINUX=0

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --windows) PUBLISH_WINDOWS=1; shift ;;
        --linux) PUBLISH_LINUX=1; shift ;;
        -h|--help)
            sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ "$PUBLISH_WINDOWS" -eq 0 ] && [ "$PUBLISH_LINUX" -eq 0 ]; then
    PUBLISH_WINDOWS=1
    PUBLISH_LINUX=1
fi

DOWNLOAD_DIR="$ROOT/dist/website_downloads"
mkdir -p "$DOWNLOAD_DIR"

WIN_SOURCE=""
LINUX_SOURCE=""
DEB_SOURCE=""
if [ "$PUBLISH_WINDOWS" -eq 1 ]; then
    WIN_SOURCE="$ROOT/dist/installer/RACoN_Setup_v${VERSION}.exe"
fi
if [ "$PUBLISH_LINUX" -eq 1 ]; then
    LINUX_SOURCE="$(find "$ROOT/dist/installer" -maxdepth 1 -name "RACoN-linux-*-v${VERSION}.tar.gz" | head -n 1)"
    DEB_SOURCE="$(find "$ROOT/dist/installer" -maxdepth 1 -name "racon_${VERSION}_*.deb" | head -n 1)"
    if [ -z "$LINUX_SOURCE" ]; then
        echo "Linux tarball not found for v$VERSION. Build it with ./scripts/build_linux.sh first." >&2
        exit 1
    fi
fi
if [ "$PUBLISH_WINDOWS" -eq 1 ] && [ ! -f "$WIN_SOURCE" ]; then
    echo "Windows installer not found: $WIN_SOURCE" >&2
    exit 1
fi

python3 - "$DOWNLOAD_DIR" "$VERSION" "$WIN_SOURCE" "$LINUX_SOURCE" "$DEB_SOURCE" <<'PY'
import datetime
import glob
import hashlib
import json
import os
import shutil
import sys

downloads, version, win_src, linux_src, deb_src = sys.argv[1:6]
path = os.path.join(downloads, "manifest.json")

old = {}
if os.path.isfile(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, json.JSONDecodeError):
        old = {}

platforms = old.get("platforms")
if not isinstance(platforms, dict):
    platforms = {}
    if old.get("file"):
        platforms["windows"] = {
            "file": old["file"],
            "size_bytes": old.get("size_bytes"),
            "sha256": old.get("sha256"),
            "built_at": old.get("built_at"),
        }


def digest(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(source, platform, pattern):
    if not source:
        return
    target = os.path.join(downloads, os.path.basename(source))
    shutil.copy2(source, target)
    platforms[platform] = {
        "file": os.path.basename(target),
        "size_bytes": os.path.getsize(target),
        "sha256": digest(target),
        "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    for stale in glob.glob(os.path.join(downloads, pattern)):
        if os.path.abspath(stale) != os.path.abspath(target):
            os.remove(stale)


publish(win_src, "windows", "RACoN_Setup_*.exe")
publish(linux_src, "linux", "RACoN-linux-*.tar.gz")

if deb_src and "linux" in platforms:
    target = os.path.join(downloads, os.path.basename(deb_src))
    shutil.copy2(deb_src, target)
    platforms["linux"]["deb"] = {
        "file": os.path.basename(target),
        "size_bytes": os.path.getsize(target),
        "sha256": digest(target),
    }
    for stale in glob.glob(os.path.join(downloads, "racon_*.deb")):
        if os.path.abspath(stale) != os.path.abspath(target):
            os.remove(stale)

manifest = {
    "product": "RACoN",
    "version": version,
    "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    "platforms": platforms,
}
# Keep top-level fields for clients still running the pre-1.1 site script.
legacy = platforms.get("windows") or platforms.get("linux")
if legacy:
    manifest.update({
        "file": legacy.get("file"),
        "size_bytes": legacy.get("size_bytes"),
        "sha256": legacy.get("sha256"),
    })

with open(path, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")

for name, entry in sorted(platforms.items()):
    print(f"{name:8} {entry['file']}  {entry['size_bytes'] / 1048576:.1f} MB  sha256 {entry['sha256']}")
PY

echo "Published manifest: $DOWNLOAD_DIR/manifest.json"
