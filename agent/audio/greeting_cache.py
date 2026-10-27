"""
High-Performance Pre-rendered & Dynamic Greeting Audio Cache.
Handles:
1. Ultra-fast retrieval of standard inbound greeting (8kHz mono mu-law).
2. Background pre-warming/pre-rendering of personalized outbound greetings during telephony ring phase.
3. Broadcast-quality audio polishing: silence trimming, peak normalization (-3dBFS), click-suppressing fade ramps, and G.711 frame alignment.
"""

import os
import sys
import re
import logging
import asyncio
import audioop
from typing import Optional, Dict, Any, Tuple

import numpy as np

from agent.audio.codecs import resample_pcm16
from backend.app.settings import settings

logger = logging.getLogger(__name__)

# Standard Inbound Greeting Text
STANDARD_INBOUND_GREETING = (
    "Hello! Thank you for calling Ultimate Smile Design. My name is Kiara. May I know your name, please?"
)

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_INBOUND_ULAW_PATH = os.path.join(BASE_DIR, "backend", "static", "greeting_inbound.ulaw")
DEFAULT_INBOUND_RAW_PATH = os.path.join(BASE_DIR, "backend", "static", "greeting_inbound_raw.audio")

# In-memory caches
_INBOUND_GREETING_CACHE: Optional[bytes] = None
_DYNAMIC_GREETING_CACHE: Dict[str, bytes] = {}
_DYNAMIC_GREETING_META: Dict[str, Dict[str, Any]] = {}
MAX_DYNAMIC_CACHE_ITEMS = 200

# TTS Model Configuration (gemini-3.8-flash-tts avoids 429 quota limits and delivers warm, responsive speech)
PRIMARY_TTS_MODEL = "gemini-3.8-flash-tts"
FALLBACK_TTS_MODELS = ["gemini-3.8-flash-lite-tts", "gemini-2.5-flash-preview-tts"]


def polish_pcm16_audio(
    pcm_bytes: bytes,
    sample_rate: int = 8000,
    silence_threshold: int = 350,
    target_peak: int = 24000,
) -> bytes:
    """
    Polishes raw 16-bit linear PCM audio for studio-grade telephony playback:
    - Trims dead leading silence (keeps crisp 30ms lead-in).
    - Trims trailing dead air (keeps clean 40ms trail-out).
    - Normalizes peak amplitude to target_peak (-2.7 dBFS) to match live Gemini speech volume.
    - Applies smooth cosine fade-in (10ms) and fade-out (15ms) to eliminate DAC click/pop artifacts.
    - Pads to an exact multiple of 160 samples (20ms frames) for standard telephony framing.
    """
    if not pcm_bytes or len(pcm_bytes) < 4:
        return b""

    arr = np.frombuffer(pcm_bytes, dtype=np.int16).copy()
    if len(arr) == 0:
        return b""

    abs_arr = np.abs(arr)
    active_indices = np.where(abs_arr > silence_threshold)[0]

    # If completely silent or barely audible, return empty or zero-padded frame
    if len(active_indices) == 0:
        remainder = len(arr) % 160
        if remainder > 0:
            arr = np.pad(arr, (0, 160 - remainder), mode="constant")
        return arr.tobytes()

    start_idx = active_indices[0]
    end_idx = active_indices[-1]

    # Leave a crisp 30ms lead margin (240 samples at 8kHz)
    lead_margin = int(sample_rate * 0.030)
    # Leave a crisp 40ms trail margin (320 samples at 8kHz)
    trail_margin = int(sample_rate * 0.040)

    trimmed_start = max(0, start_idx - lead_margin)
    trimmed_end = min(len(arr), end_idx + trail_margin + 1)
    arr = arr[trimmed_start:trimmed_end]

    if len(arr) == 0:
        return b""

    # Peak normalization to target_peak
    current_peak = int(np.max(np.abs(arr)))
    if current_peak > 0:
        # Scale only if significantly different from target
        gain = float(target_peak) / float(current_peak)
        # Bounded gain between 0.2x and 8.0x to avoid noise blowup while allowing full normalization
        gain = max(0.2, min(gain, 8.0))
        float_arr = arr.astype(np.float32) * gain
        np.clip(float_arr, -32767.0, 32767.0, out=float_arr)
        arr = float_arr.astype(np.int16)

    # 10ms smooth half-cosine fade-in
    fade_in_samples = min(int(sample_rate * 0.010), len(arr) // 2)
    if fade_in_samples > 1:
        fade_in_curve = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, fade_in_samples, endpoint=False)))
        arr[:fade_in_samples] = (arr[:fade_in_samples].astype(np.float32) * fade_in_curve).astype(np.int16)

    # 15ms smooth half-cosine fade-out
    fade_out_samples = min(int(sample_rate * 0.015), len(arr) // 2)
    if fade_out_samples > 1:
        fade_out_curve = 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, fade_out_samples, endpoint=False)))
        arr[-fade_out_samples:] = (arr[-fade_out_samples:].astype(np.float32) * fade_out_curve).astype(np.int16)

    # Frame alignment: ensure length is a multiple of 160 samples (20ms at 8kHz)
    remainder = len(arr) % 160
    if remainder > 0:
        pad_len = 160 - remainder
        arr = np.pad(arr, (0, pad_len), mode="constant")

    return arr.tobytes()


