"""Deterministic smart model router.

This ports the useful, testable part of Mesh-LLM's router to RACoN's Python control
plane: classify an OpenAI-style request from its text/structure, then pick a model that
can satisfy its hard capability requirements. No model call is used to route, so the
decision is fast, reproducible and auditable.

The router never invents a capability. Model capabilities are inferred from names and
catalog metadata and are recorded on the decision. An explicit model request is always
honoured; only ``auto`` is routed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional, Sequence


class Category(str, Enum):
    CODE = "code"
    REASONING = "reasoning"
    CHAT = "chat"
    TOOL_CALL = "tool_call"
    CREATIVE = "creative"
    INFO = "info"
    IMAGE = "image"


class Complexity(str, Enum):
    QUICK = "quick"
    MODERATE = "moderate"
    DEEP = "deep"


AUTO_MODELS = frozenset({"auto"})


@dataclass(frozen=True)
class Classification:
    category: Category
    complexity: Complexity
    needs_tools: bool
    has_media_inputs: bool
    signals: dict[str, int] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "complexity": self.complexity.value,
            "needs_tools": self.needs_tools,
            "has_media_inputs": self.has_media_inputs,
            "signals": dict(self.signals),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ModelProfile:
    """What the router knows about a servable model."""

    name: str
    runtime: str = "llama_cpp"
    size_bytes: Optional[int] = None
    parameter_count_b: Optional[float] = None
    context_length: Optional[int] = None
    min_ram_mb: Optional[float] = None
    capabilities: frozenset[str] = frozenset({"text_generation", "chat", "info", "creative"})
    loaded: bool = False
    source: str = "installed"

    @property
    def is_small(self) -> bool:
        """Small tier: explicitly under 10B. Unknown sizes stay in the big tier."""
        return self.parameter_count_b is not None and self.parameter_count_b < 10.0

    @property
    def is_embedding(self) -> bool:
        return "embedding" in self.capabilities

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "runtime": self.runtime,
            "size_bytes": self.size_bytes,
            "parameter_count_b": self.parameter_count_b,
            "context_length": self.context_length,
            "min_ram_mb": self.min_ram_mb,
            "capabilities": sorted(self.capabilities),
            "loaded": self.loaded,
            "source": self.source,
        }


@dataclass(frozen=True)
class ScoredCandidate:
    profile: ModelProfile
    score: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = self.profile.to_dict()
        payload["score"] = self.score
        payload["reasons"] = list(self.reasons)
        return payload


@dataclass(frozen=True)
class RouteDecision:
    requested_model: str
    selected_model: Optional[str]
    classification: Classification
    candidates: tuple[ScoredCandidate, ...] = ()
    fallback_used: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_model": self.requested_model,
            "selected_model": self.selected_model,
            "classification": self.classification.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "fallback_used": self.fallback_used,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Text and request classification
# ---------------------------------------------------------------------------

_CODE_SIGNALS = (
    "```", "def ", "fn ", "func ", "class ", "import ", "function", "const ",
    "let ", "var ", "return ", "write a program", "write code", "implement",
    "refactor", "debug", "fix the bug", "write a script", "code review",
    "pull request", "git ", "compile", "syntax", "python", "javascript",
    "typescript", " rust ", "golang", "java ", "c++", " ruby ", " swift ",
    "kotlin", "algorithm", "binary search", " sort ", "regex", " api ",
    " http ", " sql ", "database", " query ",
)
_REASONING_SIGNALS = (
    "prove", "explain why", "step by step", "calculate", "solve", "derive",
    "what is the probability", "how many", "analyze", "compare and contrast",
    "evaluate", "mathematical", "theorem", "equation", "logic",
    "think carefully", "reason about",
)
_CREATIVE_SIGNALS = (
    "write a story", "write a poem", "creative", "imagine", "fiction",
    "narrative", "compose", "brainstorm", "write a song", "screenplay", "dialogue",
)
_INFO_SIGNALS = (
    "what is", "who is", "when did", "where is", "how does", "define ",
    "explain ", "summarize", "summary", "overview", "tell me about", "describe ",
    "what are the", "list the", "difference between", "compare ", "history of",
)
_IMAGE_SIGNALS = (
    "image", "picture", "photo", "draw", "generate an image", "visualize",
    "diagram", "screenshot", "describe this image",
)
_DEEP_SIGNALS = (
    "architect", "design a system", "trade-off", "tradeoff", "in depth",
    "comprehensive", "thorough", "detailed analysis", "long-term", "strategy",
    "plan for", "review this codebase", "rewrite", "from scratch",
)


def _count_signals(lower: str, signals: Sequence[str]) -> int:
    return sum(1 for signal in signals if signal in lower)


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    block_type = str(block.get("type") or "")
    if block_type in ("text", "input_text") and isinstance(block.get("text"), str):
        return block["text"]
    if isinstance(block.get("text"), str):
        return block["text"]
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part for part in (_block_text(block) for block in content) if part)
    return ""


def _message_dicts(messages: Iterable[Any]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for message in messages or []:
        if isinstance(message, dict):
            normalised.append(message)
        else:
            normalised.append(
                {
                    "role": str(getattr(message, "role", "user")),
                    "content": getattr(message, "content", ""),
                }
            )
    return normalised


def _media_requirements(messages: Sequence[dict[str, Any]]) -> tuple[bool, bool]:
    has_media = False
    needs_vision = False
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type in ("image_url", "input_image", "image") or "image_url" in block:
                has_media = True
                needs_vision = True
            elif block_type in ("audio_url", "input_audio", "audio") or "audio_url" in block:
                has_media = True
    return has_media, needs_vision


def _needs_tools(messages: Sequence[dict[str, Any]], tools: Any) -> bool:
    if isinstance(tools, list) and tools:
        return True
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                return True
    return False


def _system_code_hint(messages: Sequence[dict[str, Any]]) -> bool:
    for message in messages:
        if str(message.get("role") or "") != "system":
            continue
        text = _content_text(message.get("content")).lower()
        if "developer" in text or "coding" in text or "programmer" in text:
            return True
    return False


def classify_messages(messages: Iterable[Any], tools: Any = None) -> Classification:
    """Classify an OpenAI-style request without calling a model."""
    normalised = _message_dicts(messages)
    text = "\n".join(
        _content_text(message.get("content")) for message in normalised
    )
    lower = text.lower()
    has_media, needs_vision = _media_requirements(normalised)
    tool_required = _needs_tools(normalised, tools)

    scores = {
        "code": _count_signals(lower, _CODE_SIGNALS),
        "reasoning": _count_signals(lower, _REASONING_SIGNALS),
        "creative": _count_signals(lower, _CREATIVE_SIGNALS),
        "info": _count_signals(lower, _INFO_SIGNALS),
        "image": _count_signals(lower, _IMAGE_SIGNALS),
        "deep": _count_signals(lower, _DEEP_SIGNALS),
    }

    if _system_code_hint(normalised) or scores["code"] >= 2 or (
        scores["code"] >= 1 and scores["reasoning"] == 0 and scores["creative"] == 0
    ):
        category = Category.CODE
    elif scores["reasoning"] >= 2:
        category = Category.REASONING
    elif scores["creative"] >= 1:
        category = Category.CREATIVE
    elif needs_vision or scores["image"] >= 1:
        category = Category.IMAGE
    elif tool_required and scores["code"] == 0 and scores["reasoning"] == 0 and scores["creative"] == 0:
        category = Category.TOOL_CALL
    elif scores["info"] >= 2 and scores["code"] == 0:
        category = Category.INFO
    else:
        category = Category.CHAT

    last_user = ""
    for message in reversed(normalised):
        if str(message.get("role") or "") == "user":
            last_user = _content_text(message.get("content"))
            break
    last_user_len = len(last_user)
    message_count = len(normalised)

    if scores["deep"] >= 1 or last_user_len > 500 or message_count > 10:
        complexity = Complexity.DEEP
    elif (
        last_user_len < 60
        and message_count <= 2
        and scores["reasoning"] == 0
        and scores["deep"] == 0
    ):
        complexity = Complexity.QUICK
    else:
        complexity = Complexity.MODERATE

    top = sorted(
        ((name, value) for name, value in scores.items() if value),
        key=lambda item: (-item[1], item[0]),
    )
    signal_text = ", ".join(f"{name}={value}" for name, value in top[:3]) or "no strong signals"
    reason = (
        f"{category.value} / {complexity.value}; {signal_text}; "
        f"tools={'yes' if tool_required else 'no'}; media={'yes' if has_media else 'no'}"
    )
    return Classification(
        category=category,
        complexity=complexity,
        needs_tools=tool_required,
        has_media_inputs=has_media,
        signals=scores,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Model capability inference
# ---------------------------------------------------------------------------

_CODE_MODEL_HINTS = (
    "coder", "code", "starcoder", "codellama", "devstral", "qwen2.5-coder",
    "qwen3-coder", "deepseek-coder",
)
_REASONING_MODEL_HINTS = (
    "reason", "r1", "think", "qwq", "o1", "o3", "math", "logic",
)
_TOOL_MODEL_HINTS = (
    "qwen2.5", "qwen3", "qwen3.5", "llama-3.1", "llama-3.2", "llama-3.3",
    "llama3.1", "llama3.2", "llama3.3", "mistral", "mixtral", "hermes",
    "deepseek", "command-r", "granite", "functionary", "gorilla", "tool",
)
_VISION_MODEL_HINTS = (
    "vl", "vision", "llava", "minicpm-v", "pixtral", "gemma3", "gemma-3",
    "qwen2-vl", "qwen3-vl", "multimodal",
)
_AUDIO_MODEL_HINTS = ("audio", "whisper", "tts", "speech")

_PARAM_RE = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*([bBmM])(?![A-Za-z0-9])"
)


def parse_parameter_count(value: str) -> Optional[float]:
    """Extract a parameter count in billions from a model name or size string."""
    text = value or ""
    match = _PARAM_RE.search(text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "m":
        number /= 1000.0
    if number <= 0 or number > 10_000:
        return None
    return round(number, 3)


def infer_capabilities(
    name: str,
    *,
    family: str = "",
    description: str = "",
    parameter_size: str = "",
) -> frozenset[str]:
    """Infer capabilities from names and catalog metadata.

    These are hints, not proofs. The decision records them, and a task that fails on a
    hinted capability is reported as a failure rather than retried forever.
    """
    haystack = f"{name} {family} {description} {parameter_size}".lower()
    capabilities = {"text_generation", "chat", "info", "creative"}

    if "embed" in haystack:
        capabilities.add("embedding")
        capabilities.discard("chat")
        return frozenset(capabilities)

    if any(hint in haystack for hint in _CODE_MODEL_HINTS):
        capabilities.add("code")
    elif any(token in haystack for token in (
        "qwen", "llama", "deepseek", "mistral", "gemma", "phi", "granite",
    )):
        capabilities.add("code")

    if any(hint in haystack for hint in _REASONING_MODEL_HINTS):
        capabilities.add("reasoning")

    if any(hint in haystack for hint in _TOOL_MODEL_HINTS):
        capabilities.add("tool_use")

    if any(hint in haystack for hint in _VISION_MODEL_HINTS):
        capabilities.add("vision")
        capabilities.add("multimodal")

    if any(hint in haystack for hint in _AUDIO_MODEL_HINTS):
        capabilities.add("audio")
        capabilities.add("multimodal")

    return frozenset(capabilities)


def profile_from_installed(model: Any) -> ModelProfile:
    name = str(getattr(model, "id", None) or getattr(model, "name", "") or "").strip()
    family = str(getattr(model, "family", "") or "")
    parameter_size = str(getattr(model, "parameter_size", "") or "")
    return ModelProfile(
        name=name,
        runtime=str(getattr(model, "runtime", "llama_cpp") or "llama_cpp"),
        size_bytes=getattr(model, "size_bytes", None),
        parameter_count_b=parse_parameter_count(parameter_size or name),
        context_length=getattr(model, "context_length", None),
        min_ram_mb=getattr(model, "min_ram_mb", None),
        capabilities=infer_capabilities(name, family=family, parameter_size=parameter_size),
        loaded=bool(getattr(model, "loaded", False)),
        source="installed",
    )


def profile_from_runtime(model: Any) -> ModelProfile:
    name = str(getattr(model, "id", None) or getattr(model, "name", "") or "").strip()
    family = str(getattr(model, "family", "") or "")
    parameter_size = str(getattr(model, "parameter_size", "") or "")
    return ModelProfile(
        name=name,
        runtime=str(getattr(model, "runtime", "ollama") or "ollama"),
        size_bytes=getattr(model, "size_bytes", None),
        parameter_count_b=parse_parameter_count(parameter_size or name),
        context_length=getattr(model, "context_length", None),
        capabilities=infer_capabilities(name, family=family, parameter_size=parameter_size),
        loaded=bool(getattr(model, "loaded", False)),
        source="runtime",
    )


def profile_from_catalog(entry: Any) -> ModelProfile:
    name = str(getattr(entry, "id", "") or "")
    description = str(getattr(entry, "description", "") or "")
    parameter_size = str(getattr(entry, "parameter_size", "") or "")
    return ModelProfile(
        name=name,
        runtime=str(getattr(entry, "runtime", "llama_cpp") or "llama_cpp"),
        size_bytes=getattr(entry, "total_size_bytes", None),
        parameter_count_b=parse_parameter_count(parameter_size or name),
        context_length=getattr(entry, "context_length", None),
        min_ram_mb=getattr(entry, "min_ram_mb", None),
        capabilities=infer_capabilities(name, description=description, parameter_size=parameter_size),
        loaded=False,
        source="catalog",
    )


def merge_profiles(profiles: Iterable[ModelProfile]) -> list[ModelProfile]:
    """Deduplicate by name, preferring a loaded/runtime profile over a catalog one."""
    order = {"runtime": 3, "installed": 2, "catalog": 1}
    merged: dict[str, ModelProfile] = {}
    for profile in profiles:
        if not profile.name:
            continue
        current = merged.get(profile.name)
        if current is None or order.get(profile.source, 0) > order.get(current.source, 0):
            merged[profile.name] = profile
        elif current is not None:
            # Preserve the richest capability set and any measured size.
            merged[profile.name] = ModelProfile(
                name=current.name,
                runtime=current.runtime,
                size_bytes=current.size_bytes or profile.size_bytes,
                parameter_count_b=current.parameter_count_b or profile.parameter_count_b,
                context_length=current.context_length or profile.context_length,
                min_ram_mb=current.min_ram_mb or profile.min_ram_mb,
                capabilities=current.capabilities | profile.capabilities,
                loaded=current.loaded or profile.loaded,
                source=current.source,
            )
    return sorted(merged.values(), key=lambda item: item.name.lower())


async def build_profiles_from_engine(engine: Any) -> list[ModelProfile]:
    """Collect servable models from the model manager and the active runtime."""
    profiles: list[ModelProfile] = []
    try:
        for model in engine.models.installed():
            profiles.append(profile_from_installed(model))
    except Exception:
        pass
    try:
        runtime = engine.runtime()
        for model in await runtime.list_models():
            profiles.append(profile_from_runtime(model))
    except Exception:
        pass
    return merge_profiles(profiles)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _filter_for_classification(
    classification: Classification,
    profiles: Sequence[ModelProfile],
) -> tuple[list[ModelProfile], str, bool]:
    candidates = [profile for profile in profiles if not profile.is_embedding]
    if not candidates:
        return [], "no chat-capable model is installed or reachable", True

    required: Optional[str] = None
    if classification.needs_tools:
        required = "tool_use"
    elif classification.category == Category.REASONING:
        required = "reasoning"
    elif classification.category == Category.CODE:
        required = "code"
    elif classification.category == Category.IMAGE or classification.has_media_inputs:
        required = "vision"

    if required is None:
        return candidates, "no hard capability required", False

    matching = [profile for profile in candidates if profile.supports(required)]
    if not matching:
        return (
            candidates,
            f"no model advertises '{required}'; falling back to all chat models",
            True,
        )
    return matching, f"hard filter: {required}", False


def _score(profile: ModelProfile, classification: Classification, ram_available_mb: Optional[float]) -> ScoredCandidate:
    score = 0.0
    reasons: list[str] = []

    if classification.needs_tools and profile.supports("tool_use"):
        score += 4.0
        reasons.append("tool_use")
    if classification.category == Category.REASONING and profile.supports("reasoning"):
        score += 4.0
        reasons.append("reasoning")
    if classification.category == Category.CODE and profile.supports("code"):
        score += 2.0
        reasons.append("code")
    if (classification.category == Category.IMAGE or classification.has_media_inputs) and profile.supports("vision"):
        score += 5.0
        reasons.append("vision")

    if profile.loaded:
        score += 1.5
        reasons.append("already loaded")

    if classification.complexity == Complexity.QUICK:
        if profile.is_small:
            score += 1.0
            reasons.append("small/fast tier")
        else:
            score -= 0.3
        # Within a tier, prefer a small model for short prompts, but keep a quality floor:
        # a 135M model is fast and usually too weak to be useful.
        if profile.parameter_count_b is not None:
            score -= min(profile.parameter_count_b, 8.0) * 0.08
            if profile.parameter_count_b < 0.4:
                score -= 0.4
    elif classification.complexity == Complexity.DEEP:
        if profile.is_small:
            score -= 0.5
        else:
            score += 1.0
            reasons.append("large/quality tier")

    if profile.parameter_count_b:
        score += min(profile.parameter_count_b / 20.0, 1.0) * 0.2

    if (
        ram_available_mb is not None
        and profile.min_ram_mb is not None
        and profile.min_ram_mb > ram_available_mb * 0.9
    ):
        score -= 6.0
        reasons.append("may not fit available RAM")

    return ScoredCandidate(profile=profile, score=round(score, 4), reasons=tuple(reasons))


def _tier(profiles: Sequence[ModelProfile], classification: Classification) -> list[ModelProfile]:
    big = [profile for profile in profiles if not profile.is_small]
    small = [profile for profile in profiles if profile.is_small]
    if classification.complexity == Complexity.QUICK and small:
        return small
    if classification.complexity == Complexity.DEEP and big:
        return big
    if big:
        return big
    return small or list(profiles)


def route_request(
    messages: Iterable[Any],
    *,
    requested_model: str,
    profiles: Iterable[ModelProfile],
    tools: Any = None,
    ram_available_mb: Optional[float] = None,
) -> RouteDecision:
    """Classify a request and choose a model. Explicit models are always honoured."""
    classification = classify_messages(messages, tools=tools)
    available = merge_profiles(profile for profile in profiles if not profile.is_embedding)
    requested = (requested_model or "auto").strip()
    requested_lower = requested.lower()

    if not available:
        return RouteDecision(
            requested_model=requested,
            selected_model=None,
            classification=classification,
            fallback_used=True,
            reason="no servable model found",
        )

    if requested_lower not in AUTO_MODELS:
        match = next((profile for profile in available if profile.name.lower() == requested_lower), None)
        if match is None:
            return RouteDecision(
                requested_model=requested,
                selected_model=requested,
                classification=classification,
                candidates=tuple(
                    _score(profile, classification, ram_available_mb) for profile in available[:8]
                ),
                fallback_used=True,
                reason="explicit model is not advertised by the current runtime; the runtime will decide or fail",
            )
        return RouteDecision(
            requested_model=requested,
            selected_model=match.name,
            classification=classification,
            candidates=(_score(match, classification, ram_available_mb),),
            fallback_used=False,
            reason="explicit model request",
        )

    filtered, filter_reason, fallback_used = _filter_for_classification(classification, available)
    tier = _tier(filtered, classification)
    scored = sorted(
        (_score(profile, classification, ram_available_mb) for profile in tier),
        key=lambda candidate: (-candidate.score, candidate.profile.name.lower()),
    )
    if not scored:
        return RouteDecision(
            requested_model=requested,
            selected_model=None,
            classification=classification,
            fallback_used=True,
            reason=filter_reason,
        )

    winner = scored[0]
    reason = (
        f"{filter_reason}; chose {winner.profile.name} "
        f"at {winner.score:.3f} for {classification.category.value}/{classification.complexity.value}"
    )
    if winner.reasons:
        reason += f" ({', '.join(winner.reasons)})"

    return RouteDecision(
        requested_model=requested,
        selected_model=winner.profile.name,
        classification=classification,
        candidates=tuple(scored[:8]),
        fallback_used=fallback_used,
        reason=reason,
    )
