"""
Conversation summarization utility for Ultimate Smile Design voice agent.
Uses Google Gemini API to extract structured call summaries, patient intent,
sentiment, booking outcome, and actionable follow-ups from live conversation turns.
"""

import json
import logging
import os
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from backend.app.settings import settings

logger = logging.getLogger(__name__)

CALLS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "calls.json")


class CallSummarySchema(BaseModel):
    """Structured CRM schema for post-call conversation summary."""
    summary: str = Field(
        ...,
        description="A concise 2-3 sentence overview of what the caller needed and what was discussed."
    )
    key_concerns: List[str] = Field(
        default_factory=list,
        description="Specific dental concerns, symptoms, treatments, or questions raised by the caller."
    )
    booking_status: str = Field(
        ...,
        description="Booking status outcome: 'booked', 'declined', 'in_progress', or 'not_discussed'."
    )
    customer_sentiment: str = Field(
        ...,
        description="Overall customer sentiment: 'positive', 'neutral', 'hesitant', or 'frustrated'."
    )
    language_used: str = Field(
        ...,
        description="Dominant language spoken by caller: 'English', 'Hindi', 'Gujarati', or 'Mixed'."
    )
    next_actions: List[str] = Field(
        default_factory=list,
        description="Actionable next steps for the dental clinic team (e.g. WhatsApp follow-up, doctor review, appointment confirmation)."
    )


async def generate_call_summary(
    conversation_history: List[Dict[str, Any]],
    caller_info: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Asynchronously generates a structured CRM call summary using Gemini.
    
    Guards against empty or single-turn calls (e.g., caller hung up on greeting)
    to prevent burning unnecessary API tokens.
    """
    if not conversation_history:
        logger.info("[Summarizer] No conversation history provided. Skipping summary.")
        return None

    # Filter out empty turns
    valid_turns = [
        t for t in conversation_history
        if (t.get("content") or t.get("text", "")).strip()
    ]

    # Guard: Require at least 2 conversational turns (e.g. greeting + at least one response)
    if len(valid_turns) < 2:
        logger.info("[Summarizer] Skipped summary: Call was empty or abandoned during greeting (< 2 turns).")
        return None

    # Build clean human-readable transcript for the LLM
    transcript_lines = []
    for turn in valid_turns:
        role = "Caller" if turn.get("role") == "user" else "Kiara (AI Assistant)"
        text = (turn.get("content") or turn.get("text", "")).strip()
        transcript_lines.append(f"{role}: {text}")
    transcript_text = "\n".join(transcript_lines)

    # Sanitize known caller info
    clean_caller_info = {k: v for k, v in (caller_info or {}).items() if v}

    prompt = f"""You are an expert clinical CRM coordinator and analyst for Ultimate Smile Design (a premier dental aligner and cosmetic dental clinic).
Analyze the following recorded phone conversation between a caller and Kiara (the AI assistant) and produce an accurate executive summary.

Caller Information Known Before/During Call:
{json.dumps(clean_caller_info, indent=2) if clean_caller_info else 'None'}

Conversation Transcript:
{transcript_text}

Instructions:
1. If the caller spoke Hindi, Gujarati, or mixed Hinglish, accurately analyze their intent and provide all summary fields in clear English.
2. Accurately identify their dental interests (e.g., clear aligners, smile makeover, braces, implants, pricing inquiry, consultation booking).
3. Accurately reflect whether an in-clinic consultation was successfully scheduled ('booked'), explicitly postponed or refused ('declined'), currently in progress ('in_progress'), or not discussed ('not_discussed').
4. List concrete, helpful next actions for the clinic front desk staff.
"""

    model_name = settings.GEMINI_MODEL or "gemini-2.5-flash"

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CallSummarySchema,
                temperature=0.2,
            )
        )

        if response.text:
            parsed = json.loads(response.text)
            logger.info(f"[Summarizer] Successfully generated summary for call.")
            return parsed
        else:
            logger.warning("[Summarizer] Received empty response from Gemini API.")
            return None

    except Exception as e:
        logger.error(f"[Summarizer Error] Failed to generate conversation summary: {e}", exc_info=True)
        return None


def save_call_record(call_id: str, summary: Optional[Dict[str, Any]], transcript: Optional[List[Dict[str, Any]]] = None, include_transcript: bool = False) -> None:
    """
    Helper to persist call summary and metadata to data/calls.json.
    Safely ignores IO errors so background logging never impacts runtime.
    """
    if not call_id:
        return

    try:
        data = {}
        if os.path.exists(CALLS_FILE):
            try:
                with open(CALLS_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
            except Exception:
                data = {}

        record: Dict[str, Any] = {
            "call_id": call_id,
            "summary": summary,
            "transcript_turn_count": len(transcript) if transcript else 0
        }
        if include_transcript and transcript:
            record["transcript"] = transcript

        data[call_id] = record

        os.makedirs(os.path.dirname(CALLS_FILE), exist_ok=True)
        with open(CALLS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        logger.info(f"[Summarizer] Saved call record for {call_id} to data/calls.json")
    except Exception as e:
        logger.warning(f"[Summarizer] Could not write call record to disk: {e}")
