"""Deliver a call status to the AI-agent webhook: {"lead_id": ..., "status": ...}."""
import logging

import httpx

from backend.app.settings import settings

logger = logging.getLogger(__name__)


async def send_status_to_ai_agent(lead_id: str, status: str) -> bool:
    if not settings.AI_AGENT_STATUS_WEBHOOK_URL or not lead_id or not status:
        return False
    headers = {"Content-Type": "application/json",
               "X-Webhook-Secret": settings.AI_AGENT_WEBHOOK_SECRET}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.AI_AGENT_STATUS_WEBHOOK_URL,
                                         json={"lead_id": str(lead_id), "status": status}, headers=headers)
            if response.status_code not in (200, 201, 202, 204):
                logger.warning("AI-agent status webhook returned HTTP %s: %s",
                               response.status_code, response.text[:200])
                print(f"[AI-Agent Webhook] ⚠️ Received HTTP {response.status_code} for lead {lead_id} ({status}): {response.text[:200]}")
                return False
            print(f"[AI-Agent Webhook] ✅ Dispatched status '{status}' for lead '{lead_id}' (HTTP {response.status_code})")
            return True
    except Exception as e:
        logger.exception("AI-agent status delivery failed for lead %s status %s", lead_id, status)
        print(f"[AI-Agent Webhook] ❌ Delivery failed for lead {lead_id} ({status}): {e}")
        return False

