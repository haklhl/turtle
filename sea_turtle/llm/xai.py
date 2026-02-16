"""xAI Grok LLM provider implementation (reuses OpenAI SDK with custom base_url)."""

from sea_turtle.llm.openai import OpenAIProvider

XAI_BASE_URL = "https://api.x.ai/v1"


class XAIProvider(OpenAIProvider):
    """xAI Grok API provider. Reuses OpenAI SDK since xAI is API-compatible."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, base_url=XAI_BASE_URL, **kwargs)
