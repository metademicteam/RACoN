# RACoN Desktop — Laya Decision Engine Integration

## 1. Overview & Purpose
Laya (from https://github.com/NandhaKishorM/laya) is a multilingual, non-autoregressive System 1 decision engine.
In RACoN, **Laya is used strictly as an intelligent decision advisor** to assist in choosing optimal worker placements from a pre-validated set of feasible candidates.

> **CRITICAL ARCHITECTURAL DISTINCTION**:
> Laya is **not** an LLM inference runtime or chat generator. It does not distribute neural network layers across PCs, nor does it generate text for users.
> Text generation and model execution are handled by dedicated model runtimes (such as Ollama).

## 2. API Contract & Implementation

### 2.1 The Router API
RACoN interfaces with Laya through its documented `Router` interface:
```python
from laya import Router

# Initialize lazily with memory constraint
router = Router(max_loaded=1)

# Format candidates as a typed 'choice' question
questions = {
    "best_worker": {
        "type": "choice",
        "instructions": "Which worker is the best placement target for this workload?",
        "criteria": candidate_criteria_dict,
    }
}

result = router.predict(state_context, questions)
choice = result["answers"]["best_worker"]["choice"]
confidence = result["answers"]["best_worker"]["confidence"]
```

### 2.2 Lifecycle States
1. `UNINITIALIZED`: Default state upon startup. No checkpoints are downloaded or resident in RAM.
2. `LOADING`: Checkpoint download or construction in progress in a non-blocking background worker.
3. `READY`: Router is resident and ready to accept candidate selection requests.
4. `UNAVAILABLE`: Package `laya` is not installed.
5. `FAILED`: Checkpoint initialization failed or experienced an out-of-memory error.

### 2.3 Strict Constraints & Non-Bypassing Rules
- **Hard Constraints are Sovereign**: Laya is never consulted on infeasible candidates. Candidates with insufficient RAM, incompatible device runtimes, or busy/offline status are eliminated deterministically first.
- **Candidate Validation**: The candidate ID returned by Laya must strictly match one of the passed candidate IDs.
- **Fallback**: If Laya is uninitialized, returns an invalid ID, times out, or raises an error, the deterministic scheduler's score is used immediately as the fallback.
- **Shadow Mode**: In Shadow mode, the deterministic choice is executed; Laya's recommendation is recorded alongside it for benchmarking without altering execution.
