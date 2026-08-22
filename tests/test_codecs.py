import math
import struct
from agent.audio.codecs import mulaw_to_pcm16, pcm16_to_mulaw, resample_pcm16

def generate_sine_wave_pcm16(frequency: float, duration_sec: float, sample_rate: int) -> bytes:
    """Helper to generate a simple sine wave in 16-bit PCM."""
    num_samples = int(duration_sec * sample_rate)
    pcm_data = bytearray()
    for i in range(num_samples):
        # Generate sine wave
        t = i / sample_rate
        value = int(32767 * math.sin(2 * math.pi * frequency * t))
        # Pack as 16-bit PCM
        pcm_data.extend(struct.pack("<h", value))
    return bytes(pcm_data)

def test_mulaw_pcm_roundtrip():
    # 20ms of audio at 8kHz is 160 samples (20ms frame size)
    original_pcm = generate_sine_wave_pcm16(440.0, 0.02, 8000)
    
    # PCM -> mulaw
    mulaw = pcm16_to_mulaw(original_pcm)
    assert len(mulaw) == 160, "8kHz mulaw for 20ms should be 160 bytes"
    
    # mulaw -> PCM
    recovered_pcm = mulaw_to_pcm16(mulaw)
    assert len(recovered_pcm) == 320, "8kHz 16-bit PCM for 20ms should be 320 bytes"
    
    # Since mu-law is lossy, we don't expect bit-identical round trip, 
    # but the length should match.
    assert len(original_pcm) == len(recovered_pcm)

def test_resampling():
    original_pcm_8k = generate_sine_wave_pcm16(440.0, 0.02, 8000)
    assert len(original_pcm_8k) == 320
    
    # Resample 8kHz -> 16kHz (Inbound path)
    resampled_16k, state = resample_pcm16(original_pcm_8k, 8000, 16000)
    assert 636 <= len(resampled_16k) <= 642, f"Expected ~640 bytes, got {len(resampled_16k)}"
    
    # Resample back 16kHz -> 8kHz
    resampled_8k, _ = resample_pcm16(resampled_16k, 16000, 8000, state)
    assert len(resampled_8k) == 320, "8kHz 16-bit PCM should be back to 320 bytes"

def test_direct_24k_to_8k_resampling():
    # 100ms of audio at 24kHz = 2400 samples = 4800 bytes
    pcm_24k = generate_sine_wave_pcm16(440.0, 0.10, 24000)
    assert len(pcm_24k) == 4800

    # Direct 24kHz -> 8kHz (Outbound path: 3:1 integer decimation)
    resampled_8k, state = resample_pcm16(pcm_24k, 24000, 8000)
    # Expected: 800 samples = 1600 bytes at 8kHz PCM16
    assert len(resampled_8k) == 1600, f"Expected 1600 bytes, got {len(resampled_8k)}"

    # Convert to mu-law: 800 samples = 800 bytes
    mulaw = pcm16_to_mulaw(resampled_8k)
    assert len(mulaw) == 800, f"Expected 800 bytes mu-law, got {len(mulaw)}"

    # Check 160-byte frame division (5 frames of 20ms each)
    assert len(mulaw) % 160 == 0
    assert len(mulaw) // 160 == 5

def test_direct_24k_to_8k_chunked_streaming():
    # Stream 1 second of 24kHz audio in 20ms chunks (480 samples = 960 bytes each)
    full_24k = generate_sine_wave_pcm16(440.0, 1.0, 24000)
    chunk_size = 960  # 20ms at 24kHz
    state = None
    accumulated_8k = bytearray()

    for i in range(0, len(full_24k), chunk_size):
        chunk = full_24k[i:i + chunk_size]
        resampled_chunk, state = resample_pcm16(chunk, 24000, 8000, state)
        accumulated_8k.extend(resampled_chunk)

    # 1 second of 8kHz PCM16 should be exactly 8000 samples = 16000 bytes (within +-2 samples)
    assert 15990 <= len(accumulated_8k) <= 16010, f"Got {len(accumulated_8k)} bytes"

