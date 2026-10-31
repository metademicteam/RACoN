# RACoN on Ubuntu / Linux

RACoN runs on Ubuntu (x86_64) as a desktop app or as a headless cluster worker. The PyInstaller
bundle can only run on the glibc version of the machine that built it or newer, so build on the
oldest Ubuntu release you need to support. The published 1.0.6 artifacts were built on Ubuntu
24.04 and require glibc 2.38 or newer.

Windows and Ubuntu nodes use the same coordinator, enrollment flow, protocol and scheduler; a
Linux worker is a normal approved node and its layers are planned by the same head.

## 1. Release artifacts

`scripts/build_linux.sh` produces:

| Artifact | Path | Use |
|---|---|---|
| Portable tarball | `dist/installer/RACoN-linux-x86_64-v<version>.tar.gz` | glibc 2.38+ when built on Ubuntu 24.04. Contains `install.sh`, the PyInstaller bundle, llama.cpp binaries (with `libgomp.so.1`) and the desktop entry. |
| Debian package | `dist/installer/racon_<version>_amd64.deb` | `sudo apt install ./racon_<version>_amd64.deb` installs to `/opt/racon`. |

Both install without root in the tarball case. The tarball installer writes the app to
`~/.local/share/racon-app`, symlinks `~/.local/bin/racon`, and adds a desktop entry.

## 2. Build prerequisites

```bash
sudo apt update
sudo apt install -y build-essential cmake git python3 python3-venv python3-pip python3-dev \
    libgomp1 libgl1 libegl1 libglib2.0-0 libfontconfig1 libdbus-1-3 \
    libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-keysyms1 libxcb-shape0 \
    libxcb-icccm4 libxcb-image0 libxcb-render-util0 libxcb-xinerama0
```

`pyinstaller` and the Python dependencies are installed by the build script into `.venv-linux`.

### llama.cpp

The RPC protocol checks versions between the head and every worker, so build or download **one**
llama.cpp release and use it on all Windows and Ubuntu nodes.

```bash
git clone https://github.com/ggml-org/llama.cpp
cmake -S llama.cpp -B llama.cpp/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON \
  -DGGML_RPC=ON \
  -DGGML_BACKEND_DL=ON \
  -DGGML_CPU_ALL_VARIANTS=ON \
  -DGGML_NATIVE=OFF
cmake --build llama.cpp/build -j"$(nproc)"
```

Do **not** use `GGML_NATIVE=ON` for a release. That compiles for the build machine's CPU and older
workers die with SIGILL (exit `-4`). `GGML_CPU_ALL_VARIANTS` builds baseline x64, SSE4.2, AVX,
AVX2 and AVX-512 backends; llama.cpp selects the best supported one at startup. Add
`-DGGML_VULKAN=ON` only if you also provide `libvulkan.so.1` on the target machines.

The binaries land in `llama.cpp/build/bin` (`rpc-server`, `llama-server`, `llama-cli`,
`llama-gguf-split`). RACoN bundles them inside the release and prepends their folder to
`LD_LIBRARY_PATH` at runtime, so the `.so` files are found without a system install.

To use a prebuilt llama.cpp archive instead, unpack it and point `RACON_LLAMA_DIR` at the folder
that contains `rpc-server` and `llama-server`.

## 3. Build

```bash
cd /path/to/RACoN
RACON_LLAMA_DIR=/path/to/llama.cpp/build/bin ./scripts/build_linux.sh
```

Useful variables:

| Variable | Default | Meaning |
|---|---|---|
| `RACON_LLAMA_DIR` | auto-detected | Folder with llama.cpp binaries and shared libraries. |
| `VERSION` | `racon/config.py` | Release version written into the artifact names. |
| `PYTHON` | `python3` | Interpreter used to create `.venv-linux`. |
| `VENV` | `.venv-linux` | Build virtualenv. |
| `SKIP_DEPS` | `0` | Set to `1` to reuse an existing environment. |

## 4. Install

### Tarball (per-user, no root)

```bash
tar -xzf RACoN-linux-x86_64-v1.0.2.tar.gz
cd RACoN-linux-x86_64-v1.0.2
./install.sh                 # app + desktop entry
./install.sh --autostart     # also join in the tray at login
./install.sh --service       # also run a systemd user worker (headless)
```

`~/.local/bin` must be on `PATH` (it is on Ubuntu by default). Launch with `racon`.

Uninstall while keeping user data:

