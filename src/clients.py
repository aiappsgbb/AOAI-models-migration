"""
Client factory and model calling for Azure OpenAI.

Handles the difference between classic (AzureOpenAI) and v1 (OpenAI) clients.
Authentication: Entra ID (DefaultAzureCredential) by default, API key as fallback.
"""

import os
from typing import Any, Callable, Optional

from openai import AzureOpenAI, OpenAI

from src.config import (
    MAX_COMPLETION_TOKEN_MODELS,
    is_reasoning,
    is_responses_only,
    is_v1,
    uses_developer_role,
)


V1_TOKEN_SCOPE = "https://ai.azure.com/.default"
CLASSIC_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


def _get_token_provider(scope: str) -> Callable[[], str]:
    """Create an Entra ID token provider for the requested Azure resource."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    return get_bearer_token_provider(DefaultAzureCredential(), scope)


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
            token_provider = _get_token_provider(V1_TOKEN_SCOPE)
            return OpenAI(base_url=base_url, api_key=token_provider)
        return OpenAI(base_url=base_url, api_key=api_key)
    else:
        if use_entra:
            return AzureOpenAI(
                azure_endpoint=endpoint,
                azure_ad_token_provider=_get_token_provider(CLASSIC_TOKEN_SCOPE),
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
    if is_responses_only(model_name):
        raise ValueError(
            f"{model_name} is only available through the Responses API; "
            "call client.responses.create() instead of call_model()."
        )

    # max_tokens → max_completion_tokens for models that require it
    if "max_tokens" in params and model_name in MAX_COMPLETION_TOKEN_MODELS:
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

    try:
        return client.chat.completions.create(
            model=deployment or model_name,
            messages=messages,
            **params,
        )
    except Exception as e:
        error_msg = str(e)
        if "DeploymentNotFound" in error_msg or "404" in error_msg:
            raise RuntimeError(
                f"Deployment '{deployment or model_name}' not found. "
                f"Check your deployment name in Azure portal or .env file. "
                f"Available env vars: {model_name.upper().replace('-', '_').replace('.', '')}_DEPLOYMENT"
            ) from e
        if "401" in error_msg or "AuthenticationError" in error_msg:
            raise RuntimeError(
                f"Authentication failed for '{deployment or model_name}'. "
                f"Run 'az login' for Entra ID auth, or check your API key."
            ) from e
        raise
