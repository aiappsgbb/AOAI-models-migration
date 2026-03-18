"""
Realtime-specific Metrics for Speech-to-Speech Evaluation
=========================================================

Complements the existing metrics dataclasses with audio-specific
measurements that only apply to Realtime API evaluations.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class RealtimeMetrics:
    """Metrics specific to Realtime (speech-to-speech) evaluations.

    These metrics capture audio-specific latency, duration, and cost
    dimensions that do not exist for text-only models.
    """
    # Latency
    mean_time_to_first_audio_ms: float = 0.0
    mean_session_time_ms: float = 0.0
    mean_ws_connect_time_ms: float = 0.0
    p95_session_time_ms: float = 0.0

    # Audio durations
    mean_input_audio_duration_ms: float = 0.0
    mean_output_audio_duration_ms: float = 0.0

    # Tokens
    mean_input_audio_tokens: float = 0.0
    mean_output_audio_tokens: float = 0.0

    # TTS pipeline
    mean_tts_latency_ms: float = 0.0
    tts_cache_hit_rate: float = 0.0  # 0-100%

    # Cost
    audio_cost_per_request: float = 0.0
    total_audio_cost: float = 0.0

    def to_dict(self) -> Dict:
        return {
            'mean_time_to_first_audio_ms': self.mean_time_to_first_audio_ms,
            'mean_session_time_ms': self.mean_session_time_ms,
            'mean_ws_connect_time_ms': self.mean_ws_connect_time_ms,
            'p95_session_time_ms': self.p95_session_time_ms,
            'mean_input_audio_duration_ms': self.mean_input_audio_duration_ms,
            'mean_output_audio_duration_ms': self.mean_output_audio_duration_ms,
            'mean_input_audio_tokens': self.mean_input_audio_tokens,
            'mean_output_audio_tokens': self.mean_output_audio_tokens,
            'mean_tts_latency_ms': self.mean_tts_latency_ms,
            'tts_cache_hit_rate': self.tts_cache_hit_rate,
            'audio_cost_per_request': self.audio_cost_per_request,
            'total_audio_cost': self.total_audio_cost,
        }
