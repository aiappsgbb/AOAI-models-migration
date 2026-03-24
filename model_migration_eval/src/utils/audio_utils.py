"""
Audio Utilities for Speech-to-Speech Evaluation
================================================

Provides helpers for PCM16 audio handling, WAV conversion, duration
calculation, and TTS audio caching.

All audio data flows through this module as raw ``bytes`` in PCM16 LE
format at 24 kHz mono (the native format of the Azure Realtime API).
"""

import hashlib
import io
import json
import struct
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Constants ───────────────────────────────────────────────────────────────

SAMPLE_RATE = 24_000       # Realtime API native rate
CHANNELS = 1               # mono
SAMPLE_WIDTH = 2           # 16-bit (2 bytes per sample)
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 48 000 B/s


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class AudioSegment:
    """Container for a chunk of PCM16 audio with metadata."""
    data: bytes
    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    sample_width: int = SAMPLE_WIDTH

    @property
    def duration_ms(self) -> float:
        """Duration of this audio segment in milliseconds."""
        return pcm16_duration_ms(self.data, self.sample_rate, self.channels, self.sample_width)

    def to_wav_bytes(self) -> bytes:
        """Export as in-memory WAV."""
        return pcm16_to_wav(self.data, self.sample_rate, self.channels, self.sample_width)


# ── Pure functions ──────────────────────────────────────────────────────────

def pcm16_duration_ms(
    data: bytes,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    sample_width: int = SAMPLE_WIDTH,
) -> float:
    """Return duration in milliseconds of raw PCM16 audio data."""
    if not data:
        return 0.0
    bytes_per_second = sample_rate * channels * sample_width
    return (len(data) / bytes_per_second) * 1000.0


def pcm16_to_wav(
    data: bytes,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    sample_width: int = SAMPLE_WIDTH,
) -> bytes:
    """Wrap raw PCM16 data in a WAV header and return the complete WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(data)
    return buf.getvalue()


def wav_to_pcm16(wav_bytes: bytes) -> bytes:
    """Extract raw PCM16 frames from a WAV file in memory."""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        return wf.readframes(wf.getnframes())


def text_to_audio_hash(text: str, voice: str = "alloy") -> str:
    """Deterministic hash for a (text, voice) pair — used as cache key."""
    payload = f"{voice}:{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── TTS Audio Cache ─────────────────────────────────────────────────────────

class TTSAudioCache:
    """Simple file-system cache for TTS-generated audio.

    Structure::

        cache_dir/
            {hash}.pcm16          # raw audio data
            {hash}.meta.json      # { text, voice, duration_ms, sample_rate, created }
    """

    def __init__(self, cache_dir: str = ".cache/tts_audio"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, text: str, voice: str = "alloy") -> Optional[AudioSegment]:
        """Return cached AudioSegment or None if not cached."""
        h = text_to_audio_hash(text, voice)
        pcm_path = self.cache_dir / f"{h}.pcm16"
        if not pcm_path.exists():
            return None
        data = pcm_path.read_bytes()
        return AudioSegment(data=data)

    def put(self, text: str, voice: str, audio: AudioSegment) -> None:
        """Store an AudioSegment in the cache."""
        h = text_to_audio_hash(text, voice)
        pcm_path = self.cache_dir / f"{h}.pcm16"
        meta_path = self.cache_dir / f"{h}.meta.json"
        pcm_path.write_bytes(audio.data)
        meta = {
            "text": text[:200],  # truncate for readability
            "voice": voice,
            "duration_ms": audio.duration_ms,
            "sample_rate": audio.sample_rate,
            "created": time.time(),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def has(self, text: str, voice: str = "alloy") -> bool:
        """Check if the cache has an entry for (text, voice)."""
        h = text_to_audio_hash(text, voice)
        return (self.cache_dir / f"{h}.pcm16").exists()

    def clear(self) -> int:
        """Delete all cached entries. Returns count of files removed."""
        count = 0
        for f in self.cache_dir.iterdir():
            if f.suffix in (".pcm16", ".json"):
                f.unlink(missing_ok=True)
                count += 1
        return count
