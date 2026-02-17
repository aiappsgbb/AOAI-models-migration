"""
Configuration and model helpers for Azure OpenAI migration.

Simple helpers to distinguish model families (v1 vs classic API, reasoning vs standard).
All detailed model documentation lives in the notebooks and README.
"""

import os
from typing import Optional
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Model family helpers
# ---------------------------------------------------------------------------

# Models using the new v1 API (OpenAI client with /openai/v1/ endpoint)
V1_MODELS = {
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5", "gpt-5.1", "gpt-5-mini", "gpt-5-nano",
}

# Reasoning models (no temperature/top_p, use max_completion_tokens)
REASONING_MODELS = {
    "gpt-5", "gpt-5.1", "gpt-5-mini", "gpt-5-nano",
}


def is_v1(model: str) -> bool:
    """True if the model uses the v1 API (OpenAI client)."""
    return model in V1_MODELS


def is_reasoning(model: str) -> bool:
    """True if the model is a reasoning model (no temperature/top_p)."""
    return model in REASONING_MODELS


# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

def load_config(env_path: Optional[str] = None) -> dict:
    """Load configuration from .env file and return deployment mappings."""
    load_dotenv(env_path or ".env")

    config = {
        "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
        "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
        "deployments": {},
    }

    deployment_vars = {
        "gpt-4o": "GPT4O_DEPLOYMENT",
        "gpt-4o-mini": "GPT4O_MINI_DEPLOYMENT",
        "gpt-4.1": "GPT41_DEPLOYMENT",
        "gpt-4.1-mini": "GPT41_MINI_DEPLOYMENT",
        "gpt-5.1": "GPT51_DEPLOYMENT",
        "gpt-5": "GPT5_DEPLOYMENT",
        "gpt-5-mini": "GPT5_MINI_DEPLOYMENT",
    }

    for model_name, env_var in deployment_vars.items():
        value = os.getenv(env_var)
        if value:
            config["deployments"][model_name] = value

    # Foundry evaluation config
    config["foundry_endpoint"] = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    config["eval_model"] = os.getenv("EVAL_MODEL_DEPLOYMENT", "gpt-4.1")
    config["model_deployment_name"] = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", config["eval_model"])

    return config
