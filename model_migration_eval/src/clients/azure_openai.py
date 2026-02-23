"""
Azure OpenAI Client Wrapper for Model Migration Evaluation
Provides unified interface for GPT-4 and GPT-5 model interactions
"""

import os
import json
import time
import asyncio
import hashlib
import logging
from typing import Optional, Dict, Any, List, Generator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env file (encoding='utf-8-sig' strips BOM if present,
    # preventing the first variable from being mis-named)
    load_dotenv(override=True, encoding='utf-8-sig')
except ImportError:
    pass  # python-dotenv is optional

import yaml
import httpx
from openai import AzureOpenAI, AsyncAzureOpenAI
from openai.types.chat import ChatCompletion
import diskcache

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a specific model deployment"""
    deployment_name: str
    model_version: str
    max_tokens: int = 4096
    temperature: float = 0.1
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    seed: Optional[int] = None
    reasoning_effort: Optional[str] = None  # GPT-5/o-series only
    use_max_completion_tokens: Optional[bool] = None  # Auto-detected if None
    

@dataclass
class RequestMetrics:
    """Metrics collected for each request"""
    request_id: str
    model: str
    start_time: float
    end_time: float = 0.0
    ttft: float = 0.0  # Time to first token
    total_time: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    status: str = "pending"
    error: Optional[str] = None
    
    def finalize(self, completion: ChatCompletion = None, error: str = None):
        """Finalize metrics after request completion"""
        self.end_time = time.time()
        self.total_time = self.end_time - self.start_time
        
        if error:
            self.status = "error"
            self.error = error
        elif completion:
            self.status = "success"
            usage = completion.usage
            if usage:
                self.prompt_tokens = usage.prompt_tokens
                self.completion_tokens = usage.completion_tokens
                self.total_tokens = usage.total_tokens
                # Handle cached tokens if available
                if hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
                    self.cached_tokens = getattr(usage.prompt_tokens_details, 'cached_tokens', 0) or 0
                # Handle reasoning tokens for o-series
                if hasattr(usage, 'completion_tokens_details') and usage.completion_tokens_details:
                    self.reasoning_tokens = getattr(usage.completion_tokens_details, 'reasoning_tokens', 0) or 0


@dataclass
class CompletionResult:
    """Result wrapper for model completions"""
    content: str
    metrics: RequestMetrics
    raw_response: Optional[ChatCompletion] = None
    parsed_json: Optional[Dict] = None
    
    def __post_init__(self):
        """Try to parse content as JSON if possible"""
        # Safety: if content arrived as dict (SDK v2 json_object mode), serialise it
        if isinstance(self.content, dict):
            self.parsed_json = self.content
            self.content = json.dumps(self.content, ensure_ascii=False)
        elif self.content is None:
            self.content = ""
        elif self.content and not self.parsed_json:
            try:
                self.parsed_json = json.loads(self.content)
            except json.JSONDecodeError:
                pass


class AzureOpenAIClient:
    """
    Unified client for Azure OpenAI model interactions.
    Supports both GPT-4 and GPT-5 series models with metrics collection.
    """
    
    def __init__(
        self,
        endpoint: str = None,
        api_key: str = None,
        api_version: str = "2024-12-01-preview",
        config_path: str = None
    ):
        """
        Initialize the Azure OpenAI client.
        
        Args:
            endpoint: Azure OpenAI endpoint URL
            api_key: API key (or use AZURE_OPENAI_API_KEY env var)
            api_version: API version string
            config_path: Path to settings.yaml config file
        """
        # Load config if provided
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                azure_config = config.get('azure', {})
                endpoint = endpoint or azure_config.get('endpoint')
                api_key = api_key or azure_config.get('api_key')
                api_version = api_version or azure_config.get('api_version', api_version)
        
        # Resolve environment variable references (${VAR_NAME} syntax)
        self.endpoint = self._resolve_env_var(endpoint) or os.getenv('AZURE_OPENAI_ENDPOINT')
        self.api_key = self._resolve_env_var(api_key) or os.getenv('AZURE_OPENAI_API_KEY')
        self.api_version = self._resolve_env_var(api_version) or api_version
        
        if not self.endpoint or not self.api_key:
            raise ValueError(
                "Azure OpenAI endpoint and API key are required. "
                "Set via parameters, config file, or environment variables."
            )
        
        # Initialize clients
        self.client = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=self.api_version,
            timeout=300.0,          # 5-minute timeout per request
            max_retries=3,
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=0,   # disable keep-alive
                ),
            ),
        )
        
        self.async_client = AsyncAzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=self.api_version,
            timeout=300.0,
            max_retries=3,
            http_client=httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=20,             # support parallel evaluations
                    max_keepalive_connections=10,
                ),
            ),
        )
        
        # Model configurations
        self.models: Dict[str, ModelConfig] = {}
        
        # Metrics storage
        self.metrics_history: List[RequestMetrics] = []
        
        # Cache setup
        self._cache: Optional[diskcache.Cache] = None
        
    def _resolve_env_var(self, value: str) -> str:
        """Resolve environment variable references like ${VAR_NAME}"""
        if value and value.startswith('${') and value.endswith('}'):
            env_var = value[2:-1]
            return os.getenv(env_var, value)
        return value
    
    def register_model(self, name: str, config: ModelConfig):
        """Register a model configuration by name"""
        self.models[name] = config
        
    def register_models_from_config(self, config_path: str):
        """Load and register models from config file"""
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            
        models_config = config.get('azure', {}).get('models', {})
        for name, params in models_config.items():
            self.register_model(name, ModelConfig(**params))
            
    def enable_caching(self, cache_dir: str = ".cache/prompts"):
        """Enable response caching for repeated queries"""
        self._cache = diskcache.Cache(cache_dir)
        
    def _get_cache_key(self, model: str, messages: List[Dict], **kwargs) -> str:
        """Generate cache key from request parameters"""
        cache_data = {
            'model': model,
            'messages': messages,
            **kwargs
        }
        cache_str = json.dumps(cache_data, sort_keys=True)
        return hashlib.sha256(cache_str.encode()).hexdigest()
    
    def _needs_max_completion_tokens(self, config: ModelConfig) -> bool:
        """Determine if the model requires max_completion_tokens instead of max_tokens.
        
        GPT-5.x, o-series, and reasoning models use max_completion_tokens.
        """
        if config.use_max_completion_tokens is not None:
            return config.use_max_completion_tokens
        
        name = config.deployment_name.lower()
        # GPT-5.x, o1, o3, o4 series and reasoning models need max_completion_tokens
        return any(prefix in name for prefix in ('gpt-5', 'gpt5', 'o1', 'o3', 'o4')) or config.reasoning_effort is not None
    
    def _build_request_params(
        self,
        config: ModelConfig,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        stream: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """Build request parameters dict, handling model-specific differences."""
        request_params = {
            'model': config.deployment_name,
            'messages': messages,
        }
        
        # Use max_completion_tokens for GPT-5/o-series, max_tokens for GPT-4
        token_limit = kwargs.get('max_tokens', config.max_tokens)
        if self._needs_max_completion_tokens(config):
            request_params['max_completion_tokens'] = token_limit
        else:
            request_params['max_tokens'] = token_limit
        
        # Temperature (some reasoning models ignore it, but we send it anyway)
        request_params['temperature'] = kwargs.get('temperature', config.temperature)
        request_params['top_p'] = kwargs.get('top_p', config.top_p)
        request_params['frequency_penalty'] = kwargs.get('frequency_penalty', config.frequency_penalty)
        request_params['presence_penalty'] = kwargs.get('presence_penalty', config.presence_penalty)
        
        # Optional parameters
        seed = kwargs.get('seed', config.seed)
        if seed is not None:
            request_params['seed'] = seed
            
        if response_format:
            request_params['response_format'] = response_format
            
        if tools:
            request_params['tools'] = tools
            
        if stream:
            request_params['stream'] = True
            
        # GPT-5/o-series specific: reasoning_effort
        reasoning_effort = kwargs.get('reasoning_effort', config.reasoning_effort)
        if reasoning_effort:
            request_params['reasoning_effort'] = reasoning_effort
        
        return request_params
    
    def complete(
        self,
        messages: List[Dict[str, str]],
        model_name: str = "gpt4",
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        use_cache: bool = False,
        **kwargs
    ) -> CompletionResult:
        """
        Send a completion request to the specified model.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model_name: Registered model name (e.g., 'gpt4', 'gpt5')
            response_format: Response format specification (e.g., {"type": "json_object"})
            tools: Tool/function definitions for function calling
            use_cache: Whether to use cached responses
            **kwargs: Additional parameters to override model config
            
        Returns:
            CompletionResult with content, metrics, and parsed data
        """
        if model_name not in self.models:
            raise ValueError(f"Model '{model_name}' not registered. Use register_model() first.")
            
        config = self.models[model_name]
        
        # Build request parameters (handles max_tokens vs max_completion_tokens)
        request_params = self._build_request_params(
            config, messages,
            response_format=response_format,
            tools=tools,
            **kwargs
        )
            
        # Check cache
        if use_cache and self._cache:
            cache_key = self._get_cache_key(model_name, messages, **request_params)
            cached = self._cache.get(cache_key)
            if cached:
                return cached
        
        # Initialize metrics
        metrics = RequestMetrics(
            request_id=f"{model_name}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            model=config.deployment_name,
            start_time=time.time()
        )
        
        try:
            # Make the API call
            response = self.client.chat.completions.create(**request_params)
            
            # Extract content — SDK v2 may return a parsed dict with
            # response_format={"type": "json_object"}, so normalise to str.
            raw_content = response.choices[0].message.content if response.choices else ""
            if isinstance(raw_content, dict):
                content = json.dumps(raw_content, ensure_ascii=False)
            elif raw_content is None:
                content = ""
            else:
                content = raw_content
            
            # Finalize metrics
            metrics.finalize(completion=response)
            
            # Create result
            result = CompletionResult(
                content=content,
                metrics=metrics,
                raw_response=response
            )
            
            # Cache if enabled
            if use_cache and self._cache:
                self._cache.set(cache_key, result)
                
            # Store metrics
            self.metrics_history.append(metrics)
            
            return result
            
        except Exception as e:
            metrics.finalize(error=str(e))
            self.metrics_history.append(metrics)
            raise
            
    async def complete_async(
        self,
        messages: List[Dict[str, str]],
        model_name: str = "gpt4",
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> CompletionResult:
        """Async version of complete()"""
        if model_name not in self.models:
            raise ValueError(f"Model '{model_name}' not registered.")
            
        config = self.models[model_name]
        
        request_params = self._build_request_params(
            config, messages,
            response_format=response_format,
            tools=tools,
            **kwargs
        )
            
        metrics = RequestMetrics(
            request_id=f"{model_name}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            model=config.deployment_name,
            start_time=time.time()
        )
        
        try:
            response = await self.async_client.chat.completions.create(**request_params)
            raw_content = response.choices[0].message.content if response.choices else ""
            if isinstance(raw_content, dict):
                content = json.dumps(raw_content, ensure_ascii=False)
            elif raw_content is None:
                content = ""
            else:
                content = raw_content
            metrics.finalize(completion=response)
            self.metrics_history.append(metrics)
            
            return CompletionResult(
                content=content,
                metrics=metrics,
                raw_response=response
            )
        except Exception as e:
            metrics.finalize(error=str(e))
            self.metrics_history.append(metrics)
            raise
            
    def stream_complete(
        self,
        messages: List[Dict[str, str]],
        model_name: str = "gpt4",
        **kwargs
    ) -> Generator[str, None, CompletionResult]:
        """
        Stream completion response token by token.
        
        Yields:
            Content chunks as they arrive
            
        Returns:
            Final CompletionResult after streaming completes
        """
        if model_name not in self.models:
            raise ValueError(f"Model '{model_name}' not registered.")
            
        config = self.models[model_name]
        
        request_params = self._build_request_params(
            config, messages, stream=True, **kwargs
        )
            
        metrics = RequestMetrics(
            request_id=f"{model_name}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            model=config.deployment_name,
            start_time=time.time()
        )
        
        full_content = ""
        first_token = True
        
        try:
            stream = self.client.chat.completions.create(**request_params)
            
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    
                    if first_token:
                        metrics.ttft = time.time() - metrics.start_time
                        first_token = False
                        
                    full_content += content
                    yield content
                    
            metrics.end_time = time.time()
            metrics.total_time = metrics.end_time - metrics.start_time
            metrics.status = "success"
            self.metrics_history.append(metrics)
            
            return CompletionResult(content=full_content, metrics=metrics)
            
        except Exception as e:
            metrics.finalize(error=str(e))
            self.metrics_history.append(metrics)
            raise
            
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary statistics of all requests"""
        if not self.metrics_history:
            return {"total_requests": 0}
            
        successful = [m for m in self.metrics_history if m.status == "success"]
        
        if not successful:
            return {
                "total_requests": len(self.metrics_history),
                "successful_requests": 0,
                "error_rate": 1.0
            }
            
        import numpy as np
        latencies = np.array([m.total_time for m in successful])
        tokens = [m.total_tokens for m in successful]
        
        return {
            "total_requests": len(self.metrics_history),
            "successful_requests": len(successful),
            "error_rate": 1 - (len(successful) / len(self.metrics_history)),
            "latency": {
                "mean": float(np.mean(latencies)),
                "min": float(np.min(latencies)),
                "max": float(np.max(latencies)),
                "p50": float(np.median(latencies)),
                "p95": float(np.percentile(latencies, 95)),
                "p99": float(np.percentile(latencies, 99))
            },
            "tokens": {
                "total": sum(tokens),
                "mean_per_request": sum(tokens) / len(tokens)
            }
        }
        
    def clear_metrics(self):
        """Clear metrics history"""
        self.metrics_history.clear()


def create_client_from_config(config_path: str = "config/settings.yaml") -> AzureOpenAIClient:
    """
    Factory function to create a configured client from settings file.
    
    Args:
        config_path: Path to settings.yaml
        
    Returns:
        Configured AzureOpenAIClient instance
    """
    client = AzureOpenAIClient(config_path=config_path)
    client.register_models_from_config(config_path)
    
    # Load cache settings
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
        
    cache_config = config.get('caching', {})
    if cache_config.get('enabled', False):
        client.enable_caching(cache_config.get('cache_dir', '.cache/prompts'))
        
    return client


# Example usage
if __name__ == "__main__":
    # Demo with mock configuration
    print("Azure OpenAI Client Module")
    print("=" * 50)
    print("\nUsage:")
    print("  from src.clients.azure_openai import create_client_from_config")
    print("  client = create_client_from_config('config/settings.yaml')")
    print("  result = client.complete(messages=[{'role': 'user', 'content': 'Hello'}], model_name='gpt4')")
    print("  print(result.content)")
