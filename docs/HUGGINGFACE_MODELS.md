# Hugging Face Model Distribution

**Status:** implemented and verified against the live Hub (2026-11-05)
**Code:** `racon/models/huggingface.py`, `racon/models/catalog.py`
**Routes:** `backend/racon/api/routes_models.py` (`/api/v1/models/hf/*`)

---

## 1. Why Hugging Face

RACoN hosts model *metadata* locally and downloads model *artifacts* from Hugging Face over
HTTPS. The Hub is the distribution layer; RACoN never ships weights inside the installer.

A Hugging Face token is **optional**. It is read at request time from `HF_TOKEN`,
`HUGGINGFACE_TOKEN`, or `HUGGING_FACE_HUB_TOKEN` (or `Settings.hf_token`) and is never
written into a catalog, a log, an API response, or the repository. Public repositories work
without a token; gated/private repositories require one with access.

## 2. Built-in hardware-compatible entries

| Catalog id | Hub repository | File | Size | License |
|---|---|---|---|---|
| `SmolLM2-360M-Instruct-Q4_K_M` | `bartowski/SmolLM2-360M-Instruct-GGUF` | `SmolLM2-360M-Instruct-Q4_K_M.gguf` | 270,590,880 B | Apache-2.0 |
| `Qwen2.5-0.5B-Instruct-Q4_K_M` | `bartowski/Qwen2.5-0.5B-Instruct-GGUF` | `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` | 397,808,192 B | See model card |
| `Llama-3.2-1B-Instruct-Q4_K_M` | `bartowski/Llama-3.2-1B-Instruct-GGUF` | `Llama-3.2-1B-Instruct-Q4_K_M.gguf` | 807,694,464 B | Llama 3.2 Community License |

Each entry carries the Hub's LFS SHA-256, so a download cannot be installed unless it
matches. `min_ram_mb` is a declared requirement used for recommendations before install;
after install the real GGUF geometry drives the estimate.

## 3. Integrity guarantees

1. Only `https://huggingface.co/...` URLs are accepted.
2. The SHA-256 comes from the Hub's LFS metadata, not from the client.
3. The model manager downloads to a `.part` file, verifies SHA-256, renames atomically, then
   validates the GGUF header.
4. A Hub file without an LFS SHA-256 is refused (`422`) rather than installed unverified.
5. Repository ids and revisions are pattern-validated before a URL is built, preventing path
   injection.
6. The install route resolves the requested filename against the Hub listing, not against
   client-supplied URL data.

## 4. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/models/hf/files?repo_id=owner/name&revision=main` | List GGUF files with size, SHA-256, and HTTPS URL |
| POST | `/api/v1/models/hf/install` | `{repo_id, filename, revision?, model_id?}` — start a verified install |
| GET | `/api/v1/models/hf/whoami` | Report whether a token is configured (no token returned) |
| POST | `/api/v1/models/pull` | Existing route now routes HF catalog ids to the model manager |
| GET | `/api/v1/models/pull/status?model_name=` | Shared progress for HF and Ollama downloads |
| GET | `/api/v1/models/recommendations` | Includes HF entries with `downloadable=true`, `fits`, `repo_id`, `filename`, `sha256` |

Example:

```powershell
# Discover files
curl "http://127.0.0.1:8765/api/v1/models/hf/files?repo_id=bartowski/Qwen2.5-0.5B-Instruct-GGUF" `
  -H "X-RACON-TOKEN: $env:RACON_AUTH_TOKEN"

# Install the catalog entry (recommended path)
curl -X POST "http://127.0.0.1:8765/api/v1/models/pull" `
  -H "X-RACON-TOKEN: $env:RACON_AUTH_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"model_name":"Qwen2.5-0.5B-Instruct-Q4_K_M"}'
```

## 5. Verified evidence

Executed on 2026-11-05:

```
python scripts/verify_huggingface.py
OK   SmolLM2-360M-Instruct-Q4_K_M: ... 270590880 bytes sha=2fa3f013dcdd7b99…
OK   Qwen2.5-0.5B-Instruct-Q4_K_M: ... 397808192 bytes sha=6eb923e7d26e9cea…
OK   Llama-3.2-1B-Instruct-Q4_K_M: ... 807694464 bytes sha=6f85a640a97cf2bf…
RESULT: PASS

python scripts/verify_huggingface.py --download SmolLM2-360M-Instruct-Q4_K_M
100.0%  258.1 / 258.1 MB
OK   installed SmolLM2-360M-Instruct-Q4_K_M … size=270590880 verified=True
RESULT: PASS
```

The download was 270,590,880 bytes, SHA-256 verified, and accepted only after GGUF header
validation.

## 6. Adding another model

Two supported paths:

1. **Operator catalog** — append a `CatalogEntry` with a `ModelFile(url, sha256, size_bytes)`
   and point `ModelManager(catalog_path=...)` at it. It then appears in recommendations and
   `/api/v1/models/pull`.
2. **Dynamic install** — call `POST /api/v1/models/hf/install` with a repository and filename.
   RACoN reads the Hub listing, takes the SHA-256, and installs it. The entry id defaults to
   the GGUF filename stem, which is also the id the runtime reports after installation.

## 7. Not done

- Uploading weights from RACoN to the Hub. RACoN is a downloader, not a publisher; publishing
  a model requires the operator's explicit license and token confirmation.
- A signed remote catalog. The built-in table is code-reviewed metadata.
- Resumable cross-restart download state beyond the existing `.part` file.
