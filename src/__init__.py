"""
Azure OpenAI Migration Toolkit

Provides utilities for migrating from GPT-4o/GPT-4o-mini to newer models
(GPT-4.1, GPT-5.1, GPT-5 series) and evaluating migration quality.
"""

from src.config import load_config, is_v1, is_reasoning
from src.clients import create_client, call_model

__all__ = [
    "load_config",
    "is_v1",
    "is_reasoning",
    "create_client",
    "call_model",
]
