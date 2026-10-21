# RACoN — Tailscale Funnel & Production Docker Architecture Guide

This guide explains how to share the RACoN Desktop platform with anyone worldwide using **Tailscale Funnel**, how other users can install and use the desktop app with the **Handy-style Model Hub**, and how the **5-service Docker cluster** is organized.

---

## 1. Global Public Access via Tailscale Funnel

### What Tailscale Funnel Provides
Standard Tailscale is private to devices authorized on your Tailnet. **Tailscale Funnel** exposes your local RACoN Desktop application to the **entire public internet** over secure HTTPS with an automated Let's Encrypt SSL certificate.

Your host machine has an active Funnel domain:
```
Public Internet Domain: https://desktop-b7l73cl.tail6cb521.ts.net
Private Tailscale IPv4: 100.76.208.54
```

### Quick Commands

We provide a dedicated Funnel controller script: [`scripts/tailscale_funnel.bat`](file:///f:/RACoN/scripts/tailscale_funnel.bat) and [`scripts/tailscale_funnel.ps1`](file:///f:/RACoN/scripts/tailscale_funnel.ps1).

| Goal | Command |
| :--- | :--- |
| **Check Funnel Status** | `.\scripts\tailscale_funnel.bat status` |
| **Route Funnel to Desktop UI (Port 1420)** | `.\scripts\tailscale_funnel.bat webapp` |
| **Route Funnel to Coordinator Backend (Port 8765)** | `.\scripts\tailscale_funnel.bat backend` |
| **Turn off Public Funnel** | `.\scripts\tailscale_funnel.bat off` |

When Funnel is active on the webapp, **any person anywhere in the world** can navigate to:
```
https://desktop-b7l73cl.tail6cb521.ts.net
```
and immediately use RACoN Desktop with zero setup!

---

## 2. Handy-Style Model Hub (1-Click Real Model Downloads)

Modeled after the user-friendly **Handy** desktop interface:
- **Title**: *To get started, choose an AI model or download one with a single click*
- **Sections**:
  1. **Compatible & Installed Models**: Lists active local LLMs ready for instant inference and scheduling.
  2. **Available to Download**: Curated compact models specifically optimized for Intel i3 / 16 GB RAM / CPU execution:
     - **Llama 3.2 1B (Instruct)** — 1.3 GB (Meta AI, Accuracy 86%, Speed 96%)
     - **Qwen 2.5 0.5B (Pocket)** — 397 MB (Alibaba Qwen, Accuracy 78%, Speed 99%)
     - **Qwen 2.5 1.5B (Balanced)** — 986 MB (Alibaba Qwen, Accuracy 89%, Speed 92%)
     - **SmolLM2 360M (Instant)** — 229 MB (Hugging Face, Accuracy 72%, Speed 100%)
     - **SmolLM2 1.7B (Instruct)** — 1.0 GB (Hugging Face, Accuracy 88%, Speed 90%)
     - **DeepSeek-R1 Distill 1.5B** — 1.1 GB (DeepSeek AI, Accuracy 92%, Speed 85%)
     - **Phi-3.5 Mini (3.8B)** — 2.2 GB (Microsoft Research, Accuracy 95%, Speed 78%)
  3. **Direct GGUF Import**: Paste any Hugging Face GGUF URL or local file path to import custom weights.

### Real-Time Download Execution
Clicking **⬇ Download** triggers background pulling via Ollama or direct GGUF downloader with live progress updates (`status`, `percentage`, `downloaded MB`, `speed`, and progress bars).

---

## 3. How Other Users Can Install and Use the Desktop App

Other users on Windows can install and start using RACoN in two easy ways:

### Method A: One-Click Desktop Installer
1. Double-click [`install_desktop_app.bat`](file:///f:/RACoN/install_desktop_app.bat) in the project folder.
2. An official **RACoN Desktop** shortcut with application icon is placed directly on their Windows Desktop (`RACoN Desktop.lnk`).
3. Double-clicking the shortcut launches both the backend coordinator and opens the desktop interface.

### Method B: Zero-Install Browser Access
1. If the host machine is running `run.bat` and Tailscale Funnel:
   - Navigate to `https://desktop-b7l73cl.tail6cb521.ts.net` (from anywhere in the world) or `http://100.76.208.54:1420` (within Tailnet).
   - The user can browse models, test scheduling, and trigger compute jobs without downloading anything.

---

## 4. Production 5-Service Docker Architecture

Matching your previous Docker Desktop configuration, [`docker-compose.yml`](file:///f:/RACoN/docker-compose.yml) organizes the platform into 5 modular microservices:

```
                  ┌──────────────────────────────────────────────┐
                  │          Tailscale Funnel Gateway            │
                  │   https://desktop-b7l73cl.tail6cb521.ts.net  │
                  └──────────────────────┬───────────────────────┘
                                         │
        ┌────────────────────────────────┴───────────────────────────────┐
        ▼                                                                ▼
┌───────────────────────────┐                        ┌──────────────────────────────────┐
│        racn-webapp        │                        │         racn-coordinator         │
│  (React UI / Desktop App) │                        │       (FastAPI Core Engine)      │
│     Ports: 3000 / 1420    │                        │      Ports: 8765 / 8443 (SSL)    │
└───────────────────────────┘                        └──────┬────────────┬───────────┬──┘
                                                            │            │           │
                     ┌──────────────────────────────────────┘            │           └──────────────────────────────────────┐
                     ▼                                                   ▼                                                  ▼
      ┌─────────────────────────────┐                     ┌─────────────────────────────┐                    ┌─────────────────────────────┐
      │          racn-llama         │                     │     racn-model-registry     │                    │          racn-redis         │
      │  (llama.cpp CPU Inference)  │                     │   (GGUF Model Storage/API)  │                    │   (Cluster Queue & Cache)   │
      │         Port: 8080          │                     │          Port: 8001         │                    │          Port: 6379         │
      └─────────────────────────────┘                     └─────────────────────────────┘                    └─────────────────────────────┘
```

### Services Overview

| Container Name | Base Image | Ports | Purpose |
| :--- | :--- | :--- | :--- |
| **`racn-coordinator`** | `python:3.12-slim` | `8765:8765`, `8443:8443` | Main coordinator API, Laya advisor, node scheduling |
| **`racn-webapp`** | `node:20-alpine` + `nginx:alpine` | `3000:80`, `1420:80` | Production React dashboard & Handy Model Hub |
| **`racn-llama`** | `python:3.11-slim` | `8080:8080` | Dedicated lightweight llama.cpp CPU inference server |
| **`racn-model-registry`** | `python:3.11-slim` | `8001:8001` | GGUF model file storage, caching, and catalog metadata |
| **`racn-redis`** | `redis:7-alpine` | `6379:6379` | Worker heartbeats, state cache, and job queue |
| **`racn-funnel`** *(profile: funnel)* | `tailscale/tailscale:latest` | — | Secure internet tunneling with automated SSL |

### Starting the Docker Cluster
```bash
# Build and start all 5 services
docker compose up -d --build

# Inspect running containers
docker compose ps

# Check logs
docker compose logs -f racn-coordinator
```
