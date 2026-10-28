"""Authenticated lifecycle callbacks, separate from the voice-bot endpoint."""
import asyncio
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request
from backend.app.settings import settings
from backend.app.services.call_record_service import get_call_store, TRIGGERS, CallStoreError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/smartflo/webhook")
@router.api_route("/smartflo/events", methods=["GET", "POST"])
@router.api_route("/smartflo/cdr", methods=["GET", "POST"])
async def receive_smartflo_webhook(request: Request):
    expected = settings.SMARTFLO_WEBHOOK_SECRET
    if expected:
        supplied = request.headers.get("X-Smartflo-Webhook-Secret", "") or request.query_params.get("secret", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(401, "Invalid webhook authentication")
    else:
        logger.info("Smartflo webhook received (SMARTFLO_WEBHOOK_SECRET not configured, accepting callback).")

    if request.method == "GET":
        payload = {key: value for key, value in request.query_params.items() if key != "trigger"}
    elif request.headers.get("content-type", "").split(";", 1)[0].strip() == "application/x-www-form-urlencoded":
        payload = dict(await request.form())
    else:
        try:
            payload = await request.json()
        except (ValueError, Exception):
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    trigger_input = str(request.query_params.get("trigger") or payload.get("trigger") or "").strip().lower()

    if trigger_input in TRIGGERS:
        trigger = trigger_input
    elif "missed" in trigger_input:
        trigger = "hangup_missed"
    elif "answered" in trigger_input and "hangup" in trigger_input:
        trigger = "hangup_answered"
    elif "answered" in trigger_input:
        trigger = "customer_answered"
    else:
        # Extract and sanitize status and cause fields
        raw_status = str(
            payload.get("call_status")
            or payload.get("$call_status")
            or payload.get("status")
            or payload.get("$status")
            or ""
        ).strip().lower()
        if raw_status.startswith("$"):
            raw_status = ""

        cause_key = str(
            payload.get("hangup_cause_key")
            or payload.get("hangupcause_key")
            or payload.get("reason_key")
            or ""
        ).strip().upper()
        if cause_key.startswith("$"):
            cause_key = ""

        cause_code = str(
            payload.get("hangup_cause_code")
            or payload.get("hangupcause_code")
            or ""
        ).strip()
        if cause_code.startswith("$"):
            cause_code = ""

        cause_desc = str(
            payload.get("hangup_cause_description")
            or payload.get("hangupcause_desc")
            or payload.get("hangup_cause")
            or ""
        ).strip().lower()
        if cause_desc.startswith("$"):
            cause_desc = ""

        is_connected = payload.get("call_connected") in (True, "true", "True", 1, "1")
        has_end = bool(
            payload.get("end_stamp")
            or payload.get("duration")
            or payload.get("billsec")
            or cause_key
            or cause_code
        )

        is_missed = (
            "missed" in raw_status
            or cause_key in ("NO_ANSWER", "NO_USER_RESPONSE", "USER_BUSY", "CALL_REJECTED", "ORIGINATOR_CANCEL", "UNALLOCATED_NUMBER", "DESTINATION_OUT_OF_ORDER")
            or cause_code in ("1", "17", "18", "19", "21", "27", "486", "603")
            or any(m in cause_desc for m in ("busy", "reject", "no answer", "no user response", "unallocated", "out of order"))
        )

        if is_missed:
            trigger = "hangup_missed"
        elif "answered" in raw_status or is_connected:
            trigger = "hangup_answered" if has_end else "customer_answered"
        elif has_end:
            trigger = "hangup_answered"
        else:
            trigger = "customer_answered"


    try:
        result = await asyncio.to_thread(get_call_store().receive_event, trigger, payload)
    except (CallStoreError, OSError) as e:
        logger.exception("Smartflo webhook storage warning: %s", e)
        return {"success": True, "event_id": None, "warning": str(e)}

    logger.info("Smartflo webhook event=%s trigger=%s matched=%s applied=%s",
                result.get("event_id"), trigger, result.get("matched"), result.get("applied"))

    # A hung-up attempt no longer needs its pre-warmed Gemini session (no-op once answered)
    if trigger.startswith("hangup") and result.get("attempt_id"):
        from backend.app.services.prewarm_service import discard_prewarmed
        asyncio.create_task(discard_prewarmed(result["attempt_id"]))

    # Background: fetch real recording URL from Smartflo CDR API after a completed call
    if trigger == "hangup_answered" and result.get("attempt_id"):
        rec_url = payload.get("recording_url")
        if rec_url and not str(rec_url).startswith("$"):
            print(f"[Recording] 🎙️ Recording URL: {rec_url}")
        from backend.app.services.smartflo_recording_service import enrich_recording
        cid = payload.get("call_id") or payload.get("callId") or payload.get("uuid")
        asyncio.create_task(enrich_recording(
            attempt_id=result["attempt_id"],
            call_id=str(cid) if cid and not str(cid).startswith("$") else None,
            uuid=str(payload.get("uuid")) if payload.get("uuid") and not str(payload.get("uuid")).startswith("$") else None,
        ))

    # Also directly notify AI-agent status webhook if lead_id or ref_id is present
    raw_lead = payload.get("lead_id") or payload.get("ref_id")
    lead_id = str(raw_lead).strip() if isinstance(raw_lead, (str, int)) and not isinstance(raw_lead, bool) else ""
    if lead_id and not lead_id.startswith("$") and lead_id.lower() != "none":
        from backend.app.services.ai_agent_webhook import send_status_to_ai_agent
        from backend.app.services.call_record_service import agent_status_for
        agent_status = agent_status_for(trigger, payload)
        if agent_status:
            asyncio.create_task(send_status_to_ai_agent(lead_id, agent_status))

    return {
        "success": True,
        "event_id": result.get("event_id"),
        "trigger": trigger,
        "matched": result.get("matched"),
        "applied": result.get("applied")
    }



@router.get("/internal/smartflo/delivery-health")
async def smartflo_delivery_health(request: Request):
    expected = settings.INTERNAL_KEY
    supplied = request.headers.get("X-Internal-Key", "")
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Unauthorized")
    return await asyncio.to_thread(get_call_store().health)
