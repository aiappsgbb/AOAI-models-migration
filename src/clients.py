"""
Client factory and model calling for Azure OpenAI.

Handles the difference between classic (AzureOpenAI) and v1 (OpenAI) clients.
Authentication: Entra ID (DefaultAzureCredential) by default, API key as fallback.
"""

import os
from typing import Any, Optional

from openai import AzureOpenAI, OpenAI

from src.config import is_v1, is_reasoning, uses_developer_role


def _get_token_provider():
    """Create an Entra ID token provider for Azure Cognitive Services."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    return get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )


class _TokenRefreshingOpenAI(OpenAI):
    """OpenAI client wrapper that refreshes Entra ID tokens automatically.

    The base OpenAI client accepts a static api_key string. For Entra ID auth
    with the v1 API, we need to refresh the token on each request since tokens
    expire (typically after 1 hour).
    """

    def __init__(self, *, base_url: str, token_provider):
        self._token_provider = token_provider
        super().__init__(base_url=base_url, api_key=token_provider())

    def _prepare_options(self, options):
        # Refresh the token before each request
        self.api_key = self._token_provider()
        return super()._prepare_options(options)


def create_client(
    model_name: str,
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    api_version: str = "2024-12-01-preview",
) -> AzureOpenAI | OpenAI:
    """
    Create the appropriate client for a given model.

    - Legacy models (gpt-4o, gpt-4o-mini): AzureOpenAI with api_version
    - New models (gpt-4.1+, gpt-5+): OpenAI with /openai/v1/ endpoint
    - Default auth: Entra ID. Only uses API key if explicitly passed.
    """
    endpoint = endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        raise ValueError("endpoint is required (or set AZURE_OPENAI_ENDPOINT)")

    use_entra = api_key is None

    if is_v1(model_name):
        base_url = endpoint.rstrip("/") + "/openai/v1"
        if use_entra:
            # Return a wrapper that refreshes the token on each request
            token_provider = _get_token_provider()
            return _TokenRefreshingOpenAI(base_url=base_url, token_provider=token_provider)
        return OpenAI(base_url=base_url, api_key=api_key)
    else:
        if use_entra:
            return AzureOpenAI(
                azure_endpoint=endpoint,
                azure_ad_token_provider=_get_token_provider(),
                api_version=api_version,
            )
        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )


def call_model(
    client: AzureOpenAI | OpenAI,
    model_name: str,
    messages: list[dict[str, str]],
    deployment: Optional[str] = None,
    **params: Any,
) -> Any:
    """
    Call a model with automatic parameter and message adaptation.

    Handles:
    - max_tokens → max_completion_tokens for v1 models
    - Drops temperature/top_p for reasoning models
    - system → developer role for reasoning models (GPT-5+, o-series)
    """
    # max_tokens → max_completion_tokens for v1 models
    if "max_tokens" in params and is_v1(model_name):
        params["max_completion_tokens"] = params.pop("max_tokens")

    # Reasoning models don't support temperature/top_p
    if is_reasoning(model_name):
        params.pop("temperature", None)
        params.pop("top_p", None)

    # Reasoning models use "developer" role instead of "system"
    if uses_developer_role(model_name):
        messages = [
            {**m, "role": "developer"} if m.get("role") == "system" else m
            for m in messages
        ]

    return client.chat.completions.create(
        model=deployment or model_name,
        messages=messages,
        **params,
    )
