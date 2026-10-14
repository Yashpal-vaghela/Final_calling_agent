"""
Audio codec utilities for μ-law <-> PCM16 conversion and high-fidelity resampling.

Supports both:
1. `soxr` (libsoxr 64-bit bandlimited sinc interpolation with steep anti-aliasing)
   for crystal-clear telephony audio without high-frequency attenuation.
2. `audioop` (provided by `audioop-lts` in Python 3.13+) as lightweight C fallback.
"""
import os
from typing import Tuple, Optional, Any

try:
    import audioop
except ImportError as exc:
    raise ImportError(
        "audioop is required for audio conversions. "
        "On Python 3.13+, install 'audioop-lts' (which provides the 'audioop' module)."
    ) from exc

import numpy as np

try:
    import soxr
    HAS_SOXR = True
except ImportError:
    HAS_SOXR = False
    soxr = None


def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    """
    Converts μ-law audio to 16-bit PCM.
    
    Args:
        mulaw_bytes (bytes): Bytes containing μ-law audio.
        
    Returns:
        bytes: 16-bit PCM audio.
    """
    return audioop.ulaw2lin(mulaw_bytes, 2)


def pcm16_to_mulaw(pcm_bytes: bytes) -> bytes:
    """
    Converts 16-bit PCM audio to μ-law.
    
    Args:
        pcm_bytes (bytes): Bytes containing 16-bit PCM audio.
        
    Returns:
        bytes: μ-law audio.
    """
    return audioop.lin2ulaw(pcm_bytes, 2)


def soxr_resample_pcm16(
    pcm_bytes: bytes,
    in_rate: int,
    out_rate: int,
    state: Optional[Any] = None,
    quality: str = "HQ"
) -> Tuple[bytes, Optional[Any]]:
    """
    High-fidelity stateful resampling for 16-bit mono linear PCM using libsoxr (64-bit bandlimited sinc).
    Maintains overlap filter state across streaming chunks to eliminate boundary phase clicks while
    preserving crisp high frequencies (1.5 kHz - 3.8 kHz) for speech clarity.
    
    Args:
        pcm_bytes (bytes): 16-bit PCM mono audio bytes.
        in_rate (int): Input sample rate (e.g., 24000 or 8000).
        out_rate (int): Output sample rate (e.g., 8000 or 16000).
        state (tuple, optional): State tuple carrying overlap samples from previous chunk.
        quality (str): Quality setting ('QQ', 'LQ', 'MQ', 'HQ', 'VHQ').
        
    Returns:
        Tuple[bytes, tuple]: Resampled audio bytes and new state for continuous streaming.
    """
    if in_rate == out_rate:
        return pcm_bytes, state
    if not pcm_bytes:
        return b"", state

    if not HAS_SOXR or soxr is None:
        raise RuntimeError("soxr is required for soxr_resample_pcm16. Please install 'soxr'.")

    in_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
    
    # 48 input samples (~2ms at 24kHz, 6ms at 8kHz) gives seamless overlap-save across frames
    overlap_in = 48
    if state is not None and isinstance(state, tuple) and len(state) >= 1 and isinstance(state[0], np.ndarray):
        history = state[0]
        extended = np.concatenate([history, in_arr])
        out_arr = soxr.resample(extended, in_rate, out_rate, quality=quality).astype(np.int16)
        out_overlap = round(len(history) * (out_rate / in_rate))
        out_valid = out_arr[out_overlap:]
    else:
        out_valid = soxr.resample(in_arr, in_rate, out_rate, quality=quality).astype(np.int16)

    keep_len = min(len(in_arr), overlap_in)
    new_state = (in_arr[-keep_len:], None)
    return out_valid.tobytes(), new_state


def resample_pcm16(
    pcm_bytes: bytes, 
    in_rate: int, 
    out_rate: int, 
    state: Optional[Any] = None,
    engine: Optional[str] = None
) -> Tuple[bytes, Optional[Any]]:
    """
    Resamples 16-bit mono linear PCM audio.
    
    When HAS_SOXR is available and engine is not explicitly 'audioop', uses libsoxr for
    high-fidelity bandlimited sinc interpolation with steep anti-aliasing to eliminate
    muffled/muddy telephone audio.
    Falls back gracefully to audioop.ratecv.
    
    Args:
        pcm_bytes (bytes): 16-bit PCM mono audio bytes.
        in_rate (int): Original sample rate (e.g., 24000 or 8000).
        out_rate (int): Target sample rate (e.g., 8000 or 16000).
        state (tuple, optional): State from previous call for continuous streaming.
        engine (str, optional): 'soxr' or 'audioop'. Defaults to AUDIO_RESAMPLE_ENGINE env or 'soxr'.
        
    Returns:
        Tuple[bytes, tuple]: Resampled audio bytes and the new state for the next chunk.
    """
    if in_rate == out_rate:
        return pcm_bytes, state
        
    if not pcm_bytes:
        return b"", state

    active_engine = engine or os.getenv("AUDIO_RESAMPLE_ENGINE", "soxr" if HAS_SOXR else "audioop")
    if active_engine == "soxr" and HAS_SOXR:
        return soxr_resample_pcm16(pcm_bytes, in_rate, out_rate, state)

    # If state was from soxr, reset it to None for audioop to avoid TypeError
    if state is not None and isinstance(state, tuple) and len(state) >= 1 and isinstance(state[0], np.ndarray):
        state = None

    # audioop.ratecv arguments: (fragment, width, nchannels, inrate, outrate, state[, weightA[, weightB]])
    # For 16-bit mono PCM: width=2, nchannels=1
    resampled_bytes, new_state = audioop.ratecv(pcm_bytes, 2, 1, in_rate, out_rate, state)
    return resampled_bytes, new_state
