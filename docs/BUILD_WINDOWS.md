# RACoN Desktop — Windows Build & Application Executable Guide

This guide describes how to run and distribute the native Windows desktop application executable (`RACoN_Desktop.exe`).

---

## 1. Where is the Desktop App Executable?

The native Windows desktop application executable has been compiled and is ready at:

```
F:\RACoN\dist\RACoN_Desktop\RACoN_Desktop.exe
```

A Windows Desktop shortcut has also been installed directly onto your Windows desktop:
```
C:\Users\Haque\Desktop\RACoN Desktop.lnk
```

---

## 2. How to Launch the Desktop Application

### Method 1: Double-Click the Desktop Shortcut
Double-click the **RACoN Desktop** icon on your Windows desktop. It opens directly as an independent native window (not in a web browser).

### Method 2: Run `RACoN_Desktop.exe` Directly
Navigate to `dist\RACoN_Desktop\` and double-click:
```
RACoN_Desktop.exe
```

### Method 3: Using the Project Helper
Double-click [`run.bat`](file:///f:/RACoN/run.bat) in the project folder or in PowerShell:
```powershell
.\run.ps1
```
It automatically detects `dist\RACoN_Desktop\RACoN_Desktop.exe` and launches the native desktop window.

*(If you ever specifically want to preview the app in a web browser tab instead, run: `.\run.ps1 -Mode web`).*

---

## 3. What the Native Desktop App Provides

1. **Independent Native Window**:
   - Custom titlebar: *"RACoN Desktop — Local Distributed AI Compute & Model Hub"*
   - High-resolution RACoN application icon
   - Native minimize, maximize, and close controls
   - No browser URL bar, no browser tabs, no browser dependencies

2. **Automated Background Engine**:
   - Automatically connects to or starts the local RACoN Coordinator backend on port `8765`.
   - Handles clean shutdown when the window is closed.

3. **Handy-Style Model Hub**:
   - 1-click model downloads for **Llama 3.2 1B**, **Qwen 2.5 0.5B/1.5B**, **SmolLM2 360M/1.7B**, **DeepSeek-R1 Distill 1.5B**, and **Phi-3.5 Mini**.
   - Real-time download progress bars with percentage and transfer speed.
   - Live CPU-friendly inference playground.

4. **Global Sharing via Tailscale Funnel**:
   - Shareable with anyone via `https://desktop-b7l73cl.tail6cb521.ts.net`.

---

## 4. Recompiling the Executable

If you modify backend or frontend code and want to recompile the `.exe`:

```powershell
# 1. Rebuild frontend bundle
npm --prefix apps/desktop run build

# 2. Recompile RACoN_Desktop.exe
.\.venv\Scripts\pyinstaller.exe --noconsole --name="RACoN_Desktop" --icon="apps/desktop/icon.ico" desktop_app.py

# 3. Copy dist assets into the executable folder
New-Item -ItemType Directory -Path "dist\RACoN_Desktop\apps\desktop" -Force
Copy-Item -Path "apps\desktop\dist" -Destination "dist\RACoN_Desktop\apps\desktop\dist" -Recurse -Force
Copy-Item -Path "apps\desktop\icon.ico" -Destination "dist\RACoN_Desktop\apps\desktop\icon.ico" -Force
```
