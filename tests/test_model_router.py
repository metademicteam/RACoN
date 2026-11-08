"""Tests for the deterministic Mesh-LLM-inspired model router."""
from backend.racon.orchestration.model_router import (
    AUTO_MODELS,
    Category,
    Complexity,
    ModelProfile,
    classify_messages,
    infer_capabilities,
    parse_parameter_count,
    route_request,
)


def _profile(name: str, capabilities: set[str], parameter_count_b: float | None = None,
             loaded: bool = False) -> ModelProfile:
    return ModelProfile(
        name=name,
        capabilities=frozenset({"text_generation", "chat", "info", "creative"} | capabilities),
        parameter_count_b=parameter_count_b,
        loaded=loaded,
    )


def test_classify_code_with_tools_keeps_code_category():
    result = classify_messages(
        [{"role": "user", "content": "Write a Python function to sort a list"}],
        tools=[{"type": "function", "function": {"name": "run"}}],
    )
    assert result.category == Category.CODE
    assert result.needs_tools is True
    assert result.complexity in (Complexity.QUICK, Complexity.MODERATE)


def test_classify_short_hello_is_quick_chat():
    result = classify_messages([{"role": "user", "content": "Hello"}])
    assert result.category == Category.CHAT
    assert result.complexity == Complexity.QUICK
    assert result.needs_tools is False
    assert result.has_media_inputs is False


def test_classify_reasoning_and_deep():
    result = classify_messages(
        [
            {
                "role": "user",
                "content": (
                    "Prove that the square root of 2 is irrational. Explain step by step "
                    "and analyze the trade-offs in depth."
                ),
            }
        ]
    )
    assert result.category == Category.REASONING
    assert result.complexity == Complexity.DEEP


def test_classify_creative():
    result = classify_messages(
        [{"role": "user", "content": "Write a story about a robot who learns to paint"}]
    )
    assert result.category == Category.CREATIVE


def test_classify_image_block():
    result = classify_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
    )
    assert result.category == Category.IMAGE
    assert result.has_media_inputs is True


def test_parse_parameter_count_and_capabilities():
    assert parse_parameter_count("Qwen2.5-0.5B-Instruct-Q4_K_M") == 0.5
    assert parse_parameter_count("SmolLM2-360M-Instruct") == 0.36
    assert parse_parameter_count("Llama-3.2-1B-Instruct") == 1.0
    assert parse_parameter_count("model-Q4_K_M") is None

    caps = infer_capabilities("qwen2.5-coder-7b-instruct")
    assert "code" in caps
    assert "tool_use" in caps
    assert "text_generation" in caps

    vision = infer_capabilities("Qwen2-VL-7B-Instruct")
    assert "vision" in vision
    assert "multimodal" in vision


def test_phi35_is_not_advertised_as_tool_capable():
    caps = infer_capabilities("phi3.5:latest")
    assert "tool_use" not in caps
    assert "text_generation" in caps


def test_auto_tool_request_skips_phi35_for_qwen():
    profiles = [
        _profile("phi3.5:latest", set(), parameter_count_b=3.8),
        _profile("qwen2.5:0.5b", {"tool_use"}, parameter_count_b=0.5),
    ]
    decision = route_request(
        [{"role": "user", "content": "Write a file on disk"}],
        requested_model="auto",
        profiles=profiles,
        tools=[{"type": "function", "function": {"name": "write"}}],
    )
    assert decision.selected_model == "qwen2.5:0.5b"


def test_auto_picks_tool_capable_model():
    profiles = [
        _profile("plain-chat-7b", set()),
        _profile("qwen2.5-7b-instruct", {"tool_use", "code"}),
    ]
    decision = route_request(
        [{"role": "user", "content": "Read the file and fix the bug"}],
        requested_model="auto",
        profiles=profiles,
        tools=[{"type": "function", "function": {"name": "read"}}],
    )
    assert decision.selected_model == "qwen2.5-7b-instruct"
    assert decision.fallback_used is False


def test_auto_picks_reasoning_model():
    profiles = [
        _profile("chat-31b", {"code"}),
        _profile("deepseek-r1-32b", {"reasoning"}),
    ]
    decision = route_request(
        [{"role": "user", "content": "Prove this theorem step by step and analyze it"}],
        requested_model="auto",
        profiles=profiles,
    )
    assert decision.selected_model == "deepseek-r1-32b"


def test_auto_prefers_small_model_for_quick_chat():
    profiles = [
        _profile("big-chat-31b", set(), parameter_count_b=31.0),
        _profile("small-chat-1b", set(), parameter_count_b=1.0),
        _profile("tiny-chat-360m", set(), parameter_count_b=0.36),
    ]
    decision = route_request(
        [{"role": "user", "content": "hi"}],
        requested_model="auto",
        profiles=profiles,
    )
    assert decision.selected_model in ("small-chat-1b", "tiny-chat-360m")
    assert decision.classification.complexity == Complexity.QUICK


def test_explicit_model_is_honoured():
    profiles = [_profile("a", set()), _profile("b", set())]
    decision = route_request(
        [{"role": "user", "content": "hello"}],
        requested_model="b",
        profiles=profiles,
    )
    assert decision.selected_model == "b"
    assert decision.fallback_used is False
    assert decision.reason == "explicit model request"


def test_no_profiles_returns_no_route():
    decision = route_request(
        [{"role": "user", "content": "hello"}],
        requested_model="auto",
        profiles=[],
    )
    assert decision.selected_model is None
    assert decision.fallback_used is True


def test_missing_capability_falls_back_to_all():
    profiles = [_profile("plain-a", set(), parameter_count_b=3.0)]
    decision = route_request(
        [{"role": "user", "content": "Run the tests and read the file"}],
        requested_model="auto",
        profiles=profiles,
        tools=[{"type": "function", "function": {"name": "run"}}],
    )
    assert decision.selected_model == "plain-a"
    assert decision.fallback_used is True
    assert "falling back" in decision.reason


def test_auto_models_constant():
    assert "auto" in AUTO_MODELS
