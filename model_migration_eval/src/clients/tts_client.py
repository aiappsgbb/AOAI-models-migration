"""
Text-to-Speech Client for Speech-to-Speech Evaluation
=====================================================

Converts test-case text to audio using Azure OpenAI TTS models
(e.g. ``gpt-4o-mini-tts``).  Generated audio is cached on disk so
repeated evaluation runs don't re-synthesize the same text.

The client produces raw PCM16 audio at 24 kHz mono — the native
input format for the Azure Realtime API.
"""

import base64
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from ..utils.audio_utils import AudioSegment, TTSAudioCache

logger = logging.getLogger(__name__)


@dataclass
class TTSConfig:
    """Configuration for the TTS pipeline.

    All fields can be overridden from ``settings.yaml → realtime.tts``.
    """
    model: str = ""           # deployment name — loaded from settings.yaml
    voice: str = "alloy"
    speed: float = 1.0
    response_format: str = "pcm"   # "pcm" → raw PCM16 24 kHz mono
    cache_enabled: bool = True
    cache_dir: str = ".cache/tts_audio"


def load_tts_config_from_settings(settings: dict) -> TTSConfig:
    """Build a ``TTSConfig`` from the ``realtime.tts`` section of settings.yaml."""
    rt = settings.get('realtime', {})
    tts_cfg = rt.get('tts', {})
    return TTSConfig(
        model=tts_cfg.get('deployment_name', 'gpt-4o-mini-tts'),
        voice=tts_cfg.get('voice', 'alloy'),
        speed=float(tts_cfg.get('speed', 1.0)),
        response_format=tts_cfg.get('response_format', 'pcm'),
        cache_enabled=bool(tts_cfg.get('cache_enabled', True)),
        cache_dir=tts_cfg.get('cache_dir', '.cache/tts_audio'),
    )


@dataclass
class TTSResult:
    """Result of a single TTS synthesis."""
    audio: AudioSegment
    tts_latency_ms: float
    cached: bool = False


class TTSClient:
    """
    Text-to-Speech client backed by Azure OpenAI.

    Usage::

        from src.clients.azure_openai import AzureOpenAIClient
        tts = TTSClient(openai_client.client, config=TTSConfig())
        result = tts.synthesize("Hello world")
        # result.audio.data  → raw PCM16 bytes
    """

    def __init__(self, openai_client, config: Optional[TTSConfig] = None):
        """
        Args:
            openai_client: An ``openai.AzureOpenAI`` (sync) client instance.
            config: TTS configuration.
        """
        self._client = openai_client
        self.config = config or TTSConfig()
        self._cache: Optional[TTSAudioCache] = None
        if self.config.cache_enabled:
            self._cache = TTSAudioCache(self.config.cache_dir)

    def synthesize(self, text: str, voice: Optional[str] = None) -> TTSResult:
        """Convert *text* to audio, using cache when available.

        Args:
            text: The text to speak.
            voice: Override voice (default: config voice).

        Returns:
            TTSResult with audio data and timing info.
        """
        voice = voice or self.config.voice

        # Check cache
        if self._cache:
            cached = self._cache.get(text, voice)
            if cached is not None:
                logger.debug("TTS cache hit for voice=%s len=%d", voice, len(text))
                return TTSResult(audio=cached, tts_latency_ms=0.0, cached=True)

        # Call Azure OpenAI TTS
        t0 = time.perf_counter()
        response = self._client.audio.speech.create(
            model=self.config.model,
            voice=voice,
            input=text,
            response_format=self.config.response_format,
            speed=self.config.speed,
        )
        # response is an HttpxBinaryResponseContent; read all bytes
        audio_bytes = response.read()
        latency = (time.perf_counter() - t0) * 1000.0

        segment = AudioSegment(data=audio_bytes)
        logger.info(
            "TTS synthesized: voice=%s len=%d chars -> %.0f ms audio (%.0f ms latency)",
            voice, len(text), segment.duration_ms, latency,
        )

        # Store in cache
        if self._cache:
            self._cache.put(text, voice, segment)

        return TTSResult(audio=segment, tts_latency_ms=latency, cached=False)

    def synthesize_batch(self, texts: List[str], voice: Optional[str] = None) -> List[TTSResult]:
        """Synthesize a list of texts sequentially (cache makes this fast on reruns)."""
        return [self.synthesize(t, voice) for t in texts]
