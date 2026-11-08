"""RACoN Runtime module for local, Ollama, and distributed RPC/GGUF model execution."""
from racon.runtime.base import InferenceRuntime
from racon.runtime.distributed_gguf import (
    GgufShardManifest,
    ShardInfo,
    compute_file_sha256,
    find_llama_tool,
    inspect_gguf_metadata,
    split_gguf_model,
    verify_shard,
)
from racon.runtime.llamacpp import LlamaCppRuntime, build_server_command, describe_exit
from racon.runtime.ollama import OllamaRuntime
from racon.runtime.registry import (
    LLAMA_CPP,
    OLLAMA,
    SUPPORTED_RUNTIMES,
    available_runtimes,
    create_runtime,
    runtime_capabilities,
)
from racon.runtime.rpc_worker import (
    DistributedInferenceResult,
    DistributedPipelineEngine,
    RpcServerProcess,
)
from racon.runtime.types import (
    ChatChunk,
    ChatMessage,
    ChatResult,
    ChatUsage,
    GenerationOptions,
    InferenceCancelledError,
    InferenceExecutionError,
    ModelInfo,
    ModelNotFoundError,
    ModelNotLoadedError,
    RaconRuntimeError,
    RuntimeCompatibilityError,
    RuntimeHealth,
    RuntimeUnavailableError,
)

__all__ = [
    # Stage 2 distributed RPC
    "GgufShardManifest",
    "ShardInfo",
    "compute_file_sha256",
    "find_llama_tool",
    "inspect_gguf_metadata",
    "split_gguf_model",
    "verify_shard",
    "DistributedInferenceResult",
    "DistributedPipelineEngine",
    "RpcServerProcess",
    # Unified runtime adapters
    "InferenceRuntime",
    "LlamaCppRuntime",
    "OllamaRuntime",
    "build_server_command",
    "describe_exit",
    "create_runtime",
    "available_runtimes",
    "runtime_capabilities",
    "LLAMA_CPP",
    "OLLAMA",
    "SUPPORTED_RUNTIMES",
    # Types and errors
    "ChatChunk",
    "ChatMessage",
    "ChatResult",
    "ChatUsage",
    "GenerationOptions",
    "ModelInfo",
    "RuntimeHealth",
    "RaconRuntimeError",
    "RuntimeUnavailableError",
    "ModelNotFoundError",
    "ModelNotLoadedError",
    "InferenceExecutionError",
    "InferenceCancelledError",
    "RuntimeCompatibilityError",
]