def pcm24k_to_polished_mulaw8k(pcm24k_bytes: bytes) -> bytes:
    """
    Converts 24kHz 16-bit linear PCM from Gemini TTS into studio-grade 8kHz mono mu-law audio.
    """
    if not pcm24k_bytes:
        return b""
    # High-quality sinc downsampling from 24000Hz to 8000Hz
    pcm8k, _ = resample_pcm16(pcm24k_bytes, 24000, 8000)
    # Audio polish: silence trimming, normalization, anti-click windowing
    polished_pcm8k = polish_pcm16_audio(pcm8k, sample_rate=8000)
    # Convert to 8-bit ITU-T G.711 mu-law
    mulaw_data = audioop.lin2ulaw(polished_pcm8k, 2)
    return mulaw_data


def load_inbound_greeting_cache() -> Optional[bytes]:
    """
    Dynamic greeting mode active: pre-recorded audio disabled.
    All greetings are synthesized dynamically on-the-fly by Gemini Live.
    """
    return None


def build_greeting_text(context_data: Dict[str, Any], opening_intent: Optional[str] = None) -> str:
    """
    Builds the deterministic greeting text for any caller context and intent.
    Mirrors VoicePipelineOrchestrator greeting construction to guarantee 100% synchronization.
    """
    ctx = context_data or {}
    lead_name = ctx.get("name") or ""
    lead_city = ctx.get("city") or ""
    intent = opening_intent or ctx.get("intent") or ctx.get("opening_intent") or "inbound"

    smile_diff = ctx.get("smile_diff") if isinstance(ctx.get("smile_diff"), dict) else None
    smile_highlight = ""
    if smile_diff and smile_diff.get("has_valid_comparison"):
        raw_highlight = (smile_diff.get("opening_highlight_sentence") or "").strip().rstrip(".!?")
        if raw_highlight:
            smile_highlight = raw_highlight

    if lead_name and lead_name.lower() not in ("there", "none", "", "the caller", "customer", "the customer"):
        city_str = f" in {lead_city}" if lead_city else ""
        if intent == "outbound_booking_form":
            first_name = ctx.get("first_name") or lead_name.split()[0]
            clean_first_name = re.sub(r"[\x00-\x1F<>\"\\{}]", "", first_name).strip()[:50] or "the customer"
            doctor = ctx.get("doctor", "your selected doctor")
            return (
                f"Hi {clean_first_name}, this is Kiara from Ultimate Smile Design. I'm calling to confirm that we've received your appointment booking request with {doctor}{city_str}. "
                "Your consultation has been scheduled, and our team will contact you shortly to confirm the details. How can I help you today?"
            )
        elif intent == "outbound_smile_preview":
            first_name = ctx.get("first_name") or lead_name.split()[0]
            clean_first_name = re.sub(r"[\x00-\x1F<>\"\\{}]", "", first_name).strip()[:50] or "the customer"
            preview_clause = (
                f"{smile_highlight}. It naturally suits your face and matches your personality"
                if smile_highlight
                else "It naturally suits your face and matches your personality"
            )
            return (
                f"Hi {clean_first_name}, this is Kiara from Ultimate Smile Design. "
                f"I saw your AI Smile Preview, {preview_clause}! "
                f"How did the simulation look to you?"
            )
        elif intent in ("follow-up", "outbound_contact_form"):
            return (
                f"Hi {lead_name}, this is Kiara from Ultimate Smile Design, "
                f"following up on your{city_str} smile consultation enquiry. How can I help you today?"
            )
        else:
            return f"Hi {lead_name}, this is Kiara from Ultimate Smile Design. How can I help you today?"
    else:
        if intent == "outbound_booking_form":
            city_str = f" {lead_city}" if lead_city else ""
            doctor = ctx.get("doctor", "your selected doctor")
            return (
                f"Hello! This is Kiara from Ultimate Smile Design. I'm calling to confirm that we've received your appointment booking request with {doctor}{city_str}. "
                "Your consultation has been scheduled, and our team will contact you shortly to confirm the details. How can I help you today?"
            )
        elif intent == "outbound_smile_preview":
            city_str = f" in {lead_city}" if lead_city else ""
            preview_clause = (
                f"{smile_highlight}. It naturally suits your face and matches your personality"
                if smile_highlight
                else "It naturally suits your face and matches your personality"
            )
            return (
                f"Hello! This is Kiara from Ultimate Smile Design. "
                f"I saw your AI Smile Preview{city_str}, {preview_clause}! "
                f"How did the simulation look to you?"
            )
        elif intent in ("follow-up", "outbound_contact_form"):
            city_str = f" {lead_city}" if lead_city else ""
            return (
                f"Hello! This is Kiara from Ultimate Smile Design, "
                f"following up on your{city_str} smile consultation enquiry. May I know your name, please?"
            )
        else:
            return STANDARD_INBOUND_GREETING


