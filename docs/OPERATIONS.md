# RACoN Platform — Operations & Coordinator Runbook

**Version:** 1.0  
**Target:** Operator & Coordinator Host  
**Updated:** 2026-10-21  

---

## 1. Coordinator Setup & Prerequisites

### 1.1 Operating Environment
- **Host OS:** Windows 11 (or Windows Server 2022+ / Linux for headless server).
- **Python:** Python 3.12 (in `.venv`).
- **Tailscale:** Version 1.38.3 or higher.

### 1.2 Tailscale Funnel Configuration
1. **Sign in to Tailscale on Coordinator Host:**
   ```powershell
   tailscale login
   ```
2. **Enable Funnel in Tailnet ACL Policy:**
   In the Tailscale Admin Console (Access Controls / ACLs), verify or add:
   ```json
   "nodeAttrs": [
     {"target": ["tag:coordinator"], "app": {"tailscale.com/cap/funnel": [{}]}}
   ]
   ```
3. **Enable HTTPS Certificates:**
   In the Tailscale Admin Console, under **DNS > MagicDNS**, enable HTTPS Certificates.

### 1.3 Windows Power Management (Preventing Sleep)
Coordinator hosts must not sleep or hibernate while serving traffic.
```powershell
# Disable standby sleep while plugged into AC power:
powercfg /change standby-timeout-ac 0

# Disable disk spindown on AC:
powercfg /change disk-timeout-ac 0
```
RACoN's `CoordinatorPowerGuard` monitors this setting and emits a warning if standby timeout is non-zero.

---

## 2. Launching the Coordinator

The coordinator operates dual listeners:
- **Worker Ingress API:** `http://127.0.0.1:8765` (published via Tailscale Funnel).
- **Admin Control API:** `http://127.0.0.1:8766` (loopback only, never published).

### Starting via Python
```powershell
# Run the coordinator server
.\.venv\Scripts\python.exe -m racon.coordinator.server
```

### Enabling Public Funnel Ingress
From the Operator Admin panel or PowerShell:
```powershell
# Enable Funnel mapping port 443 to local worker port 8765:
tailscale funnel 443 on 127.0.0.1:8765

# Check Funnel status:
tailscale funnel status
```

---

## 3. Worker Onboarding & Approval Workflow

1. **Generate an Invite Code:**
   In the Admin UI or via loopback curl:
   ```powershell
   Invoke-RestMethod -Uri "http://127.0.0.1:8766/admin/invites" -Method Post -Body '{"max_uses": 1, "expiry_hours": 24.0}' -ContentType "application/json"
   ```
   *Output:* `{"invite_code": "RCN-A1B2C3D4E5F6", "max_uses": 1, "expiry_hours": 24}`
2. **Share Code Securely:** Provide the single-use invite code to the contributor.
3. **Review & Approve Node:**
   When the contributor enrolls, their node enters `PENDING_APPROVAL`:
   ```powershell
   # List pending nodes:
   Invoke-RestMethod -Uri "http://127.0.0.1:8766/admin/nodes" -Method Get

   # Approve node:
   Invoke-RestMethod -Uri "http://127.0.0.1:8766/admin/nodes/<node_id>/approve" -Method Post
   ```

---

## 4. Emergency Kill Switch & Incident Response

### One-Click Public Access Shutdown
To immediately drop all incoming internet traffic at the Tailscale relay:
```powershell
# Disable Funnel:
tailscale funnel 443 off
```
Or click **"Stop Public Access"** in the operator dashboard.

### Immediate Worker Revocation
If a worker exhibits misbehavior or reports invalid metrics:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8766/admin/nodes/<node_id>/revoke" -Method Post
```
This drops the worker's session token and causes subsequent requests to be rejected.

---

## 5. Database Backup, Compaction & Restore

The coordinator stores all metadata, audit trails, and benchmark logs in SQLite WAL mode.

### Safe Live Backup (Without Stopping Server)
SQLite's WAL mode allows atomic backups while active writes occur:
```powershell
# Perform safe online backup:
sqlite3 "$env:LOCALAPPDATA\RACoN\RACoN Lab\racon_lab.db" ".backup '$env:LOCALAPPDATA\RACoN\racon_backup_$(Get-Date -Format yyyyMMdd).db'"
```

### WAL Checkpoint & Compaction
```powershell
sqlite3 "$env:LOCALAPPDATA\RACoN\RACoN Lab\racon_lab.db" "PRAGMA wal_checkpoint(TRUNCATE); VACUUM;"
```

### Database Restore Procedure
1. Stop the coordinator process.
2. Replace `racon_lab.db` with the verified backup file.
3. Remove any leftover `racon_lab.db-wal` and `racon_lab.db-shm` files.
4. Restart the coordinator. Nodes will re-authenticate cleanly on their next heartbeat.
