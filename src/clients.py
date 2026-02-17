"""
Client factory and model calling for Azure OpenAI.

Handles the difference between classic (AzureOpenAI) and v1 (OpenAI) clients.
Authentication: Entra ID (DefaultAzureCredential) by default, API key as fallback.
"""

import os
from typing import Any, Optional

from openai import AzureOpenAI, OpenAI

from src.config import is_v1, is_reasoning


def _get_token_provider():
    """Create an Entra ID token provider for Azure Cognitive Services."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    return get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )


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
            return OpenAI(base_url=base_url, api_key=_get_token_provider()())
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
    Call a model with minimal parameter adaptation.

    Handles two differences:
    - max_tokens → max_completion_tokens for v1 models
    - Drops temperature/top_p for reasoning models
    """
    # max_tokens → max_completion_tokens for v1 models
    if "max_tokens" in params and is_v1(model_name):
        params["max_completion_tokens"] = params.pop("max_tokens")

    # Reasoning models don't support temperature/top_p
    if is_reasoning(model_name):
        params.pop("temperature", None)
        params.pop("top_p", None)

    return client.chat.completions.create(
        model=deployment or model_name,
        messages=messages,
        **params,
    )
