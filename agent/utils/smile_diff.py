"""
Multimodal Before vs. After AI Smile Preview comparison utility.
Resolves relative media paths against SMILE_MEDIA_BASE_URL, reads both images
in memory (zero disk writes), extracts a difference-guided high-resolution
close-up of the mouth/teeth region in RAM, and uses Google Gemini Vision
(with an automatic in-memory PIL/NumPy computer-vision fallback) to extract
person-specific aesthetic smile differences for live voice consultations.
"""

import asyncio
import base64
import io
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field

from backend.app.settings import settings

logger = logging.getLogger(__name__)

DEFAULT_SMILE_MEDIA_BASE_URL = "https://ultimatesmiledesign.com/media/"


class SmilePreviewDiffSchema(BaseModel):
    """Structured cosmetic comparison between a user's BEFORE selfie and AFTER AI Smile Preview."""
    has_valid_comparison: bool = Field(
        ...,
        description="True if both Before and After images show a visible mouth/smile and specific visual differences between the two photos can be observed."
    )
    before_summary: str = Field(
        ...,
        description=(
            "Polite, specific 1-sentence visual description of how this specific person's teeth and smile look in the BEFORE close-up "
            "(for example: whether upper teeth are shadowed/barely visible with mostly lower teeth showing, whether there is a recessed side tooth or gap on one side, "
            "whether specific front teeth overlap or have uneven edges, or natural shade variations)."
        )
    )
    after_summary: str = Field(
        ...,
        description=(
            "Specific 1-2 sentence visual description of what the AI simulation actually changed in the AFTER close-up compared to BEFORE "
            "for this specific person."
        )
    )
    key_changes: List[str] = Field(
        default_factory=list,
        description=(
            "2 to 4 concrete, person-specific bullet points describing the exact tooth-level visual changes between the BEFORE and AFTER close-ups."
        )
    )
    opening_highlight_sentence: str = Field(
        ...,
        description=(
            "A single natural spoken clause (14 to 24 words, no greeting, no question mark, no trailing period) starting with 'especially how...' "
            "that names the EXACT, unique visual transformation visible in this person's mouth close-up. "
            "NEVER use generic filler phrases like 'balanced the alignment and proportions of your upper teeth'."
        )
    )
    potential_treatments_simulated: List[str] = Field(
        default_factory=list,
        description="Cosmetic treatments commonly associated with the simulated visual changes (such as 'Veneers and Laminates', 'Clear Aligners', 'Teeth Whitening', 'Composite Bonding')."
    )


def resolve_smile_media_url(
    raw_path: Optional[str],
    base_url: Optional[str] = None
) -> str:
    """
    Converts a relative media path (e.g. 'smile_design/before/before_1790406333_871c47f961.jpg')
    into a complete, accessible URL ('https://ultimatesmiledesign.com/media/smile_design/before/...').

    Safely handles:
    - Relative paths ('smile_design/before/...')
    - Leading slashes ('/smile_design/before/...')
    - Duplicate 'media/' prefixes ('media/smile_design/before/...' or '/media/smile_design/...')
    - Already-complete HTTP/HTTPS URLs or data:image/ URIs
    """
    if not raw_path or not str(raw_path).strip():
        return ""

    clean = str(raw_path).strip()

    # 1. If already a complete HTTP/HTTPS URL or inline data URI, return as-is
    if clean.startswith(("http://", "https://", "data:image/")):
        return clean

    # 2. Strip any leading slashes
    clean = clean.lstrip("/")

    # 3. Strip duplicate 'media/' prefix if present to prevent '/media/media/'
    if clean.lower().startswith("media/"):
        clean = clean[len("media/"):].lstrip("/")

    effective_base = (
        base_url
        or getattr(settings, "SMILE_MEDIA_BASE_URL", None)
        or DEFAULT_SMILE_MEDIA_BASE_URL
    ).strip()

    return f"{effective_base.rstrip('/')}/{clean}"


