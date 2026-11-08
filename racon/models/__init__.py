"""Local model catalog, storage and estimation."""

from racon.models.catalog import (
    BUILTIN_MODELS,
    BUILTIN_OLLAMA_TAGS,
    LOCAL_GEMMA_FILENAME,
    CatalogEntry,
    ModelFile,
    build_catalog,
    find_entry,
    load_operator_catalog,
    ollama_entries,
)
from racon.models.estimate import estimate_fits, estimate_memory
from racon.models.huggingface import (
    HfFile,
    HuggingFaceClient,
    HuggingFaceError,
    build_entry,
    huggingface_token,
)
from racon.models.manager import (
    InsufficientDiskSpaceError,
    InstalledModel,
    InstallResult,
    ModelManager,
    ModelStorageError,
    ModelVerificationError,
    UntrustedSourceError,
)

__all__ = [
    "BUILTIN_MODELS",
    "BUILTIN_OLLAMA_TAGS",
    "LOCAL_GEMMA_FILENAME",
    "CatalogEntry",
    "ModelFile",
    "build_catalog",
    "find_entry",
    "load_operator_catalog",
    "ollama_entries",
    "estimate_memory",
    "estimate_fits",
    "HfFile",
    "HuggingFaceClient",
    "HuggingFaceError",
    "build_entry",
    "huggingface_token",
    "ModelManager",
    "InstalledModel",
    "InstallResult",
    "ModelStorageError",
    "InsufficientDiskSpaceError",
    "ModelVerificationError",
    "UntrustedSourceError",
]
