"""
Azure OpenAI Clients Package
"""
from .azure_openai import (
    AzureOpenAIClient,
    ModelConfig,
    RequestMetrics,
    CompletionResult,
    create_client_from_config
)
from .tts_client import TTSClient, TTSConfig, TTSResult, load_tts_config_from_settings
from .realtime_client import RealtimeClient, RealtimeConfig, RealtimeResult, is_realtime_available

__all__ = [
    'AzureOpenAIClient',
    'ModelConfig', 
    'RequestMetrics',
    'CompletionResult',
    'create_client_from_config',
    'TTSClient',
    'TTSConfig',
    'load_tts_config_from_settings',
    'TTSResult',
    'RealtimeClient',
    'RealtimeConfig',
    'RealtimeResult',
    'is_realtime_available',
]