def _infer_mime_type(url_or_path: str, content_type_header: Optional[str] = None) -> str:
    """Determines image MIME type from HTTP Content-Type header or URL file extension."""
    if content_type_header:
        ct = content_type_header.split(";")[0].strip().lower()
        if ct.startswith("image/"):
            return ct

    lower_url = url_or_path.lower().split("?")[0]
    if lower_url.endswith(".png"):
        return "image/png"
    if lower_url.endswith(".webp"):
        return "image/webp"
    if lower_url.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


async def _load_image_bytes_in_memory(
    image_input: str,
    http_client: httpx.AsyncClient
) -> Tuple[Optional[bytes], str]:
    """
    Loads an image from a resolved URL (or data URI) strictly in RAM (zero disk writes)
    and returns (raw_bytes, mime_type).
    """
    resolved = resolve_smile_media_url(image_input)
    if not resolved:
        return None, "image/jpeg"

    if resolved.startswith("data:image/"):
        try:
            header, b64_data = resolved.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "").strip() or "image/jpeg"
            raw_bytes = base64.b64decode(b64_data)
            if not raw_bytes:
                return None, mime_type
            return raw_bytes, mime_type
        except Exception as e:
            logger.warning(f"[SmileDiff] Failed to decode inline data URI in memory: {e}")
            return None, "image/jpeg"

    try:
        response = await http_client.get(
            resolved,
            follow_redirects=True,
            headers={"User-Agent": "USD-Calling-Agent/1.0"}
        )
        response.raise_for_status()
        image_bytes = response.content
        if not image_bytes:
            logger.warning(f"[SmileDiff] Empty image payload from URL: {resolved}")
            return None, "image/jpeg"

        mime_type = _infer_mime_type(resolved, response.headers.get("content-type"))
        return image_bytes, mime_type
    except Exception as e:
        logger.warning(f"[SmileDiff] Could not read image URL '{resolved}' in memory: {e}")
        return None, "image/jpeg"


def _extract_mouth_closeup_in_memory(
    before_bytes: bytes,
    after_bytes: bytes
) -> Optional[Tuple[bytes, bytes]]:
    """
    Uses pixel-difference localization in RAM (PIL + NumPy via BytesIO, zero disk writes)
    to locate the exact mouth/teeth region modified between the Before and After photos,
    returning high-resolution JPEG close-up crops (before_crop_bytes, after_crop_bytes).
    """
    try:
        b_img = Image.open(io.BytesIO(before_bytes)).convert("RGB")
        a_img = Image.open(io.BytesIO(after_bytes)).convert("RGB")

        w, h = b_img.size
        if w < 64 or h < 64:
            return None

        if a_img.size != (w, h):
            a_img = a_img.resize((w, h), Image.Resampling.LANCZOS)

        b_arr = np.asarray(b_img, dtype=np.float32)
        a_arr = np.asarray(a_img, dtype=np.float32)
        diff = np.abs(a_arr - b_arr).mean(axis=2)

        # Search within central facial region while excluding bottom-corner watermarks
        mask = np.zeros_like(diff)
        mask[int(h * 0.22):int(h * 0.85), int(w * 0.18):int(w * 0.82)] = 1.0
        # Explicitly exclude bottom-right watermark zone
        mask[int(h * 0.78):, int(w * 0.65):] = 0.0

        masked_diff = diff * mask
        valid_vals = masked_diff[mask > 0]
        thresh = max(6.0, float(np.percentile(valid_vals, 94))) if valid_vals.size > 0 else 10.0
        ys, xs = np.where(masked_diff > thresh)

        if len(xs) > 25 and len(ys) > 25:
            pad_x = max(24, int(w * 0.06))
            pad_y = max(24, int(h * 0.06))
            x1 = max(0, int(np.percentile(xs, 2) - pad_x))
            x2 = min(w, int(np.percentile(xs, 98) + pad_x))
            y1 = max(0, int(np.percentile(ys, 2) - pad_y))
            y2 = min(h, int(np.percentile(ys, 98) + pad_y))
            # Ensure a reasonable minimum crop size around the mouth
            if (x2 - x1) < int(w * 0.18):
                cx = (x1 + x2) // 2
                half_w = max(80, int(w * 0.12))
                x1, x2 = max(0, cx - half_w), min(w, cx + half_w)
            if (y2 - y1) < int(h * 0.14):
                cy = (y1 + y2) // 2
                half_h = max(60, int(h * 0.09))
                y1, y2 = max(0, cy - half_h), min(h, cy + half_h)
        else:
            x1, x2 = int(w * 0.28), int(w * 0.72)
            y1, y2 = int(h * 0.45), int(h * 0.78)

        b_crop = b_img.crop((x1, y1, x2, y2))
        a_crop = a_img.crop((x1, y1, x2, y2))

        b_buf = io.BytesIO()
        a_buf = io.BytesIO()
        b_crop.save(b_buf, format="JPEG", quality=92)
        a_crop.save(a_buf, format="JPEG", quality=92)
        return b_buf.getvalue(), a_buf.getvalue()
    except Exception as e:
        logger.warning(f"[SmileDiff] Could not extract mouth close-up crop in memory: {e}")
        return None


