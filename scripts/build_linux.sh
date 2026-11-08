#!/usr/bin/env bash
# Build the RACoN Linux/Ubuntu release: PyInstaller bundle -> tarball + .deb.
#
# Run on Ubuntu 22.04/24.04 from the repository root:
#   ./scripts/build_linux.sh
#   RACON_LLAMA_DIR=/opt/racon/llama VERSION=1.1.0 ./scripts/build_linux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="${VERSION:-$(sed -n 's/^VERSION = "\(.*\)"/\1/p' racon/config.py | head -n 1)}"
VERSION="${VERSION:-1.0.2}"
PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$ROOT/.venv-linux}"
SKIP_DEPS="${SKIP_DEPS:-0}"
ARCH="$(uname -m)"
[ "$ARCH" = "x86_64" ] && DEB_ARCH="amd64" || DEB_ARCH="$ARCH"

if [ "$(uname -s)" != "Linux" ]; then
    echo "This script must run on Linux (Ubuntu 22.04/24.04 recommended)." >&2
    exit 1
fi

find_llama_dir() {
    for candidate in \
        "${RACON_LLAMA_DIR:-}" \
        "/opt/racon/llama" \
        "/opt/llama.cpp/build/bin" \
        "$HOME/llama.cpp/build/bin" \
        "$HOME/.local/lib/racon/llama"; do
        if [ -n "$candidate" ] && [ -d "$candidate" ] && { [ -f "$candidate/rpc-server" ] || [ -f "$candidate/llama-server" ]; }; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

LLAMA_DIR="$(find_llama_dir || true)"
if [ -z "$LLAMA_DIR" ]; then
    cat >&2 <<'EOF'
llama.cpp binaries were not found.
Build or download llama.cpp for Linux and point RACON_LLAMA_DIR at the folder containing
rpc-server, llama-server, llama-cli and the libggml*.so files. Example:

  git clone https://github.com/ggml-org/llama.cpp
  cmake -S llama.cpp -B llama.cpp/build -DGGML_RPC=ON -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build llama.cpp/build -j
  RACON_LLAMA_DIR=llama.cpp/build/bin ./scripts/build_linux.sh
EOF
    exit 1
fi

echo "==> RACoN Linux build"
echo "    version: $VERSION"
echo "    llama:   $LLAMA_DIR"

if [ "$SKIP_DEPS" -eq 0 ]; then
    if [ ! -d "$VENV" ]; then
        "$PYTHON" -m venv "$VENV"
    fi
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
    python -m pip install --upgrade pip >/dev/null
    python -m pip install \
        PySide6 psutil platformdirs fastapi uvicorn pydantic pydantic-settings \
        sqlalchemy typer rich httpx cryptography pyinstaller
else
    if [ ! -d "$VENV" ]; then
        echo "VENV=$VENV does not exist. Unset SKIP_DEPS or create the environment first." >&2
        exit 1
    fi
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
fi

echo "==> Building the PyInstaller bundle"
# Use a Linux-only output folder: dist/racon and dist/RACoN are the same directory on a
# case-insensitive Windows mount and would collide with the Windows PyInstaller bundle.
LINUX_DIST="$ROOT/dist/linux-build"
rm -rf "$LINUX_DIST"
RACON_LLAMA_DIR="$LLAMA_DIR" python -m PyInstaller RACoN_Linux.spec \
    --noconfirm --clean \
    --distpath "$LINUX_DIST" \
    --workpath "$ROOT/build/linux"

STAGE_NAME="RACoN-linux-${ARCH}-v${VERSION}"
STAGE="$ROOT/dist/linux/$STAGE_NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "$LINUX_DIST/racon" "$STAGE/racon"
cp "$ROOT/packaging/linux/install.sh" "$STAGE/install.sh"
cp "$ROOT/packaging/linux/racon.desktop" "$STAGE/racon.desktop"
cp "$ROOT/packaging/linux/racon-worker.service" "$STAGE/racon-worker.service"
cp "$ROOT/apps/desktop/icon.png" "$STAGE/icon.png"
cp "$ROOT/LICENSE" "$STAGE/LICENSE"
cp "$ROOT/docs/BUILD_LINUX.md" "$STAGE/README.md"
chmod +x "$STAGE/install.sh" "$STAGE/racon/racon"

mkdir -p "$ROOT/dist/installer"
TARBALL="$ROOT/dist/installer/${STAGE_NAME}.tar.gz"
# Normalize modes on the native filesystem first. Windows drvfs mounts report 777,
# which would otherwise ship world-writable binaries in the release.
TAR_ROOT="${TMPDIR:-/tmp}/racon-release/$STAGE_NAME"
rm -rf "${TMPDIR:-/tmp}/racon-release"
mkdir -p "${TMPDIR:-/tmp}/racon-release"
cp -a "$STAGE" "$TAR_ROOT"
chmod -R u=rwX,go=rX "$TAR_ROOT"
chmod 755 "$TAR_ROOT/install.sh" "$TAR_ROOT/racon/racon"
tar -C "${TMPDIR:-/tmp}/racon-release" -czf "$TARBALL" "$STAGE_NAME"
echo "==> Tarball: $TARBALL"

DEB=""
if command -v dpkg-deb >/dev/null 2>&1; then
    echo "==> Building the .deb package"
    # dpkg-deb rejects world-writable directories. Building on a Windows drvfs mount
    # (/mnt/f, /mnt/c) produces mode 777, so stage the package on the native filesystem.
    PKGROOT="${RACON_DEB_BUILD_DIR:-${TMPDIR:-/tmp}/racon-deb}/racon"
    rm -rf "$PKGROOT"
    mkdir -p "$PKGROOT/DEBIAN" \
             "$PKGROOT/opt/racon" \
             "$PKGROOT/usr/bin" \
             "$PKGROOT/usr/share/applications" \
             "$PKGROOT/usr/share/icons/hicolor/256x256/apps" \
             "$PKGROOT/usr/lib/systemd/user"
    cp -a "$LINUX_DIST/racon/." "$PKGROOT/opt/racon/"
    cp "$ROOT/apps/desktop/icon.png" "$PKGROOT/usr/share/icons/hicolor/256x256/apps/racon.png"
    cp "$ROOT/packaging/linux/racon.desktop" "$PKGROOT/usr/share/applications/racon.desktop"
    sed -i "s|@EXEC@|/usr/bin/racon|g; s|@ICON@|racon|g" "$PKGROOT/usr/share/applications/racon.desktop"
    sed "s|@EXEC@|/usr/bin/racon|g" "$ROOT/packaging/linux/racon-worker.service" \
        > "$PKGROOT/usr/lib/systemd/user/racon-worker.service"

    cat > "$PKGROOT/usr/bin/racon" <<'WRAPPER'
#!/bin/sh
exec /opt/racon/racon "$@"
WRAPPER
    chmod 755 "$PKGROOT/usr/bin/racon"
    chmod -R u=rwX,go=rX "$PKGROOT"
    chmod 755 "$PKGROOT/DEBIAN"

    INSTALLED_SIZE="$(du -sk "$PKGROOT/opt/racon" | cut -f1)"
    cat > "$PKGROOT/DEBIAN/control" <<EOF
Package: racon
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${DEB_ARCH}
Installed-Size: ${INSTALLED_SIZE}
Maintainer: RACoN <racon@metademic.org>
Depends: libc6, libgomp1, libgl1, libegl1, libglib2.0-0, libfontconfig1, libdbus-1-3, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-keysyms1, libxcb-shape0, libxcb-icccm4, libxcb-image0, libxcb-render-util0, libxcb-xinerama0
Recommends: tailscale
Description: Resource-Aware Collaborative Network
 Local-first distributed AI compute client and worker. Joins a RACoN cluster,
 runs the desktop UI, and shares this machine's hardware with Windows and Linux peers.
EOF
    DEB="$ROOT/dist/installer/racon_${VERSION}_${DEB_ARCH}.deb"
    dpkg-deb --build --root-owner-group "$PKGROOT" "$DEB" >/dev/null
    echo "==> Debian package: $DEB"
fi

echo "==> Checksums"
(
    cd "$ROOT/dist/installer"
    sha256sum "$(basename "$TARBALL")" > "${STAGE_NAME}.tar.gz.sha256"
    [ -n "$DEB" ] && sha256sum "$(basename "$DEB")" >> "${STAGE_NAME}.tar.gz.sha256"
)

echo
echo "Build complete:"
echo "  $TARBALL"
[ -n "$DEB" ] && echo "  $DEB"
echo
echo "Publish to the website with:"
echo "  ./scripts/publish_website.sh --version $VERSION --linux"
