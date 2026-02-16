"""Model registry with preset model lists and pricing for all supported providers."""

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class ModelInfo:
    """Information about a single LLM model."""
    name: str
    provider: str
    context_window: int
    input_price_per_1m: float
    output_price_per_1m: float
    description: str = ""
    supports_tools: bool = True


# --- Google Gemini ---
GOOGLE_MODELS: list[ModelInfo] = [
    ModelInfo("gemini-2.5-pro", "google", 1_000_000, 1.25, 10.0, "Most capable reasoning model"),
    ModelInfo("gemini-2.5-flash", "google", 1_000_000, 0.15, 0.60, "Best price-performance (default)"),
    ModelInfo("gemini-2.0-flash", "google", 1_000_000, 0.10, 0.40, "Fast responses"),
    ModelInfo("gemini-2.0-flash-lite", "google", 1_000_000, 0.075, 0.30, "Lowest cost"),
    ModelInfo("gemini-1.5-pro", "google", 2_000_000, 1.25, 5.00, "Long context"),
    ModelInfo("gemini-1.5-flash", "google", 1_000_000, 0.075, 0.30, "Lightweight fast"),
]

# --- OpenAI ---
OPENAI_MODELS: list[ModelInfo] = [
    ModelInfo("gpt-4o", "openai", 128_000, 2.50, 10.00, "Flagship multimodal"),
    ModelInfo("gpt-4o-mini", "openai", 128_000, 0.15, 0.60, "Small and fast"),
    ModelInfo("gpt-4.1", "openai", 1_000_000, 2.00, 8.00, "Latest flagship"),
    ModelInfo("gpt-4.1-mini", "openai", 1_000_000, 0.40, 1.60, "Balanced"),
    ModelInfo("gpt-4.1-nano", "openai", 1_000_000, 0.10, 0.40, "Fastest and cheapest"),
    ModelInfo("o3", "openai", 200_000, 10.00, 40.00, "Advanced reasoning"),
    ModelInfo("o3-mini", "openai", 200_000, 1.10, 4.40, "Efficient reasoning"),
    ModelInfo("o4-mini", "openai", 200_000, 1.10, 4.40, "Latest reasoning"),
]

# --- Anthropic (Claude) ---
ANTHROPIC_MODELS: list[ModelInfo] = [
    ModelInfo("claude-sonnet-4-20250514", "anthropic", 200_000, 3.00, 15.00, "Latest Sonnet"),
    ModelInfo("claude-3.5-sonnet-20241022", "anthropic", 200_000, 3.00, 15.00, "Sonnet 3.5"),
    ModelInfo("claude-3.5-haiku-20241022", "anthropic", 200_000, 0.80, 4.00, "Fast and affordable"),
]

# --- xAI (Grok) ---
XAI_MODELS: list[ModelInfo] = [
    ModelInfo("grok-3", "xai", 131_072, 3.00, 15.00, "Flagship Grok"),
    ModelInfo("grok-3-mini", "xai", 131_072, 0.30, 0.50, "Fast Grok"),
]

# --- All preset models ---
ALL_MODELS: list[ModelInfo] = GOOGLE_MODELS + OPENAI_MODELS + ANTHROPIC_MODELS + XAI_MODELS

# --- Lookup maps ---
MODEL_BY_NAME: dict[str, ModelInfo] = {m.name: m for m in ALL_MODELS}
MODELS_BY_PROVIDER: dict[str, list[ModelInfo]] = {}
for _m in ALL_MODELS:
    MODELS_BY_PROVIDER.setdefault(_m.provider, []).append(_m)

SUPPORTED_PROVIDERS = ["google", "openai", "anthropic", "openrouter", "xai"]


def get_model_info(model_name: str) -> ModelInfo | None:
    """Look up a model by name. Returns None if not found in registry."""
    return MODEL_BY_NAME.get(model_name)


def list_models(provider: str | None = None) -> list[ModelInfo]:
    """List available models, optionally filtered by provider.

    Args:
        provider: Filter by provider name. None returns all models.

    Returns:
        List of ModelInfo objects.
    """
    if provider:
        return MODELS_BY_PROVIDER.get(provider, [])
    return ALL_MODELS


def get_pricing(model_name: str) -> tuple[float, float] | None:
    """Get pricing for a model.

    Args:
        model_name: Model name string.

    Returns:
        Tuple of (input_price_per_1m, output_price_per_1m), or None if unknown.
    """
    info = get_model_info(model_name)
    if info:
        return (info.input_price_per_1m, info.output_price_per_1m)
    return None


def resolve_provider(model_name: str, default_provider: str = "google") -> str:
    """Determine which provider a model belongs to.

    Args:
        model_name: Model name string.
        default_provider: Fallback provider if model not found in registry.

    Returns:
        Provider name string.
    """
    info = get_model_info(model_name)
    if info:
        return info.provider

    # Heuristic: detect provider from model name prefix
    if model_name.startswith("gemini"):
        return "google"
    elif model_name.startswith("gpt") or model_name.startswith("o3") or model_name.startswith("o4"):
        return "openai"
    elif model_name.startswith("claude"):
        return "anthropic"
    elif model_name.startswith("grok"):
        return "xai"
    elif "/" in model_name:
        return "openrouter"

    return default_provider


def format_model_list(models: list[ModelInfo]) -> str:
    """Format a list of models for display.

    Returns:
        Formatted string table.
    """
    if not models:
        return "No models found."

    lines = []
    current_provider = ""
    for m in models:
        if m.provider != current_provider:
            if current_provider:
                lines.append("")
            lines.append(f"📦 {m.provider.upper()}")
            lines.append(f"{'Model':<35} {'Context':>10} {'Input $/1M':>12} {'Output $/1M':>12}")
            lines.append("-" * 72)
            current_provider = m.provider
        ctx = f"{m.context_window // 1000}K" if m.context_window < 1_000_000 else f"{m.context_window // 1_000_000}M"
        lines.append(f"{m.name:<35} {ctx:>10} {f'${m.input_price_per_1m:.3f}':>12} {f'${m.output_price_per_1m:.3f}':>12}")

    return "\n".join(lines)
