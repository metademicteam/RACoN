# Model Registry

**Status:** local catalog implemented; hosted remote index not yet implemented
**Code:** `racon/models/catalog.py`, `manager.py`, `estimate.py`

---

## 1. Catalog entry format

```json
{
  "id": "gemma-3-1b-racon",
  "display_name": "Gemma 3 1B (RACoN build)",
  "runtime": "llama_cpp",
  "files": [
    {
      "filename": "model.gguf",
      "url": "https://example.com/model.gguf",
      "sha256": "<64 hex>",
      "size_bytes": 805000000
    }
  ],
  "family": "gemma3",
  "parameter_size": "1B",
  "quantization": "Q4_K_M",
  "context_length": 32768,
  "min_ram_mb": 1100.0,
  "license": "Gemma Terms of Use",
  "description": "",
  "source": "builtin",
  "ollama_tag": null
}
```

`files[0]` is used for download; multi-file entries are not supported yet.

## 2. Built-in entries

| Id | Runtime | Source | Notes |
|---|---|---|---|
| `gemma-3-1b-racon` | `llama_cpp` | local-only | The GGUF shipped in the repository root. Has no URL/SHA, so it cannot be downloaded through the manager. |
| `SmolLM2-360M-Instruct-Q4_K_M` | `llama_cpp` | Hugging Face | 270 MB, Apache-2.0, verified LFS SHA-256. |
| `Qwen2.5-0.5B-Instruct-Q4_K_M` | `llama_cpp` | Hugging Face | 398 MB, verified LFS SHA-256. |
| `Llama-3.2-1B-Instruct-Q4_K_M` | `llama_cpp` | Hugging Face | 808 MB, verified LFS SHA-256. |
| `ollama:qwen2.5:0.5b` | `ollama` | Ollama | Pulled by tag through the local Ollama daemon. |
| `ollama:qwen2.5:1.5b` | `ollama` | Ollama | |
| `ollama:llama3.2:1b` | `ollama` | Ollama | |
| `ollama:smollm2:360m` | `ollama` | Ollama | |

Hugging Face entries are described in [`HUGGINGFACE_MODELS.md`](HUGGINGFACE_MODELS.md).
The UI catalog merges these entries with the Ollama list; entries without a URL are never
presented as downloadable.

An operator catalog can be merged in through `ModelManager(catalog_path=...)`; no
environment variable or HTTP catalog is wired into the product yet.

## 3. Install guarantees

`ModelManager.download()` enforces:

1. HTTPS only — plain HTTP raises `untrusted_model_source`
2. SHA-256 is mandatory unless `allow_unverified=True`
3. free space ≥ file size + 1,024 MB headroom
4. resumable `.part` download with `Range`; server ignoring the range restarts cleanly
5. hash verified **before** the `.part` file is renamed into place
6. GGUF header validated after rename; invalid files are deleted
7. an existing different file with the same name is never overwritten

`install_from_path()` imports a local file (used by the Model Manager UI), validates the
GGUF header, and refuses non-GGUF or missing paths.

## 4. Memory estimate

`estimate_memory(path, context_size, kv_cache_type)` returns:

- `weights_mb` — actual file size
- `kv_cache_mb` — computed from real GGUF geometry (`block_count`, `embedding_length`,
  `attention.head_count`, `attention.head_count_kv`, optional key/value lengths)
- `overhead_mb` — 8 % of weights, explicitly labelled a heuristic
- `confident` and `basis` strings

When geometry is missing, `kv_cache_mb` is `None` and `confident` is `False`. No estimate is
invented.

`estimate_fits(model_path, available_ram_mb, context_size, kv_cache_type, headroom)` exists
and is exported, but **has no production call site**. Hardware-based model recommendation
is therefore **not implemented** (`docs/DEVELOPMENT_ROADMAP.md` Phase 2).

## 5. Shard manifest

Stage-2 layer splitting uses a separate manifest format
(`models/shards/gemma-3-1b/shards_manifest.json`), parsed by `GgufShardManifest` in
`racon/runtime/distributed_gguf.py`:

```json
{
  "model_name": "...", "architecture": "...",
  "total_layers": 26, "total_tensors": 0,
  "original_file_size": 0, "original_sha256": "...",
  "split_strategy": "layer", "hf_repo_id": "...", "created_at": "...",
  "shards": [
    {
      "shard_index": 0, "total_shards": 2,
      "filename": "...", "file_path": "...", "download_url": "...",
      "file_size_bytes": 0, "sha256": "...", "tensor_count": 0,
      "layer_start": 0, "layer_end": 12, "min_ram_mb": 900
    }
  ]
}
```

`select_optimal_shard()` chooses the first shard that fits the measured available RAM;
`ensure_worker_shard()` downloads and verifies it. This is the only real hardware→workload
matching in the product today.

## 6. Planned work

1. Signed, versioned remote catalog (operator JSON over HTTPS) with rollback.
2. Hardware-driven recommendation refinement using measured GPU VRAM when a runtime can
   actually offload layers; until then recommendations use system RAM and say so.
3. Per-worker installed-model inventory reported at enrollment and on change, so the
   scheduler can enforce model fit instead of treating it as unmeasured.
4. Multi-file model support (split GGUFs already exist for the shard path).
5. Optional Supabase registry synchronization. The client exists
   (`racon/cloud/supabase.py`) and reads `models`/`model_endpoints`; the shared registry has
   no `model_versions` rows yet, so remote entries are metadata-only and marked
   `installable: false`.
