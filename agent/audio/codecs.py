"""
Audio codec utilities for μ-law <-> PCM16 conversion and resampling.

This module uses `audioop` (provided by the `audioop-lts` package in Python 3.13+)
for high-performance, C-level audio conversions without heavyweight dependencies 
like scipy or numpy.
"""
import audioop
from typing import Tuple, Optional

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

import math
import numpy as np
from scipy.signal import resample_poly

def resample_pcm16(
    pcm_bytes: bytes, 
    in_rate: int, 
    out_rate: int, 
    state: Optional[tuple] = None
) -> Tuple[bytes, Optional[tuple]]:
    """
    Resamples 16-bit PCM audio using high-quality Polyphase FIR filtering.
    Maintains streaming state across chunks to prevent boundary clicks.
    
    Args:
        pcm_bytes (bytes): 16-bit PCM audio.
        in_rate (int): Original sample rate (e.g., 24000).
        out_rate (int): Target sample rate (e.g., 8000).
        state (tuple, optional): (zi,) State from previous call for continuous streaming.
        
    Returns:
        Tuple[bytes, tuple]: Resampled audio bytes and the new state for the next chunk.
    """
    if in_rate == out_rate:
        return pcm_bytes, state
        
    pcm_array = np.frombuffer(pcm_bytes, dtype=np.int16)
    if len(pcm_array) == 0:
        return b"", state
        
    gcd = math.gcd(in_rate, out_rate)
    up = out_rate // gcd
    down = in_rate // gcd
    
    # Calculate the filter half-length to determine overlap needed for continuity
    # resample_poly uses a kaiser window with half_len = 10 * max(up, down)
    half_len = 10 * max(up, down)
    overlap_samples = 2 * half_len + 1
    
    prev_overlap = state[0] if state is not None else np.array([], dtype=np.int16)
    
    # Concatenate previous overlap with current chunk
    combined = np.concatenate([prev_overlap, pcm_array])
    
    # Resample the combined block
    resampled = resample_poly(
        combined, 
        up, 
        down, 
        window=('kaiser', 5.0), 
        padtype='constant'
    )
    
    # Discard the output corresponding to the prepended overlap
    if len(prev_overlap) > 0:
        skip_out_samples = math.ceil(len(prev_overlap) * up / down)
    else:
        skip_out_samples = 0
        
    valid_resampled = resampled[skip_out_samples:]
    
    # Save the tail of the current array for the next overlap
    next_overlap = combined[-overlap_samples:] if len(combined) > overlap_samples else combined
    
    # Clip and convert back to int16 bytes
    resampled_int16 = np.clip(np.round(valid_resampled), -32768, 32767).astype(np.int16)
    return resampled_int16.tobytes(), (next_overlap,)
