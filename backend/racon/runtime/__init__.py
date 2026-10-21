"""RACoN Inference Runtime Package.

Provides runtime abstractions, real Ollama integration, and multi-worker
distributed runtime execution protocols.
"""

from backend.racon.runtime.base import (
    InferenceExecutionError,
    InferenceRuntime,
    ModelNotFoundError,
    RACoNRuntimeError,
    RuntimeUnavailableError,
)
from backend.racon.runtime.distributed import (
    DistributedRuntime,
    DistributedTask,
    ExecutionMode,
    LayerExecutionResult,
    LayerPartition,
    PartitionPlan,
)
from backend.racon.runtime.ollama import OllamaConnectionError, OllamaRuntime

__all__ = [
    "DistributedRuntime",
    "DistributedTask",
    "ExecutionMode",
    "InferenceExecutionError",
    "InferenceRuntime",
    "LayerExecutionResult",
    "LayerPartition",
    "ModelNotFoundError",
    "OllamaConnectionError",
    "OllamaRuntime",
    "PartitionPlan",
    "RACoNRuntimeError",
    "RuntimeUnavailableError",
]
