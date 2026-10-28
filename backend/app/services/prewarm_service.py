"""
Pre-warms the Gemini Live pipeline while an outbound call is ringing.

Each dial gets its own pipeline keyed by attempt_id, so concurrent or repeated calls
never share caller details. Gemini connects and receives the opening context during
ringing but speaks only after the customer answers (no audio is generated in advance).
Anything not claimed on answer is closed after PREWARM_TTL_SECONDS; a missing or failed
pre-warm simply falls back to the standard cold-start path.
"""
import asyncio
import logging
import os
from typing import Optional

from agent.pipeline import VoicePipelineOrchestrator

logger = logging.getLogger(__name__)

PREWARM_TTL_SECONDS = int(os.getenv("PREWARM_TTL_SECONDS", "90"))
_PREWARMED: dict[str, VoicePipelineOrchestrator] = {}


def prewarm_enabled() -> bool:
    return os.getenv("PREWARM_ENABLED", "true").lower() in ("1", "true", "yes")


async def start_prewarm(attempt_id: str, caller_context: Optional[dict],
                        lead_id: Optional[str] = None, opening_intent: Optional[str] = None) -> None:
    """Starts Gemini Live for this dial attempt; never raises so dialing is unaffected."""
    if not prewarm_enabled() or not attempt_id or not caller_context:
        return
    try:
        ctx = caller_context
        # Mirrors how the media-stream route builds the pipeline once a context is bound
        orchestrator = VoicePipelineOrchestrator(
            websocket=None,
            call_id=f"prewarm-{attempt_id}",
            stream_sid="",
            opening_intent=str(ctx.get("intent") or opening_intent or "outbound_contact_form"),
            lead_id=str(ctx.get("id") or lead_id or ""),
            lead_name=ctx.get("name"),
            lead_city=ctx.get("city"),
            lead_phone=ctx.get("phone"),
            lead_email=ctx.get("email"),
            lead_subject=ctx.get("subject"),
            lead_message=ctx.get("message") or ctx.get("notes"),
            lead_pincode=ctx.get("pincode"),
            caller_context=ctx,
        )
        _PREWARMED[attempt_id] = orchestrator
        await orchestrator.prewarm()
        asyncio.create_task(_expire(attempt_id, orchestrator))
    except Exception as e:
        _PREWARMED.pop(attempt_id, None)
        logger.warning(f"[Prewarm] Could not pre-warm attempt {attempt_id}: {e}")


def claim_prewarmed(attempt_id: Optional[str], opening_intent: Optional[str]) -> Optional[VoicePipelineOrchestrator]:
    """Returns the ready pipeline for this answered attempt, or None to use the standard path."""
    orchestrator = _PREWARMED.pop(attempt_id, None) if attempt_id else None
    if orchestrator is None:
        return None
    if orchestrator._stopped or orchestrator.opening_intent != opening_intent:
        print(f"[Prewarm] Discarding pre-warmed pipeline for attempt {attempt_id} "
              f"(stopped={orchestrator._stopped}, intent={orchestrator.opening_intent} vs {opening_intent})")
        asyncio.create_task(orchestrator.stop())
        return None
    print(f"[Prewarm] Using pre-warmed pipeline for attempt {attempt_id}")
    return orchestrator


async def discard_prewarmed(attempt_id: Optional[str]) -> None:
    """Closes the pre-warmed pipeline of an attempt that ended without being answered."""
    orchestrator = _PREWARMED.pop(attempt_id, None) if attempt_id else None
    if orchestrator is not None:
        print(f"[Prewarm] Closing pre-warmed pipeline for attempt {attempt_id} (call ended unanswered)")
        await orchestrator.stop()


async def _expire(attempt_id: str, orchestrator: VoicePipelineOrchestrator) -> None:
    await asyncio.sleep(PREWARM_TTL_SECONDS)
    if _PREWARMED.get(attempt_id) is orchestrator:
        _PREWARMED.pop(attempt_id, None)
        print(f"[Prewarm] Attempt {attempt_id} not answered within {PREWARM_TTL_SECONDS}s; closing pre-warmed pipeline")
        await orchestrator.stop()