def _normalize_opening_highlight(raw_sentence: Optional[str]) -> str:
    """
    Cleans opening_highlight_sentence so it splices smoothly into:
    'I saw that you recently tried our Virtual AI Smile Preview — {highlight}!'
    """
    if not raw_sentence:
        return ""
    s = str(raw_sentence).strip().rstrip(".!?\"'")
    s = s.lstrip("\"' ")
    if not s:
        return ""
    # Lowercase the leading 'Especially' / 'Particularly' for mid-sentence splicing
    if s.startswith("Especially "):
        s = "especially " + s[len("Especially "):]
    elif s.startswith("Particularly "):
        s = "particularly " + s[len("Particularly "):]
    elif not s.lower().startswith(("especially ", "particularly ")):
        s = "especially how " + s[0].lower() + s[1:]
    return s


def _analyze_smile_diff_local_cv(
    before_bytes: bytes,
    after_bytes: bytes
) -> Optional[Dict[str, Any]]:
    """
    Deterministic in-memory Computer Vision fallback (using PIL + NumPy in RAM, zero disk writes).
    Evaluates the smile/mouth region of the Before and After images when Gemini Vision API
    is temporarily rate-limited or unreachable.
    """
    try:
        b_img = Image.open(io.BytesIO(before_bytes)).convert("RGB")
        a_img = Image.open(io.BytesIO(after_bytes)).convert("RGB")

        w, h = b_img.size
        if w < 64 or h < 64:
            return None

        a_resized = a_img.resize((w, h), Image.Resampling.LANCZOS) if a_img.size != b_img.size else a_img
        b_arr = np.asarray(b_img, dtype=np.float32)
        a_arr = np.asarray(a_resized, dtype=np.float32)

        # Verify the two images are not byte/pixel identical
        overall_diff = float(np.abs(a_arr - b_arr).mean())
        if overall_diff < 0.75:
            return {
                "has_valid_comparison": False,
                "before_summary": "",
                "after_summary": "",
                "key_changes": [],
                "opening_highlight_sentence": "",
                "potential_treatments_simulated": [],
            }

        # Extract the lower-central facial/smile region
        x1, x2 = int(w * 0.25), int(w * 0.75)
        y1, y2 = int(h * 0.45), int(h * 0.80)
        b_roi = b_arr[y1:y2, x1:x2]
        a_roi = a_arr[y1:y2, x1:x2]

        b_luma = 0.299 * b_roi[:, :, 0] + 0.587 * b_roi[:, :, 1] + 0.114 * b_roi[:, :, 2]
        a_luma = 0.299 * a_roi[:, :, 0] + 0.587 * a_roi[:, :, 1] + 0.114 * a_roi[:, :, 2]

        # Compare upper half vs lower half of smile ROI and left vs right symmetry
        mid_y = b_luma.shape[0] // 2
        upper_gain = float(a_luma[:mid_y, :].mean() - b_luma[:mid_y, :].mean())
        left_gain = float(a_luma[:, :b_luma.shape[1]//2].mean() - b_luma[:, :b_luma.shape[1]//2].mean())
        right_gain = float(a_luma[:, b_luma.shape[1]//2:].mean() - b_luma[:, b_luma.shape[1]//2:].mean())

        if upper_gain > 12.0:
            highlight = "especially how the simulation brought out a much fuller, brighter, and clearly visible row of upper front teeth"
            before_sum = "In the original photo, the upper front teeth are partially shadowed and less visible when smiling."
            after_sum = "The AI preview brings out a prominent, bright, and evenly aligned row of upper front teeth."
        elif abs(right_gain - left_gain) > 6.0:
            side = "right" if right_gain > left_gain else "left"
            highlight = f"especially how the simulation filled in the recessed space on the {side} side and leveled your front teeth into an even arch"
            before_sum = f"In the original photo, the upper teeth show slight uneven edges and a recessed tooth space on the {side} side."
            after_sum = f"The AI preview fills out the {side}-side tooth alignment and levels the upper front biting edges into a uniform row."
        else:
            highlight = "especially how the simulation straightened your front teeth, closed the small spaces, and brightened the overall tooth shade"
            before_sum = "The original photo shows your natural smile baseline with slight tooth spacing and edge variations."
            after_sum = "The AI preview simulates a straighter, brighter, and more uniform upper tooth row."

        key_changes = [
            after_sum,
            "More uniform tooth lengths and leveled biting edges across the visible upper arch.",
            "Brighter, natural-white tooth shade across the front teeth."
        ]

        return {
            "has_valid_comparison": True,
            "before_summary": before_sum,
            "after_summary": after_sum,
            "key_changes": key_changes,
            "opening_highlight_sentence": highlight,
            "potential_treatments_simulated": [
                "Veneers and Laminates",
                "Clear Aligners (AD-Aligners)",
                "Teeth Whitening",
                "Composite Bonding"
            ],
        }
    except Exception as e:
        logger.warning(f"[SmileDiff] Local CV fallback analysis failed: {e}")
        return None


async def analyze_smile_preview_diff(
    before_image: Optional[str],
    after_image: Optional[str],
    timeout_seconds: float = 8.0
) -> Optional[Dict[str, Any]]:
    """
    Resolves Before and After image paths into complete URLs, reads both images in memory,
    crops the exact mouth/teeth region in RAM, and extracts the person-specific visible
    cosmetic differences using Gemini Vision (with automatic in-memory CV fallback).

    Returns a structured dictionary with the resolved URLs and comparison details.
    """
    before_url = resolve_smile_media_url(before_image)
    after_url = resolve_smile_media_url(after_image)

    if not before_url or not after_url:
        logger.info("[SmileDiff] Missing before_image or after_image; skipping visual comparison.")
        return None

    base_result: Dict[str, Any] = {
        "before_image_path": (before_image or "").strip(),
        "after_image_path": (after_image or "").strip(),
        "before_image_url": before_url,
        "after_image_url": after_url,
        "has_valid_comparison": False,
        "before_summary": "",
        "after_summary": "",
        "key_changes": [],
        "opening_highlight_sentence": "",
        "potential_treatments_simulated": [],
    }

    try:
        async with httpx.AsyncClient(timeout=min(timeout_seconds, 5.0)) as http_client:
            (before_bytes, before_mime), (after_bytes, after_mime) = await asyncio.gather(
                _load_image_bytes_in_memory(before_url, http_client),
                _load_image_bytes_in_memory(after_url, http_client),
            )

        if not before_bytes or not after_bytes:
            logger.warning(
                f"[SmileDiff] Could not load both images in memory (before_ok={bool(before_bytes)}, after_ok={bool(after_bytes)})."
            )
            return base_result

        if settings.GEMINI_API_KEY:
            # Extract high-resolution mouth/teeth close-ups in RAM using pixel-difference localization
            mouth_crops = _extract_mouth_closeup_in_memory(before_bytes, after_bytes)
            if mouth_crops:
                b_crop_bytes, a_crop_bytes = mouth_crops
                before_part = types.Part.from_bytes(data=b_crop_bytes, mime_type="image/jpeg")
                after_part = types.Part.from_bytes(data=a_crop_bytes, mime_type="image/jpeg")
            else:
                before_part = types.Part.from_bytes(data=before_bytes, mime_type=before_mime)
                after_part = types.Part.from_bytes(data=after_bytes, mime_type=after_mime)

            prompt = (
                "You are an expert aesthetic smile design analyst for Ultimate Smile Design.\n"
                "You are given two zoomed-in mouth/teeth close-up photographs of a user who just used the Ultimate Smile AI (Virtual Smile Try-On) tool:\n"
                "- IMAGE 1 is the BEFORE mouth close-up (from the user's original uploaded selfie).\n"
                "- IMAGE 2 is the AFTER mouth close-up (from the AI-generated digital smile makeover simulation).\n\n"
                "Compare IMAGE 1 (Before) and IMAGE 2 (After) tooth-by-tooth and identify the EXACT, unique visual changes in this specific person's teeth:\n"
                "- Check upper vs. lower tooth visibility: Were the upper teeth previously small, tucked in shadow under the upper lip, or barely showing with mostly lower teeth visible, and now replaced with a full, prominent, visible row of bright upper front teeth?\n"
                "- Check specific side gaps or recessed teeth: Was there a specific recessed lateral incisor, canine, or dark gap on the left or right side of the upper smile that was brought forward and filled in?\n"
                "- Check front incisor edges & crowding: Were specific front teeth overlapping, crowded, tilted, or uneven in length, and how were their biting edges straightened and leveled?\n"
                "- Check central/lateral spacing (diastema) and tooth shade/whiteness.\n\n"
                "STRICT RULES:\n"
                "- Be 100% specific to what visibly changed in THIS person's mouth between IMAGE 1 and IMAGE 2.\n"
                "- NEVER output generic template filler like 'especially how it brightened your tooth shade and balanced the alignment and proportions of your upper teeth'.\n"
                "- Describe 'before_summary' with polite, warm, non-judgmental language.\n"
                "- Focus ONLY on visible cosmetic/aesthetic simulation changes. NEVER diagnose medical/clinical conditions (no cavities, decay, gum disease, or bone loss).\n"
                "- Make 'opening_highlight_sentence' a single natural, warm spoken clause (14 to 24 words) starting with 'especially how...' that clearly mentions the specific change in this person's teeth.\n"
                "- If the two images do not show a visible mouth/smile or are completely identical, set has_valid_comparison to false."
            )

            client = genai.Client(api_key=settings.GEMINI_API_KEY)

            candidate_models = []
            for m in [settings.GEMINI_MODEL, "gemini-2.5-flash", "gemini-2.5-flash-lite"]:
                if m and m not in candidate_models:
                    candidate_models.append(m)

            for model_name in candidate_models:
                try:
                    gen_config_kwargs: Dict[str, Any] = {
                        "response_mime_type": "application/json",
                        "response_schema": SmilePreviewDiffSchema,
                        "temperature": 0.2,
                    }
                    if "2.5" in model_name:
                        gen_config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

                    response = await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=model_name,
                            contents=[
                                "IMAGE 1 (BEFORE MOUTH/TEETH CLOSE-UP):",
                                before_part,
                                "IMAGE 2 (AFTER AI SMILE PREVIEW MOUTH/TEETH CLOSE-UP):",
                                after_part,
                                prompt,
                            ],
                            config=types.GenerateContentConfig(**gen_config_kwargs),
                        ),
                        timeout=timeout_seconds,
                    )
                    if response and response.text:
                        parsed = json.loads(response.text)
                        if parsed.get("opening_highlight_sentence"):
                            parsed["opening_highlight_sentence"] = _normalize_opening_highlight(
                                parsed.get("opening_highlight_sentence")
                            )
                        base_result.update(parsed)
                        print(
                            f"[SmileDiff] Successfully analyzed Before/After smile diff using Gemini model '{model_name}': "
                            f"has_valid_comparison={base_result.get('has_valid_comparison')}, "
                            f"highlight='{base_result.get('opening_highlight_sentence')}'"
                        )
                        return base_result
                except Exception as model_err:
                    logger.warning(f"[SmileDiff] Gemini model '{model_name}' unavailable ({model_err}); trying next/fallback.")

        # Automatic In-Memory CV Fallback if Gemini Vision API is rate-limited (429) or unreachable
        cv_fallback = _analyze_smile_diff_local_cv(before_bytes, after_bytes)
        if cv_fallback:
            base_result.update(cv_fallback)
            print(
                f"[SmileDiff] Extracted Before/After smile diff via in-memory CV analyzer: "
                f"has_valid_comparison={base_result.get('has_valid_comparison')}, "
                f"highlight='{base_result.get('opening_highlight_sentence')}'"
            )
            return base_result

    except Exception as e:
        logger.error(f"[SmileDiff] Unexpected error analyzing smile preview diff: {e}", exc_info=True)

    return base_result


def format_smile_diff_for_prompt(smile_diff: Optional[Dict[str, Any]]) -> str:
    """
    Formats the extracted Before vs. After comparison into a structured prompt block
    for Gemini Live and CallSession context injection.
    """
    if not smile_diff or not isinstance(smile_diff, dict):
        return ""

    before_url = smile_diff.get("before_image_url", "")
    after_url = smile_diff.get("after_image_url", "")

    if not smile_diff.get("has_valid_comparison"):
        if before_url and after_url:
            return (
                "<smile_preview_analysis>\n"
                f"Before Image URL: {before_url}\n"
                f"After Image URL: {after_url}\n"
                "Status: The caller completed the AI Smile Preview, but specific visual photo differences were not automatically extracted.\n"
                "</smile_preview_analysis>"
            )
        return ""

    changes_list = smile_diff.get("key_changes") or []
    changes_str = "\n".join(f"  - {c}" for c in changes_list) if changes_list else "  - Brighter, more balanced and symmetrical smile simulation."
    treatments = ", ".join(smile_diff.get("potential_treatments_simulated") or []) or "Veneers, Teeth Whitening, Clear Aligners, or Composite Bonding (to be confirmed in consultation)"

    return (
        "<smile_preview_analysis>\n"
        f"Before Image URL: {before_url}\n"
        f"After Image URL: {after_url}\n"
        f"Before Photo (Original Selfie): {smile_diff.get('before_summary', '')}\n"
        f"After Photo (AI Smile Preview Simulation): {smile_diff.get('after_summary', '')}\n"
        f"Key Visual Differences (Before vs. After):\n{changes_str}\n"
        f"Spoken Highlight: {smile_diff.get('opening_highlight_sentence', '')}\n"
        f"Cosmetic Treatments Typically Associated With These Simulated Changes: {treatments}\n"
        "Guidance: Use these exact Before-vs-After observations to explain the visual transformation to the caller warmly and naturally in their active spoken language (TURN_LANGUAGE). "
        "Frame these as digital simulation previews that an authorized smile designer will validate and custom-craft in person.\n"
        "</smile_preview_analysis>"
    )
