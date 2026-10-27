"""Restart-safe, leased Zoho status deliveries. Delivery states are not call statuses."""
import asyncio
import logging

from backend.app.services.call_record_service import get_call_store
from backend.app.services.ai_agent_webhook import send_status_to_ai_agent
from backend.app.services.zoho_service import send_call_status_to_zoho
from backend.app.settings import settings

logger = logging.getLogger(__name__)


async def deliver_once() -> bool:
    worked = False
    if settings.ZOHO_FLOW_WEBHOOK_URL:
        worked = await _deliver("zoho", send_call_status_to_zoho)
    if settings.AI_AGENT_STATUS_WEBHOOK_URL:
        worked = await _deliver("ai_agent", _send_to_ai_agent) or worked
    return worked


async def _send_to_ai_agent(payload: dict) -> bool:
    return await send_status_to_ai_agent(payload["lead_id"], payload["status"])


async def _deliver(target: str, sender) -> bool:
    store = get_call_store()
    job = await asyncio.to_thread(store.claim_delivery, None, target)
    if job is None:
        return False
    try:
        success = await sender(job["payload"])
    except asyncio.CancelledError:
        # The lease expires after restart; the stable event ID makes retry safe.
        raise
    except Exception:
        logger.exception("%s delivery failed for event %s", target, job["event_id"])
        success = False
    await asyncio.to_thread(store.finish_delivery, job["event_id"], job["claim_token"], success)
    logger.info("%s status event=%s delivered=%s", target, job["event_id"], success)
    return True


async def delivery_loop():
    while True:
        try:
            worked = await deliver_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Smartflo delivery worker failed; persisted jobs retained")
            worked = False
        await asyncio.sleep(.05 if worked else 1)
