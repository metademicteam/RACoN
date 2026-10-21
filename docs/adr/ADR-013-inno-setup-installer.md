# ADR-013: Windows Per-User Installer Packaging via Inno Setup

## Status
Accepted

## Date
2026-10-21

## Context
Section 10 of the RACoN Production Master Plan mandates a per-user Windows desktop installer that:
- Installs without requiring Windows Administrator / UAC elevation (per-user install in `%LOCALAPPDATA%\Programs\RACoN`).
- Requires no external pre-requisites (bundles the PyInstaller standalone distribution).
- Creates Desktop and Start Menu shortcuts.
- Installs cleanly into paths with spaces or Unicode characters.
- Provides a clean uninstaller that removes executables, shortcuts, and startup entries, and optionally offers to delete or preserve local databases and model caches in `%LOCALAPPDATA%\RACoN`.

## Decision
We select **Inno Setup 6** for generating the Windows installer:
1. **Per-User Configuration (`PrivilegesRequired=lowest`):**
   - Installs to `{localappdata}\Programs\RACoN Lab`.
   - Never prompts for UAC elevation during installation.
2. **Standalone One-Folder Source:**
   - Bundles the verified PyInstaller output from `dist\RACoN_Lab\*`.
3. **Clean Uninstallation:**
   - Registers standard Windows "Add or Remove Programs" uninstaller.
   - Cleans up registry and Start Menu shortcuts automatically.

## Consequences
### Positive
- Zero administrator privilege friction for standard Windows users.
- Clean, reliable uninstallation process.
- Mature, scriptable, industry-standard tooling for Windows desktop delivery.

### Negative / Trade-offs
- Building the final `.exe` installer requires Inno Setup Compiler (`iscc.exe`) installed on the build machine.