async def synthesize_greeting_audio(text: str, voice_name: Optional[str] = None) -> Optional[bytes]:
    """
    Synthesizes greeting text into polished 8kHz mono mu-law audio using Gemini TTS.
    Bounded by a 6.0s timeout to never hang call flows.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("[GreetingCache] No GEMINI_API_KEY available for TTS synthesis.")
        return None

    voice = voice_name or getattr(settings, "GEMINI_LIVE_VOICE", "Aoede") or "Aoede"

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    models_to_try = [PRIMARY_TTS_MODEL] + [m for m in FALLBACK_TTS_MODELS if m != PRIMARY_TTS_MODEL]

    for model_name in models_to_try:
        try:
            async def _do_gen():
                return await client.aio.models.generate_content(
                    model=model_name,
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                            )
                        ),
                    ),
                )

            res = await asyncio.wait_for(_do_gen(), timeout=6.0)
            if not res or not res.candidates:
                continue

            candidate = res.candidates[0]
            if not candidate.content or not candidate.content.parts:
                continue

            for part in candidate.content.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    pcm24k = inline_data.data
                    mulaw8k = pcm24k_to_polished_mulaw8k(pcm24k)
                    logger.info(
                        f"[GreetingCache] Successfully synthesized greeting via {model_name} ({voice}): "
                        f"{len(mulaw8k)} bytes ({len(mulaw8k)/8000:.2f}s)"
                    )
                    return mulaw8k

        except asyncio.TimeoutError:
            logger.warning(f"[GreetingCache] TTS synthesis timed out (>6s) with model {model_name}")
        except Exception as e:
            logger.warning(f"[GreetingCache] TTS synthesis error with model {model_name}: {e}")

    return None


async def prewarm_outbound_greeting(context_data: Dict[str, Any]) -> None:
    """
    Disabled in dynamic greeting mode: outbound calls synthesize their greeting dynamically via Gemini Live upon connection.
    """
    return


def get_greeting_audio(
    greeting_text: str,
    caller_context: Optional[Dict[str, Any]] = None,
) -> Optional[bytes]:
    """
    Dynamic greeting mode active: always returns None to ensure Gemini Live generates all greetings dynamically.
    """
    return None


def remove_dynamic_greeting(lead_id: Optional[str] = None, phone: Optional[str] = None) -> None:
    """Removes dynamic prewarmed greeting audio when a call concludes."""
    if lead_id:
        _DYNAMIC_GREETING_CACHE.pop(lead_id, None)
        _DYNAMIC_GREETING_META.pop(lead_id, None)
    if phone:
        digits = re.sub(r"\D", "", phone)
        for pk in [digits, digits[-10:] if len(digits) >= 10 else None]:
            if pk:
                meta = _DYNAMIC_GREETING_META.get(pk)
                if not meta or not lead_id or meta.get("lead_id") == lead_id:
                    _DYNAMIC_GREETING_CACHE.pop(pk, None)
                    _DYNAMIC_GREETING_META.pop(pk, None)


def clear_dynamic_cache() -> None:
    """Helper to clear dynamic greeting cache for clean testing."""
    _DYNAMIC_GREETING_CACHE.clear()
    _DYNAMIC_GREETING_META.clear()