```bash
./install.sh --uninstall
```

### Debian package

```bash
sudo apt install ./racon_1.0.2_amd64.deb
racon
```

The package installs to `/opt/racon`, adds `/usr/bin/racon`, the desktop entry, the icon and a
systemd user unit. User data stays under `~/.local/share/RACoN`. On a server, enable the worker:

```bash
systemctl --user enable --now racon-worker.service
systemctl --user status racon-worker.service
```

## 5. Join Windows and Ubuntu into one cluster

1. Install Tailscale on every machine and log in to the same tailnet:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   tailscale ip -4
   ```
2. Launch RACoN once on each node. The built-in join flow enrolls the node with the coordinator
   (`RACON_COORDINATOR_URL` overrides it) and the coordinator auto-approves it. Enrollment issues a
   per-node gateway key.
3. The agent binds `rpc-server` to the Tailscale address (LAN address if Tailscale is absent) and
   advertises `<ip>:50052` in its capability report. Linux workers therefore appear in
   `GET /admin/nodes` exactly like Windows workers.
4. Allow the RPC port on Ubuntu:
   ```bash
   sudo ufw allow 50052/tcp
   ```
   `rpc-server` has no authentication, so never bind it to `0.0.0.0`; RACoN refuses to.
5. On the head machine (Windows or Ubuntu), run:
   ```bash
   python -m racon.cluster serve
   ```
   The head reads approved nodes from the coordinator, probes each device with
   `llama-cli --list-devices --rpc …`, and builds `-ts` ratios from measured free memory. Windows
   and Ubuntu workers are interchangeable in that list.

### Headless Ubuntu Server

A machine without a desktop session can contribute compute:

```bash
racon --headless
```

or as a systemd user service:

```bash
systemctl --user enable --now racon-worker.service
systemctl --user status racon-worker.service
```

The headless mode enrolls, starts `rpc-server`, sends heartbeats and executes allow-listed tasks.

## 6. Publishing the Linux download

After building:

```bash
./scripts/publish_website.sh --version 1.0.2 --linux
```

This copies the tarball and `.deb` into `dist/website_downloads`, computes SHA-256, and merges a
`linux` entry into `manifest.json` **without removing the Windows entry**. The site shows both
downloads and recommends the visitor's OS automatically.

Publishing a new Windows installer on the Windows host keeps the Linux entry:

```powershell
.\scripts\publish_website.ps1 -Version 1.0.2
```

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `llama.cpp binaries were not found` | Set `RACON_LLAMA_DIR` to the folder with `rpc-server` and `llama-server`. |
| `rpc-server has no such file or directory` | The bundled `.so` files are missing; rebuild with the complete llama.cpp `bin` folder and install `libgomp1`. |
| Enrollment returns HTTP 502 through the Tailscale URL | The funnel is proxying the wrong port. `tailscale funnel status` must show `127.0.0.1:8765`; fix it with `tailscale funnel --bg 8765`. |
| Node enrolls but the head ignores it | `rpc_endpoint` must be a Tailscale/LAN IP, not loopback. Restart with `RACON_AUTO_CONNECT=1` and a running Tailscale. |
| Head sees no worker memory | Probe output must list a device (`CPU:`, `Vulkan0:`, `RPC0:`). Update to a build with the Linux device parser. |
| RPC connection refused | Open TCP 50052 on the worker (`sudo ufw allow 50052/tcp`) and confirm `tailscale ping <worker>`. |
| Head/worker mismatch | Use the same llama.cpp release on both sides; `rpc-server` rejects different RPC protocol versions. |

## 8. Current limits

- The tarball targets x86_64. ARM64 needs an ARM64 llama.cpp build and a PyInstaller build on that host.
- NVIDIA telemetry uses `nvidia-smi`; AMD/Intel GPU names come from `lspci`, and VRAM/utilization stay
  `Unavailable` rather than being fabricated.
- The desktop bundle requires Qt/X11 or Wayland libraries. Ubuntu Server nodes should use
  `--headless`.
- The `.deb` requires the build host's glibc or newer. The published 1.0.6 package targets
  Ubuntu 24.04+ (glibc 2.38) and also needs `libxcb-cursor0`. Build on Ubuntu 22.04 to ship a
  22.04-compatible release.
- `libgomp.so.1` is bundled next to the llama.cpp binaries, so a stock Ubuntu installation does
  not need the `libgomp1` package. The Qt/X11 packages are still required for the desktop UI.
