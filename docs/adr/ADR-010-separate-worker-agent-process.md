# ADR-010: Decoupled Worker Agent Process with Authenticated Loopback IPC

## Status
Accepted

## Date
2026-10-21

## Context
In a desktop computing environment:
- Users open and close windows frequently.
- UI rendering crashes, GUI freezes, or desktop session locks must not abort long-running computational jobs or drop network heartbeats.
- The worker agent may run as a background service or tray worker process while the main UI window is closed.
- Embedding long-running network connections and runtime subprocesses directly in the PySide6 UI thread or Qt event loop risks GUI freezing, deadlock, and complex process teardown.

## Decision
We decouple the **RACoN Desktop UI** and the **RACoN Agent** into separate processes communicating via authenticated loopback IPC (Decision D6):
1. **RACoN Agent (Worker Process):**
   - Independent background process running as standard user (no admin privileges needed).
   - Manages coordinator outbound HTTPS/WSS communication, heartbeat timer, local runtime execution (e.g. Ollama), resource quota enforcement, and task lifecycle.
   - Continues running even when the UI window is minimized or closed (if configured by user).
2. **RACoN Desktop (GUI Process):**
   - Native PySide6 user interface.
   - Communicates with the local Agent via loopback IPC (`127.0.0.1:<agent-ipc-port>` or named pipe) protected by an ephemeral session token generated on agent launch.
   - Allows the user to configure resource ceilings, view live hardware and network metrics, inspect task queues, and pause or stop participation.

## Consequences
### Positive
- Crash isolation: A GUI crash or UI thread hang does not crash the running inference task or drop cluster connection.
- Clean lifecycle: The agent can run headless or be cleanly stopped without terminating UI settings.

### Negative / Trade-offs
- Requires managing an inter-process communication protocol between UI and local agent on the contributor machine.
