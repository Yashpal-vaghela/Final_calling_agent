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
    if not expected:
        raise HTTPException(503, "Smartflo webhook authentication is not configured")
    supplied = request.headers.get("X-Smartflo-Webhook-Secret", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Invalid webhook authentication")
    trigger = request.query_params.get("trigger")
    if trigger not in TRIGGERS:
        raise HTTPException(422, "trigger must be customer_answered, hangup_answered, or hangup_missed")
    if request.method == "GET":
        payload = {key: value for key, value in request.query_params.items() if key != "trigger"}
    elif request.headers.get("content-type", "").split(";", 1)[0].strip() == "application/x-www-form-urlencoded":
        payload = dict(await request.form())
    else:
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "A JSON object is required")
    if not isinstance(payload, dict):
        raise HTTPException(422, "A JSON object is required")
    try:
        result = await asyncio.to_thread(get_call_store().receive_event, trigger, payload)
    except (CallStoreError, OSError):
        logger.exception("Smartflo webhook could not be durably stored")
        raise HTTPException(503, "Webhook persistence unavailable; retry delivery")
    logger.info("Smartflo webhook event=%s matched=%s applied=%s duplicate=%s", result["event_id"],
                result["matched"], result["applied"], result["duplicate"])
    return result


@router.get("/internal/smartflo/delivery-health")
async def smartflo_delivery_health(request: Request):
    expected = settings.INTERNAL_KEY
    supplied = request.headers.get("X-Internal-Key", "")
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Unauthorized")
    return await asyncio.to_thread(get_call_store().health)
