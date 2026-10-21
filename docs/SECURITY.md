# RACoN Desktop — Security Architecture & Threat Model

## 1. Threat Model & Security Posture
RACoN Desktop is designed as a **local-first** platform. All sensitive operations, hardware queries, inference executions, and task states remain strictly confined to the local machine unless explicitly configured otherwise.

### 1.1 Local Loopback Isolation
- The internal FastAPI backend binds **exclusively to `127.0.0.1`**.
- Binding to `0.0.0.0` or public network interfaces is forbidden in code.
- Every administrative route under `/api/v1/*` requires an ephemeral authorization token passed via the `X-RACON-TOKEN` header.

### 1.2 Ephemeral Authentication Token
- A cryptographically secure 256-bit token is generated at startup (`secrets.token_urlsafe(32)`).
- The desktop frontend receives this token during startup through a protected environment channel.
- Token comparison uses constant-time validation (`hmac.compare_digest`) to defend against timing attacks.

### 1.3 Subprocess & Command Safety
- Subprocess invocations (such as hardware WMI queries and worker launches) use explicit, non-shell argument arrays with `shell=False`.
- Untrusted user input is never passed directly into command lines or eval statements.

### 1.4 Secret & Token Redaction in Logs
- The structured logging layer filters authorization tokens, user passwords, private keys, and session secrets before writing to disk or streaming to the UI.

### 1.5 Least-Privilege Desktop Shell
- Tauri v2 capability configuration restricts webview permissions to only necessary IPC channels.
- External network requests from the webview are limited by Content Security Policy (`CSP`) to `127.0.0.1`.

### 1.6 External Cloud Adapters
- Optional adapters (e.g. Supabase) operate with minimal publishable privileges.
- Network outages or invalid credentials fail safely without crashing the local compute engine.
