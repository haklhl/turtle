"""OpenRouter LLM provider implementation (reuses OpenAI SDK with custom base_url)."""

from sea_turtle.llm.openai import OpenAIProvider

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter API provider. Reuses OpenAI SDK since OpenRouter is API-compatible.

    Model names use the format: provider/model, e.g. 'google/gemini-2.5-flash'.
    """

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, base_url=OPENROUTER_BASE_URL, **kwargs)
